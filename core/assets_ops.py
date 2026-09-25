# -*- coding: utf-8 -*-
"""Asset discovery and replacement, with recoverable originals."""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Dict, List

IMAGE_EXTS = {".png"}
IMAGE_EXTS_COMMON = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
AUDIO_EXTS_ALLOWED = {".wav", ".ogg", ".mp3"}
AUDIO_EXTS_COMMON = AUDIO_EXTS_ALLOWED | {".flac"}
BACKUP_FOLDER = "__conflicts_backup"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def backup_dir(skin_root: Path) -> Path:
    parent = skin_root / BACKUP_FOLDER
    ensure_dir(parent)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-")
    return Path(tempfile.mkdtemp(prefix=stamp, dir=parent))


def _list_assets(skin_root: Path, extensions: set[str]) -> List[Path]:
    """Do not descend into backups or linked directories."""
    out = []
    for folder, directories, filenames in os.walk(skin_root, followlinks=False):
        directories[:] = [name for name in directories
                          if name.casefold() != BACKUP_FOLDER.casefold()
                          and not (Path(folder) / name).is_symlink()]
        for filename in filenames:
            path = Path(folder) / filename
            if path.suffix.lower() in extensions and path.is_file() and not path.is_symlink():
                out.append(path)
    return sorted(out, key=lambda path: str(path).casefold())


def list_images(skin_root: Path) -> List[Path]:
    return _list_assets(skin_root, IMAGE_EXTS_COMMON)


def list_audio(skin_root: Path) -> List[Path]:
    return _list_assets(skin_root, AUDIO_EXTS_COMMON)


def stem_conflicts(paths: List[Path]) -> Dict[str, List[Path]]:
    """Group the same stem within one directory, matching Windows case rules."""
    by_stem: Dict[str, List[Path]] = {}
    for path in paths:
        key = str(path.resolve().with_suffix("")).casefold()
        by_stem.setdefault(key, []).append(path)
    return {key: values for key, values in by_stem.items() if len(values) > 1}


def _checked_root(dst: Path, skin_root: Path | None) -> Path:
    root = Path(skin_root).resolve() if skin_root else dst.parent.resolve()
    try:
        relative = dst.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("目标文件必须位于当前皮肤目录内") from exc
    if any(part.casefold() == BACKUP_FOLDER.casefold() for part in relative.parts):
        raise ValueError("不能直接替换备份目录中的文件")
    return root


def _backup_files(paths: List[Path], root: Path) -> Path | None:
    existing = list(dict.fromkeys(path for path in paths if path.is_file()))
    if not existing:
        return None
    directory = backup_dir(root)
    for path in existing:
        relative = path.resolve().relative_to(root)
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return directory


def _temporary_destination(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".osu-asset-", suffix=dst.suffix, dir=dst.parent)
    os.close(descriptor)
    return Path(name)


def replace_image(src: Path, dst: Path, make_png: bool = True,
                  *, skin_root: Path | None = None) -> Path:
    """Stage and validate the replacement before backing up the old image."""
    from PIL import Image

    src, original = Path(src), Path(dst)
    final = original.with_suffix(".png") if make_png else original
    root = _checked_root(final, skin_root)
    staged = _temporary_destination(final)
    try:
        with Image.open(src) as image:
            image.load()
            if src.suffix.lower() == final.suffix.lower() and (not make_png or image.format == "PNG"):
                shutil.copy2(src, staged)
            elif make_png:
                image.convert("RGBA").save(staged, format="PNG")
            else:
                image.convert("RGB" if final.suffix.lower() in {".jpg", ".jpeg"} else "RGBA").save(staged)
        _backup_files([original, final], root)
        os.replace(staged, final)
        if original.resolve() != final.resolve() and original.exists():
            original.unlink()
        return final
    finally:
        staged.unlink(missing_ok=True)


def replace_audio(src: Path, dst: Path, prefer_ext: str = ".wav",
                  *, skin_root: Path | None = None) -> Path:
    """Keep encoding and suffix aligned; retain all replaced variants in backups."""
    src, dst = Path(src), Path(dst)
    prefer_ext = prefer_ext.lower()
    if prefer_ext not in AUDIO_EXTS_ALLOWED:
        prefer_ext = ".wav"
    final = dst.with_suffix(prefer_ext)
    root = _checked_root(final, skin_root)
    staged = _temporary_destination(final)
    try:
        if src.suffix.lower() == prefer_ext:
            shutil.copy2(src, staged)
        else:
            try:
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn", str(staged)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    check=True, timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except FileNotFoundError as exc:
                raise RuntimeError("转换音频需要 ffmpeg；也可以选择 WAV、OGG 或 MP3 并保留其格式") from exc
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("音频转换失败，请检查源文件；原素材未更改") from exc
        if not staged.stat().st_size:
            raise RuntimeError("音频文件为空，原素材未更改")
        old_files = [path for path in final.parent.iterdir()
                     if path.is_file() and path.suffix.lower() in AUDIO_EXTS_COMMON
                     and path.stem.casefold() == final.stem.casefold()]
        _backup_files(old_files, root)
        os.replace(staged, final)
        for old in old_files:
            if old.resolve() != final.resolve():
                old.unlink()
        return final
    finally:
        staged.unlink(missing_ok=True)


def resolve_audio_conflicts(skin_root: Path, keep_choice: Dict[str, Path]) -> Path:
    """Back up and remove alternate encodings only beside each chosen file."""
    root = Path(skin_root).resolve()
    to_remove = []
    # Validate all choices before making any changes.
    for keep in keep_choice.values():
        keep = Path(keep).resolve()
        _checked_root(keep, root)
        if not keep.is_file() or keep.suffix.lower() not in AUDIO_EXTS_COMMON:
            raise ValueError("要保留的音频文件不存在或格式不受支持")
        to_remove.extend(path for path in keep.parent.iterdir()
                         if path.is_file() and not path.is_symlink()
                         and path.suffix.lower() in AUDIO_EXTS_COMMON
                         and path.stem.casefold() == keep.stem.casefold()
                         and path.resolve() != keep)
    directory = _backup_files(to_remove, root)
    if directory is None:
        return root / BACKUP_FOLDER
    for path in dict.fromkeys(to_remove):
        path.unlink()
    return directory
