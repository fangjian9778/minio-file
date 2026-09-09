# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件
目标: Windows 7+ 32位 桌面版程序 (内置浏览器窗口)
注意: 必须在 32位 Python 3.8 环境下运行 pyinstaller
"""

import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# 收集 pythonnet (.NET 互操作 DLL) 与 webview (内置浏览器)
_py_datas, _py_binaries, _py_hidden = collect_all('pythonnet')
_wv_datas, _wv_binaries, _wv_hidden = collect_all('webview')

# 收集数据文件: HTML 模板 + webview/pythonnet 附带文件
data_files = [('templates', 'templates')] + _py_datas + _wv_datas
if os.path.exists('.minio_config.json'):
    data_files.append(('.minio_config.json', '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_py_binaries + _wv_binaries,
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
        # 内置浏览器相关 (pywebview 运行时动态导入)
        'webview',
        'webview.platforms',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr',
        'pythonnet',
    ] + _py_hidden + _wv_hidden,
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
    console=False,  # 桌面版: 隐藏控制台窗口
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
