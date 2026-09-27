import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PySide6.QtCore import QObject, QByteArray, QCoreApplication, QEvent, QUrl, QTimer, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.update_service import UpdateService, ANNOUNCEMENTS_URL, MANIFEST_URL, METADATA_LIMIT, _safe_url


APP = QApplication.instance() or QApplication([])


class FakeReply(QObject):
    readyRead = Signal()
    metaDataChanged = Signal()
    finished = Signal()
    sslErrors = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = 0
        self.headers = {}
        self.redirect = None
        self.buffer = bytearray()
        self.network_error = QNetworkReply.NoError
        self.aborted = False
        self.buffer_limit = None

    def setReadBufferSize(self, value):
        self.buffer_limit = value

    def attribute(self, attribute):
        if attribute == QNetworkRequest.HttpStatusCodeAttribute:
            return self.status
        if attribute == QNetworkRequest.RedirectionTargetAttribute:
            return QUrl(self.redirect) if self.redirect else None

    def rawHeader(self, name):
        if not isinstance(name, str):
            raise TypeError("Qt6.11 rawHeader expects str")
        return QByteArray(self.headers.get(name.lower().encode(), b""))

    def bytesAvailable(self):
        return len(self.buffer)

    def read(self, size):
        data = self.buffer[:size]
        del self.buffer[:size]
        return QByteArray(bytes(data))

    def error(self):
        return self.network_error

    def abort(self):
        self.aborted = True
        self.network_error = QNetworkReply.OperationCanceledError
        self.finished.emit()

    def begin(self, status=200, headers=None, redirect=None):
        self.status = status
        self.headers = {k.lower().encode(): str(v).encode() for k, v in (headers or {}).items()}
        self.redirect = redirect
        self.metaDataChanged.emit()

    def push(self, body):
        self.buffer.extend(body)
        self.readyRead.emit()

    def finish(self, error=QNetworkReply.NoError):
        self.network_error = error
        self.finished.emit()

    def respond(self, body=b"", status=200, headers=None, redirect=None):
        self.begin(status, headers, redirect)
        if not self.aborted:
            self.push(body)
            self.finish()


class FakeNetwork(QObject):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.replies = []

    def get(self, request):
        self.requests.append(QNetworkRequest(request))
        reply = FakeReply(self)
        self.replies.append(reply)
        return reply

    @property
    def latest(self):
        return self.replies[-1]


class UpdateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.network = FakeNetwork()
        self.service = UpdateService(cache_dir=self.root, network_manager=self.network,
                                     current_build_number=5, public_key=self.public)
        self.errors, self.updates, self.announcements, self.ready, self.progress, self.states = [], [], [], [], [], []
        self.service.failed.connect(lambda category, message: self.errors.append((category, message)))
        self.service.update_ready.connect(self.updates.append)
        self.service.announcements_ready.connect(self.announcements.append)
        self.service.download_ready.connect(lambda path, release: self.ready.append((path, release)))
        self.service.download_progress.connect(lambda received, total: self.progress.append((received, total)))
        self.service.state_changed.connect(self.states.append)
        self.body = b"MZ"+bytes(range(256))*1000
        self.release = {
            "version": "1.6.0-preview.6", "build_id": "preview-r6", "build_number": 6,
            "channel": "preview", "platform": "windows-x64",
            "url": "https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/preview-r6/editor.exe",
            "sha256": hashlib.sha256(self.body).hexdigest(), "size": len(self.body), "notes_id": "preview-r6",
        }

    def tearDown(self):
        self.service.close()
        self.service.deleteLater()
        self.network.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def signed(self, release=None, stable=None):
        payload = json.dumps({"schema_version": 1, "published_at": "2026-09-29T00:00:00Z",
                              "channels": {"stable": stable, "preview": self.release if release is None else release}},
                             separators=(",", ":")).encode()
        return json.dumps({"schema_version": 1, "payload": base64.b64encode(payload).decode(),
                           "signature": base64.b64encode(self.private.sign(payload)).decode()}).encode()

    def authorize(self):
        self.service.check_updates("preview")
        self.network.latest.respond(self.signed(), headers={"ETag": '"one"'})
        self.assertEqual(self.updates[-1], self.release)
        self.assertFalse(self.errors)

    def start_download(self):
        self.authorize()
        self.assertTrue(self.service.download_release(self.release))
        return self.network.latest

    def test_metadata_is_asynchronous_coalesced_and_uses_no_credentials(self):
        self.assertTrue(self.service.check_updates("preview"))
        self.assertFalse(self.updates)
        self.service.check_updates("preview", force=True)
        self.assertEqual(len(self.network.requests), 1)
        request = self.network.requests[0]
        self.assertEqual(request.url().toString(), MANIFEST_URL)
        self.assertFalse(bytes(request.rawHeader("Authorization")))
        self.assertFalse(bytes(request.rawHeader("Cookie")))
        self.assertEqual(request.attribute(QNetworkRequest.RedirectPolicyAttribute), QNetworkRequest.ManualRedirectPolicy)
        self.network.latest.respond(self.signed())
        self.assertEqual(self.updates[-1], self.release)
        self.assertEqual(self.service.state, "idle")

    def test_real_signature_is_required_and_unverified_release_cannot_download(self):
        self.assertFalse(self.service.download_release(self.release))
        self.assertFalse(self.network.requests)
        self.service.check_updates("preview")
        envelope = json.loads(self.signed())
        envelope["signature"] = base64.b64encode(bytes(64)).decode()
        self.network.latest.respond(json.dumps(envelope).encode())
        self.assertFalse(self.updates)
        self.assertFalse((self.root / "updates.json").exists())
        self.assertFalse(self.service.download_release(self.release))
        self.assertEqual(self.errors[-1][0], "download")

    def test_valid_release_dictionary_cannot_be_mutated_to_inject_a_download_url(self):
        self.authorize()
        malicious = deepcopy(self.updates[-1])
        malicious["url"] = "https://github.com/other/project/releases/download/v1/editor.exe"
        self.assertFalse(self.service.download_release(malicious))
        self.assertEqual(len(self.network.requests), 1)

    def test_signed_channels_and_current_build_selection(self):
        stable = dict(self.release, channel="stable", version="1.5.0", build_number=4)
        self.service.check_updates("preview")
        self.service.check_updates("stable", force=True)
        self.network.latest.respond(self.signed(stable=stable))
        self.assertEqual(self.updates, [None])
        self.assertEqual(len(self.network.requests), 1)

    def test_cache_is_reverified_etag_supported_and_force_bypasses_ttl(self):
        self.authorize()
        self.service.check_updates("preview")
        self.assertEqual(len(self.network.requests), 1)
        self.assertTrue(self.service.last_result_from_cache["updates"])
        self.service.check_updates("preview", force=True)
        self.assertEqual(len(self.network.requests), 2)
        self.assertEqual(bytes(self.network.requests[-1].rawHeader("If-None-Match")), b'"one"')
        self.network.latest.respond(status=304)
        self.assertEqual(self.updates[-1], self.release)
        self.assertFalse(self.service.last_result_from_cache["updates"])

    def test_expired_cache_is_not_reported_fresh_after_a_network_failure(self):
        self.authorize()
        cache_file = self.root / "updates.json"
        cache = json.loads(cache_file.read_text())
        cache["fetched_at"] -= 13*60*60
        cache_file.write_text(json.dumps(cache))
        original = cache_file.read_bytes()
        self.service.check_updates("preview")
        self.network.latest.begin(503)
        self.network.latest.finish(QNetworkReply.ServiceUnavailableError)
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(cache_file.read_bytes(), original)
        self.assertFalse(self.service.download_release(self.release))

    def test_tampered_cache_cannot_authorize_or_emit_a_release(self):
        self.authorize()
        cache_file = self.root / "updates.json"
        cache = json.loads(cache_file.read_text())
        cache["body"] = base64.b64encode(b"not a signed manifest").decode()
        cache_file.write_text(json.dumps(cache))
        self.service.check_updates("preview")
        self.assertEqual(len(self.network.requests), 2)
        self.assertEqual(len(self.updates), 1)

    def test_valid_announcements_use_existing_schema_and_invalid_catalog_is_not_cached(self):
        entry = {"id": "news-r1", "version": "1.6", "channel": "preview", "date": "2026-09-29",
                 "title": {"en-US": "News"}, "summary": {"en-US": "Summary"},
                 "sections": [{"title": {"en-US": "Changes"}, "items": {"en-US": ["New feature"]}}]}
        self.service.check_announcements()
        self.assertEqual(self.network.requests[-1].url().toString(), ANNOUNCEMENTS_URL)
        self.network.latest.respond(json.dumps({"schema_version": 1, "entries": [entry]}).encode())
        self.assertEqual(self.announcements, [(entry,)])
        good_cache = (self.root / "announcements.json").read_bytes()
        self.service.check_announcements(force=True)
        self.network.latest.respond(b'{"schema_version":1,"entries":[{"id":"invalid"}]}')
        self.assertEqual(len(self.announcements), 1)
        self.assertEqual((self.root / "announcements.json").read_bytes(), good_cache)
        self.assertEqual(self.errors[-1][0], "announcements")

    def test_metadata_size_limit_and_timeout_never_emit_success(self):
        self.service.check_announcements()
        self.network.latest.begin(200, {"Content-Length": METADATA_LIMIT+1})
        self.assertTrue(self.network.latest.aborted)
        self.assertFalse(self.announcements)
        with patch("core.update_service.METADATA_TIMEOUT_MS", 10):
            self.service.check_updates("preview")
            QTest.qWait(30)
        self.assertTrue(self.network.latest.aborted)
        self.assertFalse(self.updates)

    def test_chunked_metadata_without_content_length_is_still_bounded(self):
        self.service.check_announcements()
        self.network.latest.begin(200)
        self.network.latest.push(b" "*(METADATA_LIMIT+1))
        QTest.qWait(10)
        self.assertTrue(self.network.latest.aborted)
        self.assertFalse((self.root / "announcements.json").exists())

    def test_https_redirects_allow_only_project_assets_and_known_cdn_hosts(self):
        reply = self.start_download()
        reply.respond(status=302, redirect="https://release-assets.githubusercontent.com/github-production-release-asset/123?sig=test")
        self.assertEqual(len(self.network.requests), 3)
        self.network.latest.respond(self.body, headers={"Content-Length": len(self.body)})
        self.assertEqual(Path(self.ready[-1][0]).read_bytes(), self.body)
        for url in ("http://github.com/XiaoLan9999/OsuSkinEditor/releases/download/r/a.exe",
                    "https://github.com.evil.example/a.exe", "https://user:token@github.com/a.exe",
                    "https://localhost/a.exe", "https://github.com:444/a.exe", "https://evil.example/a.exe"):
            self.assertFalse(_safe_url(url, download=True, redirected=True), url)

    def test_unsafe_redirect_or_tls_errors_abort_and_remove_only_partial(self):
        reply = self.start_download()
        reply.respond(status=302, redirect="https://example.com/payload.exe")
        self.assertFalse(self.ready)
        self.assertFalse(list(self.root.rglob("*.download")))
        self.assertTrue(self.service.download_release(self.release))
        self.network.latest.sslErrors.emit(["bad certificate"])
        self.assertTrue(self.network.latest.aborted)
        self.assertFalse(self.ready)

    def test_redirect_limit_downgrade_and_metadata_host_are_enforced(self):
        self.authorize()
        self.service.download_release(self.release)
        for _ in range(6):
            self.network.latest.respond(status=302, redirect=self.release["url"])
        self.assertFalse(self.service.downloading)
        self.assertFalse(self.ready)
        self.assertFalse(list(self.root.rglob("*.download")))
        self.service.check_announcements(force=True)
        request_count = len(self.network.requests)
        self.network.latest.respond(status=302, redirect="https://github.com/other/repo/announcements.json")
        self.assertEqual(len(self.network.requests), request_count)
        self.assertFalse(self.announcements)
        self.service.download_release(self.release)
        request_count = len(self.network.requests)
        self.network.latest.respond(status=302, redirect=self.release["url"].replace("https:", "http:"))
        self.assertEqual(len(self.network.requests), request_count)
        self.assertFalse(self.ready)

    def test_deeply_nested_or_noninteger_schema_announcements_fail_closed(self):
        for body in (b"["*2000+b"]"*2000, b'{"schema_version":true,"entries":[]}'):
            self.service.check_announcements(force=True)
            self.network.latest.respond(body)
            self.assertFalse(self.announcements)
            self.assertEqual(self.service.state, "idle")
        self.assertFalse((self.root / "announcements.json").exists())

    def test_streamed_download_progress_hash_size_and_atomic_payload(self):
        reply = self.start_download()
        reply.begin(200, {"Content-Length": len(self.body)})
        reply.push(self.body[:120000])
        self.assertFalse(self.ready)
        self.assertFalse(list(self.root.rglob("payload.exe")))
        self.assertTrue(list(self.root.rglob("payload.download")))
        reply.push(self.body[120000:])
        reply.finish()
        path, release = self.ready[-1]
        self.assertEqual(Path(path).name, "payload.exe")
        self.assertEqual(Path(path).read_bytes(), self.body)
        self.assertEqual(release, self.release)
        self.assertEqual(self.progress[-1], (len(self.body), len(self.body)))
        self.assertEqual(self.service.state, "ready")
        self.assertFalse(list(self.root.rglob("*.download")))
        request_count = len(self.network.requests)
        self.assertFalse(self.service.check_updates("stable", force=True))
        self.assertFalse(self.service.download_release(self.release))
        self.assertEqual(len(self.network.requests), request_count)
        self.service.discard_ready()
        self.assertTrue(Path(path).is_file())
        self.assertIsNone(self.service.ready_path)

    def test_corrupt_truncated_and_oversized_downloads_are_never_prepared(self):
        self.authorize()
        bodies = (self.body[:-1], b"X"+self.body[1:], self.body+b"X")
        for body in bodies:
            self.service.download_release(self.release)
            self.network.latest.respond(body)
            self.assertFalse(self.ready)
            self.assertFalse(list(self.root.rglob("*.download")))
            self.assertFalse(list(self.root.rglob("payload.exe")))

    def test_large_ready_buffer_yields_to_ui_before_finishing_stream(self):
        self.body = b"MZ"+b"A"*(2*1024*1024)
        self.release["size"] = len(self.body)
        self.release["sha256"] = hashlib.sha256(self.body).hexdigest()
        reply = self.start_download()
        reply.begin(200)
        reply.push(self.body)
        self.assertFalse(self.ready)
        self.assertLessEqual(self.progress[-1][0], 256*1024)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        reply.finish()
        QTest.qWait(30)
        self.assertTrue(heartbeat)
        self.assertEqual(Path(self.ready[-1][0]).read_bytes(), self.body)

    def test_disk_write_failure_aborts_and_removes_owned_partial(self):
        reply = self.start_download()
        transfer = self.service._operations["download"]
        original = transfer.stream
        class BrokenDisk:
            def write(self, _data):
                raise OSError("Simulated disk full")
            def close(self):
                original.close()
        transfer.stream = BrokenDisk()
        reply.respond(self.body)
        self.assertTrue(reply.aborted)
        self.assertFalse(self.ready)
        self.assertFalse(list(self.root.rglob("*.download")))

    def test_duplicate_cancel_idle_timeout_and_close_preserve_existing_payloads(self):
        unrelated = self.root / "staging" / "previous" / "payload.exe"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(b"keep this verified earlier download")
        reply = self.start_download()
        reply.begin(200)
        reply.push(self.body[:100])
        requests = len(self.network.requests)
        self.assertFalse(self.service.download_release(self.release))
        self.assertEqual(len(self.network.requests), requests)
        self.service.cancel_download()
        self.assertTrue(reply.aborted)
        self.assertTrue(unrelated.exists())
        self.assertFalse(list(self.root.rglob("*.download")))
        with patch("core.update_service.DOWNLOAD_IDLE_TIMEOUT_MS", 10):
            self.service.download_release(self.release)
            QTest.qWait(30)
        self.assertTrue(self.network.latest.aborted)
        self.assertFalse(list(self.root.rglob("*.download")))
        self.service.check_announcements(force=True)
        self.service.close()
        self.assertTrue(self.network.latest.aborted)
        self.assertTrue(unrelated.exists())
        self.assertFalse(self.service.check_announcements())


if __name__ == "__main__":
    unittest.main()
