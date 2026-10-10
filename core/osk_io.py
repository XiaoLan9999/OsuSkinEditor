# -*- coding: utf-8 -*-
"""Transactional OSK (ZIP) import/export, including untouched lazer JSON.

UTF-8 ZIP names are supported directly. Older osu!stable exports use CP932;
third-party Chinese ZIPs can explicitly select ``filename_encoding='gb18030'``.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED, ZIP_STORED
import json
import os
import re
import stat
import struct
import tempfile
import zipfile


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_METADATA_BYTES = 1024 * 1024
MAX_DIRECTORY_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
LAZER_LAYOUT_FILES = frozenset({
    'mainhudcomponents.json', 'songselect.json', 'playfield.json',
})
_ENCODINGS = {'utf-8', 'cp932', 'gb18030', 'cp437'}
_SKIN_ASSET = re.compile(
    r'^(?:cursor|hitcircle|approachcircle|slider|spinner|mania[-_]|'
    r'score[-_]|combo[-_]|default[-_]|hit(?:0|50|100|300)|comboburst|'
    r'inputoverlay|key[-_]|menu[-_]|selection[-_]|ranking[-_]|play[-_]|'
    r'pause[-_]|fail[-_]|taiko[-_]|fruit[-_]|(?:normal|soft|drum)[-_])',
    re.IGNORECASE,
)
Progress = Callable[[int, int], None]
Cancelled = Callable[[], bool]


class OskCancelled(InterruptedError):
    """The archive operation was cancelled before it committed."""


@dataclass(frozen=True)
class OskInspection:
    file_count: int
    total_size: int
    has_skin_ini: bool
    has_lazer_metadata: bool
    has_lazer_layouts: bool
    root_prefix: str = ''
    display_name: str | None = None
    creator: str | None = None
    lazer_instantiation: str | None = None
    filename_encoding: str = 'utf-8'
    repaired_directory_entries: int = 0


@dataclass(frozen=True)
class _Entry:
    info: ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


def _check_cancel(cancelled: Cancelled | None):
    if cancelled is not None and cancelled():
        raise OskCancelled('OSK operation cancelled')


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & 0x400)


def _absolute(path: Path) -> Path:
    # resolve() would hide a symlink/junction that must instead be rejected.
    return Path(os.path.abspath(os.fspath(path)))


def _check_path_chain(path: Path):
    for component in (path, *path.parents):
        if os.path.lexists(component) and _is_link(component):
            raise ValueError(f'Symbolic links and junctions are not supported: {component}')


def _archive_parts(name: str) -> tuple[str, ...]:
    name = name.replace('\\', '/')
    parts = tuple(name.rstrip('/').split('/'))
    if (not name or name.startswith('/') or
            any(part in ('', '.', '..') for part in parts)):
        raise ValueError(f'Unsafe archive path: {name}')
    for part in parts:
        if (part != part.rstrip(' .') or
                any(ord(char) < 32 or ord(char) == 127 or char in '<>:"|?*' for char in part) or
                re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', part, re.IGNORECASE)):
            raise ValueError(f'Unsupported archive path: {name}')
    return parts


def _archive_target(name: str, root: Path) -> Path:
    """Retained for callers of the earlier implementation."""
    return root.joinpath(*_archive_parts(name))


def _name_encoding(infos: list[ZipInfo], explicit: str | None) -> str:
    if explicit is not None:
        explicit = explicit.casefold().replace('_', '-')
        explicit = {'gbk': 'gb18030', 'shift-jis': 'cp932', 'shiftjis': 'cp932'}.get(explicit, explicit)
        if explicit not in _ENCODINGS:
            raise ValueError(f'Unsupported ZIP filename encoding: {explicit}')
        return explicit
    legacy = [i.orig_filename.encode('cp437') for i in infos
              if not i.flag_bits & 0x800 and not i.orig_filename.isascii()]
    if not legacy:
        return 'utf-8'
    # A few ZIP tools write UTF-8 names without setting their UTF-8 flag.
    try:
        for name in legacy:
            name.decode('utf-8', errors='strict')
        return 'utf-8'
    except UnicodeDecodeError:
        # Official osu!stable filename fallback; do not guess ambiguous GBK.
        return 'cp932'


def _decoded_name(info: ZipInfo, encoding: str) -> str:
    if '\x00' in info.orig_filename:
        raise ValueError('NUL characters are not supported in ZIP filenames')
    if info.flag_bits & 0x800:
        return info.orig_filename
    try:
        return info.orig_filename.encode('cp437').decode(encoding, errors='strict')
    except UnicodeDecodeError as error:
        raise ValueError(f'ZIP filename does not use {encoding}') from error


def _validate_tree(entries: list[_Entry]):
    # Check implicit parents too: A/x and a/y must not alias on Windows.
    seen_entries = set()
    nodes: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
    for entry in entries:
        identity = tuple(part.casefold() for part in entry.parts)
        if identity in seen_entries:
            raise ValueError(f'Duplicate archive path: {"/".join(entry.parts)}')
        seen_entries.add(identity)
        for length in range(1, len(entry.parts) + 1):
            key = identity[:length]
            path = entry.parts[:length]
            is_dir = length != len(entry.parts) or entry.is_dir
            old = nodes.get(key)
            if old is not None and old != (path, is_dir):
                raise ValueError(f'Conflicting archive path: {"/".join(path)}')
            nodes[key] = (path, is_dir)


def _repair_directory_placeholders(entries: list[_Entry]) -> tuple[list[_Entry], int]:
    """Recognise directory records corrupted by older lazer skin imports.

    Older lazer databases can export an ordinary empty file named ``folder``
    alongside ``folder/asset.png`` (ppy/osu #27540 and #34070). An exact-case
    strict child file is required as evidence; nonempty files and isolated
    empty files retain their original meaning. Tree validation still rejects
    duplicate or case-aliased entries after this narrowly scoped repair.
    """
    parents = {entry.parts[:length] for entry in entries if not entry.is_dir and
               stat.S_IFMT(entry.info.external_attr >> 16) in (0, stat.S_IFREG)
               for length in range(1, len(entry.parts))}
    repaired = []
    count = 0
    for entry in entries:
        kind = stat.S_IFMT(entry.info.external_attr >> 16)
        if (not entry.is_dir and entry.info.file_size == 0 and
                kind in (0, stat.S_IFREG) and entry.parts in parents):
            entry = _Entry(entry.info, entry.parts, True)
            count += 1
        repaired.append(entry)
    return repaired, count


def _looks_like_asset(entry: _Entry) -> bool:
    if entry.is_dir or not entry.info.file_size:
        return False
    filename = entry.parts[-1]
    return bool(_SKIN_ASSET.match(filename)) and Path(filename).suffix.casefold() in {
        '.png', '.jpg', '.jpeg', '.wav', '.ogg', '.mp3',
    }


def _has_skin_files(entries: list[_Entry]) -> bool:
    return any(not entry.is_dir and (
        len(entry.parts) == 1 and entry.parts[0].casefold() in {'skin.ini', 'skininfo.json'}
        or _looks_like_asset(entry)) for entry in entries)


def _read_metadata(archive: ZipFile, entry: _Entry, cancelled: Cancelled | None) -> dict:
    if entry.info.file_size > MAX_METADATA_BYTES:
        return {}
    data = bytearray()
    with archive.open(entry.info) as source:
        while True:
            _check_cancel(cancelled)
            chunk = source.read(min(CHUNK_BYTES, MAX_METADATA_BYTES - len(data) + 1))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_METADATA_BYTES:
                return {}
    try:
        value = json.loads(data.decode('utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (UnicodeError, ValueError, RecursionError):
        return {}


def _scan(archive: ZipFile, *, filename_encoding: str | None,
          cancelled: Cancelled | None) -> tuple[list[_Entry], OskInspection]:
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValueError('OSK contains too many files')
    encoding = _name_encoding(infos, filename_encoding)
    entries = []
    total_size = 0
    for info in infos:
        _check_cancel(cancelled)
        name = _decoded_name(info, encoding)
        is_dir = name.replace('\\', '/').endswith('/')
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
            raise ValueError(f'Special files are not supported: {name}')
        if info.flag_bits & 1:
            raise ValueError('Encrypted OSK files are not supported')
        if info.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            raise ValueError('OSK compression must be ZIP store or deflate')
        if info.file_size < 0 or info.file_size > MAX_FILE_BYTES:
            raise ValueError(f'OSK file exceeds the size limit: {name}')
        if is_dir and info.file_size:
            raise ValueError(f'Archive directory contains file data: {name}')
        total_size += info.file_size
        if total_size > MAX_TOTAL_BYTES:
            raise ValueError('OSK exceeds the unpacked size limit')
        entries.append(_Entry(info, _archive_parts(name), is_dir))
    if not entries:
        raise ValueError('OSK is empty')
    entries, repaired_count = _repair_directory_placeholders(entries)
    for entry in entries:
        if entry.is_dir and not entry.info.orig_filename.replace('\\', '/').endswith('/'):
            _check_cancel(cancelled)
            # A repaired wrapper can be stripped below, so verify its original
            # empty file record before it leaves the extraction entry list.
            with archive.open(entry.info) as source:
                if source.read(1):
                    raise ValueError('Archive directory contains file data')
    _validate_tree(entries)
    root_prefix = ''
    # Root layouts are preferred. Only flatten one unambiguous wrapper.
    root_marker = any(len(e.parts) == 1 and not e.is_dir and
                      e.parts[0].casefold() in {'skin.ini', 'skininfo.json'} for e in entries)
    first_parts = {e.parts[0] for e in entries}
    if not root_marker and len(first_parts) == 1 and all(e.is_dir or len(e.parts) > 1 for e in entries):
        flattened = [_Entry(e.info, e.parts[1:], e.is_dir) for e in entries if len(e.parts) > 1]
        if _has_skin_files(flattened):
            root_prefix = next(iter(first_parts))
            entries = flattened
    _validate_tree(entries)
    root_files = {e.parts[0].casefold(): e for e in entries if not e.is_dir and len(e.parts) == 1}
    metadata_entry = root_files.get('skininfo.json')
    metadata = _read_metadata(archive, metadata_entry, cancelled) if metadata_entry else {}
    has_ini = 'skin.ini' in root_files
    if not has_ini and not any(_looks_like_asset(e) for e in entries) and not any(
            isinstance(metadata.get(key), str) and metadata[key].strip()
            for key in ('Name', 'ID', 'InstantiationInfo')):
        raise ValueError('OSK does not contain recognised skin assets or metadata')
    def text(key):
        value = metadata.get(key)
        return value if isinstance(value, str) and len(value) <= 4096 else None
    result = OskInspection(
        file_count=sum(not e.is_dir for e in entries), total_size=total_size,
        has_skin_ini=has_ini, has_lazer_metadata=metadata_entry is not None,
        has_lazer_layouts=bool(LAZER_LAYOUT_FILES.intersection(root_files)),
        root_prefix=root_prefix, display_name=text('Name'), creator=text('Creator'),
        lazer_instantiation=text('InstantiationInfo'), filename_encoding=encoding,
        repaired_directory_entries=repaired_count,
    )
    return entries, result


def _preflight_archive(path: Path, cancelled: Cancelled | None):
    """Bound the ZIP directory before ZipFile allocates every ZipInfo object.

    The standard-library EOCD reader supports ZIP64 and prepended data while
    reading at most the last 64 KiB plus fixed-size ZIP64 records. Match its
    offset calculation, then scan fixed-size directory headers without ever
    allocating their potentially adversarial names, extra fields or comments.
    """
    with path.open('rb') as stream:
        end = zipfile._EndRecData(stream)
        if end is None:
            raise zipfile.BadZipFile('File is not a ZIP archive')
        count = end[zipfile._ECD_ENTRIES_TOTAL]
        size = end[zipfile._ECD_SIZE]
        if count > MAX_ENTRIES or size > MAX_DIRECTORY_BYTES:
            raise ValueError('OSK contains too many files or an oversized ZIP directory')
        if end[zipfile._ECD_DISK_NUMBER] or end[zipfile._ECD_DISK_START] or end[zipfile._ECD_ENTRIES_THIS_DISK] != count:
            raise ValueError('Multi-disk OSK archives are not supported')
        start = end[zipfile._ECD_LOCATION] - size
        if end[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
            # Older Python returns the standard EOCD position; 3.13+ returns
            # the ZIP64 EOCD position after parsing its locator.
            stream.seek(end[zipfile._ECD_LOCATION])
            if stream.read(4) == zipfile.stringEndArchive:
                start -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
        if start < 0:
            raise zipfile.BadZipFile('Invalid ZIP directory offset')
        directory_end = start + size
        stream.seek(start)
        actual_count = 0
        while stream.tell() < directory_end:
            _check_cancel(cancelled)
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b'PK\x01\x02':
                raise zipfile.BadZipFile('Invalid ZIP directory header')
            filename_size, extra_size, comment_size = struct.unpack_from('<HHH', header, 28)
            next_offset = stream.tell() + filename_size + extra_size + comment_size
            if next_offset > directory_end:
                raise zipfile.BadZipFile('Truncated ZIP directory entry')
            stream.seek(next_offset)
            actual_count += 1
            if actual_count > MAX_ENTRIES:
                raise ValueError('OSK contains too many files')
        if actual_count != count:
            raise zipfile.BadZipFile('ZIP file count is inconsistent')


def _open_archive(osk_path: Path, cancelled: Cancelled | None = None) -> ZipFile:
    archive_path = _absolute(osk_path)
    _check_path_chain(archive_path)
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('OSK exceeds the archive size limit')
    _preflight_archive(archive_path, cancelled)
    # CP437 reversibly bridges unflagged filename bytes for explicit decoding.
    return ZipFile(archive_path)


def inspect_osk(osk_path: Path, *, filename_encoding: str | None = None,
                cancelled: Cancelled | None = None) -> OskInspection:
    """Inspect a ZIP skin without creating a work directory or altering it."""
    _check_cancel(cancelled)
    with _open_archive(osk_path, cancelled) as archive:
        return _scan(archive, filename_encoding=filename_encoding, cancelled=cancelled)[1]


def _check_new_destination(destination: Path):
    _check_path_chain(destination)
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise FileExistsError(f'Destination already exists: {destination}')


def import_osk(osk_path: Path, dest_dir: Path, *, progress: Progress | None = None,
               cancelled: Cancelled | None = None,
               filename_encoding: str | None = None) -> OskInspection:
    """Atomically extract into a new/empty directory, preserving the source OSK.

    Every entry is checked first; CRC/read failure or cancellation removes only
    the staging directory. Existing nonempty skin directories are untouched.
    """
    destination = _absolute(dest_dir)
    _check_new_destination(destination)
    _check_cancel(cancelled)
    with _open_archive(osk_path, cancelled) as archive:
        entries, inspection = _scan(archive, filename_encoding=filename_encoding, cancelled=cancelled)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _check_path_chain(destination.parent)
        with tempfile.TemporaryDirectory(prefix='.osk-import-', dir=destination.parent) as staging:
            staging_root = Path(staging)
            done = 0
            if progress is not None:
                progress(done, inspection.total_size)
            for entry in entries:
                _check_cancel(cancelled)
                staged = staging_root.joinpath(*entry.parts)
                if entry.is_dir:
                    # Verify CRCs even for the zero-byte regular entries which
                    # an older lazer exporter incorrectly used as directories.
                    with archive.open(entry.info) as source:
                        if source.read(1):
                            raise ValueError('Archive directory contains file data')
                    staged.mkdir(parents=True, exist_ok=True)
                    continue
                staged.parent.mkdir(parents=True, exist_ok=True)
                entry_size = 0
                with archive.open(entry.info) as source, staged.open('xb') as output:
                    while True:
                        _check_cancel(cancelled)
                        chunk = source.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        entry_size += len(chunk)
                        done += len(chunk)
                        if entry_size > MAX_FILE_BYTES or entry_size > entry.info.file_size or done > MAX_TOTAL_BYTES:
                            raise ValueError('OSK exceeds the actual unpacked size limit')
                        output.write(chunk)
                        if progress is not None:
                            progress(done, inspection.total_size)
                if entry_size != entry.info.file_size:
                    raise ValueError(f'OSK file size is inconsistent: {entry.info.filename}')
            _check_cancel(cancelled)
            _check_new_destination(destination)
            # Windows cannot replace an existing directory. Only an empty target
            # may be moved aside, then restored on commit failure.
            placeholder = None
            if destination.exists():
                placeholder = staging_root.with_name(staging_root.name + '-empty')
                os.rename(destination, placeholder)
                # A caller can race us by writing into the empty destination
                # after its last check. Restore that directory instead of
                # leaving its new files displaced beside the work copy.
                if any(placeholder.iterdir()):
                    os.rename(placeholder, destination)
                    raise FileExistsError(f'Destination changed while importing: {destination}')
            try:
                os.rename(staging_root, destination)
            except BaseException:
                if placeholder is not None:
                    os.rename(placeholder, destination)
                raise
            else:
                if placeholder is not None:
                    placeholder.rmdir()
    return inspection


def _excluded(relative: Path) -> bool:
    return (
        any(part.casefold() in {'.skin_ini_history', '__conflicts_backup', '.git', '__pycache__'}
            for part in relative.parts) or
        relative.name.casefold() == '.osk-editor.json' or
        bool(re.match(r'^skin\.ini(?:\..*)?\.bak$', relative.name, re.IGNORECASE)) or
        (relative.name.casefold().startswith(('.skin-', '.osk-import-', '.osk-export-')) and
         relative.name.casefold().endswith('.tmp'))
    )


def _file_signature(path: Path) -> tuple:
    _check_path_chain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'Skin asset is not a regular file: {path}')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _export_candidates(source: Path, output: Path, cancelled: Cancelled | None):
    candidates = []
    total_size = 0
    for directory, directories, filenames in os.walk(source, followlinks=False):
        _check_cancel(cancelled)
        parent = Path(directory)
        directories[:] = sorted(name for name in directories
                                if not _excluded((parent / name).relative_to(source)) and
                                not _is_link(parent / name))
        for name in sorted(filenames):
            _check_cancel(cancelled)
            path = parent / name
            relative = path.relative_to(source)
            if path == output or _excluded(relative) or _is_link(path) or not path.is_file():
                continue
            _archive_parts(relative.as_posix())
            signature = _file_signature(path)
            size = signature[2]
            if size > MAX_FILE_BYTES:
                raise ValueError(f'Skin file exceeds the size limit: {relative}')
            total_size += size
            if total_size > MAX_TOTAL_BYTES or len(candidates) >= MAX_ENTRIES:
                raise ValueError('Skin exceeds the OSK size or file count limit')
            candidates.append((path, relative.as_posix(), signature))
    if not candidates:
        raise ValueError('Skin folder is empty')
    _validate_tree([_Entry(ZipInfo(relative), _archive_parts(relative), False)
                    for _, relative, _ in candidates])
    return candidates, total_size


def export_osk(src_dir: Path, out_path: Path, *, progress: Progress | None = None,
               cancelled: Cancelled | None = None):
    """Atomically export a root-layout UTF-8 ZIP, excluding editor backups.

    Failure/cancellation preserves the previous output byte-for-byte. The
    output parent must already exist, as with the original API.
    """
    source = _absolute(src_dir)
    output = _absolute(out_path)
    _check_path_chain(source)
    _check_path_chain(output)
    if not source.is_dir():
        raise NotADirectoryError(source)
    if output == source or output.is_dir():
        raise IsADirectoryError(output)
    _check_cancel(cancelled)
    candidates, total_size = _export_candidates(source, output, cancelled)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.osk-export-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
        done = 0
        if progress is not None:
            progress(done, total_size)
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
            for path, relative, signature in candidates:
                _check_cancel(cancelled)
                if _file_signature(path) != signature:
                    raise ValueError(f'Skin file changed while exporting: {relative}')
                info = ZipInfo.from_file(path, relative)
                info.compress_type = ZIP_DEFLATED
                entry_size = 0
                with path.open('rb') as source_stream, archive.open(info, 'w') as target_stream:
                    while True:
                        _check_cancel(cancelled)
                        chunk = source_stream.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        entry_size += len(chunk)
                        done += len(chunk)
                        if entry_size > signature[2] or done > MAX_TOTAL_BYTES:
                            raise ValueError(f'Skin file changed while exporting: {relative}')
                        target_stream.write(chunk)
                        if progress is not None:
                            progress(done, total_size)
                if entry_size != signature[2] or _file_signature(path) != signature:
                    raise ValueError(f'Skin file changed while exporting: {relative}')
            # Catch external changes to assets that were read earlier too.
            for path, relative, signature in candidates:
                _check_cancel(cancelled)
                if _file_signature(path) != signature:
                    raise ValueError(f'Skin file changed while exporting: {relative}')
            latest, _ = _export_candidates(source, output, cancelled)
            if latest != candidates:
                raise ValueError('Skin folder changed while exporting')
        _check_cancel(cancelled)
        _check_path_chain(output)
        if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError('OSK exceeds the archive size limit')
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
