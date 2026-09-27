"""A stdlib-only, bounded Windows portable-EXE update transaction

The caller verifies signed release metadata. This helper independently checks
the downloaded file, stages on the target volume, waits for identified parent
processes, keeps a backup, and requires a nonce-bound startup acknowledgement
Only a process tree created inside our own Windows Job Object may be stopped
No network requests, elevation, recursive deletion, or Qt imports occur here
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid


MAX_JOB_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
_NONCE = re.compile(r"[A-Za-z0-9_-]{24,128}\Z")
_HASH = re.compile(r"[a-fA-F0-9]{64}\Z")


class UpdateError(Exception):
    pass


class UpdateCancelled(UpdateError):
    pass


def _reject_reparse(path: Path):
    """Check every existing component without resolving through symlinks"""
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise UpdateError(f"Links and reparse points are not allowed: {part}")


def _path(value, name):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise UpdateError(f"Invalid {name}")
    path = Path(value)
    if not path.is_absolute() or (os.name == "nt" and ":" in str(path)[len(path.anchor):]):
        raise UpdateError(f"{name} must be an absolute filesystem path")
    path = Path(os.path.abspath(path))
    _reject_reparse(path)
    return path


def _regular(path):
    _reject_reparse(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise UpdateError(f"Not a regular file: {path}")


def _read_json(path, limit=MAX_JOB_BYTES):
    _regular(path)
    if path.stat().st_size > limit:
        raise UpdateError("Update metadata is too large")
    with path.open("rb") as stream:
        data = stream.read(limit+1)
    if len(data) > limit:
        raise UpdateError("Update metadata is too large")
    value = json.loads(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise UpdateError("Update metadata must be an object")
    return value


def file_digest(path):
    _regular(path)
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _verify(path, expected_hash, expected_size):
    digest, size = file_digest(path)
    if size != expected_size or digest != expected_hash:
        raise UpdateError("Executable size or SHA-256 does not match the update job")


def _copy_exclusive(source, target):
    _regular(source)
    _reject_reparse(target)
    created = False
    try:
        with source.open("rb") as src, target.open("xb") as dst:
            created = True
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
    except Exception:
        if created:
            try:
                _reject_reparse(target)
                target.unlink()
            except Exception:
                pass
        raise


def _write_json(path, value):
    _reject_reparse(path)
    temporary = path.with_name(path.name+".tmp-"+uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _timeout(value, default, maximum):
    value = default if value is None else value
    if isinstance(value, bool):
        raise UpdateError("Invalid update timeout")
    value = float(value)
    if not math.isfinite(value) or not 1 <= value <= maximum:
        raise UpdateError("Update timeout is outside the allowed range")
    return value


@dataclass(frozen=True)
class UpdateJob:
    job_file: Path
    staging_dir: Path
    target_exe: Path
    payload_exe: Path
    expected_sha256: str
    size: int
    parent_pids: tuple[int, ...]
    parent_creation_times: dict[int, int]
    nonce: str
    ready_file: Path
    result_file: Path
    prepared_file: Path
    cancel_file: Path
    wait_timeout_seconds: float = 60
    ready_timeout_seconds: float = 45


def load_job(job_path):
    job_file = _path(os.fspath(job_path), "job file")
    data = _read_json(job_file)
    if data.get("schema_version") != 1:
        raise UpdateError("Unsupported update job schema")
    stage = job_file.parent
    target = _path(data.get("target_exe"), "target executable")
    payload = _path(data.get("payload_exe"), "payload executable")
    if target.suffix.lower() != ".exe" or payload.suffix.lower() != ".exe":
        raise UpdateError("Both executable paths must end in .exe")
    _regular(target)
    _regular(payload)
    if stage == target.parent or stage in target.parents:
        raise UpdateError("The installed executable must be outside the staging directory")
    siblings = {"payload_exe": payload}
    for field in ("ready_file", "result_file", "prepared_file", "cancel_file"):
        siblings[field] = _path(data.get(field), field)
    if any(path.parent != stage for path in siblings.values()):
        raise UpdateError("Payload and handshake files must be direct staging-directory children")
    paths = (job_file, target, *siblings.values())
    normalized = [os.path.normcase(str(path)) for path in paths]
    if len(set(normalized)) != len(paths):
        raise UpdateError("Update file paths must be different")
    existing = [path for path in paths if path.exists()]
    for index, path in enumerate(existing):
        _regular(path)
        if any(os.path.samefile(path, other) for other in existing[index+1:]):
            raise UpdateError("Update paths must not refer to the same file or hard link")
    digest, size = data.get("expected_sha256"), data.get("size")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise UpdateError("Invalid expected SHA-256")
    if type(size) is not int or not 1 <= size <= MAX_PAYLOAD_BYTES:
        raise UpdateError("Invalid expected payload size")
    nonce = data.get("nonce")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise UpdateError("Invalid update nonce")
    pids = data.get("parent_pids")
    identities = data.get("parent_creation_times")
    if (not isinstance(pids, list) or not 1 <= len(pids) <= 8 or
            any(type(pid) is not int or not 0 < pid < 2**32 or pid == os.getpid() for pid in pids) or
            len(set(pids)) != len(pids) or not isinstance(identities, dict)):
        raise UpdateError("Invalid parent process list")
    times = {}
    for pid in pids:
        created = identities.get(str(pid))
        if type(created) is not int or not 0 < created < 2**64:
            raise UpdateError("Every parent process needs its Windows creation time")
        times[pid] = created
    return UpdateJob(job_file, stage, target, payload, digest.lower(), size, tuple(pids), times,
                     nonce, siblings["ready_file"], siblings["result_file"],
                     siblings["prepared_file"], siblings["cancel_file"],
                     _timeout(data.get("wait_timeout_seconds"), 60, 120),
                     _timeout(data.get("ready_timeout_seconds"), 45, 90))


def clean_restart_environment(environment=None):
    source = os.environ if environment is None else environment
    result = {key: value for key, value in source.items()
              if not key.upper().startswith("_PYI_") and
              key.upper() not in ("_MEIPASS", "_MEIPASS2", "PYINSTALLER_RESET_ENVIRONMENT")}
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        for key in list(result):
            if key.upper() == "PATH":
                prefix = os.path.normcase(os.path.abspath(bundle))
                result[key] = os.pathsep.join(part for part in result[key].split(os.pathsep)
                    if not (os.path.normcase(os.path.abspath(part.strip('"'))) == prefix or
                            os.path.normcase(os.path.abspath(part.strip('"'))).startswith(prefix+os.sep)))
    result["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return result


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def _kernel():
    if os.name != "nt":
        raise UpdateError("The update helper supports Windows only")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(_FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def _creation(kernel, handle):
    times = [_FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return times[0].low | (times[0].high << 32)


def capture_process_identity(pid):
    """Return a PID plus exact Windows creation FILETIME; None if it exited"""
    if type(pid) is not int or not 0 < pid < 2**32:
        raise UpdateError("Invalid process ID")
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID absent
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return {"pid": pid, "creation_time": _creation(kernel, handle)}
    finally:
        kernel.CloseHandle(handle)


class WindowsParentWaiter:
    def __init__(self, pids, creation_times):
        self.kernel = _kernel()
        self.handles = []
        try:
            for pid in pids:
                handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
                if not handle:
                    if ctypes.get_last_error() == 87:
                        continue
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append(handle)
                if _creation(self.kernel, handle) != creation_times[pid]:
                    self.kernel.CloseHandle(handle)  # Reused PID is not our parent
                    self.handles.pop()
                    continue
        except Exception:
            self.close()
            raise

    def all_exited(self):
        for handle in self.handles:
            result = self.kernel.WaitForSingleObject(handle, 0)
            if result == 258:  # WAIT_TIMEOUT
                return False
            if result != 0:
                raise ctypes.WinError(ctypes.get_last_error())
        return True

    def close(self):
        for handle in self.handles:
            self.kernel.CloseHandle(handle)
        self.handles.clear()


class WindowsTargetMutex:
    """Serialize different staging jobs targeting the same installed filename"""
    def __init__(self, target):
        self.kernel = k = _kernel()
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        k.ReleaseMutex.argtypes = [wintypes.HANDLE]
        k.ReleaseMutex.restype = wintypes.BOOL
        canonical = os.path.normcase(os.path.realpath(target))
        name = "Local\\OsuSkinEditor.Update."+hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.handle = k.CreateMutexW(None, False, name)
        self.acquired = False
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        status = k.WaitForSingleObject(self.handle, 0)
        if status not in (0, 128):  # Acquired or abandoned by an exited helper
            self.close()
            if status == 258:
                raise UpdateError("Another updater is already updating this executable")
            raise ctypes.WinError(ctypes.get_last_error())
        self.acquired = True

    def close(self):
        if self.handle:
            if self.acquired:
                self.kernel.ReleaseMutex(self.handle)
                self.acquired = False
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("basic", _BASIC_LIMIT), ("io_counters", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class _ACCOUNTING(ctypes.Structure):
    _fields_ = [("times", ctypes.c_int64 * 4), ("faults", wintypes.DWORD),
                ("total", wintypes.DWORD), ("active", wintypes.DWORD),
                ("terminated", wintypes.DWORD)]


class WindowsLaunchedProcess:
    """Create suspended, contain in a private Job, then start; no PID-tree scans"""
    def __init__(self, executable, arguments, environment):
        self.kernel = k = _kernel()
        self.job = self.process = None
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k.IsProcessInJob.restype = wintypes.BOOL
        k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.c_void_p]
        k.QueryInformationJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.ResumeThread.argtypes = [wintypes.HANDLE]
        k.ResumeThread.restype = wintypes.DWORD
        k.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        k.SetDllDirectoryW.restype = wintypes.BOOL
        k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                    ctypes.POINTER(_STARTUPINFO), ctypes.POINTER(_PROCESS_INFORMATION)]
        k.CreateProcessW.restype = wintypes.BOOL
        self.job = k.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _PROCESS_INFORMATION()
        try:
            self._set_kill_on_close(True)
            start = _STARTUPINFO()
            start.cb = ctypes.sizeof(start)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable), *arguments]))
            env = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in
                                                sorted(environment.items(), key=lambda item: item[0].upper()))+"\0\0")
            # PyInstaller documents resetting its inherited Windows DLL search
            # directory before launching an independent frozen application
            if not k.SetDllDirectoryW(None):
                raise ctypes.WinError(ctypes.get_last_error())
            flags = 0x4 | 0x400 | 0x08000000  # SUSPENDED | UNICODE_ENVIRONMENT | NO_WINDOW
            if not k.CreateProcessW(str(executable), command, None, None, False, flags,
                                    env, str(Path(executable).parent), ctypes.byref(start), ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process = info.hProcess
            self.pid = int(info.dwProcessId)
            self.creation_time = _creation(k, self.process)
            if not k.AssignProcessToJobObject(self.job, self.process):
                raise ctypes.WinError(ctypes.get_last_error())
            if k.ResumeThread(info.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            if self.process:
                k.TerminateProcess(self.process, 1)  # Own suspended process handle only
                k.WaitForSingleObject(self.process, 5000)
            self.close()
            raise
        finally:
            if info.hThread:
                k.CloseHandle(info.hThread)

    def _set_kill_on_close(self, enabled):
        limits = _EXTENDED_LIMIT()
        limits.basic.flags = 0x2000 if enabled else 0
        if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())

    def is_running(self):
        state = _ACCOUNTING()
        if not self.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(state), ctypes.sizeof(state), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(state.active)

    def contains_pid(self, pid, creation_time=None):
        if type(pid) is not int or not 0 < pid < 2**32:
            return False
        handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            return False
        try:
            member = wintypes.BOOL()
            return bool(self.kernel.IsProcessInJob(handle, self.job, ctypes.byref(member)) and
                        member.value and self.kernel.WaitForSingleObject(handle, 0) == 258 and
                        (creation_time is None or _creation(self.kernel, handle) == creation_time))
        finally:
            self.kernel.CloseHandle(handle)

    def terminate_and_wait(self, timeout=10):
        if not self.kernel.TerminateJobObject(self.job, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic()+timeout
        while self.is_running() and time.monotonic() < deadline:
            time.sleep(.1)
        return not self.is_running()

    def detach(self):
        self._set_kill_on_close(False)
        self.close()

    def close(self):
        if self.process:
            self.kernel.CloseHandle(self.process)
            self.process = None
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None


class InstallerHooks:
    """Small injectable boundary for deterministic failure/rollback tests"""
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)
    copy_file = staticmethod(_copy_exclusive)
    replace_file = staticmethod(os.replace)
    open_parents = staticmethod(WindowsParentWaiter)
    acquire_target = staticmethod(WindowsTargetMutex)
    launch = staticmethod(WindowsLaunchedProcess)


def _cancelled(job):
    if job.cancel_file.exists():
        notice = _read_json(job.cancel_file, 16384)
        if notice.get("nonce") != job.nonce or notice.get("status") != "cancel":
            raise UpdateError("Invalid update cancellation marker")
        raise UpdateCancelled("Update cancelled before completion")


def _replace_with_retry(hooks, source, target, seconds=4):
    deadline = hooks.monotonic()+seconds
    while True:
        _reject_reparse(source)
        _reject_reparse(target)
        try:
            hooks.replace_file(source, target)
            return
        except OSError:
            if hooks.monotonic() >= deadline:
                raise
            hooks.sleep(.15)


def run_update(job_path, *, hooks=None):
    """Return and persist a terminal result; invalid jobs never select outputs

    prepared_file is written only after validation, parent-handle capture and
    a verified candidate copy prove that staging on the target volume works
    """
    hooks = hooks or InstallerHooks()
    job = parents = child = target_mutex = None
    candidate = backup = rollback_copy = None
    replaced = parents_exited = False
    persist_result = False
    owned_temporary_files = set()
    original_digest = original_size = None
    result = {"schema_version": 1, "status": "failed", "phase": "validating",
              "helper_pid": os.getpid(), "rolled_back": False, "old_version_restarted": False}
    try:
        job = load_job(job_path)
        result.update(nonce=job.nonce, target_exe=str(job.target_exe), payload_exe=str(job.payload_exe))
        if job.result_file.exists():
            previous = _read_json(job.result_file)
            if previous.get("nonce") == job.nonce and previous.get("status") in ("success", "failed", "cancelled", "rollback_failed"):
                return previous
            raise UpdateError("An unrelated result already exists")
        persist_result = True
        for path in (job.ready_file, job.prepared_file):
            if path.exists():
                raise UpdateError("Handshake files must not exist before the helper starts")
        lock = job.staging_dir / ".update-helper.lock"
        _reject_reparse(lock)
        try:
            with lock.open("x", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError:
            persist_result = False
            return {**result, "status": "busy", "message": "An update helper already claimed this staging directory"}
        result["phase"] = "preparing"
        target_mutex = hooks.acquire_target(job.target_exe)
        _cancelled(job)
        _verify(job.payload_exe, job.expected_sha256, job.size)
        original_digest, original_size = file_digest(job.target_exe)
        parents = hooks.open_parents(job.parent_pids, job.parent_creation_times)
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:12]
        candidate = job.target_exe.with_name(job.target_exe.name+".update-"+suffix+".new")
        backup = job.target_exe.with_name(job.target_exe.name+".backup-"+suffix+".bak")
        result["backup_exe"] = str(backup)
        hooks.copy_file(job.payload_exe, candidate)
        owned_temporary_files.add(candidate)
        _verify(candidate, job.expected_sha256, job.size)
        _cancelled(job)
        _write_json(job.prepared_file, {"schema_version": 1, "nonce": job.nonce, "status": "waiting",
                                        "pid": os.getpid(), "backup_exe": str(backup)})
        result["phase"] = "waiting"
        deadline = hooks.monotonic()+job.wait_timeout_seconds
        while not parents.all_exited():
            _cancelled(job)
            if hooks.monotonic() >= deadline:
                raise UpdateError("Timed out waiting for the existing application to exit")
            hooks.sleep(.1)
        parents_exited = True
        _cancelled(job)
        _verify(job.target_exe, original_digest, original_size)
        _verify(candidate, job.expected_sha256, job.size)
        result["phase"] = "backing_up"
        hooks.copy_file(job.target_exe, backup)
        _verify(backup, original_digest, original_size)
        result["backup_created"] = True
        _cancelled(job)
        result["phase"] = "replacing"
        _replace_with_retry(hooks, candidate, job.target_exe)
        replaced = True
        _verify(job.target_exe, job.expected_sha256, job.size)
        result["phase"] = "starting"
        environment = clean_restart_environment()
        child = hooks.launch(job.target_exe, ["--update-ready-file", str(job.ready_file),
                                             "--update-nonce", job.nonce], environment)
        result["child_pid"] = child.pid
        result["phase"] = "waiting_ready"
        deadline = hooks.monotonic()+job.ready_timeout_seconds
        while True:
            _cancelled(job)
            if not child.is_running():
                raise UpdateError("The new application exited before confirming startup")
            if job.ready_file.exists():
                ready = _read_json(job.ready_file, 16384)
                if (ready.get("schema_version") != 1 or ready.get("status") != "ready" or
                        ready.get("nonce") != job.nonce or
                        not child.contains_pid(ready.get("pid"), ready.get("creation_time"))):
                    raise UpdateError("The startup acknowledgement does not belong to the new application")
                _verify(job.target_exe, job.expected_sha256, job.size)
                break
            if hooks.monotonic() >= deadline:
                raise UpdateError("The new application did not confirm startup in time")
            hooks.sleep(.1)
        child.detach()
        child = None
        result.update(status="success", phase="complete", message="Update installed and the new application is ready")
    except Exception as error:
        result.update(status="cancelled" if isinstance(error, UpdateCancelled) else "failed",
                      message=str(error) or type(error).__name__)
        if replaced and job is not None and backup is not None:
            result["failed_phase"] = result["phase"]
            result["phase"] = "rollback"
            try:
                if child is not None and child.is_running():
                    if not child.terminate_and_wait(10):
                        raise UpdateError("The new process tree could not be stopped safely")
                if child is not None:
                    child.close()
                    child = None
                _verify(backup, original_digest, original_size)
                rollback_copy = job.target_exe.with_name(job.target_exe.name+".rollback-"+uuid.uuid4().hex+".new")
                hooks.copy_file(backup, rollback_copy)
                owned_temporary_files.add(rollback_copy)
                _verify(rollback_copy, original_digest, original_size)
                _replace_with_retry(hooks, rollback_copy, job.target_exe)
                result["rolled_back"] = True
            except Exception as rollback_error:
                result.update(status="rollback_failed", rollback_error=str(rollback_error))
        if job is not None and parents_exited and (not replaced or result["rolled_back"]):
            try:
                _verify(job.target_exe, original_digest, original_size)
                old = hooks.launch(job.target_exe, [], clean_restart_environment())
                old.detach()
                result["old_version_restarted"] = True
            except Exception as restart_error:
                result["restart_error"] = str(restart_error)
    finally:
        if parents is not None:
            parents.close()
        if child is not None:
            # If termination could not be established, don't close a Job with
            # kill-on-close enabled and accidentally claim a clean rollback
            try:
                child.detach()
            except Exception:
                pass
        for path in owned_temporary_files:
            try:
                _reject_reparse(path)
                if path.exists():
                    path.unlink()  # Exact helper-created file, never recursive
            except Exception:
                pass
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        if job is not None and persist_result:
            try:
                _write_json(job.result_file, result)
            except Exception as write_error:
                result["result_write_error"] = str(write_error)
        if target_mutex is not None:
            target_mutex.close()
    return result
