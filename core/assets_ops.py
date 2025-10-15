
# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict
import shutil, datetime, subprocess, os

# what osu! skins typically accept
IMAGE_EXTS = {".png"}
AUDIO_EXTS_ALLOWED = {".wav", ".ogg", ".mp3"}
AUDIO_EXTS_COMMON = {".wav", ".ogg", ".mp3", ".flac"}

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def backup_dir(skin_root: Path) -> Path:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = skin_root / "__conflicts_backup" / ts
    ensure_dir(p)
    return p

def list_images(skin_root: Path) -> List[Path]:
    out = []
    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        out.extend(skin_root.glob(f"**/*{ext}"))
    # only within root, skip backup folder
    out = [p for p in out if "__conflicts_backup" not in p.parts]
    return sorted(out)

def list_audio(skin_root: Path) -> List[Path]:
    out = []
    for ext in (".wav", ".ogg", ".mp3", ".flac"):
        out.extend(skin_root.glob(f"**/*{ext}"))
    out = [p for p in out if "__conflicts_backup" not in p.parts]
    return sorted(out)

def stem_conflicts(paths: List[Path]) -> Dict[str, List[Path]]:
    by_stem: Dict[str, List[Path]] = {}
    for p in paths:
        s = p.with_suffix("").name
        by_stem.setdefault(s, []).append(p)
    return {k:v for k,v in by_stem.items() if len(v) > 1}

def replace_image(src: Path, dst: Path, make_png: bool=True) -> Path:
    """Copy/convert image to dst (png). Return final path."""
    from PIL import Image
    dst = dst.with_suffix(".png") if make_png else dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".png" and make_png:
        shutil.copy2(src, dst)
    else:
        im = Image.open(src).convert("RGBA")
        im.save(dst, format="PNG")
    return dst

def replace_audio(src: Path, dst: Path, prefer_ext: str=".wav") -> Path:
    """Copy/convert audio to dst with prefer_ext using ffmpeg if needed."""
    prefer_ext = prefer_ext.lower()
    if prefer_ext not in AUDIO_EXTS_ALLOWED:
        prefer_ext = ".wav"
    dst = dst.with_suffix(prefer_ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() in AUDIO_EXTS_ALLOWED:
        # direct copy
        shutil.copy2(src, dst)
        return dst
    # fallback convert via ffmpeg
    cmd = ["ffmpeg", "-y", "-i", str(src), str(dst)]
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return dst
    except Exception as e:
        raise RuntimeError("需要 ffmpeg 才能把该音频转换为 " + prefer_ext)

def resolve_audio_conflicts(skin_root: Path, keep_choice: Dict[str, Path]) -> Path:
    """Move non-kept duplicates into backup folder. keep_choice maps stem->path to keep."""
    bdir = backup_dir(skin_root)
    for stem, keep in keep_choice.items():
        for p in (skin_root.rglob(stem + ".*")):
            if p.suffix.lower() in AUDIO_EXTS_COMMON and p != keep:
                rel = p.relative_to(skin_root)
                dst = bdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    p.rename(dst)
                except Exception:
                    shutil.copy2(p, dst)
                    p.unlink(missing_ok=True)
    return bdir
