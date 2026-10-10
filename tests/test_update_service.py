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
                                     current_build_number=5, public_key=self.public, source_mode="github")
        self.errors, self.updates, self.announcements, self.ready, self.progress, self.states = [], [], [], [], [], []
        self.service.failed.connect(lambda category, message: self.errors.append((category, message)))
        self.service.update_ready.connect(self.updates.append)
        self.channel_results = []
        self.service.channels_ready.connect(self.channel_results.append)
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

    def signed(self, release=None, stable=None, published_at="2026-09-29T00:00:00Z"):
        payload = json.dumps({"schema_version": 1, "published_at": published_at,
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

    def test_all_channels_share_one_request_and_signature_and_authorize_either_choice(self):
        stable = dict(self.release, channel="stable", version="1.6.0", build_id="stable-r7",
                      build_number=7, notes_id="stable-r7",
                      url=self.release["url"].replace("preview-r6", "stable-r7"))
        preview = dict(self.release, build_number=8, version="1.6.1-beta.1")
        self.assertEqual(self.service.last_update_results, {})
        self.assertTrue(self.service.check_all_updates(force=True))
        self.assertEqual(len(self.network.requests), 1)
        from core.update_service import verify_manifest
        with patch("core.update_service.verify_manifest", wraps=verify_manifest) as verify:
            self.network.latest.respond(self.signed(preview, stable))
        verify.assert_called_once()
        expected = {"stable": {"release": stable, "latest": stable},
                    "preview": {"release": preview, "latest": preview}}
        self.assertEqual(self.channel_results, [expected])
        self.assertEqual(self.service.last_update_results, expected)
        self.assertEqual(self.updates, [preview])
        # The user may prefer the formal release even when Beta is newer.
        self.assertTrue(self.service.download_release(stable))
        self.assertEqual(self.network.requests[-1].url().toString(), stable["url"])
        self.network.latest.respond(self.body)
        self.assertEqual(self.ready[-1][1], stable)
        self.service.discard_ready()
        self.assertTrue(self.service.download_release(preview))
        self.service.cancel_download()

    def test_all_channels_distinguish_absent_formal_release_from_installed_beta(self):
        self.service.current_build_number = 6
        self.service.check_all_updates()
        self.network.latest.respond(self.signed())
        self.assertEqual(self.channel_results, [{"stable": {"release": None, "latest": None},
                                               "preview": {"release": None, "latest": self.release}}])
        self.assertEqual(self.updates, [None])
        self.assertFalse(self.service.download_release(self.release))

    def test_all_channels_never_offer_a_formal_build_older_than_installed_beta(self):
        stable = dict(self.release, channel="stable", version="1.6.0", build_number=4)
        self.service.check_all_updates()
        self.network.latest.respond(self.signed(stable=stable))
        self.assertEqual(self.channel_results[-1]["stable"], {"release": None, "latest": stable})
        self.assertEqual(self.channel_results[-1]["preview"]["release"], self.release)
        self.assertFalse(self.service.download_release(stable))
        self.assertTrue(self.service.download_release(self.release))
        self.service.cancel_download()

    def test_all_channels_results_and_signal_are_independent_copies(self):
        self.service.check_all_updates()
        self.network.latest.respond(self.signed())
        self.channel_results[-1]["preview"]["release"]["url"] = "https://example.com/injected.exe"
        snapshot = self.service.last_update_results
        snapshot["preview"]["latest"]["sha256"] = "0"*64
        snapshot["stable"]["latest"] = self.release
        self.assertEqual(self.service.last_update_results["preview"]["release"], self.release)
        self.assertEqual(self.service.last_update_results["preview"]["latest"], self.release)
        self.assertIsNone(self.service.last_update_results["stable"]["latest"])
        self.assertTrue(self.service.download_release(self.release))
        self.service.cancel_download()

    def test_all_check_joins_single_channel_batch_without_duplicate_requests(self):
        stable = dict(self.release, channel="stable", version="1.6.0", build_number=7)
        self.service.set_source_mode("auto")
        self.service.check_updates("stable", force=True)
        self.service.check_all_updates(force=True)
        self.service.check_all_updates(force=True)
        self.assertEqual(len(self.network.requests), 3)
        self.network.replies[0].respond(self.signed(stable=stable))
        self.network.replies[1].respond(status=503)
        self.network.replies[2].respond(status=503)
        self.assertEqual(len(self.channel_results), 1)
        self.assertEqual(self.channel_results[0]["preview"]["release"], self.release)
        self.assertEqual(self.channel_results[0]["stable"]["release"], stable)
        self.assertEqual(self.updates, [stable])

    def test_single_channel_join_does_not_remove_an_inflight_dual_check(self):
        stable = dict(self.release, channel="stable", version="1.6.0", build_number=7)
        self.service.check_all_updates()
        self.service.check_updates("preview", force=True)
        self.assertEqual(len(self.network.requests), 1)
        self.network.latest.respond(self.signed(stable=stable))
        self.assertEqual(len(self.channel_results), 1)
        self.assertEqual(self.updates, [stable])
        # A later, separate legacy check returns to its single-channel contract.
        self.service.check_updates("preview")
        self.assertEqual(len(self.channel_results), 1)
        self.assertEqual(self.updates, [stable, self.release])
        self.assertFalse(self.service.download_release(stable))

    def test_dual_check_cache_is_reverified_and_authorizes_both_without_new_network(self):
        stable = dict(self.release, channel="stable", version="1.6.0", build_number=7)
        self.service.check_updates("preview")
        self.network.latest.respond(self.signed(stable=stable), headers={"ETag": '"both"'})
        self.assertFalse(self.channel_results)
        self.service.check_all_updates()
        self.assertEqual(len(self.network.requests), 1)
        self.assertTrue(self.service.last_result_from_cache["updates"])
        self.assertEqual(self.channel_results[-1]["stable"]["release"], stable)
        self.assertEqual(self.channel_results[-1]["preview"]["release"], self.release)
        self.assertTrue(self.service.download_release(stable))
        self.service.cancel_download()

    def test_dual_refresh_revokes_old_authorizations_and_replaces_both_results(self):
        stable = dict(self.release, channel="stable", version="1.6.0", build_number=7)
        self.service.check_all_updates()
        self.network.latest.respond(self.signed(stable=stable))
        self.service.check_all_updates(force=True)
        self.assertEqual(self.service.last_update_results, {})
        self.assertFalse(self.service.download_release(stable))
        self.assertFalse(self.service.download_release(self.release))
        latest_stable = dict(stable, build_number=9, version="1.7.0")
        latest_preview = dict(self.release, build_number=10, version="1.7.1-beta.1")
        self.network.latest.respond(self.signed(latest_preview, latest_stable))
        self.assertFalse(self.service.download_release(stable))
        self.assertFalse(self.service.download_release(self.release))
        self.assertEqual(self.channel_results[-1]["stable"]["release"], latest_stable)
        self.assertTrue(self.service.download_release(latest_stable))
        self.service.cancel_download()

    def test_dual_refresh_keeps_newest_other_channel_from_a_verified_cache(self):
        newest_stable = dict(self.release, channel="stable", version="1.7.0", build_number=9)
        self.service.check_all_updates()
        self.network.latest.respond(self.signed(stable=newest_stable))
        before = (self.root/"updates.json").read_bytes()
        self.service.set_source_mode("ghfast")
        self.service.check_all_updates(force=True)
        older_stable = dict(newest_stable, version="1.6.0", build_number=7)
        older_preview = dict(self.release, version="1.6.1-beta.1", build_number=8)
        self.network.latest.respond(self.signed(older_preview, older_stable))
        self.assertEqual(self.channel_results[-1]["stable"]["latest"], newest_stable)
        self.assertEqual(self.channel_results[-1]["preview"]["latest"], self.release)
        self.assertTrue(self.service.last_result_from_cache["updates"])
        self.assertEqual((self.root/"updates.json").read_bytes(), before)

    def test_dual_refresh_uses_signed_publication_time_when_highest_build_matches(self):
        stable = dict(self.release, channel="stable", version="1.7.0", build_number=9)
        self.service.check_all_updates()
        self.network.latest.respond(self.signed(stable=stable))
        self.service.check_all_updates(force=True)
        preview = dict(self.release, build_number=8, version="1.6.1-beta.1")
        self.network.latest.respond(self.signed(preview, stable, "2026-09-30T00:00:00Z"))
        self.assertEqual(self.channel_results[-1]["preview"]["release"], preview)
        self.assertFalse(self.service.last_result_from_cache["updates"])

    def test_dual_check_refuses_to_replace_active_or_prepared_download(self):
        self.service.check_all_updates()
        self.network.latest.respond(self.signed())
        self.service.download_release(self.release)
        requests = len(self.network.requests)
        self.assertFalse(self.service.check_all_updates(force=True))
        self.assertEqual(len(self.network.requests), requests)
        self.network.latest.respond(self.body)
        self.assertFalse(self.service.check_all_updates(force=True))
        self.assertEqual(len(self.network.requests), requests)
        self.assertTrue(self.service.ready_path)

    def test_dual_check_never_authorizes_unsigned_or_expired_failed_results(self):
        self.service.check_all_updates()
        self.network.latest.respond(self.signed())
        cache_path = self.root/"updates.json"
        cached = json.loads(cache_path.read_text())
        cached["fetched_at"] -= 13*60*60
        cache_path.write_text(json.dumps(cached))
        self.service.check_all_updates()
        self.network.latest.respond(b"unsigned manifest")
        self.assertEqual(len(self.channel_results), 1)
        self.assertEqual(self.service.last_update_results, {})
        self.assertFalse(self.service.download_release(self.release))
        self.service.close()
        self.assertFalse(self.service.check_all_updates(force=True))

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

    def test_github_cdn_redirect_preserves_encoded_disposition_query(self):
        # GitHub's real Location header uses encoded spaces in the signed CDN
        # query. PrettyDecoded produces a literal space, which the production
        # URL validator correctly rejects; transport must keep FullyEncoded.
        location = (
            "https://release-assets.githubusercontent.com/github-production-release-asset/123/asset-id"
            "?response-content-disposition=attachment%3B%20filename%3DOsuSkinEditor-preview-r6.exe"
            "&response-content-type=application%2Foctet-stream&sig=opaque-signature"
        )
        decoded = QUrl(location).toString()
        self.assertIn(" ", decoded)
        self.assertFalse(_safe_url(decoded, download=True, redirected=True))
        reply = self.start_download()
        reply.respond(status=302, redirect=location)
        self.assertEqual(len(self.network.requests), 3)
        redirected_url = self.network.requests[-1].url().toString(QUrl.FullyEncoded)
        self.assertEqual(redirected_url, location)
        self.assertIn("%20", redirected_url)
        self.assertIn("%3B", redirected_url)
        self.assertNotIn(" ", redirected_url)
        self.network.latest.respond(self.body, headers={"Content-Length": len(self.body)})
        self.assertEqual(len(self.ready), 1)
        self.assertEqual(Path(self.ready[0][0]).read_bytes(), self.body)
        self.assertEqual(self.ready[0][1], self.release)
        self.assertFalse(self.errors)

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
        with patch("core.update_service.DOWNLOAD_CONNECT_TIMEOUT_MS", 10):
            self.service.download_release(self.release)
            QTest.qWait(30)
        self.assertTrue(self.network.latest.aborted)
        self.assertFalse(list(self.root.rglob("*.download")))
        self.service.check_announcements(force=True)
        self.service.close()
        self.assertTrue(self.network.latest.aborted)
        self.assertTrue(unrelated.exists())
        self.assertFalse(self.service.check_announcements())

    def test_auto_checks_real_metadata_in_parallel_and_chooses_newest_verified_release(self):
        self.service.set_source_mode("auto")
        self.service.check_updates("preview")
        self.assertEqual(len(self.network.requests), 3)
        self.service.check_updates("preview", force=True)
        self.assertEqual(len(self.network.requests), 3)
        newest = dict(self.release, build_number=7, version="1.6.0-preview.7")
        self.network.replies[1].respond(self.signed())
        self.assertFalse(self.updates)
        self.network.replies[0].respond(self.signed(newest))
        self.network.replies[2].respond(b"<html>Unavailable</html>")
        self.assertEqual(self.updates, [newest])
        self.assertFalse(self.errors)
        self.assertEqual(self.service.last_source["updates"], "raw.githubusercontent.com")

    def test_metadata_settle_deadline_aborts_slow_sources_and_delivers_once(self):
        self.service.set_source_mode("auto")
        with patch("core.update_service.METADATA_SETTLE_MS", 10):
            self.service.check_updates("preview")
            replies = list(self.network.replies)
            replies[1].respond(self.signed())
            QTest.qWait(40)
        self.assertEqual(self.updates, [self.release])
        self.assertTrue(replies[0].aborted)
        self.assertTrue(replies[2].aborted)
        self.assertEqual(self.updates, [self.release])
        self.assertFalse(self.errors)
        self.assertEqual(self.service.last_source["updates"], "ghfast.top")

    def test_original_unavailable_mirror_valid_signature_succeeds(self):
        self.service.set_source_mode("auto")
        self.service.check_updates("preview")
        replies = list(self.network.replies)
        replies[0].respond(status=503)
        replies[1].sslErrors.emit(["bad certificate"])
        replies[2].respond(self.signed())
        self.assertEqual(self.updates, [self.release])
        self.assertFalse(self.errors)
        self.assertEqual(self.service.last_source["updates"], "gh-proxy.org")

    def test_all_sources_timeout_emit_one_failure_without_authorization(self):
        self.service.set_source_mode("auto")
        with patch("core.update_service.METADATA_TIMEOUT_MS", 10):
            self.service.check_updates("preview")
            QTest.qWait(40)
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.updates)
        self.assertTrue(all(reply.aborted for reply in self.network.replies))
        self.assertEqual(self.service.state, "idle")

    def test_etag_is_bound_to_exact_source_and_unconditional_304_cannot_use_cache(self):
        self.authorize()
        self.service.set_source_mode("auto")
        self.service.check_updates("preview", force=True)
        requests, replies = self.network.requests[-3:], self.network.replies[-3:]
        self.assertEqual(bytes(requests[0].rawHeader("If-None-Match")), b'"one"')
        self.assertFalse(bytes(requests[1].rawHeader("If-None-Match")))
        self.assertFalse(bytes(requests[2].rawHeader("If-None-Match")))
        replies[0].respond(status=503)
        replies[1].respond(status=304)
        replies[2].respond(self.signed(), headers={"ETag": '"proxy"'})
        cache = json.loads((self.root/"updates.json").read_text())
        self.assertTrue(cache["source_url"].startswith("https://gh-proxy.org/"))
        self.assertEqual(cache["etag"], '"proxy"')
        self.assertEqual(len(self.updates), 2)
        self.assertFalse(self.errors)

    def test_legacy_cache_without_source_never_sends_etag(self):
        self.authorize()
        cache_path = self.root/"updates.json"
        cache = json.loads(cache_path.read_text())
        del cache["source_url"]
        cache_path.write_text(json.dumps(cache))
        self.service.check_updates("preview", force=True)
        self.assertFalse(bytes(self.network.requests[-1].rawHeader("If-None-Match")))
        self.network.latest.respond(status=304)
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(len(self.errors), 1)

    def test_redirected_metadata_etag_belongs_to_final_response_url_only(self):
        self.service.set_source_mode("ghfast")
        self.service.check_updates("preview", force=True)
        mirror_request = self.network.requests[-1]
        self.assertTrue(mirror_request.url().toString().startswith("https://ghfast.top/"))
        self.assertFalse(bytes(mirror_request.rawHeader("If-None-Match")))
        self.network.latest.respond(status=302, redirect=MANIFEST_URL)
        self.assertEqual(self.network.requests[-1].url().toString(), MANIFEST_URL)
        self.network.latest.respond(self.signed(), headers={"ETag": '"final-origin"'})
        cache = json.loads((self.root/"updates.json").read_text())
        self.assertEqual(cache["source_url"], MANIFEST_URL)
        self.assertEqual(cache["etag"], '"final-origin"')

        self.service.set_source_mode("auto")
        self.service.check_updates("preview", force=True)
        requests, replies = self.network.requests[-3:], self.network.replies[-3:]
        self.assertEqual(bytes(requests[0].rawHeader("If-None-Match")), b'"final-origin"')
        self.assertFalse(bytes(requests[1].rawHeader("If-None-Match")))
        self.assertFalse(bytes(requests[2].rawHeader("If-None-Match")))
        replies[0].respond(status=304)
        replies[1].respond(status=503)
        replies[2].respond(status=503)
        self.assertEqual(self.updates, [self.release, self.release])
        self.assertFalse(self.errors)

    def test_stale_mirror_cannot_replace_newer_verified_cache(self):
        self.authorize()
        cache_path = self.root/"updates.json"
        before = cache_path.read_bytes()
        older = dict(self.release, build_number=4, version="1.6.0-preview.4")
        self.service.set_source_mode("ghfast")
        self.service.check_updates("preview", force=True)
        self.network.latest.respond(self.signed(older))
        self.assertEqual(self.updates[-1], self.release)
        self.assertTrue(self.service.last_result_from_cache["updates"])
        self.assertEqual(cache_path.read_bytes(), before)

    def test_download_retry_resets_partial_progress_hash_and_ignores_old_callbacks(self):
        self.authorize()
        self.service.set_source_mode("auto")
        self.service.download_release(self.release)
        old = self.network.latest
        old.begin(200)
        old.push(self.body[:100])
        old.finish(QNetworkReply.RemoteHostClosedError)
        current = self.network.latest
        self.assertIsNot(old, current)
        self.assertTrue(old.aborted)
        self.assertEqual(self.progress[-1], (0, len(self.body)))
        self.assertEqual(self.service.state, "downloading")
        old.sslErrors.emit(["late error"])
        old.finished.emit()
        current.respond(self.body)
        self.assertEqual(len(self.ready), 1)
        self.assertEqual(Path(self.ready[0][0]).read_bytes(), self.body)
        self.assertEqual(self.ready[0][1], self.release)
        self.assertFalse(self.errors)

    def test_hash_mismatch_and_wrong_size_try_next_source_without_preparing(self):
        self.authorize()
        self.service.set_source_mode("auto")
        self.service.download_release(self.release)
        self.network.latest.respond(b"X"+self.body[1:])
        self.assertFalse(self.ready)
        self.network.latest.begin(200, {"Content-Length": 1})
        self.assertFalse(self.ready)
        self.network.latest.respond(self.body)
        self.assertEqual(len(self.ready), 1)
        self.assertEqual(Path(self.ready[0][0]).read_bytes(), self.body)
        self.assertFalse(self.errors)

    def test_cancel_and_close_after_retry_never_start_another_source(self):
        self.authorize()
        self.service.set_source_mode("auto")
        self.service.download_release(self.release)
        self.network.latest.respond(status=503)
        current = self.network.latest
        count = len(self.network.requests)
        self.service.cancel_download()
        current.sslErrors.emit(["late error"])
        current.finished.emit()
        QTest.qWait(10)
        self.assertEqual(len(self.network.requests), count)
        self.assertFalse(list(self.root.rglob("*.download")))
        self.service.check_updates("preview", force=True)
        pending = self.network.replies[-3:]
        count = len(self.network.requests)
        self.service.close()
        self.assertTrue(all(reply.aborted for reply in pending))
        QTest.qWait(10)
        self.assertEqual(len(self.network.requests), count)
        self.assertFalse(self.errors)

    def test_disk_error_in_auto_mode_is_terminal_without_network_retry(self):
        self.authorize()
        self.service.set_source_mode("auto")
        self.service.download_release(self.release)
        transfer = self.service._operations["download"]
        original = transfer.stream
        class BrokenDisk:
            def write(self, data):
                raise OSError("Disk full")
            def close(self):
                original.close()
        transfer.stream = BrokenDisk()
        count = len(self.network.requests)
        self.network.latest.respond(self.body)
        self.assertEqual(len(self.network.requests), count)
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.service.downloading)

    def test_first_byte_timeout_retries_and_successful_source_is_remembered(self):
        self.authorize()
        self.service.set_source_mode("auto")
        with patch("core.update_service.DOWNLOAD_CONNECT_TIMEOUT_MS", 10):
            self.service.download_release(self.release)
            old = self.network.latest
            self.service._operations["download"].timer.timeout.emit()
            current = self.network.latest
            self.assertIsNot(old, current)
            current.respond(self.body)
        self.assertEqual(len(self.ready), 1)
        preferred = self.service.last_source["download"]
        self.service.discard_ready()
        self.service.download_release(self.release)
        self.assertEqual(self.network.requests[-1].url().host(), preferred)
        self.service.cancel_download()

    def test_source_mode_changes_are_refused_while_checking_or_downloading(self):
        self.assertFalse(self.service.set_source_mode("unknown"))
        self.service.check_updates("preview")
        self.assertFalse(self.service.set_source_mode("ghfast"))
        self.network.latest.respond(self.signed())
        self.assertTrue(self.service.set_source_mode("ghfast"))
        self.service.download_release(self.release)
        self.assertTrue(self.network.requests[-1].url().toString().startswith("https://ghfast.top/"))
        self.assertFalse(self.service.set_source_mode("auto"))
        self.service.cancel_download()

    def test_synchronous_close_on_checking_state_never_starts_requests(self):
        self.service.set_source_mode("auto")
        self.service.state_changed.connect(lambda state: self.service.close() if state == "checking" else None)
        self.service.check_updates("preview")
        self.assertFalse(self.network.requests)
        self.assertFalse(self.service._operations)
        self.assertEqual(self.service.state, "idle")
        self.assertFalse(self.errors)

    def test_synchronous_cancel_on_download_source_never_starts_requests(self):
        self.authorize()
        self.service.set_source_mode("auto")
        self.service.source_changed.connect(lambda kind, _host: self.service.cancel_download()
                                            if kind == "download" else None)
        requests = len(self.network.requests)
        self.assertFalse(self.service.download_release(self.release))
        self.assertEqual(len(self.network.requests), requests)
        self.assertFalse(self.service.downloading)
        self.assertFalse(list(self.root.rglob("*.download")))
        self.assertFalse(self.errors)

    def test_redirect_pending_old_read_does_not_block_new_reply_streaming(self):
        self.body = b"MZ" + b"A"*(2*1024*1024)
        self.release["size"] = len(self.body)
        self.release["sha256"] = hashlib.sha256(self.body).hexdigest()
        old = self.start_download()
        old.begin(302, redirect="https://release-assets.githubusercontent.com/asset?sig=test")
        old.push(b"R"*(400*1024))
        self.assertTrue(self.service._operations["download"].read_queued)
        # Finishing drains the remainder and redirects while old.resume is
        # still pending. The new reply needs an independent queued-read flag.
        old.finish()
        current = self.network.latest
        self.assertIsNot(old, current)
        current.begin(200)
        current.push(self.body)
        self.assertFalse(self.ready)
        QTest.qWait(40)
        self.assertEqual(self.progress[-1], (len(self.body), len(self.body)))
        self.assertFalse(self.service._operations["download"].read_queued)
        # Assert streaming completed before finished can provide its separate
        # drain loop; otherwise that loop would hide the queue contamination.
        current.finish()
        self.assertEqual(len(self.ready), 1)
        self.assertEqual(Path(self.ready[0][0]).read_bytes(), self.body)
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
