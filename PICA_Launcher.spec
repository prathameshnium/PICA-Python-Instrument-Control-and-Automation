# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['PICA_Launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('_assets', '_assets'),
        ('README', 'README'),
        ('LICENSE', 'LICENSE'),
        ('Delta_mode', 'Delta_mode'),
        ('Keithley_2400', 'Keithley_2400'),
        ('Keithley_2400_Keithley_2182', 'Keithley_2400_Keithley_2182'),
        ('Keithley_6517B', 'Keithley_6517B'),
        ('Lakeshore_350_340', 'Lakeshore_350_340'),
        ('LCR_Keysight_E4980A', 'LCR_Keysight_E4980A'),
        ('Lock_in_amplifier', 'Lock_in_amplifier')
    ],
    hiddenimports=['pyvisa_py'],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PICA_Launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)