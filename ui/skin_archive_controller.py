"""Run archive work off the GUI thread and deliver one terminal result per task."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from core.osk_io import MAX_ENTRIES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, export_osk, import_osk
from core.mania_designer import export_skin


_METADATA_LIMIT = 128 * 1024


class _ArchiveCancelled(Exception):
    pass


@dataclass(frozen=True)
class _Result:
    kind: str
    status: str
    path: str = ""
    inspection: object = None
    source: str = ""
    message: str = ""
    export_root: str = None


def _check_cancelled(event):
    if event.is_set():
        raise _ArchiveCancelled()


def _single_line(value, fallback=""):
    """Metadata belongs on one INI value line, never in additional sections."""
    if not isinstance(value, str):
        value = fallback
    value = "".join(" " if character.isspace() or
                    unicodedata.category(character).startswith("C") else character
                    for character in value)
    return " ".join(value.split())[:200] or fallback


def _workspace_name(source):
    name = re.sub(r'[<>:"/\\|?*]', "_", _single_line(source.stem, "Skin"))
    name = name[:80].strip(" .") or "Skin"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name,
                    re.IGNORECASE):
        name = "Skin_" + name
    return name


def _metadata(root, inspection, source):
    name = _single_line(getattr(inspection, "display_name", ""),
                        _single_line(source.stem, "Skin"))
    author = _single_line(getattr(inspection, "creator", ""), "Unknown")
    candidate = next((path for path in root.iterdir()
                      if path.name.casefold() == "skininfo.json"), None)
    if candidate is None:
        return name, author
    try:
        if not candidate.is_file() or candidate.is_symlink():
            return name, author
        with candidate.open("rb") as stream:
            content = stream.read(_METADATA_LIMIT + 1)
        if len(content) > _METADATA_LIMIT:
            return name, author
        values = json.loads(content.decode("utf-8-sig"))
        if isinstance(values, dict):
            values = {key.casefold(): value for key, value in values.items()
                      if isinstance(key, str)}
            name = _single_line(values.get("name"), name)
            author = _single_line(values.get("creator", values.get("author")), author)
    except (OSError, ValueError, UnicodeError, RecursionError):
        # Metadata is optional; leave its original bytes untouched.
        pass
    return name, author


def _add_default_ini(root, inspection, source):
    if getattr(inspection, "has_skin_ini", False):
        return
    name, author = _metadata(root, inspection, source)
    with (root / "skin.ini").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"[General]\nName: {name}\nAuthor: {author}\nVersion: latest\n")


def _remove_created_workspace(root, parent):
    """Only a direct directory owned by this import is eligible for cleanup."""
    if root.parent != parent or root == parent:
        raise OSError("Cannot remove an unrelated skin workspace")
    try:
        entry = root.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or
            getattr(entry, "st_file_attributes", 0) &
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise OSError("Cannot remove a replaced skin workspace")
    shutil.rmtree(root)


def _source_fingerprint(source, event):
    """Detect source edits around the designer's copy/transform operation."""
    records = []
    total_size = 0
    file_count = 0
    directory_count = 0
    exclusions = {".skin_ini_history", "__conflicts_backup", ".git", "__pycache__"}
    for folder, directories, files in os.walk(source, followlinks=False):
        _check_cancelled(event)
        directories[:] = sorted(name for name in directories if name.casefold() not in exclusions)
        for name in directories:
            directory_count += 1
            if directory_count > MAX_ENTRIES:
                raise ValueError("Skin exceeds the OSK folder count limit")
            path = Path(folder) / name
            entry = path.lstat()
            if (stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 0x400):
                raise ValueError("Linked skin folders cannot be exported")
            records.append((path.relative_to(source).as_posix(), "directory"))
        for name in sorted(files):
            if (name.casefold() == "skin.ini.bak" or
                    (name.startswith(".skin-") and name.endswith(".tmp"))):
                continue
            path = Path(folder) / name
            entry = path.lstat()
            if (not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or
                    getattr(entry, "st_file_attributes", 0) & 0x400):
                raise ValueError("Linked or special skin files cannot be exported")
            total_size += entry.st_size
            file_count += 1
            if (entry.st_size > MAX_FILE_BYTES or total_size > MAX_TOTAL_BYTES or
                    file_count > MAX_ENTRIES):
                raise ValueError("Skin exceeds the OSK size or file count limit")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while True:
                    _check_cancelled(event)
                    chunk = stream.read(256 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            after = path.stat()
            signature = (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)
            if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise OSError("The skin changed during export; reload it and try again")
            records.append((path.relative_to(source).as_posix(), signature, digest.digest()))
    return tuple(sorted(records))


class ArchiveController(QObject):
    imported = Signal(str, object, str)
    exported = Signal(str)
    failed = Signal(str, str)
    cancelled = Signal(str)
    progress = Signal(str, int, int)
    busy_changed = Signal(bool)

    _worker_progress = Signal(int, str, int, int)

    def __init__(self, parent=None, *, importer=None, exporter=None, design_exporter=None):
        super().__init__(parent)
        self._importer = importer or import_osk
        self._exporter = exporter or export_osk
        self._design_exporter = design_exporter or export_skin
        self._busy = False
        self._closed = False
        self.last_export_root = None
        self._generation = 0
        self._kind = None
        self._future = None
        self._executor = None
        self._cancel_event = None
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        self._worker_progress.connect(self._deliver_progress, Qt.QueuedConnection)

    @property
    def busy(self):
        return self._busy

    def import_file(self, source, workspace_parent):
        return self._start("import", source, workspace_parent)

    def export_folder(self, source, out, *, design=None, keys=4, working_parent=None):
        return self._start("export", source, out, design=design, keys=keys,
                           working_parent=working_parent)

    def cancel(self):
        if self._cancel_event is not None:
            self._cancel_event.set()

    def close(self):
        if self._busy:
            self.cancel()
            return False
        self._closed = True
        self._timer.stop()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        return True

    def _start(self, kind, source, destination, *, design=None, keys=4, working_parent=None):
        if self._busy or self._closed:
            return False
        self._generation += 1
        token = self._generation
        event = threading.Event()
        self._cancel_event = event
        self._kind = kind
        self.last_export_root = None
        self._busy = True
        self.busy_changed.emit(True)
        try:
            self._executor = ThreadPoolExecutor(max_workers=1,
                                                thread_name_prefix="SkinArchive")
            self._future = self._executor.submit(self._work, token, kind,
                                                 source, destination, event,
                                                 design, keys, working_parent)
        except Exception as error:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
            self._clear_task()
            self.failed.emit(kind, str(error))
            return False
        self._timer.start()
        return True

    def _work(self, token, kind, source, destination, event, design, keys, working_parent):
        workspace = None
        workspace_parent = None
        last_report = 0.0

        def report(done, total):
            nonlocal last_report
            now = time.monotonic()
            if done == 0 or done == total or now - last_report >= 0.03:
                last_report = now
                self._worker_progress.emit(token, kind, int(done), int(total))

        try:
            _check_cancelled(event)
            source = Path(source).expanduser().absolute()
            destination = Path(destination).expanduser().absolute()
            if kind == "import":
                workspace_parent = destination.resolve()
                workspace_parent.mkdir(parents=True, exist_ok=True)
                for _ in range(5):
                    candidate = workspace_parent / f"{_workspace_name(source)}-{uuid.uuid4().hex[:10]}"
                    try:
                        candidate.mkdir()
                    except FileExistsError:
                        continue
                    workspace = candidate
                    break
                if workspace is None:
                    raise FileExistsError("Could not create a new skin workspace")
                _check_cancelled(event)
                inspection = self._importer(source, workspace, progress=report,
                                             cancelled=event.is_set)
                _check_cancelled(event)
                _add_default_ini(workspace, inspection, source)
                _check_cancelled(event)
                return _Result(kind, "success", str(workspace), inspection, str(source))
            export_source = source
            if design is not None:
                if not source.is_dir():
                    raise NotADirectoryError(source)
                source_info = source.lstat()
                if (source.is_symlink() or getattr(source_info, "st_file_attributes", 0) & 0x400):
                    raise ValueError("Linked skin folders cannot be exported")
                workspace_parent = (Path(working_parent).expanduser().resolve()
                                    if working_parent is not None else source.parent)
                if workspace_parent == source or workspace_parent.is_relative_to(source):
                    raise ValueError("Choose a design workspace outside the source skin folder")
                workspace_parent.mkdir(parents=True, exist_ok=True)
                fingerprint = _source_fingerprint(source, event)
                candidate = workspace_parent / f"{_workspace_name(source)}-{uuid.uuid4().hex[:10]}"
                report(0, 0)
                _check_cancelled(event)
                design_result = self._design_exporter(source, keys, candidate, design)
                workspace = Path(design_result.root)
                if workspace != candidate:
                    workspace = None
                    raise ValueError("The designer returned an unexpected workspace")
                _check_cancelled(event)
                if _source_fingerprint(source, event) != fingerprint:
                    raise OSError("The skin changed during export; reload it and try again")
                export_source = workspace
            self._exporter(export_source, destination, progress=report, cancelled=event.is_set)
            # A completed atomic export wins over a cancellation arriving later.
            return _Result(kind, "success", str(destination),
                           export_root=str(workspace) if workspace else None)
        except Exception as error:
            was_cancelled = isinstance(error, _ArchiveCancelled) or event.is_set()
            if workspace is not None:
                try:
                    _remove_created_workspace(workspace, workspace_parent)
                except OSError as cleanup_error:
                    return _Result(kind, "failed", message=
                                   f"{error}\nCould not remove the incomplete workspace: {cleanup_error}")
            return _Result(kind, "cancelled" if was_cancelled else "failed",
                           message=str(error))

    @Slot(int, str, int, int)
    def _deliver_progress(self, token, kind, done, total):
        if self._busy and token == self._generation and kind == self._kind:
            self.progress.emit(kind, done, total)

    def _clear_task(self):
        self._timer.stop()
        self._future = None
        self._executor = None
        self._cancel_event = None
        self._kind = None
        self._busy = False
        self.busy_changed.emit(False)

    @Slot()
    def _poll(self):
        if self._future is None or not self._future.done():
            return
        try:
            result = self._future.result()
        except Exception as error:
            result = _Result(self._kind, "failed", message=str(error))
        self._executor.shutdown(wait=False)
        self.last_export_root = result.export_root if result.status == "success" else None
        # Clear before notifying clients, allowing a terminal handler to start
        # another task without an old future or queued progress interfering.
        self._clear_task()
        if result.status == "cancelled":
            self.cancelled.emit(result.kind)
        elif result.status == "failed":
            self.failed.emit(result.kind, result.message)
        elif result.kind == "import":
            self.imported.emit(result.path, result.inspection, result.source)
        else:
            self.exported.emit(result.path)
