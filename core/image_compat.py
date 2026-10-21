"""Find disguised skin images and recover them without changing asset references.

Scanning never writes files. Repairs are atomic per image, with a verified original
in a separate backup directory before each replacement. A cancelled or failed batch
may contain completed images; its exception carries that partial result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Callable, Iterable
import warnings

from PIL import Image, UnidentifiedImageError


MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
MAX_IMAGE_DIMENSION = 131_072
BACKUP_FOLDER = "__conflicts_backup"
_EXCLUDED = {BACKUP_FOLDER.casefold(), ".skin_ini_history", ".git", "__pycache__"}
_FORMATS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP",
    ".gif": "GIF", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF",
}
_LAZER_FORMATS = {"PNG", "JPEG", "BMP", "GIF", "WEBP"}
_RGBA8_MODES = {"1", "L", "LA", "P", "RGB", "RGBA"}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
Progress = Callable[[int, int, str], None]
Cancelled = Callable[[], bool]


@dataclass(frozen=True)
class ImageIssue:
    relative_path: str
    declared_format: str
    actual_format: str
    width: int
    height: int
    status: str
    repairable: bool
    message: str
    sha256: str
    _identity: tuple[int, ...] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ScanResult:
    scanned_count: int
    issues: tuple[ImageIssue, ...]


@dataclass(frozen=True)
class RepairResult:
    repaired: tuple[str, ...]
    backup_dir: Path | None
    skipped: tuple[str, ...]


class ImageRepairError(RuntimeError):
    def __init__(self, message: str, *, partial_result: RepairResult | None = None):
        super().__init__(message)
        self.partial_result = partial_result


class ImageRepairCancelled(ImageRepairError):
    pass


def _identity(info: os.stat_result) -> tuple[int, ...]:
    # Python 3.13 on Windows may report different deprecated ctime values for
    # path.stat() and os.fstat() on the very same handle. File ID, volume, size
    # and precise write time are consistent; the content hash is also checked.
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return identity if os.name == "nt" else (*identity, info.st_ctime_ns)


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _root_path(root: Path) -> Path:
    root = Path(os.path.abspath(root))
    try:
        for part in (root, *root.parents):
            if _linked(part.lstat()):
                raise ImageRepairError("皮肤目录不能包含符号链接或目录联接")
        if not root.is_dir():
            raise ImageRepairError("皮肤目录不存在")
        return root.resolve(strict=True)
    except OSError as exc:
        raise ImageRepairError("无法访问皮肤目录") from exc


def _relative_parts(relative: str, *, allow_backup: bool = False) -> tuple[str, ...]:
    value = str(relative).replace("\\", "/")
    path = PurePosixPath(value)
    if (not value or "\x00" in value or ":" in value or path.is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ImageRepairError("素材路径无效，请重新扫描")
    if not allow_backup and any(part.casefold() in _EXCLUDED for part in path.parts):
        raise ImageRepairError("不能修改备份或内部目录中的素材")
    return path.parts


def _checked_file(root: Path, relative: str) -> tuple[Path, os.stat_result]:
    parts = _relative_parts(relative)
    current = root
    try:
        # Recheck the root as well: it may have been replaced since scanning.
        if _linked(root.lstat()) or root.resolve(strict=True) != root:
            raise ImageRepairError("皮肤目录已经改变，请重新扫描")
        for part in parts:
            current = current / part
            info = current.lstat()
            if _linked(info):
                raise ImageRepairError("素材路径包含符号链接或目录联接，请重新扫描")
        current.resolve(strict=True).relative_to(root)
        if not stat.S_ISREG(info.st_mode):
            raise ImageRepairError("素材已经改变，请重新扫描")
        return current, info
    except (OSError, ValueError) as exc:
        raise ImageRepairError("素材路径已经改变或无法访问，请重新扫描") from exc


def _cancel(cancelled: Cancelled | None, result: RepairResult | None = None) -> None:
    if cancelled is not None and cancelled():
        raise ImageRepairCancelled("操作已取消", partial_result=result)


def _read_file(root: Path, relative: str, cancelled: Cancelled | None,
               expected: tuple[int, ...] | None = None) -> tuple[bytes, tuple[int, ...]]:
    path, info = _checked_file(root, relative)
    identity = _identity(info)
    if expected is not None and identity != expected:
        raise ImageRepairError("素材在扫描后发生变化，请重新扫描")
    if info.st_size > MAX_FILE_BYTES:
        raise ImageRepairError("文件过大，已跳过以避免占用过多内存")
    chunks = []
    read_size = 0
    with path.open("rb") as stream:
        if _identity(os.fstat(stream.fileno())) != identity:
            raise ImageRepairError("素材在扫描后发生变化，请重新扫描")
        while chunk := stream.read(1024 * 1024):
            _cancel(cancelled)
            chunks.append(chunk)
            read_size += len(chunk)
            if read_size > MAX_FILE_BYTES:
                raise ImageRepairError("文件过大，已跳过以避免占用过多内存")
        if _identity(os.fstat(stream.fileno())) != identity:
            raise ImageRepairError("读取期间素材发生变化，请重新扫描")
    _, after = _checked_file(root, relative)
    if _identity(after) != identity:
        raise ImageRepairError("读取期间素材发生变化，请重新扫描")
    return b"".join(chunks), identity


def _size_check(width: int, height: int) -> None:
    if (width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS):
        raise ImageRepairError("图片尺寸过大，已跳过以避免占用过多内存")


def _conversion_obstacle(image: Image.Image, data: bytes) -> str | None:
    if getattr(image, "n_frames", 1) != 1:
        return "包含多帧，自动转换会丢失动画或图层，请手动处理"
    if image.format == "PNG" and len(data) > 24 and data[24] > 8:
        return "包含高位深数据，自动转换可能丢失精度，请手动处理"
    if image.format == "TIFF":
        bits = image.tag_v2.get(258, (8,))
        if isinstance(bits, int):
            bits = (bits,)
        sample = image.tag_v2.get(339, (1,))
        if isinstance(sample, int):
            sample = (sample,)
        if any(bit > 8 for bit in bits) or any(value != 1 for value in sample):
            return "包含高位深或浮点数据，自动转换可能丢失精度，请手动处理"
    if image.mode not in _RGBA8_MODES:
        return "颜色模式无法保证无损转换为 RGBA，请手动处理"
    return None


def _inspect(relative: str, data: bytes, identity: tuple[int, ...]) -> ImageIssue | None:
    declared = _FORMATS[Path(relative).suffix.lower()]
    digest = hashlib.sha256(data).hexdigest()
    actual, width, height = "UNKNOWN", 0, 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual = image.format or "UNKNOWN"
                width, height = image.size
                _size_check(width, height)
                obstacle = _conversion_obstacle(image, data)
                # Decode the complete first image, including images with a valid
                # header and truncated body. Never flatten multi-page images.
                image.load()
                width, height = image.size
                _size_check(width, height)
                mismatch = declared != actual
                if obstacle:
                    return ImageIssue(relative, declared, actual, width, height,
                                      "mismatch" if mismatch else "unsupported", False,
                                      obstacle, digest, identity)
                if mismatch:
                    repairable = declared == "PNG"
                    message = ("后缀为 PNG，内容实际为 " + actual + "，可无损转换为真正的 PNG"
                               if repairable else "实际格式与后缀不符，请手动另存并同步素材引用")
                    return ImageIssue(relative, declared, actual, width, height,
                                      "mismatch", repairable, message, digest, identity)
                if actual not in _LAZER_FORMATS:
                    return ImageIssue(relative, declared, actual, width, height,
                                      "unsupported", False,
                                      "lazer 不支持此格式，请另存为 PNG 并同步素材引用", digest, identity)
                return None
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, ImageRepairError) as exc:
        message = str(exc) if isinstance(exc, ImageRepairError) else "图片损坏或无法解码，请手动检查原图"
        return ImageIssue(relative, declared, actual, width, height,
                          "unreadable", False, message, digest, identity)


def _candidates(root: Path) -> list[str]:
    result = []
    for folder, directories, files in os.walk(root, followlinks=False):
        kept = []
        for name in directories:
            path = Path(folder) / name
            try:
                if name.casefold() not in _EXCLUDED and not _linked(path.lstat()):
                    kept.append(name)
            except OSError:
                continue
        directories[:] = kept
        for name in files:
            path = Path(folder) / name
            if path.suffix.lower() not in _FORMATS:
                continue
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and not _linked(info):
                    result.append(path.relative_to(root).as_posix())
            except OSError:
                continue
    return sorted(result, key=lambda value: (value.casefold(), value))


def scan_skin_images(root: Path, *, cancelled: Cancelled | None = None,
                     progress: Progress | None = None) -> ScanResult:
    root = _root_path(root)
    _cancel(cancelled)
    candidates = _candidates(root)
    issues = []
    for done, relative in enumerate(candidates, 1):
        _cancel(cancelled)
        try:
            data, identity = _read_file(root, relative, cancelled)
            issue = _inspect(relative, data, identity)
        except ImageRepairCancelled:
            raise
        except (OSError, ImageRepairError) as exc:
            message = str(exc) if isinstance(exc, ImageRepairError) else "无法读取图片，请检查文件访问权限"
            issue = ImageIssue(relative, _FORMATS[Path(relative).suffix.lower()], "UNKNOWN",
                               0, 0, "unreadable", False, message, "")
        if issue is not None:
            issues.append(issue)
        if progress is not None:
            progress(done, len(candidates), relative)
    _cancel(cancelled)
    return ScanResult(len(candidates), tuple(issues))


def _backup_directory(root: Path) -> Path:
    parent = root / BACKUP_FOLDER
    if parent.exists() or parent.is_symlink():
        if _linked(parent.lstat()) or not parent.is_dir():
            raise ImageRepairError("备份目录无法安全使用")
    else:
        parent.mkdir()
    parent.resolve(strict=True).relative_to(root)
    return Path(tempfile.mkdtemp(prefix=datetime.now().strftime("image-repair-%Y%m%d-%H%M%S-"), dir=parent))


def _write_verified_backup(directory: Path, relative: str, data: bytes, digest: str) -> None:
    target = directory.joinpath(*_relative_parts(relative))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ImageRepairError("原图备份校验失败，未替换素材")


def _stage_png(path: Path, data: bytes, cancelled: Cancelled | None) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".osu-image-repair-", suffix=".png", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with Image.open(io.BytesIO(data)) as original:
                _size_check(*original.size)
                obstacle = _conversion_obstacle(original, data)
                if obstacle:
                    raise ImageRepairError(obstacle)
                original.load()
                with original.convert("RGBA") as rgba:
                    expected_size = rgba.size
                    expected_pixels = rgba.tobytes()
                    metadata = {}
                    if isinstance(original.info.get("icc_profile"), bytes):
                        metadata["icc_profile"] = original.info["icc_profile"]
                    rgba.save(stream, format="PNG", **metadata)
            stream.flush()
            os.fsync(stream.fileno())
        _cancel(cancelled)
        with Image.open(staged) as decoded:
            decoded.load()
            if (decoded.format != "PNG" or decoded.size != expected_size
                    or decoded.convert("RGBA").tobytes() != expected_pixels):
                raise ImageRepairError("转换后的 PNG 像素校验失败，未替换素材")
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def repair_skin_images(root: Path, issues: Iterable[ImageIssue], *,
                       cancelled: Cancelled | None = None,
                       progress: Progress | None = None) -> RepairResult:
    root = _root_path(root)
    selected = tuple(issues)
    repaired: list[str] = []
    skipped: list[str] = []
    directory: Path | None = None

    def result() -> RepairResult:
        return RepairResult(tuple(repaired), directory, tuple(skipped))

    try:
        seen: set[str] = set()
        for done, issue in enumerate(selected, 1):
            _cancel(cancelled, result())
            key = issue.relative_path.casefold()
            if key in seen:
                continue
            seen.add(key)
            if not issue.repairable:
                skipped.append(issue.relative_path)
            else:
                _relative_parts(issue.relative_path)
                if (issue.declared_format != "PNG" or issue.actual_format == "PNG"
                        or issue._identity is None or not issue.sha256):
                    raise ImageRepairError("修复信息无效，请重新扫描")
                data, identity = _read_file(root, issue.relative_path, cancelled, issue._identity)
                if hashlib.sha256(data).hexdigest() != issue.sha256:
                    raise ImageRepairError("素材内容在扫描后发生变化，请重新扫描")
                checked = _inspect(issue.relative_path, data, identity)
                if checked is None or not checked.repairable:
                    raise ImageRepairError("素材无法安全自动修复，请重新扫描")
                path, _ = _checked_file(root, issue.relative_path)
                staged = _stage_png(path, data, cancelled)
                try:
                    _cancel(cancelled, result())
                    if directory is None:
                        directory = _backup_directory(root)
                    _write_verified_backup(directory, issue.relative_path, data, issue.sha256)
                    _cancel(cancelled, result())
                    # Validate both identity and bytes again immediately before
                    # replacement, including changes while PNG was being encoded.
                    latest, _ = _read_file(root, issue.relative_path, cancelled, identity)
                    if hashlib.sha256(latest).hexdigest() != issue.sha256:
                        raise ImageRepairError("素材内容在转换期间发生变化，未替换素材")
                    path, _ = _checked_file(root, issue.relative_path)
                    _cancel(cancelled, result())
                    os.replace(staged, path)
                    repaired.append(issue.relative_path)
                finally:
                    staged.unlink(missing_ok=True)
            if progress is not None:
                progress(done, len(selected), issue.relative_path)
        _cancel(cancelled, result())
        return result()
    except ImageRepairError as exc:
        exc.partial_result = result()
        raise
    except Exception as exc:
        raise ImageRepairError("修复未能完成，已完成的素材和原图备份已保留",
                               partial_result=result()) from exc
