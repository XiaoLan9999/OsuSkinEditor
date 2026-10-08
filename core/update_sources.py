"""Fixed HTTPS transports for this project's authenticated update resources.

Mirrors only change the request route. A release keeps the canonical GitHub
URL authenticated by its manifest, and the downloader still verifies the exact
signed size and digest before making a payload available to the installer.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from core.update_protocol import validate_release_url


ANNOUNCEMENTS_URL = "https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/announcements.json"
MANIFEST_URL = "https://raw.githubusercontent.com/XiaoLan9999/OsuSkinEditor/main/updates/manifest.json"
SOURCE_IDS = ("github", "ghfast", "ghproxy")
SOURCE_MODES = ("auto",) + SOURCE_IDS
CDN_HOSTS = frozenset({
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
})

_METADATA_URLS = (ANNOUNCEMENTS_URL, MANIFEST_URL)
_MIRROR_PREFIXES = {
    "ghfast": "https://ghfast.top/",
    "ghproxy": "https://gh-proxy.org/",
}


def _canonical_kind(canonical: str) -> str:
    if canonical in _METADATA_URLS:
        return "metadata"
    validate_release_url(canonical)
    return "download"


def source_urls(canonical: str, mode: str = "auto") -> tuple[tuple[str, str], ...]:
    """Return only built-in routes for a fixed metadata file or project EXE.

    ``ValueError`` is deliberate for caller-supplied arbitrary URLs or modes:
    this helper must never become a general proxy or URL rewriting facility.
    """
    _canonical_kind(canonical)
    if not isinstance(mode, str) or mode not in SOURCE_MODES:
        raise ValueError("Unknown update source mode")
    selected = SOURCE_IDS if mode == "auto" else (mode,)
    return tuple((source, canonical if source == "github" else _MIRROR_PREFIXES[source] + canonical)
                 for source in selected)


def source_host(sourceid: str, kind: str) -> str:
    """Return the request host used for a source and resource category."""
    if not isinstance(sourceid, str) or sourceid not in SOURCE_IDS:
        raise ValueError("Unknown update source")
    if kind not in ("announcements", "updates", "metadata", "download", "release"):
        raise ValueError("Unknown update resource kind")
    if sourceid != "github":
        return urlsplit(_MIRROR_PREFIXES[sourceid]).hostname
    return "github.com" if kind in ("download", "release") else "raw.githubusercontent.com"


def _https_parts(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 8192:
        return None
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127
           or character == "\\" for character in url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment or not parts.hostname):
            return None
    except (TypeError, ValueError):
        return None
    return parts


def _official_cdn(url) -> bool:
    parts = _https_parts(url)
    return bool(parts is not None and parts.hostname.casefold() in CDN_HOSTS)


def safe_transport_url(url, *, canonical, download=False, redirected=False) -> bool:
    """Allow exact routes for one resource, plus official release redirects.

    Proxy paths are never decoded or normalized: encoded nested URLs, changed
    repository paths and query strings cannot widen the canonical resource.
    Only an HTTPS redirect for an EXE may use an official GitHub CDN URL with
    its signed query string preserved by the caller's ``QUrl.FullyEncoded``.
    """
    try:
        kind = _canonical_kind(canonical)
    except (TypeError, ValueError):
        return False
    if (kind == "download") != bool(download) or _https_parts(url) is None:
        return False
    if any(url == candidate for _source, candidate in source_urls(canonical)):
        return True
    return bool(download and redirected and _official_cdn(url))


def safe_url(url, *, download=False, redirected=False) -> bool:
    """Compatibility policy when the caller has no canonical resource yet.

    Service requests should use ``safe_transport_url`` to bind a redirect to
    their resource. This wrapper still rejects arbitrary projects, EXE paths,
    encoded proxy paths, protocols and hosts for older validation callers.
    """
    if _https_parts(url) is None:
        return False
    if not download:
        return any(safe_transport_url(url, canonical=canonical) for canonical in _METADATA_URLS)
    if redirected and _official_cdn(url):
        return True
    canonical = url
    for prefix in _MIRROR_PREFIXES.values():
        if url.startswith(prefix):
            canonical = url[len(prefix):]
            break
    return safe_transport_url(url, canonical=canonical, download=True, redirected=redirected)
