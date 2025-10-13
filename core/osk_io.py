# -*- coding: utf-8 -*-
"""
OSK 导入/导出工具（骨架）。
- .osk 实际上是 zip，保留相对路径即可。
- 这里先给出最小实现，后续可加：排除大文件/只导出被修改的文件/进度条等。
"""
from zipfile import ZipFile
from pathlib import Path

def import_osk(osk_path: Path, dest_dir: Path):
    with ZipFile(osk_path) as z:
        z.extractall(dest_dir)

def export_osk(src_dir: Path, out_path: Path):
    with ZipFile(out_path, "w") as z:
        for p in Path(src_dir).rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(src_dir))
