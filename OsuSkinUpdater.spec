# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

if sys.platform == 'win32':
    system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    os.environ['PATH'] = os.pathsep.join(dict.fromkeys([
        str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
        str(system_root/'System32'), str(system_root),
    ]))

a = Analysis(['updater_entry.py'], pathex=[], binaries=[], datas=[],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PySide6', 'PIL', 'cryptography'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='OsuSkinUpdater',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False,
          icon=['assets/branding/xiaolan-tech.png'])
