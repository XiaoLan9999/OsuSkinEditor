
# -*- coding: utf-8 -*-
from pathlib import Path
import json
from PySide6.QtCore import QSettings

_LANG = "en-US"
_DICT = {}

def locales_dir() -> Path:
    import sys, os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return Path(base).parent / "locales"

def available_languages():
    return ["en-US", "zh-CN"]

def load_language(lang: str = None):
    global _LANG, _DICT
    settings = QSettings()
    if lang is None:
        lang = settings.value("ui/language", "en-US", str)
    lang = lang if lang in available_languages() else "en-US"
    _LANG = lang
    path = locales_dir() / f"{lang}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _DICT = json.load(f)
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
