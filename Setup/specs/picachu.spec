# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# --- Define paths relative to this spec file ---
# Use sys.argv[0] which reliably holds the spec file path
spec_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
project_root = os.path.abspath(os.path.join(spec_dir, '..', '..'))

block_cipher = None

a = Analysis(
    [os.path.join(project_root, 'Setup', 'Picachu.py')],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'build', 'programs'), 'programs'),
        (os.path.join(project_root, '_assets'), '_assets'),
        (os.path.join(project_root, 'LICENSE'), '.'),
        (os.path.join(project_root, 'PICA_README.md'), '.'),
        (os.path.join(project_root, 'Change_Logs.md'), '.')
    ],
    hiddenimports=['pyvisa-py'],
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
    name='Picachu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, '_assets', 'LOGO', 'PICA_LOGO.ico')
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Picachu'
)
