# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: один виконуваний файл з вбудованим Python і залежностями.
# Запуск збірки: pyinstaller --noconfirm indexer.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)

_hidden = (
    collect_submodules("google.genai")
    + collect_submodules("google.auth")
    + collect_submodules("google.oauth2")
    + collect_submodules("google_auth_oauthlib")
    + collect_submodules("googleapiclient")
    + [
        "google.auth.transport.requests",
        "httplib2",
        "uritemplate",
        "dotenv",
        "sqlite3",
    ]
)

_datas = [(str(root / "prompts"), "prompts")] + collect_data_files("certifi")

a = Analysis(
    [str(root / "indexer.py")],
    pathex=[str(root)],
    binaries=[],
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="indexer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
