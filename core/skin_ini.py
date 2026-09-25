# -*- coding: utf-8 -*-
"""Read and edit Mania sections without rewriting unrelated skin settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import tempfile
from typing import Any, Dict, List, Tuple


SECTION_RE = re.compile(r'^\s*\[(?P<name>[^\]]+)\]\s*(?://.*)?$')
KV_RE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*[:=]\s*(.*?)\s*$')
COMMENT_RE = re.compile(r'^\s*(?://|#|;)')


def decode_ini(raw: bytes) -> Tuple[str, str]:
    """Detect common skin encodings without ever discarding undecodable bytes."""
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig'), 'utf-8-sig'
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16-le' if raw.startswith(b'\xff\xfe') else 'utf-16-be'
        return raw[2:].decode(encoding), encoding
    # Do not blindly try UTF-16: it also accepts many even-length GBK files.
    if raw and len(raw) % 2 == 0:
        if raw[1::2].count(0) > len(raw) // 8:
            return raw.decode('utf-16-le'), 'utf-16-le'
        if raw[0::2].count(0) > len(raw) // 8:
            return raw.decode('utf-16-be'), 'utf-16-be'
    for encoding in ('utf-8', 'gb18030', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeError('Unable to decode skin.ini')


def split_inline_comment(value: str) -> Tuple[str, str]:
    """Return value and its comment suffix; retain URLs and path separators."""
    if value.startswith('//'):
        return '', value
    comment = re.search(r'\s+//', value)
    if comment is None:
        # Numeric skin options also commonly use compact comments: Keys: 4//4K.
        comment = re.search(r'(?<=[0-9])//', value)
        if comment and not re.fullmatch(r'[\d+.,\s-]+', value[:comment.start()]):
            comment = None
    if comment:
        return value[:comment.start()].strip(), value[comment.start():]
    return value.strip(), value[len(value.rstrip()):]


@dataclass
class ManiaBlock:
    start_idx: int
    end_idx: int
    keys: int
    kv_pairs: List[Tuple[str, str]]


@dataclass
class SkinIni:
    path: Path
    lines: List[str] = field(default_factory=list)
    sections: List[Tuple[str, int, int]] = field(default_factory=list)
    mania_by_keys: Dict[int, ManiaBlock] = field(default_factory=dict)
    _encoding: str = field(default='utf-8', repr=False)
    _bom: bytes = field(default=b'', repr=False)
    _newline: str = field(default='\n', repr=False)
    _trailing_newline: bool = field(default=True, repr=False)
    _original_bytes: bytes | None = field(default=None, repr=False)
    _original_lines: List[str] = field(default_factory=list, repr=False)

    @classmethod
    def read(cls, path: Path) -> "SkinIni":
        path = Path(path)
        raw = path.read_bytes()
        text, encoding = decode_ini(raw)
        newline = re.search(r'\r\n|\n|\r', text)
        inst = cls(
            path=path, lines=text.splitlines(), _encoding=encoding,
            _bom=raw[:2] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else b'',
            _newline=newline.group() if newline else '\n',
            _trailing_newline=text.endswith(('\n', '\r')),
            _original_bytes=raw, _original_lines=text.splitlines(),
        )
        inst._parse_sections()
        inst._parse_mania_blocks()
        return inst

    def _parse_sections(self) -> None:
        self.sections.clear()
        cur_name = None
        cur_start = 0
        for i, line in enumerate(self.lines):
            match = SECTION_RE.match(line)
            if match:
                if cur_name is not None:
                    self.sections.append((cur_name, cur_start, i))
                cur_name = match.group('name').strip()
                cur_start = i
        if cur_name is not None:
            self.sections.append((cur_name, cur_start, len(self.lines)))

    def _parse_mania_blocks(self) -> None:
        self.mania_by_keys.clear()
        for name, start, end in self.sections:
            if name.casefold() != 'mania':
                continue
            keys_val = None
            pairs: List[Tuple[str, str]] = []
            for line in self.lines[start + 1:end]:
                match = KV_RE.match(line)
                if not match or COMMENT_RE.match(line):
                    continue
                key = match.group(1)
                value, _ = split_inline_comment(match.group(2))
                pairs.append((key, value))
                if key.casefold() in ('keys', 'keycount'):
                    try:
                        keys_val = int(value)
                    except ValueError:
                        pass
            if keys_val is not None and keys_val > 0:
                self.mania_by_keys[keys_val] = ManiaBlock(start, end, keys_val, pairs)

    def available_mania_keys(self) -> List[int]:
        return sorted(self.mania_by_keys)

    def mania_get(self, keys: int) -> Dict[str, str]:
        block = self.mania_by_keys.get(keys)
        return dict(block.kv_pairs) if block else {}

    def mania_set_values(self, keys: int, updates: Dict[str, Any]) -> None:
        if int(keys) <= 0:
            raise ValueError('Keys must be positive')
        keys = int(keys)
        normalized = {}
        for key, value in updates.items():
            if value is None or key.casefold() in ('keys', 'keycount'):
                continue
            if not re.fullmatch(r'[A-Za-z0-9_]+', key):
                raise ValueError(f'Invalid skin.ini key: {key}')
            if isinstance(value, (list, tuple)):
                value = ','.join(str(int(item)) for item in value)
            elif isinstance(value, bool):
                value = '1' if value else '0'
            else:
                value = str(value)
            if '\n' in value or '\r' in value:
                raise ValueError('A skin.ini value must fit on one line')
            normalized[key.casefold()] = (key, value)
        block = self.mania_by_keys.get(keys)
        if not normalized and block is not None:
            return
        if block is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append('')
            self.lines.extend(('[Mania]', f'Keys: {keys}'))
            self.lines.extend(f'{key}: {value}' for key, value in normalized.values())
        else:
            seen = set()
            for index in range(block.start_idx + 1, block.end_idx):
                line = self.lines[index]
                match = KV_RE.match(line)
                if not match or match.group(1).casefold() not in normalized:
                    continue
                key = match.group(1).casefold()
                seen.add(key)
                # Keep indentation, original key case, delimiter and inline notes.
                prefix = re.match(r'^(\s*[A-Za-z0-9_]+\s*[:=][ \t]*)(.*)$', line)
                old_value, suffix = split_inline_comment(prefix.group(2))
                new_value = normalized[key][1]
                if old_value != new_value:
                    if suffix.startswith('//'):
                        suffix = ' ' + suffix
                    self.lines[index] = prefix.group(1) + new_value + suffix
            additions = [f'{key}: {value}' for lower, (key, value) in normalized.items() if lower not in seen]
            # Keep blank lines separating the next section after the new options.
            insert_at = block.end_idx
            while insert_at > block.start_idx + 1 and not self.lines[insert_at - 1].strip():
                insert_at -= 1
            self.lines[insert_at:insert_at] = additions
        self._parse_sections()
        self._parse_mania_blocks()

    def save(self, create_backup: bool = True) -> None:
        path = Path(self.path)
        current = path.read_bytes() if path.exists() else None
        if self._original_bytes is not None and current != self._original_bytes:
            raise OSError('skin.ini changed on disk; reload it before saving')
        if self._original_bytes is not None and self.lines == self._original_lines:
            payload = self._original_bytes
        else:
            text = self._newline.join(self.lines)
            if self._trailing_newline:
                text += self._newline
            payload = self._bom + text.encode(self._encoding)

        if create_backup and current is not None:
            backup = path.with_suffix(path.suffix + '.bak')
            if backup.exists() and not backup.is_file():
                raise OSError(f'Backup path is not a file: {backup}')
            try:
                with backup.open('xb') as stream:
                    try:
                        stream.write(current)
                    except OSError:
                        stream.close()
                        backup.unlink(missing_ok=True)
                        raise
            except FileExistsError:
                pass

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.skin-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._original_bytes = payload
        self._original_lines = list(self.lines)


def parse_list_csv(s: str) -> List[int]:
    if s is None:
        return []
    value, _ = split_inline_comment(str(s))
    result = []
    for part in value.split(','):
        try:
            result.append(int(part.strip()))
        except ValueError:
            pass
    return result
