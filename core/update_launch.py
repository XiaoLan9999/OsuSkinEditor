"""Prepare an isolated local update job and acknowledge a healthy restart."""
import ctypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

from core.resources import resource_path
from core.update_installer import capture_process_identity, clean_restart_environment


def installed_executable():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).absolute()
    return path if path.is_file() and path.suffix.lower() == ".exe" else None


def process_image_path(pid):
    if os.name != "nt":
        return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        return Path(buffer.value) if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) else None
    finally:
        kernel.CloseHandle(handle)


def read_record(path, limit=65536):
    try:
        source = Path(path)
        if source.is_symlink() or source.stat().st_size > limit:
            return None
        data = json.loads(source.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def write_record(path, data):
    path = Path(path)
    temporary = path.with_name(path.name+"."+secrets.token_hex(8)+".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_install_job(payload, release, cancel_event, *, target=None, helper=None):
    """Runs in a worker thread; it only copies into a new private staging dir."""
    target = installed_executable() if target is None else Path(target).absolute()
    if target is None:
        raise RuntimeError("In-place updates require the Windows packaged application")
    helper = Path(helper or resource_path("assets/updater/OsuSkinUpdater.exe"))
    if not helper.is_file():
        raise RuntimeError("The bundled update helper is missing")
    payload = Path(payload).absolute()
    stage = Path(tempfile.mkdtemp(prefix="install-", dir=payload.parent.parent))
    nonce = secrets.token_hex(32)
    copies = []
    try:
        for source, name in ((payload, "payload.exe"), (helper, "update-helper.exe")):
            destination = stage/name
            with source.open("rb") as reader, destination.open("xb") as writer:
                copies.append(destination)
                while block := reader.read(1024*1024):
                    if cancel_event.is_set():
                        raise InterruptedError("Update preparation cancelled")
                    writer.write(block)
        parents = [os.getpid()]
        # onefile has a parent bootloader that owns the running EXE and cleans
        # up _MEIPASS. Do not accidentally wait for Explorer or a shell.
        parent_image = process_image_path(os.getppid())
        if parent_image is not None and os.path.normcase(str(parent_image.absolute())) == os.path.normcase(str(target)):
            parents.append(os.getppid())
        identities = {}
        for pid in parents:
            identity = capture_process_identity(pid)
            if not identity:
                raise RuntimeError("Could not identify the running application process")
            identities[str(pid)] = identity["creation_time"]
        job = dict(schema_version=1, target_exe=str(target), payload_exe=str(stage/"payload.exe"),
                   expected_sha256=release["sha256"], size=release["size"], parent_pids=parents,
                   parent_creation_times=identities, nonce=nonce)
        for key, name in (("ready_file", "ready.json"), ("result_file", "result.json"),
                          ("prepared_file", "helper-ready.json"), ("cancel_file", "cancel.json")):
            job[key] = str(stage/name)
        if cancel_event.is_set():
            raise InterruptedError("Update preparation cancelled")
        write_record(stage/"job.json", job)
        return {"job": job, "job_path": str(stage/"job.json"), "helper": str(stage/"update-helper.exe")}
    except Exception:
        # These are exact files created by this attempt, never skin files or
        # an existing installation. Keep an unexpected foreign file intact.
        for path in copies:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        raise


def launch_helper(prepared):
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    previous = ctypes.create_unicode_buffer(32768)
    length = kernel.GetDllDirectoryW(len(previous), previous)
    if length >= len(previous):
        raise RuntimeError("Cannot preserve the application's DLL search path")
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return subprocess.Popen([prepared["helper"], "--job", prepared["job_path"]],
                                cwd=str(Path(prepared["job_path"]).parent),
                                env=clean_restart_environment(), startupinfo=info,
                                creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    finally:
        kernel.SetDllDirectoryW(previous.value if length else None)


def acknowledge_update_startup(ready_file, nonce):
    """Only acknowledge the job that targets this actual running executable."""
    if not ready_file or not nonce or installed_executable() is None:
        return False
    ready = Path(ready_file).absolute()
    if ready.name != "ready.json" or ready.exists():
        return False
    from core.update_installer import _reject_reparse
    try:
        _reject_reparse(ready)
        job = read_record(ready.parent/"job.json")
        if (not job or job.get("nonce") != nonce or job.get("ready_file") != str(ready)
                or os.path.normcase(str(job.get("target_exe", ""))) != os.path.normcase(str(installed_executable()))):
            return False
        identity = capture_process_identity(os.getpid())
        if not identity:
            return False
        write_record(ready, dict(schema_version=1, status="ready", nonce=nonce,
                                 pid=os.getpid(), creation_time=identity["creation_time"]))
        return True
    except Exception:
        return False
