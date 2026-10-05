#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PICA Utils — Portable
===============================================================================
 A single-window launcher for the PICA utilities that need no instrument,
 no GPIB/VISA layer and no lab PC: the plotting tools, the two PPMS planning
 tools and the three calculators.

 It is meant to be frozen into one standalone .exe (see
 PICA_Utils_Portable.spec and .github/workflows/build_portable_utils.yml), so
 a colleague can run the utilities on a machine with no Python and no packages
 installed. It also runs straight from the repository with `python
 pica_utils_portable.py`.

 The hardware-facing utilities (GPIB / VISA Scanner, SCPI Console) and the
 MD Ratio Calculator are deliberately NOT bundled — the first two are useless
 without a VISA runtime on the machine, and keeping pyvisa out of the freeze
 is what keeps this build small and dependency-free.

 Each tool is launched as a *separate process of this same executable*
 (`<exe> --tool <key>`). Every tool script builds its own `tk.Tk()` root, so
 one process per tool is the only way to run several at once without the
 Tk roots fighting over the event loop.

 Author:  Prathamesh Deshmukh
 Version: 1.0
===============================================================================
"""

import multiprocessing
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

PROGRAM_VERSION = "1.0"

FROZEN = getattr(sys, 'frozen', False)

# When frozen, PyInstaller unpacks the bundle here; the logo the tool scripts
# look for (../assets/LOGO/...) lives under it.
BUNDLE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
ICON_FILE = os.path.join(BUNDLE_DIR, 'pica', 'assets', 'LOGO', 'PICA_LOGO.ico')


# -----------------------------------------------------------------------------
#  Tool registry
# -----------------------------------------------------------------------------
# key -> (button label, module path, class name). The module is imported only
# in the child process that actually runs the tool, so the launcher window
# opens instantly instead of paying matplotlib's import cost up front.
# These imports are written out statically in `run_tool` so PyInstaller's
# analysis can see them.
TOOL_GROUPS = [
    ("Plotting", [
        ("plotter",      "Plotter Utility"),
        ("ppms-plotter", "PPMS Plotter Utility"),
        ("pe-plotter",   "P-E Plotter"),
    ]),
    ("PPMS Utilities", [
        ("seq-visualizer", "Sequence Visualizer"),
        ("time-estimator", "PPMS Time Estimator"),
    ]),
    ("Calculators", [
        ("quick-calc",     "Quick Calc"),
        ("time-utility",   "Time Utility"),
        ("unit-converter", "Unit Converter"),
    ]),
]

TOOL_KEYS = [key for _, tools in TOOL_GROUPS for key, _ in tools]


def load_app_class(key):
    """Import the tool's GUI class. Written out as a static if/elif chain so
    PyInstaller's analysis can follow every import into the bundle."""
    if key == 'plotter':
        from pica.utils.PlotterUtil_GUI import PlotterAppGUI as App
    elif key == 'ppms-plotter':
        from pica.PPMS.PPMS_Plotter_GUI import PPMSPlotterGUI as App
    elif key == 'pe-plotter':
        from pica.utils.PE_plotter import PEPlotterAppGUI as App
    elif key == 'seq-visualizer':
        from pica.PPMS.PPMS_SeqVisualizer_GUI import SeqVisualizerGUI as App
    elif key == 'time-estimator':
        from pica.PPMS.PPMS_TimeEstimator_GUI import TimeEstimatorGUI as App
    elif key == 'quick-calc':
        from pica.utils.Quick_Calc_GUI import PICAQuickCalcApp as App
    elif key == 'time-utility':
        from pica.utils.Time_Utility_GUI import PICATimeUtilityApp as App
    elif key == 'unit-converter':
        from pica.utils.Unit_Converter_GUI import PICAUnitConverterApp as App
    else:
        raise SystemExit(f"Unknown tool '{key}'. Known: {', '.join(TOOL_KEYS)}")
    return App


def run_tool(key):
    """Build the requested tool's Tk root in *this* process and run it."""
    App = load_app_class(key)
    root = tk.Tk()
    App(root)
    root.mainloop()


def selftest(report_path):
    """Import every bundled tool and report via the exit code.

    The frozen exe is windowed, so stdout goes nowhere; the CI build checks the
    exit code and reads this file for the detail.
    """
    lines, failed = [], 0
    for key in TOOL_KEYS:
        try:
            cls = load_app_class(key)
            lines.append(f"OK    {key} -> {cls.__module__}.{cls.__name__}")
        except Exception as exc:
            failed += 1
            lines.append(f"FAIL  {key}: {type(exc).__name__}: {exc}")
    lines.append(f"{len(TOOL_KEYS) - failed}/{len(TOOL_KEYS)} tools importable")
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if failed else 0


# -----------------------------------------------------------------------------
#  Launcher window
# -----------------------------------------------------------------------------
class PortableUtilsLauncher:
    """The small PICA Utils window, standing on its own."""

    # Palette and fonts copied from the v2 launcher: PICA programs never import
    # from each other, so the look is duplicated rather than shared.
    CLR_APP = '#B8A392'
    CLR_PANEL = '#E5DCD3'
    CLR_ACCENT = '#BA6B5E'
    CLR_ACCENT_SOFT = '#EAD9D2'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DIM = '#6B5F54'
    CLR_BORDER = '#C4B2A0'
    CLR_WARN = '#8B3A2F'

    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SMALL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_LABEL = ('Segoe UI', FONT_SIZE_BASE - 3, 'bold')
    FONT_CARD = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Utils — Portable v{PROGRAM_VERSION}")
        self.root.configure(bg=self.CLR_APP)
        self.root.resizable(False, False)
        self._set_icon()
        self._build_styles()
        self._build_ui()

    def _set_icon(self):
        if os.path.exists(ICON_FILE):
            try:
                self.root.iconbitmap(ICON_FILE)
            except tk.TclError:
                pass  # non-Windows Tk, or a theme that refuses .ico

    def _build_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_APP, foreground=self.CLR_TEXT,
                        font=self.FONT_BASE)
        style.configure('Aux.TButton', font=self.FONT_SMALL,
                        foreground=self.CLR_TEXT_DIM, background=self.CLR_PANEL,
                        borderwidth=1, padding=(10, 7))
        style.map('Aux.TButton', foreground=[('active', self.CLR_ACCENT)],
                  background=[('active', self.CLR_ACCENT_SOFT)])

    def _build_ui(self):
        tk.Label(self.root, text="PICA Utils", bg=self.CLR_APP, fg=self.CLR_ACCENT,
                 font=self.FONT_CARD).pack(anchor='w', padx=20, pady=(16, 2))
        tk.Label(self.root, text="Portable build — plotting, PPMS planning and calculators",
                 bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_SMALL).pack(anchor='w', padx=20, pady=(0, 12))

        body = tk.Frame(self.root, bg=self.CLR_APP)
        body.pack(fill='both', expand=True, padx=20, pady=(0, 10))
        for col, (title, tools) in enumerate(TOOL_GROUPS):
            grp = tk.LabelFrame(body, text=title, bg=self.CLR_PANEL, fg=self.CLR_TEXT,
                                font=self.FONT_LABEL, bd=1, relief='solid')
            # 'nsew' + weighted columns: the groups keep equal width and height
            # whichever one holds the most buttons.
            grp.grid(row=0, column=col, sticky='nsew', padx=5, pady=5)
            for key, label in tools:
                ttk.Button(grp, text=label, style='Aux.TButton',
                           command=lambda k=key, n=label: self.launch(k, n)
                           ).pack(fill='x', padx=8, pady=2)
        body.columnconfigure(tuple(range(len(TOOL_GROUPS))), weight=1, uniform='t')
        body.rowconfigure(0, weight=1)

        # A status strip rather than a messagebox: a launcher that pops modal
        # dialogs is a nuisance when you are opening three tools in a row.
        self.status = tk.Label(self.root, text="Ready.", bg=self.CLR_APP,
                               fg=self.CLR_TEXT_DIM, font=self.FONT_SMALL,
                               anchor='w')
        self.status.pack(fill='x', padx=20, pady=(0, 8))

        ttk.Button(self.root, text="Close", style='Aux.TButton',
                   command=self.root.destroy).pack(fill='x', padx=20, pady=(0, 16))

    def _self_command(self):
        """Argv prefix that re-runs this program: the exe, or python + script."""
        if FROZEN:
            return [sys.executable]
        return [sys.executable, os.path.abspath(__file__)]

    def launch(self, key, label):
        cmd = self._self_command() + ['--tool', key]
        try:
            # DETACHED_PROCESS keeps a stray console from flashing up when the
            # launcher itself was started from a terminal.
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            subprocess.Popen(cmd, close_fds=True, creationflags=flags)
        except Exception as exc:
            self.status.config(text=f"Could not launch {label}: {exc}",
                               fg=self.CLR_WARN)
            return
        self.status.config(text=f"Launched {label} in a new window.",
                           fg=self.CLR_TEXT_DIM)


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ('--tool', '-t'):
        if len(argv) < 2:
            raise SystemExit(f"--tool needs a key. Known: {', '.join(TOOL_KEYS)}")
        run_tool(argv[1])
        return
    if argv and argv[0] == '--selftest':
        report = argv[1] if len(argv) > 1 else 'selftest.log'
        raise SystemExit(selftest(report))
    if argv and argv[0] in ('--list', '-l'):
        for key in TOOL_KEYS:
            print(key)
        return
    if argv and argv[0] in ('--help', '-h'):
        print(f"PICA Utils — Portable v{PROGRAM_VERSION}\n"
              f"  (no arguments)      open the launcher window\n"
              f"  --tool <key>        run one tool directly\n"
              f"  --list              list tool keys\n"
              f"  --selftest [file]   import every tool; exit 1 if any fails")
        return

    root = tk.Tk()
    PortableUtilsLauncher(root)
    root.mainloop()


if __name__ == '__main__':
    # Both plotter tools spawn worker processes of their own; without this the
    # frozen exe would re-run the launcher window for every worker.
    multiprocessing.freeze_support()
    multiprocessing.set_start_method('spawn', force=True)
    main()
