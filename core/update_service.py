"""Asynchronous, bounded GitHub update transport with verified staging only.

This module never replaces or launches an application. Only a release selected
from a verified manifest can enter the downloader; the installer re-verifies
the prepared payload before replacing the user's executable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit, unquote
import base64
import hashlib
import json
import os
import tempfile
import time

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from core.app_version import BUILD_NUMBER, CHANNEL
from core.update_protocol import verify_manifest, select_release


ANNOUNCEMENTS_URL = "https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/announcements.json"
MANIFEST_URL = "https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/manifest.json"
METADATA_LIMIT = 1024*1024
DOWNLOAD_LIMIT = 512*1024*1024
CACHE_SECONDS = 12*60*60
METADATA_TIMEOUT_MS = 15000
DOWNLOAD_IDLE_TIMEOUT_MS = 30000
MAX_REDIRECTS = 5
CDN_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"})


@dataclass
class _Transfer:
    kind: str
    url: str
    timer: QTimer
    cached: dict | None = None
    reply: object = None
    data: bytearray = field(default_factory=bytearray)
    redirects: int = 0
    received: int = 0
    done: bool = False
    stream: object = None
    partial: Path | None = None
    staging: Path | None = None
    digest: object = None
    release: dict | None = None
    read_queued: bool = False


def _safe_url(url, *, download=False, redirected=False):
    """No tokens, credentials, custom ports, HTTP downgrade, or suffix hosts."""
    if not isinstance(url, str) or not 1 <= len(url) <= 8192:
        return False
    try:
        parsed = urlsplit(str(url))
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment or not parsed.hostname):
            return False
    except (TypeError, ValueError):
        return False
    host = parsed.hostname.casefold()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in url):
        return False
    if not download:
        return host == "raw.githubusercontent.com" and parsed.path in (
            urlsplit(ANNOUNCEMENTS_URL).path, urlsplit(MANIFEST_URL).path) and not parsed.query
    if host == "github.com":
        path = unquote(parsed.path)
        prefix = "/XiaoLan9999/OsuSkinEditor/releases/download/"
        tail = path[len(prefix):] if path.startswith(prefix) else ""
        return bool(tail and len(tail.split("/")) == 2 and tail.lower().endswith(".exe")
                    and not any(part in ("", ".", "..") for part in tail.split("/")) and not parsed.query)
    return redirected and host in CDN_HOSTS


def _etag(value):
    try:
        text = bytes(value).decode("ascii") if not isinstance(value, str) else value
    except (TypeError, UnicodeError):
        return ""
    return text if len(text) <= 512 and all(32 <= ord(c) < 127 for c in text) else ""


def _header(reply, name):
    # Qt6.11 exposes rawHeader(QAnyStringView) as str; older PySide versions
    # exposed QByteArray and expect bytes. Support both declared dependency APIs.
    try:
        return bytes(reply.rawHeader(name))
    except TypeError:
        return bytes(reply.rawHeader(name.encode("ascii")))


class UpdateService(QObject):
    announcements_ready = Signal(object)
    update_ready = Signal(object)
    failed = Signal(str, str)
    download_progress = Signal(int, int)
    download_ready = Signal(str, object)
    state_changed = Signal(str)

    def __init__(self, parent=None, *, cache_dir=None, network_manager=None,
                 current_build_number=None, public_key=None):
        super().__init__(parent)
        location = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        cache_base = (Path(location) if location and Path(location).is_absolute() else
                      Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "OsuSkinEditor" / "Cache")
        default_cache = cache_base / "online-updates"
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache
        self.network = network_manager or QNetworkAccessManager(self)
        self.current_build_number = BUILD_NUMBER if current_build_number is None else current_build_number
        self.public_key = public_key
        self._operations = {}
        self._channel = CHANNEL
        self._authorized = {}
        self._closed = False
        self._state = "idle"
        self._ready_path = None
        self._ready_release = None
        self.last_result_from_cache = {"announcements": False, "updates": False}

    @property
    def downloading(self):
        return "download" in self._operations

    @property
    def ready_path(self):
        return self._ready_path

    @property
    def ready_release(self):
        return deepcopy(self._ready_release)

    @property
    def state(self):
        return self._state

    def _state_update(self):
        state = "downloading" if self.downloading else "ready" if self._ready_path else "checking" if self._operations else "idle"
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    @staticmethod
    def _fingerprint(release):
        try:
            return json.dumps(release, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            return ""

    def _validate(self, kind, data):
        if len(data) > METADATA_LIMIT:
            raise ValueError("Metadata exceeds the size limit")
        if kind == "updates":
            return verify_manifest(bytes(data), public_key=self.public_key)
        from core.update_announcements import parse_announcements
        catalog = json.loads(bytes(data).decode("utf-8-sig"))
        if (not isinstance(catalog, dict) or type(catalog.get("schema_version")) is not int
                or catalog["schema_version"] != 1 or not isinstance(catalog.get("entries"), list)):
            raise ValueError("Invalid announcement catalog")
        entries = parse_announcements(bytes(data))
        if not isinstance(entries, (list, tuple)) or len(entries) != len(catalog["entries"]):
            raise ValueError("Invalid announcement catalog")
        return tuple(entries)

    def _cache_read(self, kind):
        try:
            path = self.cache_dir / (kind+".json")
            if path.stat().st_size > 2*METADATA_LIMIT:
                return None
            cache = json.loads(path.read_text(encoding="utf-8"))
            fetched = cache["fetched_at"]
            if isinstance(fetched, bool) or not isinstance(fetched, (int, float)) or not 0 <= fetched <= time.time()+60:
                return None
            data = base64.b64decode(cache["body"], validate=True)
            value = self._validate(kind, data)
            return {"body": data, "value": value, "fetched_at": fetched, "etag": _etag(cache.get("etag", ""))}
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return None

    def _cache_write(self, kind, data, tag):
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"fetched_at": time.time(), "etag": _etag(tag),
                                  "body": base64.b64encode(data).decode("ascii")})
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir,
                                             prefix=kind+"-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.cache_dir / (kind+".json"))
        except OSError:
            # Cache persistence is optional; verified live data remains usable.
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _deliver(self, kind, value, from_cache):
        self.last_result_from_cache[kind] = from_cache
        if kind == "announcements":
            self.announcements_ready.emit(deepcopy(value))
            return
        release = select_release(value, self._channel, self.current_build_number)
        self._authorized.clear()
        if release is not None:
            self._authorized[self._fingerprint(release)] = deepcopy(release)
        self.update_ready.emit(deepcopy(release))

    def check_announcements(self, force=False):
        return self._check("announcements", ANNOUNCEMENTS_URL, force)

    def check_updates(self, channel=CHANNEL, force=False):
        if channel not in ("stable", "preview"):
            self.failed.emit("updates", "Unknown update channel")
            return False
        if self.downloading or self._ready_path:
            self.failed.emit("updates", "Finish or discard the prepared update before checking another release")
            return False
        self._channel = channel
        self._authorized.clear()
        return self._check("updates", MANIFEST_URL, force)

    def _check(self, kind, url, force):
        if self._closed:
            return False
        if kind in self._operations:
            return True  # Coalesce refreshes while the same endpoint is in flight.
        cached = self._cache_read(kind)
        if cached and not force and 0 <= time.time()-cached["fetched_at"] < CACHE_SECONDS:
            self._deliver(kind, cached["value"], True)
            return True
        timer = QTimer(self)
        timer.setSingleShot(True)
        transfer = _Transfer(kind, url, timer, cached=cached)
        self._operations[kind] = transfer
        timer.timeout.connect(lambda: self._fail(transfer, "The network request timed out"))
        timer.start(METADATA_TIMEOUT_MS)
        self._request(transfer, url)
        self._state_update()
        return True

    def _request(self, transfer, url):
        if not _safe_url(url, download=transfer.kind == "download", redirected=transfer.redirects > 0):
            self._fail(transfer, "The update URL or redirect is not allowed")
            return
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setAttribute(QNetworkRequest.AuthenticationReuseAttribute, QNetworkRequest.Manual)
        request.setAttribute(QNetworkRequest.CookieLoadControlAttribute, QNetworkRequest.Manual)
        request.setAttribute(QNetworkRequest.CookieSaveControlAttribute, QNetworkRequest.Manual)
        request.setRawHeader(b"User-Agent", b"OsuSkinEditor-Update/1")
        request.setRawHeader(b"Accept", b"application/octet-stream" if transfer.kind == "download" else b"application/json")
        if transfer.cached and transfer.cached["etag"]:
            request.setRawHeader(b"If-None-Match", transfer.cached["etag"].encode("ascii"))
        try:
            reply = self.network.get(request)
        except Exception:
            self._fail(transfer, "Unable to start the network request")
            return
        transfer.url, transfer.reply = url, reply
        reply.setReadBufferSize(128*1024)
        reply.readyRead.connect(lambda: self._read(transfer, reply))
        reply.metaDataChanged.connect(lambda: self._headers(transfer, reply))
        reply.finished.connect(lambda: self._finished(transfer, reply))
        reply.sslErrors.connect(lambda _errors: self._fail(transfer, "TLS certificate validation failed"))
        if transfer.kind == "download":
            transfer.timer.start(DOWNLOAD_IDLE_TIMEOUT_MS)

    @staticmethod
    def _status(reply):
        try:
            return int(reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0)
        except (TypeError, ValueError):
            return 0

    def _headers(self, transfer, reply):
        if transfer.done or transfer.reply is not reply or self._status(reply) != 200:
            return
        try:
            length = int(_header(reply, "Content-Length"))
        except (ValueError, TypeError):
            return
        limit = transfer.release["size"] if transfer.release else METADATA_LIMIT
        if length < 0 or length > limit or transfer.release and length != transfer.release["size"]:
            self._fail(transfer, "The response size does not match the allowed size")

    def _read(self, transfer, reply):
        if transfer.done or transfer.reply is not reply:
            return
        self._headers(transfer, reply)
        if transfer.done:
            return
        status = self._status(reply)
        if status != 200:
            # Redirect and HTTP-error response bodies are never staged/cached.
            if status >= 400:
                self._fail(transfer, f"The server returned HTTP {status}")
                return
            reply.read(min(256*1024, reply.bytesAvailable()))
            self._queue_read(transfer, reply)
            return
        limit = transfer.release["size"] if transfer.release else METADATA_LIMIT
        budget = 256*1024
        while reply.bytesAvailable() and not transfer.done and budget > 0:
            chunk = bytes(reply.read(min(64*1024, reply.bytesAvailable(), budget)))
            if not chunk:
                break
            budget -= len(chunk)
            if transfer.received+len(chunk) > limit:
                self._fail(transfer, "The response exceeds the allowed size")
                return
            transfer.received += len(chunk)
            if transfer.stream:
                try:
                    written = transfer.stream.write(chunk)
                    if written != len(chunk):
                        raise OSError("Short write")
                except OSError:
                    self._fail(transfer, "Unable to write the update staging file")
                    return
                transfer.digest.update(chunk)
                transfer.timer.start(DOWNLOAD_IDLE_TIMEOUT_MS)
                self.download_progress.emit(transfer.received, transfer.release["size"])
            else:
                transfer.data.extend(chunk)
        self._queue_read(transfer, reply)

    def _queue_read(self, transfer, reply):
        if transfer.done or transfer.reply is not reply or transfer.read_queued or not reply.bytesAvailable():
            return
        # Bound work per event-loop turn even when a fast CDN keeps refilling
        # Qt's receive buffer faster than the disk writer consumes it.
        transfer.read_queued = True
        def resume():
            transfer.read_queued = False
            self._read(transfer, reply)
        QTimer.singleShot(0, resume)

    def _finished(self, transfer, reply):
        if transfer.done or transfer.reply is not reply:
            return
        self._read(transfer, reply)
        if transfer.done:
            return
        if reply.bytesAvailable():
            QTimer.singleShot(0, lambda: self._finished(transfer, reply))
            return
        status = self._status(reply)
        if status in (301, 302, 303, 307, 308):
            target = reply.attribute(QNetworkRequest.RedirectionTargetAttribute)
            transfer.redirects += 1
            if not target or transfer.redirects > MAX_REDIRECTS:
                self._fail(transfer, "The update redirect chain is invalid")
                return
            # PrettyDecoded inserts literal spaces into GitHub's signed CDN
            # query (Content-Disposition). Preserve the wire URL for policy
            # checks and subsequent requests, including its signed escaping.
            redirected = QUrl(transfer.url).resolved(QUrl(target)).toString(QUrl.FullyEncoded)
            reply.deleteLater()
            transfer.reply = None
            self._request(transfer, redirected)
            return
        if reply.error() != QNetworkReply.NoError:
            self._fail(transfer, f"The server returned HTTP {status}" if status >= 400 else "The network request failed")
            return
        if transfer.kind == "download":
            if status != 200:
                self._fail(transfer, f"The download server returned HTTP {status}")
                return
            self._finish_download(transfer)
            return
        if status == 304 and transfer.cached:
            data = transfer.cached["body"]
            tag = transfer.cached["etag"]
        elif status == 200:
            data = bytes(transfer.data)
            tag = _etag(_header(reply, "ETag"))
        else:
            self._fail(transfer, f"The metadata server returned HTTP {status}")
            return
        try:
            value = self._validate(transfer.kind, data)
            # Selection is checked before cache replacement as well.
            if transfer.kind == "updates":
                select_release(value, self._channel, self.current_build_number)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self._fail(transfer, "The metadata is invalid or its signature could not be verified")
            return
        self._cache_write(transfer.kind, data, tag)
        self._complete(transfer)
        self._deliver(transfer.kind, value, False)

    def download_release(self, release):
        if self._closed:
            return False
        if self.downloading or self._ready_path:
            self.failed.emit("download", "An update download is already active or ready")
            return False
        authorized = self._authorized.get(self._fingerprint(release))
        if (authorized is None or not _safe_url(authorized.get("url", ""), download=True)
                or isinstance(authorized.get("size"), bool)
                or not isinstance(authorized.get("size"), int)
                or not 0 < authorized["size"] <= DOWNLOAD_LIMIT):
            self.failed.emit("download", "The release has not been verified for download")
            return False
        staging, stream = None, None
        try:
            staging_root = self.cache_dir / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="update-", dir=staging_root))
            partial = staging / "payload.download"
            stream = partial.open("xb")
        except OSError:
            if stream:
                stream.close()
            if staging:
                try:
                    (staging / "payload.download").unlink(missing_ok=True)
                    staging.rmdir()
                except OSError:
                    pass
            self.failed.emit("download", "Unable to create an update staging folder")
            return False
        timer = QTimer(self)
        timer.setSingleShot(True)
        transfer = _Transfer("download", authorized["url"], timer, stream=stream, partial=partial,
                             staging=staging, digest=hashlib.sha256(), release=deepcopy(authorized))
        self._operations["download"] = transfer
        timer.timeout.connect(lambda: self._fail(transfer, "The update download stopped responding"))
        self._request(transfer, transfer.url)
        self._state_update()
        return not transfer.done

    def _finish_download(self, transfer):
        if transfer.received != transfer.release["size"] or transfer.digest.hexdigest() != transfer.release["sha256"].lower():
            self._fail(transfer, "The downloaded update failed size or SHA-256 verification")
            return
        try:
            transfer.stream.flush()
            os.fsync(transfer.stream.fileno())
            transfer.stream.close()
            transfer.stream = None
            payload = transfer.staging / "payload.exe"
            if payload.exists():
                raise OSError("Prepared path already exists")
            os.rename(transfer.partial, payload)
        except OSError:
            self._fail(transfer, "Unable to prepare the verified update file")
            return
        self._ready_path = str(payload)
        self._ready_release = deepcopy(transfer.release)
        self._complete(transfer)
        self.download_ready.emit(self._ready_path, deepcopy(self._ready_release))

    def _complete(self, transfer, abort=False):
        transfer.done = True
        transfer.timer.stop()
        transfer.timer.deleteLater()
        if self._operations.get(transfer.kind) is transfer:
            del self._operations[transfer.kind]
        if transfer.reply is not None:
            if abort:
                transfer.reply.abort()
            transfer.reply.deleteLater()
            transfer.reply = None
        self._state_update()

    @staticmethod
    def _remove_partial(transfer):
        if transfer.stream:
            try:
                transfer.stream.close()
            except OSError:
                pass
            transfer.stream = None
        if transfer.partial:
            try:
                transfer.partial.unlink(missing_ok=True)
            except OSError:
                pass
        if transfer.staging:
            try:
                transfer.staging.rmdir()  # Never recursively delete a directory.
            except OSError:
                pass

    def _fail(self, transfer, message):
        if transfer.done:
            return
        self._complete(transfer, abort=True)
        self._remove_partial(transfer)
        if not self._closed:
            self.failed.emit(transfer.kind, message)

    def cancel_download(self):
        transfer = self._operations.get("download")
        if transfer:
            self._complete(transfer, abort=True)
            self._remove_partial(transfer)

    def discard_ready(self):
        self._ready_path = None
        self._ready_release = None
        self._state_update()

    def close(self):
        self._closed = True
        for transfer in list(self._operations.values()):
            self._complete(transfer, abort=True)
            self._remove_partial(transfer)
