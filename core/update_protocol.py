"""Authenticated update metadata, independent of networking and UI.

Ed25519 authenticates the exact decoded payload bytes. JSON is only decoded
after verification, with duplicate keys and unknown schemas rejected. This
module never downloads, executes or installs a release.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PAYLOAD_BYTES = 128 * 1024
MAX_RELEASE_SIZE = 512 * 1024 * 1024
MAX_DOWNLOAD_BYTES = MAX_RELEASE_SIZE
CHANNELS = ("stable", "preview")
PLATFORM = "windows-x64"
RELEASE_PREFIX = "/XiaoLan9999/OsuSkinEditor/releases/download/"


class ManifestError(ValueError):
    """The update manifest cannot be safely used."""


def _fail(message):
    raise ManifestError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(value):
    _fail("Non-finite JSON number")


def _json(data, limit):
    if not isinstance(data, bytes) or not 0 < len(data) <= limit:
        _fail("Manifest data is empty, too large or not bytes")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("Invalid manifest JSON") from None


def _members(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        _fail(f"Invalid {name} members")


def _schema(value):
    if type(value) is not int or value != SCHEMA_VERSION:
        _fail("Unsupported update schema")


def _integer(value, name, minimum=1, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        _fail(f"Invalid {name}")
    return value


def _identifier(value, name, maximum=128, version=False):
    pattern = r"[A-Za-z0-9][A-Za-z0-9._+\-]*" if version else r"[A-Za-z0-9][A-Za-z0-9._\-]*"
    if not isinstance(value, str) or len(value) > maximum or not re.fullmatch(pattern, value):
        _fail(f"Invalid {name}")
    return value


def validate_release_url(url):
    """Allow only a direct EXE release asset in this exact GitHub repository.

    No URL decoding is performed: encoded separators/dot segments, credentials,
    ports, query strings, fragments and control characters are all rejected.
    """
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        _fail("Invalid release URL")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url):
        _fail("Invalid release URL")
    if any(character in url for character in ("%", "\\", "?", "#")):
        _fail("Invalid release URL")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ManifestError("Invalid release URL") from None
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment:
        _fail("Release URL must use the project's HTTPS GitHub release assets")
    if not parts.path.startswith(RELEASE_PREFIX):
        _fail("Release URL must use the project's release assets")
    tail = parts.path[len(RELEASE_PREFIX):].split("/")
    if len(tail) != 2:
        _fail("Release URL must contain one tag and one filename")
    tag, filename = tail
    _identifier(tag, "release tag")
    _identifier(filename, "release filename", maximum=255)
    if not filename.lower().endswith(".exe"):
        _fail("Release asset must be an EXE")
    return url


def validate_release(release, channel=None):
    """Validate release fields and return a fresh dictionary."""
    fields = {"version", "build_id", "build_number", "channel", "platform",
              "url", "sha256", "size", "notes_id"}
    _members(release, fields, "release")
    if release["channel"] not in CHANNELS or (channel is not None and release["channel"] != channel):
        _fail("Release channel does not match its manifest slot")
    if release["platform"] != PLATFORM:
        _fail("Unsupported release platform")
    _identifier(release["version"], "release version", maximum=64, version=True)
    _identifier(release["build_id"], "release build ID")
    _identifier(release["notes_id"], "release notes ID")
    _integer(release["build_number"], "release build number")
    _integer(release["size"], "release size", maximum=MAX_RELEASE_SIZE)
    validate_release_url(release["url"])
    digest = release["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", digest):
        _fail("Invalid release SHA-256")
    result = dict(release)
    result["sha256"] = digest.lower()
    return result


def validate_manifest_payload(payload):
    """Schema validation for the publisher and already-authenticated payloads.

    Network callers must use verify_manifest instead of this helper.
    """
    _members(payload, {"schema_version", "published_at", "channels"}, "payload")
    _schema(payload["schema_version"])
    published_at = payload["published_at"]
    if not isinstance(published_at, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", published_at):
        _fail("Manifest publication time must be an ISO UTC timestamp")
    try:
        timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        raise ManifestError("Invalid manifest publication time") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        _fail("Manifest publication time must use UTC")
    channels = payload["channels"]
    _members(channels, CHANNELS, "channel")
    result = {"schema_version": SCHEMA_VERSION, "published_at": published_at, "channels": {}}
    for channel in CHANNELS:
        release = channels[channel]
        result["channels"][channel] = None if release is None else validate_release(release, channel)
    return result


def _base64(value, name, maximum):
    if not isinstance(value, str) or not value or len(value) > 4 * ((maximum + 2) // 3):
        _fail(f"Invalid {name} encoding")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ManifestError(f"Invalid {name} encoding") from None
    if not decoded or len(decoded) > maximum or base64.b64encode(decoded).decode("ascii") != value:
        _fail(f"Invalid {name} encoding")
    return decoded


def _public_key(public_key):
    if public_key is None:
        try:
            from core.update_public_key import PUBLIC_KEY_HEX
            public_key = bytes.fromhex(PUBLIC_KEY_HEX)
        except (ImportError, AttributeError, ValueError, TypeError):
            raise ManifestError("Update verification key is not configured") from None
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        _fail("Update verification key must contain 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(public_key)


def verify_manifest(data: bytes, public_key: bytes | None = None) -> dict:
    """Verify an envelope with a 32-byte raw public key, then validate its JSON.

    Omitting public_key uses core.update_public_key.PUBLIC_KEY_HEX. No release
    fields or URLs are returned if authentication or schema validation fails.
    """
    envelope = _json(data, MAX_MANIFEST_BYTES)
    _members(envelope, {"schema_version", "payload", "signature"}, "envelope")
    _schema(envelope["schema_version"])
    payload_bytes = _base64(envelope["payload"], "payload", MAX_PAYLOAD_BYTES)
    signature = _base64(envelope["signature"], "signature", 64)
    if len(signature) != 64:
        _fail("Invalid Ed25519 signature length")
    try:
        _public_key(public_key).verify(signature, payload_bytes)
    except InvalidSignature:
        raise ManifestError("Update manifest signature is invalid") from None
    return validate_manifest_payload(_json(payload_bytes, MAX_PAYLOAD_BYTES))


def select_release(manifest: dict, channel: str, current_build_number: int) -> dict | None:
    """Select a newer build only within the requested channel.

    Pass a payload returned by verify_manifest. Version labels never decide
    ordering and cannot bypass the monotonic build-number comparison.
    """
    if channel not in CHANNELS:
        _fail("Unsupported update channel")
    _integer(current_build_number, "current build number", minimum=0)
    payload = validate_manifest_payload(manifest)
    release = payload["channels"][channel]
    if release is None or release["build_number"] <= current_build_number:
        return None
    return dict(release)
