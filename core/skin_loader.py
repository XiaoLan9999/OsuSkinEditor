# -*- coding: utf-8 -*-
"""Tolerant skin loading with repeated Mania sections and deterministic assets."""
from dataclasses import dataclass, field
from configparser import ConfigParser
from pathlib import Path
from typing import Dict
from io import StringIO
import re

from core.skin_ini import SECTION_RE, decode_ini, split_inline_comment


KNOWN_ASSETS = [
    'cursor', 'cursortrail', 'hitcircle', 'hitcircleoverlay',
    *[f'score-{index}' for index in range(10)],
    'mania-note1', 'mania-note1L', 'mania-note1T',
    'mania-key1', 'mania-key1D', 'mania-key1L',
]


@dataclass
class SkinAsset:
    name: str
    path: Path
    scale: int


@dataclass
class Skin:
    root: Path
    ini: ConfigParser
    assets: Dict[str, SkinAsset]
    mode_keys: int = 4
    mania_variants: Dict[int, Dict[str, str]] = field(default_factory=dict)


def _read_ini_robust(path: Path) -> ConfigParser:
    text, _ = decode_ini(Path(path).read_bytes())
    section_counts = {}
    used_sections = set()
    output = []
    has_section = False
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith(('#', ';', '//', '►', '▶', '•', '★', '※')):
            continue
        section = SECTION_RE.match(value)
        if section:
            name = section.group('name').strip()
            count = section_counts.get(name.casefold(), 0) + 1
            section_counts[name.casefold()] = count
            unique = name if count == 1 else f'{name}#{count}'
            while unique.casefold() in used_sections:
                count += 1
                unique = f'{name}#{count}'
            used_sections.add(unique.casefold())
            output.append(f'[{unique}]')
            has_section = True
            continue
        # Decorative preambles are common; settings before a section are invalid.
        if not has_section or not re.match(r'^[A-Za-z0-9_][A-Za-z0-9_ ]*\s*[:=]', value):
            continue
        option = re.match(r'^([A-Za-z0-9_][A-Za-z0-9_ ]*\s*[:=])\s*(.*)$', value)
        content, _ = split_inline_comment(option.group(2))
        output.append(option.group(1) + content)

    config = ConfigParser(strict=False, interpolation=None, empty_lines_in_values=False)
    config.optionxform = str
    config.read_file(StringIO('\n'.join(output)))
    return config


class SkinLoader:
    def load(self, directory: str) -> Skin:
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f'Skin folder not found: {root}')
        files = sorted((path for path in root.iterdir() if path.is_file()), key=lambda path: (path.name.casefold(), path.name))
        ini_path = next((path for path in files if path.name.casefold() == 'skin.ini'), None)
        if ini_path is None:
            raise FileNotFoundError('skin.ini not found in selected folder')
        ini = _read_ini_robust(ini_path)

        variants = {}
        for section in ini.sections():
            if not re.fullmatch(r'mania(?:#\d+)?', section, re.IGNORECASE):
                continue
            values = dict(ini.items(section))
            key_values = [value for key, value in values.items() if key.casefold() in ('keys', 'keycount')]
            keys = key_values[-1] if key_values else None
            try:
                keys = int(keys)
            except (TypeError, ValueError):
                continue
            if keys > 0:
                variants[keys] = values
        default_keys = 4 if 4 in variants else 7 if 7 in variants else next(iter(variants), 4)

        # Index all PNGs together so custom assets get the same @2x preference.
        candidates = {}
        canonical_names = {name.casefold(): name for name in KNOWN_ASSETS}
        for path in files:
            if path.suffix.casefold() != '.png':
                continue
            high_resolution = path.stem.casefold().endswith('@2x')
            name = path.stem[:-3] if high_resolution else path.stem
            scale = 2 if high_resolution else 1
            previous = candidates.get(name.casefold())
            if previous is None or scale > previous.scale:
                name = canonical_names.get(name.casefold(), name)
                candidates[name.casefold()] = SkinAsset(name, path, scale)
        assets = {asset.name: asset for asset in candidates.values()}
        return Skin(root=root, ini=ini, assets=assets, mode_keys=default_keys, mania_variants=variants)
