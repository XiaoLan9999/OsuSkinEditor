"""Create a signed update manifest locally; this script never uploads files.

Keep the Ed25519 private key outside the repository. Raw 32-byte keys and
unencrypted PKCS8 PEM keys are accepted. Only the public signed envelope is
written, and existing channel entries are merged only after verification.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import UnsupportedAlgorithm


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.update_protocol import (CHANNELS, MAX_MANIFEST_BYTES, MAX_RELEASE_SIZE,
                                  ManifestError, validate_manifest_payload, verify_manifest)


def _private_key(path):
    key_path = Path(path).resolve(strict=True)
    if key_path.is_relative_to(REPOSITORY_ROOT):
        raise ManifestError("Store the private signing key outside the repository")
    if not key_path.is_file() or key_path.stat().st_size > 64 * 1024:
        raise ManifestError("Invalid private signing key file")
    data = key_path.read_bytes()
    try:
        if len(data) == 32:
            return Ed25519PrivateKey.from_private_bytes(data)
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError
        return key
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ManifestError("Expected a raw or unencrypted PEM Ed25519 private key") from None


def _read_existing(path, public_key):
    existing = Path(path).resolve(strict=True)
    if not existing.is_file() or existing.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestError("Existing manifest is too large or not a file")
    return verify_manifest(existing.read_bytes(), public_key)


def build_manifest(*, exe, version, build_id, build_number, channel, url,
                   private_key, existing=None, notes_id=None, published_at=None):
    """Return a verified signed envelope without writing or uploading it."""
    if channel not in CHANNELS:
        raise ManifestError("Unsupported update channel")
    key = _private_key(private_key)
    public_key = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    previous = _read_existing(existing, public_key) if existing is not None else None
    channels = dict(previous["channels"]) if previous else {"stable": None, "preview": None}
    old_release = channels[channel]
    if type(build_number) is not int or build_number <= 0:
        raise ManifestError("Build number must be a positive integer")
    highest = max((item["build_number"] for item in channels.values() if item is not None), default=0)
    if build_number <= highest:
        raise ManifestError("A published build must increase the build number across all channels")
    binary = Path(exe).resolve(strict=True)
    if binary == Path(private_key).resolve() or binary.suffix.casefold() != ".exe" or not binary.is_file():
        raise ManifestError("Expected an EXE distinct from the signing key")
    initial_stat = binary.stat()
    size = initial_stat.st_size
    if not 0 < size <= MAX_RELEASE_SIZE:
        raise ManifestError("EXE size is outside the supported download limit")
    digest = hashlib.sha256()
    counted = 0
    with binary.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            counted += len(chunk)
            if counted > MAX_RELEASE_SIZE:
                raise ManifestError("EXE exceeds the supported download limit")
            digest.update(chunk)
    final_stat = binary.stat()
    if (counted != size or (final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ino) !=
            (initial_stat.st_size, initial_stat.st_mtime_ns, initial_stat.st_ino)):
        raise ManifestError("EXE changed while its checksum was being computed")
    channels[channel] = {
        "version": version, "build_id": build_id, "build_number": build_number,
        "channel": channel, "platform": "windows-x64", "url": url,
        "sha256": digest.hexdigest(), "size": size, "notes_id": notes_id or build_id,
    }
    payload = validate_manifest_payload({
        "schema_version": 1,
        "published_at": published_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "channels": channels,
    })
    # Serialise exactly once. Signature verification uses these exact bytes,
    # not a re-serialisation or an implicit JSON canonicalisation convention.
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    envelope = json.dumps({
        "schema_version": 1,
        "payload": base64.b64encode(payload_bytes).decode("ascii"),
        "signature": base64.b64encode(key.sign(payload_bytes)).decode("ascii"),
    }, indent=2).encode("utf-8") + b"\n"
    verify_manifest(envelope, public_key)
    return envelope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("exe", "version", "build-id", "url", "private-key", "output"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--build-number", type=int, required=True)
    parser.add_argument("--channel", choices=CHANNELS, required=True)
    parser.add_argument("--existing", help="Previously signed manifest whose other channel must be retained")
    parser.add_argument("--notes-id", help="Announcement ID, defaults to --build-id")
    args = parser.parse_args(argv)
    temporary = None
    try:
        output = Path(args.output).resolve()
        if output in (Path(args.private_key).resolve(), Path(args.exe).resolve()):
            raise ManifestError("Manifest output must not replace the signing key or EXE")
        if output.exists() and args.existing is None:
            raise ManifestError("Use --existing to verify and preserve an existing output manifest")
        if output.exists() and Path(args.existing).resolve() != output:
            raise ManifestError("Existing output must be the manifest passed to --existing")
        envelope = build_manifest(exe=args.exe, version=args.version, build_id=args.build_id,
                                  build_number=args.build_number, channel=args.channel,
                                  url=args.url, private_key=args.private_key, existing=args.existing,
                                  notes_id=args.notes_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".update-manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(envelope)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        temporary = None
        print(f"Signed manifest created: {output.name}")
        return 0
    except (OSError, ManifestError) as exc:
        # No key material, binary data or PEM parser diagnostics are printed.
        message = str(exc) if isinstance(exc, ManifestError) else "Cannot read or write the requested local files"
        print(f"Manifest creation failed: {message}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
