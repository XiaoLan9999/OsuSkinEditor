# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple, Any

SECTION_RE = re.compile(r'^\s*\[(?P<name>[^\]]+)\]\s*$')
KV_RE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*:\s*(.*?)\s*$')
COMMENT_RE = re.compile(r'^\s*//')

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

    @classmethod
    def read(cls, path: Path) -> "SkinIni":
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines(keepends=False)
        inst = cls(path=Path(path), lines=lines)
        inst._parse_sections()
        inst._parse_mania_blocks()
        return inst

    def _parse_sections(self) -> None:
        self.sections.clear()
        cur_name = None
        cur_start = 0
        for i, line in enumerate(self.lines):
            m = SECTION_RE.match(line)
            if m:
                if cur_name is not None:
                    self.sections.append((cur_name, cur_start, i))
                cur_name = m.group("name").strip()
                cur_start = i
        if cur_name is not None:
            self.sections.append((cur_name, cur_start, len(self.lines)))

    def _parse_mania_blocks(self) -> None:
        self.mania_by_keys.clear()
        for name, s, e in self.sections:
            if name.lower() != "mania":
                continue
            keys_val = None
            kv_pairs: List[Tuple[str, str]] = []
            for i in range(s + 1, e):
                line = self.lines[i]
                if not line.strip() or COMMENT_RE.match(line):
                    continue
                if SECTION_RE.match(line):
                    break
                kv = KV_RE.match(line)
                if kv:
                    k = kv.group(1).strip()
                    v = kv.group(2).strip()
                    kv_pairs.append((k, v))
                    if k.lower() == "keys":
                        try: keys_val = int(v)
                        except Exception: pass
            if keys_val is None:
                continue
            self.mania_by_keys[keys_val] = ManiaBlock(s, e, keys_val, kv_pairs)

    def available_mania_keys(self) -> List[int]:
        return sorted(self.mania_by_keys.keys())

    def mania_get(self, keys: int) -> Dict[str, str]:
        blk = self.mania_by_keys.get(keys)
        if not blk:
            return {}
        return {k: v for (k, v) in blk.kv_pairs}

    def mania_set_values(self, keys: int, updates: Dict[str, Any]) -> None:
        norm: Dict[str, str] = {}
        for k, v in updates.items():
            if v is None: continue
            if isinstance(v, (list, tuple)):
                norm[k] = ",".join(str(int(x)) for x in v)
            elif isinstance(v, bool):
                norm[k] = "1" if v else "0"
            else:
                norm[k] = str(v)

        blk = self.mania_by_keys.get(keys)
        if blk is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines.append("[Mania]")
            self.lines.append(f"Keys: {keys}")
            for k, v in norm.items():
                self.lines.append(f"{k}: {v}")
            self.lines.append("")
            self._parse_sections(); self._parse_mania_blocks()
            return

        # Preserve case/order of existing keys; update or append
        preserve_names = {k.lower(): k for (k, _) in blk.kv_pairs}
        current = {k: v for (k, v) in blk.kv_pairs}
        for k, v in norm.items():
            key_pres = preserve_names.get(k.lower(), k)
            current[key_pres] = v
            preserve_names[k.lower()] = key_pres

        new_block_lines: List[str] = []
        seen = set()
        ordered_keys = [k for (k, _) in blk.kv_pairs]
        for k in current.keys():
            if k not in ordered_keys:
                ordered_keys.append(k)

        for i in range(blk.start_idx + 1, blk.end_idx):
            line = self.lines[i]
            if COMMENT_RE.match(line) or not line.strip() or SECTION_RE.match(line):
                new_block_lines.append(line); continue
            m = KV_RE.match(line)
            if not m:
                new_block_lines.append(line); continue
            k = m.group(1).strip()
            if k.lower() == "keys":
                new_block_lines.append(f"Keys: {keys}"); seen.add("Keys")
            elif k in current:
                new_block_lines.append(f"{k}: {current[k]}"); seen.add(k)
            else:
                new_block_lines.append(line)

        for k in ordered_keys:
            if k in seen: continue
            new_block_lines.append(f"{k}: {current[k]}")

        self.lines[blk.start_idx + 1 : blk.end_idx] = new_block_lines
        self._parse_sections(); self._parse_mania_blocks()

    def save(self, create_backup: bool = True) -> None:
        p = Path(self.path)
        if create_backup:
            bak = p.with_suffix(p.suffix + ".bak")
            if not bak.exists():
                try: bak.write_text("\n".join(self.lines), encoding="utf-8")
                except Exception: pass
        p.write_text("\n".join(self.lines), encoding="utf-8")


def parse_list_csv(s: str) -> List[int]:
    if s is None: return []
    s = str(s).strip()
    if not s: return []
    out: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part: continue
        try: out.append(int(part))
        except Exception: pass
    return out
