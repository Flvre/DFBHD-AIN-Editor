# Build with: python -m PyInstaller --clean --noconfirm dfbhdain_v1.spec
from pathlib import Path

root = Path(SPECPATH)
icon = str(root / 'assets' / 'dfbhdain.ico')
a = Analysis(
    [str(root / 'ain_editor_v1_0.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(icon, 'assets')],
    hiddenimports=['PIL.ImageTk', 'PIL.IcoImagePlugin'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='dfbhdain_v1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
    version=str(root / 'packaging' / 'version_info.txt'),
)
