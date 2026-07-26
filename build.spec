# build.spec
# -*- mode: python ; coding: utf-8 -*-

import os

block_cipher = None

# Collect bundled binaries
bundled_binaries = []
if os.path.exists('bundled/ffmpeg.exe'):
    bundled_binaries.append(('bundled/ffmpeg.exe', '.'))
if os.path.exists('bundled/deno.exe'):
    bundled_binaries.append(('bundled/deno.exe', '.'))

a = Analysis(
    ['down.py'],
    pathex=[],
    binaries=bundled_binaries,  # <--- Bundled FFmpeg + Deno
    datas=[],
    hiddenimports=[
        'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'yt_dlp', 'yt_dlp.extractor', 'yt_dlp.postprocessor', 'yt_dlp.downloader',
        'yt_dlp_ejs',
        'requests', 'bs4', 'instaloader',
        'urllib3', 'certifi', 'charset_normalizer', 'idna',
        'socks', 'websockets', 'mutagen', 'pycryptodomex', 'brotli',
        'secretstorage', 'keyring', 'jeepney', 'dbus_fast'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OryvexDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OryvexDownloader',
)