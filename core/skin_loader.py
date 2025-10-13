
# -*- coding: utf-8 -*-
"""
SkinLoader (robust INI):
- Tolerates non-INI lines (//, ►/▶ bullets, plain headings) by filtering them out.
- Allows duplicate [Mania] sections by renaming to [Mania#2], [Mania#3]...
- Collects all [Mania*] blocks into skin.mania_variants: keys(int) -> kv dict.
- Picks default preview keys (4 or 7 if available, else first).
"""
from dataclasses import dataclass
from configparser import ConfigParser
from pathlib import Path
from typing import Dict, Tuple, List
from io import StringIO
import re

KNOWN_ASSETS = [
    "cursor", "cursortrail",
    "hitcircle", "hitcircleoverlay",
    "score-0","score-1","score-2","score-3","score-4",
    "score-5","score-6","score-7","score-8","score-9",
    "mania-note1", "mania-note1L", "mania-note1T",
    "mania-key1", "mania-key1D", "mania-key1L",
]

@dataclass
class SkinAsset:
    name: str
    path: Path
    scale: int  # 1 or 2

@dataclass
class Skin:
    root: Path
    ini: ConfigParser
    assets: Dict[str, SkinAsset]
    mode_keys: int = 4
    mania_variants: Dict[int, Dict[str, str]] = None

def _read_ini_robust(path: Path) -> ConfigParser:
    # decode bytes with utf-8-sig first, fallback several encodings
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gbk", "cp936", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="ignore")

    sec_re = re.compile(r'^\s*\[(.+?)\]\s*$')
    comment_prefixes = ('#',';','//','►','▶','•','★','※')
    dup_count: Dict[str,int] = {}
    out_lines: List[str] = []

    for raw_line in text.splitlines():
        s = raw_line.strip()
        if not s:
            out_lines.append('')
            continue
        m = sec_re.match(s)
        if m:
            name = m.group(1).strip()
            dup_count[name] = dup_count.get(name, 0) + 1
            if dup_count[name] > 1:
                out_lines.append(f'[{name}#{dup_count[name]}]')
            else:
                out_lines.append(f'[{name}]')
            continue
        # skip comment-like or heading lines
        if any(s.startswith(p) for p in comment_prefixes):
            continue
        # strip inline // comments
        if '//' in s:
            s = s.split('//', 1)[0].rstrip()
            if not s:
                continue
        # accept key-value-ish lines
        if '=' in s or ':' in s:
            out_lines.append(s)
        # else drop silently

    cfg = ConfigParser(strict=False)  # allow duplicate keys within section
    cfg.optionxform = str
    cfg.read_file(StringIO('\n'.join(out_lines)))
    return cfg

class SkinLoader:
    def load(self, directory: str) -> Skin:
        root = Path(directory)
        ini_path = root / "skin.ini"
        if not ini_path.exists():
            raise FileNotFoundError("skin.ini not found in selected folder")

        ini = _read_ini_robust(ini_path)

        # collect mania variants and decide default keys
        variants: Dict[int, Dict[str, str]] = {}
        default_keys = None
        for sec in ini.sections():
            if sec.startswith("Mania"):
                kv = dict(ini.items(sec))
                k = None
                for keyname in ("Keys", "keys", "KeyCount"):
                    if keyname in kv:
                        try:
                            k = int(str(kv[keyname]).strip())
                        except Exception:
                            k = None
                        break
                if k:
                    variants[k] = kv
                    if default_keys is None or k in (4,7):
                        default_keys = k
        if default_keys is None:
            default_keys = 4

        # assets discovery with @2x priority
        assets: Dict[str, SkinAsset] = {}
        def pick(name: str):
            p2 = root / f"{name}@2x.png"
            p1 = root / f"{name}.png"
            if p2.exists():
                assets[name] = SkinAsset(name, p2, 2)
            elif p1.exists():
                assets[name] = SkinAsset(name, p1, 1)

        for n in KNOWN_ASSETS:
            pick(n)
        for p in root.glob("*.png"):
            nm = p.stem.replace("@2x", "")
            if nm not in assets:
                assets[nm] = SkinAsset(nm, p, 2 if "@2x" in p.stem else 1)

        return Skin(root=root, ini=ini, assets=assets, mode_keys=default_keys, mania_variants=variants)
