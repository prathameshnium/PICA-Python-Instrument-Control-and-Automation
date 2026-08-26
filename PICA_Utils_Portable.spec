# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable PICA Utils launcher.

Builds ONE windowed .exe that carries Python, tkinter, numpy, pandas,
matplotlib and the eight bundled utilities inside it — the target machine
needs nothing installed.

Build locally with:  pyinstaller --noconfirm PICA_Utils_Portable.spec
"""

block_cipher = None

a = Analysis(
    ['pica_utils_portable.py'],
    pathex=['.'],
    binaries=[],
    # The tool scripts resolve their logo as <module dir>/../assets/LOGO/...,
    # so the assets have to keep their position relative to the pica package
    # inside the bundle.
    datas=[('pica/assets/LOGO', 'pica/assets/LOGO')],
    hiddenimports=[
        # pica/PPMS has no __init__.py, so these three are reached through a
        # namespace package; name them explicitly rather than trust discovery.
        'pica.PPMS.PPMS_Plotter_GUI',
        'pica.PPMS.PPMS_SeqVisualizer_GUI',
        'pica.PPMS.PPMS_TimeEstimator_GUI',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing bundled here talks to hardware or does numerics beyond numpy /
    # pandas; dropping these keeps the single file to a sane size.
    excludes=[
        'pyvisa', 'pyvisa_py', 'pymeasure', 'serial', 'zeroconf', 'psutil',
        'scipy', 'IPython', 'jupyter', 'notebook', 'pytest',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PICA_Utils_Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX is not on the GitHub runners; skip it everywhere
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='pica/assets/LOGO/PICA_LOGO.ico',
)
