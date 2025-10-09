# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# --- Define paths relative to this spec file ---
spec_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
project_root = os.path.abspath(os.path.join(spec_dir, '..', '..'))
icon_file = os.path.join(project_root, '_assets', 'LOGO', 'PICA_LOGO.ico')

a = Analysis(
    [os.path.join(project_root, 'Keithley_2400/RT_K2400_L350_T_Control_Frontend_v3.py')],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pyvisa-py'],
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
    [],
    exclude_binaries=True,
    name='RT_K2400_L350_T_Control_Frontend_v3',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RT_K2400_L350_T_Control_Frontend_v3'
)
