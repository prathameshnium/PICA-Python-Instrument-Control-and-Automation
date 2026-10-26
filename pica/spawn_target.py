'''
Process target for modules launched from the v2 launcher.

Kept in its own tiny, Tk-free module on purpose. With the 'spawn' start
method the child process unpickles the target by importing the module that
defines it; when the target lived in pica.main, every launched module first
imported tkinter, PIL and the whole legacy launcher (~0.3 s) before its own
script even started. Importing this file costs only os/sys/runpy.

Copied from run_script_process in pica/main.py (the v1 launcher keeps its
own copy); keep the two in step.
'''
import os
import sys
import runpy

# The window icon every PICA process uses (launchers and launched modules).
PICA_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "LOGO", "PICA_LOGO_ICON.ico")


def use_pica_icon():
    """Give every Tk window this process creates the PICA icon (Windows).

    Called at the start of a process, before any window exists, so no module
    has to know about it. tkinter is imported here, not at the top: importing
    this file must stay Tk-free (see the module docstring). Cosmetic only --
    any failure is ignored and the window keeps Tk's feather.
    """
    if sys.platform != "win32" or not os.path.exists(PICA_ICON):
        return
    try:
        import ctypes
        # Own taskbar identity, so PICA windows group as PICA and show their
        # own icon instead of python.exe's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "UGC-DAE-CSR.PICA")
    except Exception:
        pass
    try:
        import tkinter
        if getattr(tkinter.Tk, "_pica_icon", False):
            return
        original_init = tkinter.Tk.__init__

        def init_with_icon(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            try:
                # default= applies to this root and every Toplevel under it.
                self.iconbitmap(default=PICA_ICON)
                # Tk only hands Windows the 16 / 32 px images, which the
                # taskbar stretches on a scaled display (blurry at 125 %).
                # Each window, as it maps, gets the exact sizes instead.
                for cls in ('Tk', 'Toplevel'):
                    self.bind_class(cls, '<Map>', _sharp_icon, add='+')
            except Exception:
                pass
        tkinter.Tk.__init__ = init_with_icon
        tkinter.Tk._pica_icon = True
    except Exception:
        pass


def _sharp_icon(event):
    """Set a window's small and big icons at the display's real size.

    Tk is not DPI-aware, so it picks 16 / 32 px and the taskbar rescales
    them. The scale factor (125 % -> 20 / 30 px) comes from the display, and
    the .ico carries those sizes (16-48 px in steps of the usual scales).
    Done once per window.
    """
    widget = event.widget
    if getattr(widget, '_pica_sharp_icon', False):
        return
    widget._pica_sharp_icon = True
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p)
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
        hwnd = int(widget.wm_frame(), 16)
        WM_SETICON, IMAGE_ICON, LR_LOADFROMFILE = 0x0080, 1, 0x0010
        # Small = title bar (16 px at 100 %); big = the taskbar button, which
        # Windows 11 draws at 24 px at 100 % (30 px at 125 %).
        for which, base in ((0, 16), (1, 24)):      # ICON_SMALL, ICON_BIG
            px = int(round(base * scale))
            icon = user32.LoadImageW(None, PICA_ICON, IMAGE_ICON, px, px,
                                     LR_LOADFROMFILE)
            if icon:
                user32.SendMessageW(hwnd, WM_SETICON, which, icon)
    except Exception:
        pass


def run_script_process(script_path, argv=None):
    """
    Execute a script with runpy, from its own directory, as __main__.
    os.chdir is fine here: this runs in a separate spawned process and does
    not touch the launcher's working directory.

    'argv' is an optional list of command-line arguments (e.g. data files to
    pre-load); it is placed in sys.argv exactly as a shell invocation would.
    """
    use_pica_icon()
    try:
        os.chdir(os.path.dirname(script_path))
        sys.argv = [script_path] + list(argv or [])
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")
