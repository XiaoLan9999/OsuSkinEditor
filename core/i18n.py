
# -*- coding: utf-8 -*-
from pathlib import Path
import json
from PySide6.QtCore import QSettings, QLocale

_LANG = "en-US"
_DICT = {}

def locales_dir() -> Path:
    """Return locales/ path for both dev and PyInstaller(onefile)."""
    import sys, os
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "locales"
    return Path(__file__).resolve().parent.parent / "locales"

def available_languages():
    return ["en-US", "zh-CN"]

def load_language(lang: str = None):
    global _LANG, _DICT
    settings = QSettings()
    if lang is None:
        default = "zh-CN" if QLocale.system().name().startswith("zh") else "en-US"
        lang = settings.value("ui/language", default, str)
    lang = lang if lang in available_languages() else "en-US"
    _LANG = lang
    path = locales_dir() / f"{lang}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _DICT = json.load(f)
        extra = locales_dir() / f"{lang}.add.json"
        if extra.exists():
            additions = json.loads(extra.read_text(encoding="utf-8"))
            for key, value in additions.items():
                if isinstance(value, dict) and isinstance(_DICT.get(key), dict):
                    _DICT[key].update(value)
                else:
                    _DICT[key] = value
    except Exception:
        _DICT = {}
    settings.setValue("ui/language", _LANG)

def lang() -> str:
    return _LANG

def t(key: str, default: str = None) -> str:
    if not _DICT:
        load_language(_LANG)
    cur = _DICT
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default if default is not None else key
        cur = cur[part]
    if isinstance(cur, str):
        return cur
    return default if default is not None else key
