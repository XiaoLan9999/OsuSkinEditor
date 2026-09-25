"""Resolve bundled UI assets in source and PyInstaller builds."""
from pathlib import Path
import sys


def resource_path(relative: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(root / relative)


def brand_image_path() -> str:
    for name in ("xiaolan-tech.png", "xiaolan-original.png"):
        path = resource_path("assets/branding/" + name)
        if Path(path).is_file():
            return path
    return resource_path("ico/xiaolan.ico")
