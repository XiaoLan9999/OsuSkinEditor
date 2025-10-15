# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
block_cipher = None

datas = [
    # 若语言包在根：
    ('i18n_patch', 'i18n_patch'),
    # 若语言包在 core\i18n_patch：
    # ('core/i18n_patch', 'i18n_patch'),
    ('ico', 'ico'),
]

hiddenimports = ['PySide6.QtMultimedia']  # 音频预览
# Pillow 的编解码数据
datas += collect_data_files('PIL')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    a.scripts, pyz,
    name='OsuSkinEditor',
    icon='ico/xiaolan.ico',
    console=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False
)
