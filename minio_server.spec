# -*- mode: python ; coding: utf-8 -*-
import sys
import os

def _collect_pywebview_datas():
    """Collect pywebview bundled DLLs and data files."""
    import importlib.util
    try:
        spec = importlib.util.find_spec("webview")
        if spec and spec.origin:
            wv_dir = os.path.dirname(spec.origin)
            parent = os.path.dirname(wv_dir)
            datas = []
            for root, dirs, files in os.walk(parent):
                for f in files:
                    if f.endswith((".dll", ".pdb", ".json")):
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, parent)
                        datas.append((full, os.path.dirname(rel)))
            return datas
    except Exception:
        pass
    return []

def _collect_pythonnet_datas():
    """Collect pythonnet clr.dll and friends from the package folder."""
    import importlib.util
    try:
        spec = importlib.util.find_spec("clr")
        if spec and spec.origin:
            clr_dir = os.path.dirname(spec.origin)
            datas = []
            for f in os.listdir(clr_dir):
                if f.endswith((".dll", ".pdb")):
                    datas.append((os.path.join(clr_dir, f), "."))
            return datas
    except Exception:
        pass
    return []

_wv_datas = _collect_pywebview_datas()
_py_datas = _collect_pythonnet_datas()

# Collect data files: HTML templates + app icon + webview/pythonnet bundled files
data_files = [('templates', 'templates'), ('assets/app.ico', 'assets')] + _py_datas + _wv_datas

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=data_files,
    hiddenimports=[
        'minio',
        'flask',
        'flask_cors',
        'webview',
        'clr',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

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
    console=False,
    icon='assets/app.ico',
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