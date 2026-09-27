import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.update_protocol import (MAX_MANIFEST_BYTES, MAX_PAYLOAD_BYTES, MAX_RELEASE_SIZE,
                                  ManifestError, select_release, validate_release_url, verify_manifest)
from tools.publish_update import build_manifest, main as publish_main


BASE_URL = "https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/"
URL = BASE_URL + "v1.6.0-preview.5/OsuSkinEditor-v1.6.0-preview.5-windows-x64.exe"


class UpdateProtocolTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw,
                                                        serialization.PublicFormat.Raw)
        self.release = {
            "version": "1.6.0-preview.5", "build_id": "preview-r5", "build_number": 5,
            "channel": "preview", "platform": "windows-x64", "url": URL,
            "sha256": "ab" * 32, "size": 1024, "notes_id": "preview-r5",
        }
        self.payload = {"schema_version": 1, "published_at": "2026-09-28T13:00:00Z",
                        "channels": {"stable": None, "preview": self.release}}

    def sign_bytes(self, raw, key=None):
        return json.dumps({
            "schema_version": 1, "payload": base64.b64encode(raw).decode("ascii"),
            "signature": base64.b64encode((key or self.key).sign(raw)).decode("ascii"),
        }).encode("utf-8")

    def sign(self, payload=None):
        return self.sign_bytes(json.dumps(self.payload if payload is None else payload).encode("utf-8"))

    def invalid_release(self, **updates):
        payload = deepcopy(self.payload)
        payload["channels"]["preview"].update(updates)
        with self.assertRaises(ManifestError):
            verify_manifest(self.sign(payload), self.public)

    def test_valid_manifest_returns_authenticated_fields(self):
        self.assertEqual(verify_manifest(self.sign(), self.public), self.payload)

    def test_exact_utf8_payload_bytes_are_verified_without_reserialising(self):
        raw = json.dumps(self.payload, indent=4).encode("utf-8") + b"\n"
        envelope = self.sign_bytes(raw)
        self.assertEqual(verify_manifest(envelope, self.public), self.payload)
        changed = json.loads(envelope)
        changed["payload"] = base64.b64encode(json.dumps(self.payload).encode()).decode()
        with self.assertRaises(ManifestError):
            verify_manifest(json.dumps(changed).encode(), self.public)

    def test_wrong_key_and_tampered_signature_or_payload_fail(self):
        other = Ed25519PrivateKey.generate()
        with self.assertRaises(ManifestError):
            verify_manifest(self.sign_bytes(json.dumps(self.payload).encode(), other), self.public)
        envelope = json.loads(self.sign())
        for field in ("payload", "signature"):
            with self.subTest(field=field):
                damaged = dict(envelope)
                raw = bytearray(base64.b64decode(damaged[field]))
                raw[-1] ^= 1
                damaged[field] = base64.b64encode(raw).decode()
                with self.assertRaises(ManifestError):
                    verify_manifest(json.dumps(damaged).encode(), self.public)

    def test_embedded_key_is_used_when_override_is_absent(self):
        with patch("core.update_public_key.PUBLIC_KEY_HEX", self.public.hex()):
            self.assertEqual(verify_manifest(self.sign()), self.payload)

    def test_invalid_key_configuration_fails_closed(self):
        for key in (b"", b"x" * 31, b"x" * 33, self.public.hex()):
            with self.subTest(key_type=type(key)), self.assertRaises(ManifestError):
                verify_manifest(self.sign(), key)
        with patch("core.update_public_key.PUBLIC_KEY_HEX", "not a public key"):
            with self.assertRaises(ManifestError):
                verify_manifest(self.sign())

    def test_unknown_or_boolean_schema_is_rejected_before_release_selection(self):
        for version in (True, 2, "1", None):
            with self.subTest(schema=version):
                envelope = json.loads(self.sign())
                envelope["schema_version"] = version
                with self.assertRaises(ManifestError):
                    verify_manifest(json.dumps(envelope).encode(), self.public)
                payload = deepcopy(self.payload)
                payload["schema_version"] = version
                with self.assertRaises(ManifestError):
                    verify_manifest(self.sign(payload), self.public)

    def test_duplicate_members_are_rejected_in_envelope_and_signed_payload(self):
        envelope = self.sign().decode()
        duplicate_envelope = envelope.replace('{', '{"schema_version":1,', 1).encode()
        with self.assertRaises(ManifestError):
            verify_manifest(duplicate_envelope, self.public)
        raw = json.dumps(self.payload).replace('"build_number": 5', '"build_number": 4, "build_number": 5')
        with self.assertRaises(ManifestError):
            verify_manifest(self.sign_bytes(raw.encode()), self.public)

    def test_nonfinite_json_numbers_invalid_utf8_and_truncated_json_fail(self):
        for raw in (b"\xff", b'{"schema_version":', b"NaN", b"Infinity"):
            with self.subTest(raw=raw):
                with self.assertRaises(ManifestError):
                    verify_manifest(self.sign_bytes(raw), self.public)

    def test_unknown_and_missing_members_fail_closed(self):
        for location in ("envelope", "payload", "channels", "release"):
            for extra in (True, False):
                with self.subTest(location=location, extra=extra):
                    payload = deepcopy(self.payload)
                    envelope = json.loads(self.sign())
                    target = {"envelope": envelope, "payload": payload,
                              "channels": payload["channels"],
                              "release": payload["channels"]["preview"]}[location]
                    if extra:
                        target["surprise"] = "value"
                    else:
                        target.pop(next(iter(target)))
                    data = json.dumps(envelope).encode() if location == "envelope" else self.sign(payload)
                    with self.assertRaises(ManifestError):
                        verify_manifest(data, self.public)

    def test_only_direct_project_https_exe_release_urls_are_accepted(self):
        self.assertEqual(validate_release_url(URL), URL)
        invalid = [
            URL.replace("https:", "http:"), URL.replace("github.com", "github.com.evil.test"),
            URL.replace("github.com", "github.com@evil.test"),
            URL.replace("github.com", "attacker@github.com"),
            URL.replace("github.com", "github.com:443"),
            URL.replace("XiaoLan9999", "somebody"), URL.replace("OsuSkinEditor/releases", "other/releases"),
            BASE_URL + "../program.exe", BASE_URL + "v1/../program.exe",
            BASE_URL + "%2e%2e/program.exe", BASE_URL + "v1%2f..%2fprogram.exe",
            BASE_URL + "v1\\program.exe", BASE_URL + "v1//program.exe",
            BASE_URL + "v1/program.exe/extra", BASE_URL + "v1/program.zip",
            URL + "?token=value", URL + "?", URL + "#fragment", URL + "#",
            " " + URL, URL + "\n", URL.replace("github.com", "github.com\t"),
            URL.replace("github.com", "github.com."), URL.replace("github.com", "github．com"),
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ManifestError):
                validate_release_url(url)

    def test_signed_untrusted_url_is_still_rejected(self):
        self.invalid_release(url="https://example.com/update.exe")

    def test_build_and_size_are_positive_integers_not_booleans(self):
        for value in (True, False, 0, -1, 5.0, "5", None):
            with self.subTest(build=value):
                self.invalid_release(build_number=value)
            with self.subTest(size=value):
                self.invalid_release(size=value)
        self.invalid_release(size=MAX_RELEASE_SIZE + 1)
        payload = deepcopy(self.payload)
        payload["channels"]["preview"]["size"] = MAX_RELEASE_SIZE
        self.assertEqual(verify_manifest(self.sign(payload), self.public)["channels"]["preview"]["size"], MAX_RELEASE_SIZE)

    def test_channel_platform_hash_and_identifiers_are_validated(self):
        for updates in ({"channel": "stable"}, {"channel": "beta"}, {"platform": "linux-x64"},
                        {"sha256": "a" * 63}, {"sha256": "x" * 64}, {"sha256": 0},
                        {"version": ""}, {"build_id": "../bad"}, {"notes_id": "bad\nvalue"}):
            with self.subTest(updates=updates):
                self.invalid_release(**updates)
        payload = deepcopy(self.payload)
        payload["channels"]["preview"]["sha256"] = "AB" * 32
        self.assertEqual(verify_manifest(self.sign(payload), self.public)["channels"]["preview"]["sha256"], "ab" * 32)

    def test_publication_timestamp_requires_valid_utc_iso_date(self):
        for value in ("2026-09-28", "2026-09-28T13:00:00", "2026-09-28T13:00:00+08:00",
                      "2026-02-30T13:00:00Z", "2026-09-28T25:00:00Z", True):
            payload = deepcopy(self.payload)
            payload["published_at"] = value
            with self.subTest(value=value), self.assertRaises(ManifestError):
                verify_manifest(self.sign(payload), self.public)
        for value in ("2026-09-28T13:00:00+00:00", "2026-09-28T13:00:00.123456Z"):
            payload = deepcopy(self.payload)
            payload["published_at"] = value
            self.assertEqual(verify_manifest(self.sign(payload), self.public)["published_at"], value)

    def test_malformed_base64_and_short_signature_are_rejected(self):
        for field, value in (("payload", "%%%"), ("payload", ""),
                             ("signature", base64.b64encode(b"x" * 63).decode()),
                             ("signature", base64.b64encode(b"x" * 65).decode())):
            envelope = json.loads(self.sign())
            envelope[field] = value
            with self.subTest(field=field), self.assertRaises(ManifestError):
                verify_manifest(json.dumps(envelope).encode(), self.public)
        envelope = json.loads(self.sign())
        envelope["payload"] += "\n"
        with self.assertRaises(ManifestError):
            verify_manifest(json.dumps(envelope).encode(), self.public)

    def test_envelope_and_payload_size_limits_are_enforced(self):
        with self.assertRaises(ManifestError):
            verify_manifest(b" " * (MAX_MANIFEST_BYTES + 1), self.public)
        with self.assertRaises(ManifestError):
            verify_manifest(self.sign_bytes(b" " * (MAX_PAYLOAD_BYTES + 1)), self.public)

    def test_selection_uses_build_number_only_and_never_crosses_channels(self):
        payload = verify_manifest(self.sign(), self.public)
        self.assertEqual(select_release(payload, "preview", 4), self.release)
        self.assertIsNone(select_release(payload, "preview", 5))
        self.assertIsNone(select_release(payload, "preview", 6))
        self.assertIsNone(select_release(payload, "stable", 0))
        payload["channels"]["preview"]["version"] = "0.0.1"
        self.assertIsNotNone(select_release(payload, "preview", 4))
        payload["channels"]["preview"]["version"] = "9999.9999"
        self.assertIsNone(select_release(payload, "preview", 6))
        for current in (True, -1, "4", 4.0):
            with self.subTest(current=current), self.assertRaises(ManifestError):
                select_release(payload, "preview", current)
        with self.assertRaises(ManifestError):
            select_release(payload, "beta", 4)

    def test_selection_revalidates_channel_and_returns_an_independent_release(self):
        selected = select_release(self.payload, "preview", 4)
        selected["size"] = 1
        self.assertEqual(self.release["size"], 1024)
        damaged = deepcopy(self.payload)
        damaged["channels"]["preview"]["channel"] = "stable"
        with self.assertRaises(ManifestError):
            select_release(damaged, "preview", 4)


class PublishManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.key_path = self.root / "private.pem"
        self.key_path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
                                                       serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.exe = self.root / "app.exe"
        self.exe.write_bytes(b"MZ" + b"fixture executable" * 5)
        self.args = dict(exe=self.exe, version="1.6.0-preview.5", build_id="preview-r5",
                         build_number=5, channel="preview", url=URL, private_key=self.key_path,
                         published_at="2026-09-28T13:00:00Z")

    def tearDown(self):
        self.temp.cleanup()

    def test_publisher_hashes_actual_binary_and_signs_exact_payload(self):
        envelope = build_manifest(**self.args)
        payload = verify_manifest(envelope, self.public)
        release = payload["channels"]["preview"]
        self.assertEqual(release["sha256"], hashlib.sha256(self.exe.read_bytes()).hexdigest())
        self.assertEqual(release["size"], self.exe.stat().st_size)
        self.assertEqual(release["notes_id"], "preview-r5")
        self.assertIsNone(payload["channels"]["stable"])

    def test_existing_manifest_is_verified_and_other_channel_preserved(self):
        initial = dict(self.args, version="1.5", build_id="v1.5", build_number=1, channel="stable")
        existing = self.root / "manifest.json"
        existing.write_bytes(build_manifest(**initial))
        old = verify_manifest(existing.read_bytes(), self.public)
        updated = verify_manifest(build_manifest(**self.args, existing=existing), self.public)
        self.assertEqual(updated["channels"]["stable"], old["channels"]["stable"])
        self.assertEqual(updated["channels"]["preview"]["build_number"], 5)

    def test_unsigned_or_different_key_existing_manifest_cannot_be_merged(self):
        existing = self.root / "manifest.json"
        existing.write_text('{"schema_version":1,"channels":{"stable":null,"preview":null}}')
        with self.assertRaises(ManifestError):
            build_manifest(**self.args, existing=existing)
        other = Ed25519PrivateKey.generate()
        other_key = self.root / "other.key"
        other_key.write_bytes(other.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                serialization.NoEncryption()))
        existing.write_bytes(build_manifest(**dict(self.args, private_key=other_key)))
        with self.assertRaises(ManifestError):
            build_manifest(**self.args, existing=existing)

    def test_publisher_rejects_equal_or_older_build_in_same_channel(self):
        existing = self.root / "manifest.json"
        existing.write_bytes(build_manifest(**self.args))
        for number in (4, 5):
            with self.subTest(number=number), self.assertRaises(ManifestError):
                build_manifest(**dict(self.args, build_number=number), existing=existing)

    def test_publisher_requires_build_number_to_increase_across_channels(self):
        existing = self.root / "manifest.json"
        existing.write_bytes(build_manifest(**self.args))  # Preview build 5
        stable = dict(self.args, version="1.6.0", build_id="stable-r6", channel="stable",
                      url=BASE_URL + "v1.6.0/OsuSkinEditor-v1.6.0-windows-x64.exe")
        with self.assertRaises(ManifestError):
            build_manifest(**dict(stable, build_number=4), existing=existing)
        updated = verify_manifest(build_manifest(**dict(stable, build_number=6), existing=existing), self.public)
        self.assertEqual(updated["channels"]["preview"]["build_number"], 5)
        self.assertEqual(updated["channels"]["stable"]["build_number"], 6)

    def test_raw_private_key_is_supported_and_repository_key_is_rejected(self):
        raw_path = self.root / "private.key"
        raw_path.write_bytes(self.key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                  serialization.NoEncryption()))
        self.assertEqual(verify_manifest(build_manifest(**dict(self.args, private_key=raw_path)), self.public)
                         ["channels"]["preview"]["build_number"], 5)
        with patch("tools.publish_update.REPOSITORY_ROOT", self.root):
            with self.assertRaises(ManifestError):
                build_manifest(**self.args)

    def cli_args(self, output):
        return ["--exe", str(self.exe), "--version", "1.6.0-preview.5", "--build-id", "preview-r5",
                "--build-number", "5", "--channel", "preview", "--url", URL,
                "--private-key", str(self.key_path), "--output", str(output)]

    def test_cli_writes_only_signed_envelope_without_printing_key_material(self):
        output = self.root / "public" / "manifest.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(publish_main(self.cli_args(output)), 0)
        self.assertEqual(verify_manifest(output.read_bytes(), self.public)["channels"]["preview"]["build_number"], 5)
        self.assertNotIn("PRIVATE KEY", stdout.getvalue())
        self.assertNotIn(self.key_path.read_text(), stdout.getvalue())
        self.assertFalse(list(output.parent.glob(".update-manifest-*")))

    def test_cli_refuses_to_overwrite_key_binary_or_unverified_manifest(self):
        old_key = self.key_path.read_bytes()
        old_exe = self.exe.read_bytes()
        existing = self.root / "existing.json"
        existing.write_bytes(b"must be retained")
        for output in (self.key_path, self.exe, existing):
            with self.subTest(output=output.name), redirect_stderr(io.StringIO()):
                self.assertEqual(publish_main(self.cli_args(output)), 1)
        self.assertEqual(self.key_path.read_bytes(), old_key)
        self.assertEqual(self.exe.read_bytes(), old_exe)
        self.assertEqual(existing.read_bytes(), b"must be retained")

    def test_invalid_private_key_errors_do_not_echo_its_content(self):
        secret = b"this is private data and must not be printed"
        self.key_path.write_bytes(secret)
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(publish_main(self.cli_args(self.root / "out.json")), 1)
        self.assertNotIn(secret.decode(), errors.getvalue())
        self.assertFalse((self.root / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
