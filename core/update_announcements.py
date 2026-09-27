"""Bundled release notes and per-user read state, with no network requests."""
import json
import re
from datetime import date
from pathlib import Path
from core.resources import resource_path
from core.app_version import BUILD_ID

CURRENT_BUILD_ID = BUILD_ID
SEEN_KEY = "updates/seen_ids"
AUTO_SHOW_KEY = "updates/show_on_start"
CHANGELOG_URL = "https://github.com/XiaoLan9999/OsuSkinEditor/blob/main/CHANGELOG.md"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


def localized(value, language):
    if not isinstance(value, dict):
        return ""
    for key in (language, "en-US", "zh-CN"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def localized_items(value, language):
    if not isinstance(value, dict):
        return ()
    for key in (language, "en-US", "zh-CN"):
        items = value.get(key)
        if isinstance(items, list) and all(isinstance(item, str) for item in items):
            return tuple(item.strip() for item in items if item.strip())
    return ()


def load_announcements(path=None):
    """A missing or malformed catalog must never prevent application startup."""
    source = Path(path) if path is not None else Path(resource_path("assets/updates.json"))
    try:
        if source.stat().st_size > 1024*1024:
            return ()
        raw = source.read_bytes()
    except (OSError, ValueError, UnicodeError):
        return ()
    return parse_announcements(raw)


def parse_announcements(raw):
    """Validate a bounded local or remote catalog without rendering HTML."""
    try:
        if not isinstance(raw, bytes) or len(raw) > 1024*1024:
            return ()
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return ()
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
        return ()
    entries, ids = [], set()
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in ids:
            continue
        if not isinstance(entry.get("version"), str) or entry.get("channel") not in ("preview", "stable", "release"):
            continue
        try:
            date.fromisoformat(entry.get("date", ""))
        except (TypeError, ValueError):
            continue
        if not localized(entry.get("title"), "en-US") or not localized(entry.get("summary"), "en-US"):
            continue
        sections = entry.get("sections")
        if not isinstance(sections, list) or not sections:
            continue
        if any(not isinstance(section, dict) or not localized(section.get("title"), "en-US")
               or not localized_items(section.get("items"), "en-US") for section in sections):
            continue
        entries.append(entry)
        ids.add(identifier)
    return tuple(entries)


def seen_announcements(settings):
    values = settings.value(SEEN_KEY, [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return set()
    return {value for value in values if isinstance(value, str) and _ID.fullmatch(value)}


def should_show_announcement(settings, entries, current_id=CURRENT_BUILD_ID):
    return (settings.value(AUTO_SHOW_KEY, True, bool)
            and any(entry.get("id") == current_id for entry in entries)
            and current_id not in seen_announcements(settings))


def mark_announcements_seen(settings, identifiers):
    seen = seen_announcements(settings)
    seen.update(value for value in identifiers if isinstance(value, str) and _ID.fullmatch(value))
    settings.setValue(SEEN_KEY, sorted(seen))
    settings.sync()
