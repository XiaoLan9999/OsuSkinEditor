# -*- coding: utf-8 -*-
"""Import/export .osk archives, retaining source files and relative paths."""
from zipfile import ZipFile, ZIP_DEFLATED
from pathlib import Path
import os
import re
import shutil
import stat
import tempfile


def _archive_target(name: str, root: Path) -> Path:
    # ZIP uses '/', but archives made on Windows sometimes contain backslashes.
    name = name.replace('\\', '/')
    parts = name.rstrip('/').split('/')
    if (not name or name.startswith('/') or
            any(part in ('', '.', '..') or ':' in part for part in parts)):
        raise ValueError(f'Unsafe archive path: {name}')
    # Windows silently aliases trailing dots/spaces and reserved device names.
    for part in parts:
        if (part != part.rstrip(' .') or
                re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.IGNORECASE)):
            raise ValueError(f'Unsupported archive path: {name}')
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Archive path escapes destination: {name}')
    return target


def import_osk(osk_path: Path, dest_dir: Path):
    """Extract a skin after validating every path; never overwrite existing files."""
    destination = Path(dest_dir).resolve()
    with ZipFile(osk_path) as archive:
        entries = []
        seen = set()
        for entry in archive.infolist():
            target = _archive_target(entry.filename, destination)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f'Symbolic links are not supported: {entry.filename}')
            identity = str(target).casefold()
            if identity in seen:
                raise ValueError(f'Duplicate archive path: {entry.filename}')
            seen.add(identity)
            if target.exists() and (not entry.is_dir() or not target.is_dir()):
                raise FileExistsError(f'Destination already exists: {target}')
            for parent in target.parents:
                if parent == destination.parent:
                    break
                if parent.exists() and not parent.is_dir():
                    raise FileExistsError(f'Destination is not a folder: {parent}')
            entries.append((entry, target))

        # Read and verify all CRCs before placing any files in the destination.
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.osk-import-', dir=destination.parent) as staging:
            staging_root = Path(staging)
            for entry, target in entries:
                staged = staging_root / target.relative_to(destination)
                if entry.is_dir():
                    staged.mkdir(parents=True, exist_ok=True)
                else:
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, staged.open('xb') as output:
                        shutil.copyfileobj(source, output)
            if not destination.exists():
                os.replace(staging_root, destination)
            else:
                for entry, target in entries:
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with (staging_root / target.relative_to(destination)).open('rb') as source, target.open('xb') as output:
                            shutil.copyfileobj(source, output)


def export_osk(src_dir: Path, out_path: Path):
    """Create a compressed archive atomically, excluding editor backups and itself."""
    source = Path(src_dir).resolve()
    output = Path(out_path).resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)
    if output == source or output.is_dir():
        raise IsADirectoryError(output)
    candidates = []
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if (not path.is_file() or path.is_symlink() or path.resolve() == output or
                any(part in ('.skin_ini_history', '__conflicts_backup') for part in relative.parts) or
                path.name.casefold() == 'skin.ini.bak' or
                (path.name.startswith('.skin-') and path.suffix == '.tmp')):
            continue
        if not path.resolve().is_relative_to(source):
            continue
        candidates.append((path, relative.as_posix()))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.osk-export-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
            for path, relative in candidates:
                archive.write(path, relative)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
