# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件
目标: Windows 7+ 32位可执行文件
注意: 必须在 32位 Python 3.8 环境下运行 pyinstaller
"""

import os

block_cipher = None

# 收集数据文件
data_files = [('templates', 'templates')]
if os.path.exists('.minio_config.json'):
    data_files.append(('.minio_config.json', '.'))

a = Analysis(
    ['minio_server.py'],
    pathex=[],
    binaries=[],
    datas=data_files,
    hiddenimports=[
        'flask',
        'flask_cors',
        'minio',
        'minio.error',
        'werkzeug',
        'werkzeug.utils',
        'werkzeug.serving',
        'jinja2',
        'markupsafe',
        'certifi',
        'cryptography',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'pytest',
        'setuptools',
        'pip',
    ],
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
    name='minio_file_service',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='minio_file_service',
)