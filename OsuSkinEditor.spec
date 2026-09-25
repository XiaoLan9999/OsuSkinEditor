# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

# Build only against this Python installation and Windows system libraries.
# Unrelated SDKs on PATH can inject an incompatible ICU/UCRT into the bundle.
# This changes the build process environment only, never the machine PATH.
if sys.platform == 'win32':
    system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    os.environ['PATH'] = os.pathsep.join(dict.fromkeys([
        str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
        str(system_root / 'System32'), str(system_root),
    ]))

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('locales', 'locales'), ('i18n_patch', 'i18n_patch'), ('ico', 'ico'), ('assets', 'assets')]
hiddenimports = ['PySide6.QtMultimedia']
datas += collect_data_files('PIL')
hiddenimports += collect_submodules('PIL')


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OsuSkinEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/branding/xiaolan-tech.png'],
)
