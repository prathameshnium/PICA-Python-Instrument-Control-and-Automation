'''
===============================================================================
 PROGRAM:      PICA Launcher (v2)

 PURPOSE:      A modernised dashboard for launching PICA measurement scripts.
               Adds a native menu bar (File / Tools / View / Help), a two-view
               module selector (Browse cards + Quick Select dropdowns), and an
               instrument status strip.

 STATUS SAFETY (important):
               The launcher talks to the instruments ONLY at startup and when
               the user presses "Reload" on the status strip. It NEVER polls in
               the background, so once a measurement is launched the launcher
               stays off the GPIB bus. A running measurement must never be
               disturbed -- Reload therefore warns and asks for confirmation if
               any measurement launched from this window is still alive.

               The Novocontrol Alpha-AN is NEVER auto-probed: it uses a custom
               command-ack protocol and a stray *IDN? would desync it. Its
               address (if known) can be listed in SKIP_GPIB_ADDRESSES so it is
               skipped even when discovered on the bus.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre
===============================================================================
'''
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font, scrolledtext, Toplevel
import os
import re
import sys
import platform
import subprocess
import webbrowser
import threading
import multiprocessing
from multiprocessing import Process
from datetime import datetime

# Reuse the proven helpers from the legacy launcher so the launch path and the
# resolved script locations stay identical between v1 and v2.
from pica.main import (
    resource_path,
    run_script_process,
    launch_gpib_scanner,
    launch_plotter_utility,
    PICALauncherApp,
)

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# -----------------------------------------------------------------------------
#  Instrument identification (read-only, startup / reload only)
# -----------------------------------------------------------------------------
# Each entry: friendly name -> substrings that identify it in a *IDN? reply.
KNOWN_INSTRUMENTS = [
    ("Lakeshore 350",   ["MODEL350", "MODEL 350", "LSCI"]),
    ("Keithley 2400",   ["MODEL 2400"]),
    ("Keithley 6221",   ["MODEL 6221"]),
    ("Keithley 2182",   ["MODEL 2182"]),
    ("Keithley 6517B",  ["MODEL 6517"]),
    ("Keysight E4980A", ["E4980"]),
]

# Instruments that must never be auto-probed. Shown on the strip but always as
# "manual" -- the launcher does not send them anything.
NEVER_PROBED_INSTRUMENTS = ["Novocontrol Alpha-AN"]

# GPIB primary addresses that must be skipped during the scan even if VISA lists
# them. Put the Novocontrol Alpha's address here (e.g. {20}) so a stray *IDN? is
# never sent to it. Leave empty only if the Alpha is powered off / disconnected.
SKIP_GPIB_ADDRESSES = set()

# Short per-resource *IDN? timeout so a non-responding address does not stall the
# whole scan. The Lakeshore temperature read gets a little longer.
IDN_TIMEOUT_MS = 900
TEMP_TIMEOUT_MS = 1500


def _gpib_address_of(resource):
    """Return the integer GPIB primary address of a VISA resource, or None."""
    m = re.search(r'GPIB\d+::(\d+)::', resource.upper())
    return int(m.group(1)) if m else None


def scan_instruments():
    """Perform a one-shot, read-only VISA scan.

    Runs in a worker thread -- must not touch any Tk object. Returns a plain
    dict the GUI can apply on the main thread. Never probes SKIP_GPIB_ADDRESSES
    and never talks to the Novocontrol Alpha.
    """
    result = {
        'available': PYVISA_AVAILABLE,
        'error': None,
        'resources': [],
        'detected': {name: None for name, _ in KNOWN_INSTRUMENTS},
        'temperature': None,
        'temp_source': None,
        'timestamp': datetime.now().strftime("%H:%M:%S"),
    }
    if not PYVISA_AVAILABLE:
        result['error'] = "PyVISA not installed"
        return result

    try:
        rm = pyvisa.ResourceManager()
        resources = list(rm.list_resources())
    except Exception as e:
        result['error'] = f"VISA backend error: {e}"
        return result

    result['resources'] = resources
    lakeshore_resource = None

    for res in resources:
        addr = _gpib_address_of(res)
        if addr is not None and addr in SKIP_GPIB_ADDRESSES:
            continue  # never probe a protected address (e.g. the Alpha)

        idn = None
        try:
            inst = rm.open_resource(res)
            inst.timeout = IDN_TIMEOUT_MS
            idn = inst.query("*IDN?").strip()
        except Exception:
            idn = None
        finally:
            try:
                inst.close()
            except Exception:
                pass

        if not idn:
            continue

        up = idn.upper()
        for name, subs in KNOWN_INSTRUMENTS:
            if any(s.upper() in up for s in subs):
                result['detected'][name] = res
                if name == "Lakeshore 350":
                    lakeshore_resource = res

    # One temperature snapshot from the Lakeshore, if present.
    if lakeshore_resource:
        try:
            inst = rm.open_resource(lakeshore_resource)
            inst.timeout = TEMP_TIMEOUT_MS
            temp = inst.query("KRDG? A").strip()
            result['temperature'] = float(temp)
            result['temp_source'] = "Lakeshore 350 · Input A"
            inst.close()
        except Exception:
            result['temperature'] = None

    return result


# -----------------------------------------------------------------------------
#  Measurement catalogue (mirrors the module suites; maps to SCRIPT_PATHS keys)
# -----------------------------------------------------------------------------
# Each module: (label, script_key, family) where family is 'control' | 'sensing'
# | 'master' | None. script_key must exist in PICALauncherApp.SCRIPT_PATHS.
CATALOG = [
    {
        'category': "Low Resistance (10 nΩ – 100 MΩ)",
        'type': "Current Driven",
        'instruments': "K6221 · K2182 · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("Sweep Mode I-V", "Delta Mode I-V Sweep", None),
            ("Delta Mode R vs. T (T Control)", "Delta Mode R-T", "control"),
            ("Delta Mode R vs. T (T Sensing, L350)", "Delta Mode R-T (T_Sensing)", "sensing"),
            ("Delta Mode R vs. T (T Sensing, Cryocon 34)", "Delta Mode R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Mid Resistance (100 µΩ – 200 MΩ)",
        'type': "Current Driven",
        'instruments': "K2400 · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("I-V Sweep", "K2400 I-V", None),
            ("R vs. T (T Control)", "K2400 R-T", "control"),
            ("R vs. T (T Sensing, L350)", "K2400 R-T (T_Sensing)", "sensing"),
            ("R vs. T (T Sensing, Cryocon 34)", "K2400 R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Mid Resistance (Precision, 1 µΩ – 100 MΩ)",
        'type': "Current Driven",
        'instruments': "K2400 · K2182 · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("I-V Sweep", "K2400_2182 I-V", None),
            ("R vs. T (T Control)", "K2400_2182 R-T", "control"),
            ("R vs. T (T Sensing, L350)", "K2400_2182 R-T (T_Sensing)", "sensing"),
            ("R vs. T (T Sensing, Cryocon 34)", "K2400_2182 R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "High Resistance (1 Ω – 10 PΩ)",
        'type': "Voltage Driven",
        'instruments': "K6517B · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("I-V Sweep", "K6517B I-V", None),
            ("R vs. T (T Control)", "K6517B R-T", "control"),
            ("R vs. T (T Sensing, L350)", "K6517B R-T (T_Sensing)", "sensing"),
            ("R vs. T (T Sensing, Cryocon 34)", "K6517B R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Pyroelectric Measurement",
        'type': "Current Sensing",
        'instruments': "K6517B",
        'modules': [
            ("PyroCurrent vs. T", "Pyroelectric Current", None),
            ("Voltage Polling (Bias)", "K6517B Polling (Bias)", None),
        ],
    },
    {
        'category': "Temperature Utilities",
        'type': "Control Utility",
        'instruments': "Lakeshore 350 · Cryocon 34",
        'modules': [
            ("Temperature Ramp (L350)", "Lakeshore Temp Control", "control"),
            ("Step-wise Control (Basic, L350)", "Lakeshore Step Control", "control"),
            ("Step-wise Control (Advanced, L350)", "Lakeshore Step Control (Advanced)", "control"),
            ("Direct Control (L350)", "Lakeshore Direct Control", "control"),
            ("Temperature Monitor (L350)", "Lakeshore Temp Monitor", "sensing"),
            ("Direct Control (Cryocon 34)", "Cryocon Direct Control", "control"),
            ("Temperature Monitor (Cryocon 34)", "Cryocon Temp Monitor", "sensing"),
        ],
    },
    {
        'category': "Impedance Spectroscopy",
        'type': "Voltage Driven",
        'instruments': "Keysight E4980A",
        'modules': [
            ("C-V Measurement", "LCR C-V Measurement", None),
            ("Dielectric Frequency Scan", "LCR Frequency Scan", None),
            ("Temp. Step Freq. Scan (T Control)", "LCR Temp. Step Freq. Scan (T_Control)", "control"),
            ("Temp. Step Freq. Scan (PPMS, T Sensing)", "PPMS Sync Freq. Scan", "sensing"),
            ("Dielectric Master Tscan+Fscan (PPMS)", "PPMS Dielectric Master", "master"),
            ("Dielectric Temp. Scan (T Control)", "LCR Temp. Scan (T_Control)", "control"),
            ("Dielectric Temp. Scan (T Sensing)", "LCR Temp. Scan (T_Sensing)", "sensing"),
        ],
    },
    {
        'category': "Broadband Dielectric Spectroscopy",
        'type': "Voltage Driven",
        'instruments': "Novocontrol Alpha-AN · ZG4",
        'experimental': True,
        'modules': [
            ("Frequency Scan (fixed T)", "Alpha-AN Freq. Scan", None),
        ],
    },
]


def _run_legacy_launcher():
    """Target for a spawned process: run the classic v1 launcher."""
    from pica.main import main as legacy_main
    legacy_main()


class PICALauncherV2:
    PROGRAM_VERSION = "2.0.0"

    # --- Palette: matches the v1 launcher (warm sand / cream / terracotta) ---
    CLR_APP = '#B8A392'          # tan window background (v1 CLR_BG_DARK)
    CLR_PANEL = '#E5DCD3'        # cream panels / cards / rail (v1 CLR_FRAME_BG)
    CLR_PANEL2 = '#F1EBE4'       # lighter cream for input fields / console
    CLR_ACCENT = '#BA6B5E'       # terracotta accent (v1 CLR_ACCENT_GOLD)
    CLR_ACCENT_SOFT = '#EAD9D2'  # pale terracotta tint for soft fills
    CLR_HOVER = '#8B3A2F'        # deep maroon hover (v1 CLR_HOVER)
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DIM = '#6B5F54'
    CLR_TEXT_FAINT = '#8A7C6E'
    CLR_TEXT_LIGHT = '#FFFFFF'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_BORDER = '#C4B2A0'
    CLR_BORDER_STRONG = '#B8A392'
    CLR_OK = '#5B7A3F'           # olive green = connected (semantic status)
    CLR_WARN = '#8B3A2F'         # deep maroon = experimental (matches v1)
    CLR_FAMILY_SENSING = '#D8C0A8'   # pale sand   (v1)
    CLR_FAMILY_CONTROL = '#B04A38'   # terracotta  (v1)
    CLR_FAMILY_MASTER = '#4A3222'    # espresso    (v1)

    # --- Type scale: anchored on the same base size as the v1 launcher ---
    FONT_SIZE_BASE = PICALauncherApp.FONT_SIZE_BASE          # 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SMALL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_LABEL = ('Segoe UI', FONT_SIZE_BASE - 3, 'bold')
    FONT_WORDMARK = ('Segoe UI', FONT_SIZE_BASE + 10, 'bold')  # v1 FONT_TITLE
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')    # v1 FONT_INSTITUTE
    FONT_CARD = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')     # v1 FONT_SUBTITLE
    FONT_INSTITUTE = ('Segoe UI', FONT_SIZE_BASE + 4, 'bold')
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE)                 # v1 FONT_INFO
    FONT_INFO_BOLD = ('Segoe UI', FONT_SIZE_BASE, 'bold')
    FONT_STAT = ('Consolas', FONT_SIZE_BASE + 4, 'bold')
    FONT_MONO = ('Consolas', 10)                             # v1 FONT_CONSOLE

    SCRIPT_PATHS = PICALauncherApp.SCRIPT_PATHS
    LOGO_FILE = resource_path("assets/LOGO/UGC_DAE_CSR_NBG.jpeg")
    LOGO_SIZE = PICALauncherApp.LOGO_SIZE                    # 140, as in v1
    RAIL_WIDTH = 300
    # Card grid reflows with the window: a maximised 1920 px screen fits three
    # columns, a restored window two, a narrow one a single column.
    MAX_CARD_COLS = 3
    CARD_MIN_WIDTH = 380
    MANUAL_FILE = resource_path("docs/User_Manual.md")
    README_FILE = resource_path("README.md")
    CHANGELOG_FILE = resource_path("CHANGELOG.md")
    LICENSE_FILE = resource_path("LICENSE")
    REPO_URL = PICALauncherApp.REPO_URL

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher — v{self.PROGRAM_VERSION}")
        self.root.geometry("1280x820")
        self.root.configure(bg=self.CLR_APP)
        try:
            self.root.state('zoomed')
        except tk.TclError:
            pass

        # Instrument-status state (updated only on scan)
        self._scanning = False
        # Browse-grid layout state, set before the first <Configure> fires.
        self._browse_cols = 2
        self._browse_width = 900
        self._rendering = False
        # Folder the File menu's pickers open in (last one the user visited).
        self._last_data_dir = os.getcwd()
        self.chip_widgets = {}
        self.launched_processes = []

        self._setup_styles()
        self._build_menubar()
        self._build_statusbar()
        self._build_body()

        self.log(f"PICA Launcher v{self.PROGRAM_VERSION} initialized.")
        # First (and only automatic) instrument scan, shortly after the UI draws.
        self.root.after(400, self.start_scan)

    # ---------------------------------------------------------------- styling
    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_APP, foreground=self.CLR_TEXT,
                        font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_APP)
        style.configure('Panel.TFrame', background=self.CLR_PANEL)
        style.configure('Card.TFrame', background=self.CLR_PANEL)
        style.configure('TLabel', background=self.CLR_APP, foreground=self.CLR_TEXT)
        style.configure('Panel.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT)
        style.configure('Dim.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT_DIM,
                        font=self.FONT_SMALL)
        style.configure('Faint.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT_FAINT,
                        font=self.FONT_LABEL)
        style.configure('Stat.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_STAT)
        style.configure('CardTitle.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_CARD)
        style.configure('Mono.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT_DIM,
                        font=self.FONT_MONO)
        # Rail identity lines (institute, credits) read at full base size --
        # Dim.TLabel is the 10 pt style used for status-strip captions.
        style.configure('Info.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_INFO)
        style.configure('Institute.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_INSTITUTE)
        style.configure('InfoBold.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_INFO_BOLD)

        # Primary launch button (accent fill)
        style.configure('Launch.TButton', font=('Segoe UI', self.FONT_SIZE_BASE, 'bold'),
                        foreground=self.CLR_TEXT_LIGHT, background=self.CLR_ACCENT,
                        borderwidth=0, focusthickness=0, focuscolor='none', padding=(12, 8))
        style.map('Launch.TButton', background=[('active', self.CLR_HOVER),
                                                ('disabled', self.CLR_BORDER_STRONG)])

        # Module row button (flat, left-aligned; maroon hover like v1)
        style.configure('Mod.TButton', font=self.FONT_BASE, anchor='w',
                        foreground=self.CLR_TEXT, background=self.CLR_PANEL,
                        borderwidth=0, focusthickness=0, focuscolor='none', padding=(8, 6))
        style.map('Mod.TButton', background=[('active', self.CLR_HOVER)],
                  foreground=[('active', self.CLR_TEXT_LIGHT)])

        # Auxiliary / secondary button (outlined)
        style.configure('Aux.TButton', font=self.FONT_SMALL, foreground=self.CLR_TEXT_DIM,
                        background=self.CLR_PANEL, borderwidth=1, padding=(10, 7))
        style.map('Aux.TButton', foreground=[('active', self.CLR_ACCENT)],
                  background=[('active', self.CLR_ACCENT_SOFT)])

        style.configure('TNotebook', background=self.CLR_APP, borderwidth=0)
        style.configure('TNotebook.Tab', font=('Segoe UI', self.FONT_SIZE_BASE - 1, 'bold'),
                        padding=(16, 8), background=self.CLR_APP, foreground=self.CLR_TEXT)
        style.map('TNotebook.Tab',
                  background=[('selected', self.CLR_APP)],
                  foreground=[('selected', self.CLR_ACCENT)])

        style.configure('TCombobox', fieldbackground=self.CLR_PANEL2,
                        background=self.CLR_PANEL2, foreground=self.CLR_TEXT,
                        arrowcolor=self.CLR_TEXT_DIM, padding=4)
        style.configure('Vertical.TScrollbar', troughcolor=self.CLR_APP,
                        background=self.CLR_BORDER_STRONG, arrowcolor=self.CLR_TEXT_DIM,
                        borderwidth=0)

    # --------------------------------------------------------------- menu bar
    def _build_menubar(self):
        menubar = tk.Menu(self.root)

        # Classic File menu: opening things comes first, then the app commands.
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open Data File…", accelerator="Ctrl+O",
                              command=self.open_data_file)
        file_menu.add_command(label="Open Data as Graph…", accelerator="Ctrl+G",
                              command=self.open_data_as_graph)
        file_menu.add_command(label="Open Data in Text Editor…",
                              command=self.open_data_in_editor)
        file_menu.add_separator()
        file_menu.add_command(label="Open Folder…", accelerator="Ctrl+Shift+O",
                              command=self.open_folder)
        file_menu.add_command(label="Open PICA Folder", command=self.open_pica_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Reload Instrument Status", command=self.start_scan)
        file_menu.add_command(label="Refresh Module List", command=self._rebuild_browse)
        file_menu.add_separator()
        file_menu.add_command(label="Open Legacy Launcher (v1)", command=self.open_legacy_launcher)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", accelerator="Alt+F4", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        self.root.bind_all("<Control-o>", lambda _e: self.open_data_file())
        self.root.bind_all("<Control-g>", lambda _e: self.open_data_as_graph())
        self.root.bind_all("<Control-O>", lambda _e: self.open_folder())

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="GPIB / VISA Scanner", command=self._launch_gpib_scanner)
        tools_menu.add_command(label="SCPI Console", command=lambda: self.launch_script("SCPI Console"))
        tools_menu.add_command(label="Plotter Utility", command=launch_plotter_utility)
        tools_menu.add_separator()
        tools_menu.add_command(label="PICA Utils…", command=self.open_tools_popup)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Browse All Modules", command=lambda: self.notebook.select(0))
        view_menu.add_command(label="Quick Select", command=lambda: self.notebook.select(1))
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Show Console", variable=self._make_console_var())
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="User Manual", command=lambda: self._open_path(self.MANUAL_FILE))
        help_menu.add_command(label="README", command=lambda: self._open_path(self.README_FILE))
        help_menu.add_command(label="Change Log", command=lambda: self._open_path(self.CHANGELOG_FILE))
        help_menu.add_separator()
        help_menu.add_command(label="View Project on GitHub", command=lambda: webbrowser.open(self.REPO_URL))
        help_menu.add_command(label="About PICA", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _make_console_var(self):
        self._console_visible = tk.BooleanVar(value=True)
        self._console_visible.trace_add('write', lambda *a: self._toggle_console())
        return self._console_visible

    # ------------------------------------------------------------ status strip
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=self.CLR_PANEL, highlightthickness=1,
                       highlightbackground=self.CLR_BORDER)
        bar.pack(side='top', fill='x')
        inner = tk.Frame(bar, bg=self.CLR_PANEL)
        inner.pack(fill='x', padx=14, pady=8)

        # Temperature snapshot tile
        temp_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        temp_tile.pack(side='left', padx=(0, 18))
        ttk.Label(temp_tile, text="TEMPERATURE (SNAPSHOT)", style='Faint.TLabel').pack(anchor='w')
        # Fixed width: the value grows from "—" to "300.00 K" on every scan and
        # an elastic tile would shove the rest of the strip sideways each time.
        self.temp_value = ttk.Label(temp_tile, text="—", style='Stat.TLabel',
                                    width=10, anchor='w')
        self.temp_value.pack(anchor='w', fill='x')
        self.temp_sub = ttk.Label(temp_tile, text="not scanned yet", style='Dim.TLabel')
        self.temp_sub.pack(anchor='w')

        tk.Frame(inner, bg=self.CLR_BORDER, width=1).pack(side='left', fill='y', padx=(0, 18))

        # GPIB link tile
        link_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        link_tile.pack(side='left', padx=(0, 18))
        ttk.Label(link_tile, text="GPIB / VISA LINK", style='Faint.TLabel').pack(anchor='w')
        self.link_value = ttk.Label(link_tile, text="—", style='Stat.TLabel',
                                    width=4, anchor='w')
        self.link_value.pack(anchor='w', fill='x')
        self.link_sub = ttk.Label(link_tile, text="press Reload to scan", style='Dim.TLabel')
        self.link_sub.pack(anchor='w')

        tk.Frame(inner, bg=self.CLR_BORDER, width=1).pack(side='left', fill='y', padx=(0, 18))

        # Reload is packed before the elastic chip grid so pack reserves its
        # width at the right edge instead of letting the chips squeeze it out.
        self.reload_btn = ttk.Button(inner, text="⟳ Reload", style='Aux.TButton',
                                     command=self.start_scan)
        self.reload_btn.pack(side='right', padx=(18, 0))

        # Instrument chips
        chips = tk.Frame(inner, bg=self.CLR_PANEL)
        chips.pack(side='left', fill='x', expand=True)
        chip_names = [n for n, _ in KNOWN_INSTRUMENTS] + NEVER_PROBED_INSTRUMENTS
        # Uniform columns keep the status dots on a vertical line instead of
        # letting the longest name in each column set its own indent.
        chips.columnconfigure(tuple(range(4)), weight=1, uniform='chip')
        for i, name in enumerate(chip_names):
            chip = tk.Frame(chips, bg=self.CLR_PANEL)
            chip.grid(row=i // 4, column=i % 4, sticky='w', padx=(0, 16), pady=2)
            dot = tk.Label(chip, text="●", bg=self.CLR_PANEL, fg=self.CLR_TEXT_FAINT,
                           font=self.FONT_SMALL)
            dot.pack(side='left', padx=(0, 5))
            lbl = tk.Label(chip, text=name, bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                           font=self.FONT_SMALL)
            lbl.pack(side='left')
            manual = name in NEVER_PROBED_INSTRUMENTS
            if manual:
                dot.config(fg=self.CLR_TEXT_FAINT)
                lbl.config(text=f"{name} (manual)")
            self.chip_widgets[name] = (dot, lbl, manual)

    # ------------------------------------------------------------------- body
    def _build_body(self):
        body = ttk.Frame(self.root)
        body.pack(side='top', fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_rail(body)

        # Right side: notebook with two views
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky='nsew', padx=(0, 12), pady=12)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(right)
        self.notebook.grid(row=0, column=0, sticky='nsew')

        self.browse_tab = ttk.Frame(self.notebook)
        self.quick_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.browse_tab, text="Browse All Modules")
        self.notebook.add(self.quick_tab, text="Quick Select")

        self._build_browse(self.browse_tab)
        self._build_quick(self.quick_tab)

    def _build_rail(self, parent):
        # Rail is wide enough for the 140 px logo plus the v1-scale title type.
        rail = tk.Frame(parent, bg=self.CLR_PANEL, width=self.RAIL_WIDTH,
                        highlightthickness=1, highlightbackground=self.CLR_BORDER)
        rail.grid(row=0, column=0, sticky='nsew', padx=12, pady=12)
        # The rail's children are packed, so pack_propagate is what pins the
        # width -- grid_propagate does nothing here and the rail was growing to
        # whatever the console asked for (~600 px), eating the module area.
        rail.pack_propagate(False)

        pad = tk.Frame(rail, bg=self.CLR_PANEL)
        pad.pack(fill='both', expand=True, padx=16, pady=16)

        # UGC-DAE CSR logo (loaded after the window draws, like the v1 launcher)
        self.logo_image = None
        self.logo_canvas = tk.Canvas(pad, width=self.LOGO_SIZE, height=self.LOGO_SIZE,
                                     bg=self.CLR_PANEL, highlightthickness=0)
        self.logo_canvas.pack(anchor='w', pady=(0, 12))
        self.root.after(60, self._load_logo)

        # Wraps to the rail width rather than at hardcoded newlines, so the
        # institute name can carry a larger size without overflowing.
        ttk.Label(pad, text="UGC-DAE Consortium for Scientific Research, Mumbai Centre",
                  style='Institute.TLabel', justify='left',
                  wraplength=self.RAIL_WIDTH - 32).pack(anchor='w', pady=(0, 10))
        # Wordmark carries the largest type (v1 title size); the expansion sits
        # under it as a subtitle -- the acronym is what the eye lands on first.
        ttk.Label(pad, text="PICA", background=self.CLR_PANEL,
                  foreground=self.CLR_ACCENT, font=self.FONT_WORDMARK).pack(anchor='w')
        ttk.Label(pad, text="Python Instrument\nControl & Automation",
                  style='CardTitle.TLabel', justify='left').pack(anchor='w', pady=(0, 12))
        ttk.Label(pad, text="Prathamesh Deshmukh", style='InfoBold.TLabel').pack(anchor='w')
        ttk.Label(pad, text="Development", style='Dim.TLabel').pack(anchor='w', pady=(0, 6))
        ttk.Label(pad, text="Dr. Sudip Mukherjee", style='InfoBold.TLabel').pack(anchor='w')
        ttk.Label(pad, text="Vision & Guidance", style='Dim.TLabel').pack(anchor='w', pady=(0, 12))

        tk.Frame(pad, bg=self.CLR_BORDER, height=1).pack(fill='x', pady=8)

        # Console (packed from the bottom up, so the links sit just above it)
        self.console_frame = tk.Frame(pad, bg=self.CLR_PANEL)
        self.console_frame.pack(side='bottom', fill='both', expand=True)

        links = tk.Frame(pad, bg=self.CLR_PANEL)
        links.pack(side='bottom', fill='x', pady=(6, 10))
        ttk.Label(links, text=f"Version {self.PROGRAM_VERSION}",
                  style='Faint.TLabel').pack(anchor='w', pady=(0, 4))
        link_font = font.Font(family='Segoe UI', size=self.FONT_SIZE_BASE - 2, underline=True)
        for text, action in [("Change Log", lambda: self._open_path(self.CHANGELOG_FILE)),
                             ("User Manual", lambda: self._open_path(self.MANUAL_FILE)),
                             ("GitHub", lambda: webbrowser.open(self.REPO_URL))]:
            lnk = tk.Label(links, text=text, bg=self.CLR_PANEL, fg=self.CLR_ACCENT,
                           font=link_font, cursor="hand2")
            lnk.pack(side='left', padx=(0, 12))
            lnk.bind("<Button-1>", lambda _e, a=action: a())

        ttk.Label(self.console_frame, text="CONSOLE", style='Faint.TLabel').pack(anchor='w', pady=(0, 4))
        self.console_widget = scrolledtext.ScrolledText(
            self.console_frame, state='disabled', bg=self.CLR_PANEL2, fg=self.CLR_TEXT_DIM,
            font=self.FONT_MONO, wrap='word', bd=0, relief='flat', height=10, width=1)
        self.console_widget.pack(fill='both', expand=True)

    def _load_logo(self):
        """Load the institute logo into the rail canvas (deferred, like v1)."""
        if not (PIL_AVAILABLE and os.path.exists(self.LOGO_FILE)):
            self.log("Logo not loaded: PIL unavailable or file missing.")
            return
        try:
            img = Image.open(self.LOGO_FILE)
            img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
            self.logo_image = ImageTk.PhotoImage(img)  # keep a reference
            self.logo_canvas.create_image(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                                          image=self.logo_image)
        except Exception as e:
            self.log(f"ERROR: Failed to load logo. {e}")

    # ---------------------------------------------------- Browse (card grid)
    def _build_browse(self, parent):
        container = tk.Frame(parent, bg=self.CLR_APP)
        container.pack(fill='both', expand=True)

        # Legend
        legend = tk.Frame(container, bg=self.CLR_APP)
        legend.pack(fill='x', padx=12, pady=(10, 4))
        for text, color in [("T Sensing", self.CLR_FAMILY_SENSING),
                            ("T Control", self.CLR_FAMILY_CONTROL),
                            ("Master Sequence", self.CLR_FAMILY_MASTER)]:
            item = tk.Frame(legend, bg=self.CLR_APP)
            item.pack(side='left', padx=(0, 16))
            tk.Frame(item, bg=color, width=10, height=10).pack(side='left', padx=(0, 5))
            tk.Label(item, text=text, bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                     font=self.FONT_SMALL).pack(side='left')

        # Scrollable canvas
        canvas = tk.Canvas(container, bg=self.CLR_APP, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.browse_frame = tk.Frame(canvas, bg=self.CLR_APP)
        self.browse_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=self.browse_frame, anchor="nw")

        def _on_canvas_resize(event):
            canvas.itemconfig(window_id, width=event.width)
            self._reflow_cards(event.width)
        canvas.bind("<Configure>", _on_canvas_resize)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left', fill='both', expand=True, padx=(12, 0), pady=(0, 12))
        scrollbar.pack(side='right', fill='y', pady=(0, 12))

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        container.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        container.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        self._render_cards()

    def _reflow_cards(self, width):
        """Re-lay the card grid if the available width changed the column count."""
        if self._rendering:
            return
        cols = max(1, min(self.MAX_CARD_COLS, width // self.CARD_MIN_WIDTH))
        self._browse_width = width
        if cols != self._browse_cols:
            self._browse_cols = cols
            self._render_cards()

    def _render_cards(self):
        # Guard: the masonry pass calls update_idletasks, which can dispatch the
        # canvas <Configure> and re-enter this method mid-build.
        if self._rendering:
            return
        self._rendering = True
        try:
            self._render_cards_inner()
        finally:
            self._rendering = False
        # A resize that arrived while we were building is applied now.
        self._reflow_cards(self._browse_width)

    def _render_cards_inner(self):
        for w in self.browse_frame.winfo_children():
            w.destroy()
        cols = self._browse_cols
        for c in range(self.MAX_CARD_COLS):
            self.browse_frame.columnconfigure(
                c, weight=1 if c < cols else 0, uniform='cols' if c < cols else '')
        # Titles wrap inside their own column, not at a fixed 2-column width.
        wrap = max(180, self._browse_width // cols - 90)

        # Masonry layout. A grid row is as tall as its tallest card, so a
        # 1-module card next to a 7-module one left ~200 px of dead space under
        # it. Independent columns let each card sit right under the previous
        # one; every card goes to whichever column is currently shortest.
        columns = []
        for c in range(cols):
            col = tk.Frame(self.browse_frame, bg=self.CLR_APP)
            col.grid(row=0, column=c, sticky='new', padx=6)
            columns.append(col)
        for cat in CATALOG:
            self.browse_frame.update_idletasks()   # settle heights before choosing
            target = min(columns, key=lambda f: f.winfo_reqheight())
            self._make_card(target, cat, wrap).pack(fill='x', pady=(0, 12))

    def _rebuild_browse(self):
        self._render_cards()
        self.log("Module list refreshed.")

    def _make_card(self, parent, cat, wrap=320):
        card = tk.Frame(parent, bg=self.CLR_PANEL, highlightthickness=1,
                        highlightbackground=self.CLR_BORDER)
        head = tk.Frame(card, bg=self.CLR_PANEL)
        head.pack(fill='x', padx=12, pady=(10, 6))

        top = tk.Frame(head, bg=self.CLR_PANEL)
        top.pack(fill='x')
        tk.Label(top, text=cat['category'], bg=self.CLR_PANEL, fg=self.CLR_TEXT,
                 font=self.FONT_CARD, anchor='w', justify='left',
                 wraplength=wrap).pack(side='left', anchor='w')
        first_key = cat['modules'][0][1]
        ttk.Button(top, text="📁", style='Aux.TButton', width=3,
                   command=lambda k=first_key: self.open_script_folder(k)).pack(side='right')

        meta = tk.Frame(head, bg=self.CLR_PANEL)
        meta.pack(fill='x', pady=(6, 0))
        badge = tk.Label(meta, text=cat['type'].upper(), bg=self.CLR_ACCENT,
                         fg=self.CLR_TEXT_DARK, font=self.FONT_LABEL, padx=6, pady=2)
        badge.pack(side='left')
        tk.Label(meta, text=cat['instruments'], bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_MONO).pack(side='left', padx=(8, 0))
        if cat.get('experimental'):
            tk.Label(meta, text="EXPERIMENTAL", bg=self.CLR_WARN, fg=self.CLR_TEXT_LIGHT,
                     font=self.FONT_LABEL, padx=6, pady=2).pack(side='left', padx=(8, 0))

        tk.Frame(card, bg=self.CLR_BORDER, height=1).pack(fill='x', padx=0, pady=(6, 0))

        body = tk.Frame(card, bg=self.CLR_PANEL)
        body.pack(fill='x', padx=6, pady=6)
        family_colors = {'sensing': self.CLR_FAMILY_SENSING,
                         'control': self.CLR_FAMILY_CONTROL,
                         'master': self.CLR_FAMILY_MASTER}
        for label, key, family in cat['modules']:
            # No pady: consecutive rows butt together so the family strips form
            # one continuous bar instead of a dashed column of 4 px stubs.
            row = tk.Frame(body, bg=self.CLR_PANEL)
            row.pack(fill='x')
            strip = tk.Frame(row, bg=family_colors.get(family, self.CLR_PANEL2), width=5)
            strip.pack(side='left', fill='y')
            ttk.Button(row, text=label, style='Mod.TButton',
                       command=lambda k=key: self.launch_script(k)).pack(
                side='left', fill='x', expand=True)
        return card

    # -------------------------------------------------- Quick Select (form)
    def _build_quick(self, parent):
        wrap = tk.Frame(parent, bg=self.CLR_APP)
        wrap.pack(fill='both', expand=True, padx=20, pady=20)

        card = tk.Frame(wrap, bg=self.CLR_PANEL, highlightthickness=1,
                        highlightbackground=self.CLR_BORDER)
        card.pack(fill='x')
        inner = tk.Frame(card, bg=self.CLR_PANEL)
        inner.pack(fill='x', padx=22, pady=18)

        tk.Label(inner, text="Select a measurement module", bg=self.CLR_PANEL,
                 fg=self.CLR_TEXT, font=self.FONT_CARD).pack(anchor='w', pady=(0, 14))

        grid = tk.Frame(inner, bg=self.CLR_PANEL)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1), weight=1, uniform='q')

        cat_col = tk.Frame(grid, bg=self.CLR_PANEL)
        cat_col.grid(row=0, column=0, sticky='ew', padx=(0, 7))
        tk.Label(cat_col, text="Category", bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_LABEL).pack(anchor='w', pady=(0, 4))
        self.cat_var = tk.StringVar()
        self.cat_combo = ttk.Combobox(cat_col, textvariable=self.cat_var, state='readonly',
                                      values=[c['category'] for c in CATALOG])
        self.cat_combo.pack(fill='x')
        self.cat_combo.bind("<<ComboboxSelected>>", self._on_category)

        mod_col = tk.Frame(grid, bg=self.CLR_PANEL)
        mod_col.grid(row=0, column=1, sticky='ew', padx=(7, 0))
        tk.Label(mod_col, text="Module", bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_LABEL).pack(anchor='w', pady=(0, 4))
        self.mod_var = tk.StringVar()
        self.mod_combo = ttk.Combobox(mod_col, textvariable=self.mod_var, state='disabled')
        self.mod_combo.pack(fill='x')
        self.mod_combo.bind("<<ComboboxSelected>>", self._on_module)

        # Detail line
        self.detail_label = tk.Label(inner, text="Select a module to see instrument details.",
                                     bg=self.CLR_PANEL, fg=self.CLR_TEXT_FAINT,
                                     font=self.FONT_SMALL, anchor='w', justify='left')
        self.detail_label.pack(fill='x', pady=(14, 14))

        # Action row
        actions = tk.Frame(inner, bg=self.CLR_PANEL)
        actions.pack(fill='x')
        self.launch_btn = ttk.Button(actions, text="▶  Launch Module", style='Launch.TButton',
                                    state='disabled', command=self._launch_selected)
        self.launch_btn.pack(side='left', fill='x', expand=True)
        self.folder_btn = ttk.Button(actions, text="📁 Folder", style='Aux.TButton',
                                    state='disabled', command=self._folder_selected)
        self.folder_btn.pack(side='left', padx=(8, 0))
        self.data_btn = ttk.Button(actions, text="≡ Data", style='Aux.TButton',
                                  state='disabled', command=self._data_selected)
        self.data_btn.pack(side='left', padx=(8, 0))
        self.plot_btn = ttk.Button(actions, text="📈 Plot", style='Aux.TButton',
                                  state='disabled', command=lambda: launch_plotter_utility())
        self.plot_btn.pack(side='left', padx=(8, 0))

        self._selected_key = None

    def _on_category(self, _event=None):
        idx = self.cat_combo.current()
        if idx < 0:
            return
        modules = CATALOG[idx]['modules']
        self.mod_combo.config(state='readonly', values=[m[0] for m in modules])
        self.mod_var.set('')
        self.detail_label.config(text="Select a module to see instrument details.",
                                 fg=self.CLR_TEXT_FAINT)
        self._set_quick_actions(False)

    def _on_module(self, _event=None):
        cat_idx = self.cat_combo.current()
        mod_idx = self.mod_combo.current()
        if cat_idx < 0 or mod_idx < 0:
            return
        cat = CATALOG[cat_idx]
        label, key, family = cat['modules'][mod_idx]
        self._selected_key = key
        tags = [cat['type']]
        if family:
            tags.append({'control': 'T Control', 'sensing': 'T Sensing',
                         'master': 'Master Sequence'}[family])
        if cat.get('experimental'):
            tags.append('Experimental')
        self.detail_label.config(text=f"{cat['instruments']}     [ {'  ·  '.join(tags)} ]",
                                 fg=self.CLR_TEXT_DIM)
        self._set_quick_actions(True)

    def _set_quick_actions(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for b in (self.launch_btn, self.folder_btn, self.data_btn, self.plot_btn):
            b.config(state=state)
        if not enabled:
            self._selected_key = None

    def _launch_selected(self):
        if self._selected_key:
            self.launch_script(self._selected_key)

    def _folder_selected(self):
        if self._selected_key:
            self.open_script_folder(self._selected_key)

    def _data_selected(self):
        if not self._selected_key:
            return
        script_path = self.SCRIPT_PATHS.get(self._selected_key)
        start_dir = os.path.dirname(os.path.abspath(script_path)) if script_path else os.getcwd()
        path = filedialog.askopenfilename(
            title="Open data file",
            initialdir=start_dir,
            filetypes=[("Data files", "*.txt *.csv *.dat"), ("All files", "*.*")])
        if path:
            self._open_path(path)

    # ------------------------------------------------------- scan lifecycle
    def start_scan(self):
        """Trigger a one-shot instrument scan (startup or manual reload)."""
        if self._scanning:
            return
        # Guard: never scan the bus while a launched measurement is still running.
        self.launched_processes = [p for p in self.launched_processes if p.is_alive()]
        if self.launched_processes:
            proceed = messagebox.askyesno(
                "Measurement in progress",
                "A measurement launched from this window is still running.\n\n"
                "Reloading the instrument status sends commands over the GPIB bus "
                "and can disturb a running measurement.\n\n"
                "Reload anyway?",
                icon='warning', default='no')
            if not proceed:
                self.log("Reload cancelled — measurement still running.")
                return

        self._scanning = True
        self.reload_btn.config(state='disabled', text="⟳ Scanning…")
        self.link_sub.config(text="scanning the VISA bus…")
        self.log("Scanning instruments (startup/reload only)…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        results = scan_instruments()
        # Marshal back to the Tk thread.
        self.root.after(0, lambda: self._apply_scan(results))

    def _apply_scan(self, r):
        self._scanning = False
        self.reload_btn.config(state='normal', text="⟳ Reload")

        if not r['available']:
            self.temp_value.config(text="—")
            self.temp_sub.config(text="PyVISA not installed")
            self.link_value.config(text="—")
            self.link_sub.config(text="install pyvisa to scan")
            self.log("PyVISA not available — instrument status unavailable.")
            return
        if r['error']:
            self.link_value.config(text="—")
            self.link_sub.config(text=r['error'])
            self.log(f"Scan error: {r['error']}")
            return

        detected = r['detected']
        n_found = sum(1 for v in detected.values() if v)
        n_known = len(KNOWN_INSTRUMENTS)
        self.link_value.config(text=str(n_found), foreground=self.CLR_OK if n_found else self.CLR_TEXT)
        self.link_sub.config(text=f"of {n_known} known · scan {r['timestamp']}")

        # Temperature snapshot
        if r['temperature'] is not None:
            self.temp_value.config(text=f"{r['temperature']:.2f} K")
            self.temp_sub.config(text=f"{r['temp_source']} · as of {r['timestamp']}")
        else:
            self.temp_value.config(text="—")
            self.temp_sub.config(text="no Lakeshore reading")

        # Chips
        for name, (dot, lbl, manual) in self.chip_widgets.items():
            if manual:
                continue
            if detected.get(name):
                dot.config(fg=self.CLR_OK)
                lbl.config(fg=self.CLR_TEXT)
            else:
                dot.config(fg=self.CLR_TEXT_FAINT)
                lbl.config(fg=self.CLR_TEXT_DIM)

        self.log(f"Scan complete — {n_found}/{n_known} instruments detected "
                 f"across {len(r['resources'])} VISA resource(s).")

    # ----------------------------------------------------------- launching
    def launch_script(self, script_key, argv=None):
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path:
            self.log(f"ERROR: Unknown module key '{script_key}'.")
            messagebox.showerror("Not Found", f"Module '{script_key}' is not defined.")
            return
        abs_path = os.path.abspath(script_path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Script not found: {abs_path}")
            messagebox.showerror("File Not Found", f"Script not found:\n\n{abs_path}")
            return
        try:
            proc = Process(target=run_script_process, args=(abs_path, argv or []))
            proc.start()
            self.launched_processes.append(proc)
            self.log(f"Launched '{os.path.basename(abs_path)}' in a new process.")
        except Exception as e:
            self.log(f"ERROR: Failed to launch. {e}")
            messagebox.showerror("Launch Error", f"Could not launch the module:\n\n{e}")

    # -------------------------------------------------------- File menu actions
    DATA_FILETYPES = [("Data files", "*.txt *.csv *.dat"),
                      ("Text files", "*.txt"),
                      ("CSV files", "*.csv"),
                      ("All files", "*.*")]

    def _ask_data_files(self, title, multiple=False):
        """Common data-file picker; remembers the last folder used."""
        ask = filedialog.askopenfilenames if multiple else filedialog.askopenfilename
        chosen = ask(title=title, initialdir=self._last_data_dir,
                     filetypes=self.DATA_FILETYPES)
        if not chosen:
            return [] if multiple else None
        first = chosen[0] if multiple else chosen
        self._last_data_dir = os.path.dirname(first)
        return list(chosen) if multiple else chosen

    def open_data_file(self):
        """Open a data file in whatever application Windows associates with it."""
        path = self._ask_data_files("Open data file")
        if path:
            self.log(f"Opening '{os.path.basename(path)}'.")
            self._open_path(path)

    def open_data_in_editor(self):
        """Open a data file in Notepad (a plain text editor on other platforms)."""
        path = self._ask_data_files("Open data file in text editor")
        if not path:
            return
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["notepad.exe", os.path.abspath(path)])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", "-e", os.path.abspath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.abspath(path)])
            self.log(f"Opened '{os.path.basename(path)}' in a text editor.")
        except Exception as e:
            self.log(f"ERROR: Could not open the text editor. {e}")
            messagebox.showerror("Error", f"Could not open a text editor:\n\n{e}")

    def open_data_as_graph(self):
        """Plot one or more data files by handing them to the Plotter Utility."""
        paths = self._ask_data_files("Open data as graph", multiple=True)
        if not paths:
            return
        self.log(f"Plotting {len(paths)} file(s) in the Plotter Utility.")
        self.launch_script("Plotter Utility", argv=[os.path.abspath(p) for p in paths])

    def open_folder(self):
        """Open any folder in the system file browser."""
        folder = filedialog.askdirectory(title="Open folder",
                                         initialdir=self._last_data_dir)
        if folder:
            self._last_data_dir = folder
            self._open_path(folder)

    def open_pica_folder(self):
        """Open the PICA installation folder itself."""
        self._open_path(os.path.dirname(os.path.abspath(self.README_FILE)))

    def open_script_folder(self, script_key):
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path:
            return
        folder = os.path.dirname(os.path.abspath(script_path))
        if os.path.exists(folder):
            self._open_path(folder)
        else:
            messagebox.showwarning("Not Found", f"Folder not found:\n\n{folder}")

    def open_legacy_launcher(self):
        try:
            Process(target=_run_legacy_launcher).start()
            self.log("Opened the legacy (v1) launcher.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the legacy launcher:\n\n{e}")

    def _launch_gpib_scanner(self):
        if not PYVISA_AVAILABLE:
            messagebox.showerror("Dependency Missing",
                                 "The 'pyvisa' library is required.\n\npip install pyvisa pyvisa-py")
            return
        launch_gpib_scanner()

    # -------------------------------------------------------- tools popup
    # Every standalone utility PICA ships, grouped by what it is used for.
    # An entry is (button label, module key) or (button label, callable) where a
    # tool needs the dedicated launcher with its own dependency check.
    def _utils_groups(self):
        return [
            ("Plotting", [("Plotter Utility", launch_plotter_utility),
                          ("PPMS Plotter Utility", "PPMS Plotter Utility"),
                          ("P-E Plotter", "PE Plotter")]),
            ("PPMS Utilities", [("Sequence Visualizer", "Sequence Visualizer"),
                                ("PPMS Time Estimator", "PPMS Time Estimator"),
                                ("MD Ratio Calculator", "MD Ratio Calculator")]),
            ("Communication", [("GPIB / VISA Scanner", self._launch_gpib_scanner),
                               ("SCPI Console", "SCPI Console")]),
            ("Calculators", [("Quick Calc", "Quick Calc"),
                             ("Time Utility", "Time Utility"),
                             ("Unit Converter", "Unit Converter")]),
        ]

    def open_tools_popup(self):
        win = Toplevel(self.root)
        win.title("PICA Utils")
        win.configure(bg=self.CLR_APP)
        win.transient(self.root)
        win.resizable(False, False)

        tk.Label(win, text="PICA Utils", bg=self.CLR_APP, fg=self.CLR_ACCENT,
                 font=self.FONT_CARD).pack(anchor='w', padx=20, pady=(16, 2))
        tk.Label(win, text="Standalone utilities bundled with PICA", bg=self.CLR_APP,
                 fg=self.CLR_TEXT_DIM, font=self.FONT_SMALL).pack(anchor='w', padx=20, pady=(0, 12))

        body = tk.Frame(win, bg=self.CLR_APP)
        body.pack(fill='both', expand=True, padx=20, pady=(0, 16))
        groups = self._utils_groups()
        for i, (title, tools) in enumerate(groups):
            grp = tk.LabelFrame(body, text=title, bg=self.CLR_PANEL, fg=self.CLR_TEXT,
                                font=self.FONT_LABEL, bd=1, relief='solid')
            # 'nsew' + a weighted row: paired groups stretch to equal height, so
            # the shorter one no longer leaves a hole under it.
            grp.grid(row=i // 2, column=i % 2, sticky='nsew', padx=5, pady=5)
            for label, target in tools:
                cmd = target if callable(target) else (lambda k=target: self.launch_script(k))
                ttk.Button(grp, text=label, style='Aux.TButton',
                           command=cmd).pack(fill='x', padx=8, pady=2)
        body.columnconfigure((0, 1), weight=1, uniform='t')
        for r in range((len(groups) + 1) // 2):
            body.rowconfigure(r, weight=1)

        ttk.Button(win, text="Close", style='Aux.TButton',
                   command=win.destroy).pack(fill='x', padx=20, pady=(0, 16))

    def show_about(self):
        messagebox.showinfo(
            "About PICA",
            f"PICA — Python Instrument Control & Automation\n"
            f"Launcher v{self.PROGRAM_VERSION}\n\n"
            "UGC-DAE Consortium for Scientific Research, Mumbai Centre\n\n"
            "Development: Prathamesh Deshmukh\n"
            "Vision & Guidance: Dr. Sudip Mukherjee\n\n"
            "Licensed under the MIT License.")

    # ------------------------------------------------------------- helpers
    def _toggle_console(self):
        if not hasattr(self, 'console_frame'):
            return
        if self._console_visible.get():
            self.console_frame.pack(side='bottom', fill='both', expand=True)
        else:
            self.console_frame.pack_forget()

    def _open_path(self, path):
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Path not found: {abs_path}")
            messagebox.showwarning("Not Found", f"Path does not exist:\n\n{abs_path}")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(abs_path)
            elif platform.system() == "Darwin":
                subprocess.run(['open', abs_path], check=True)
            else:
                subprocess.run(['xdg-open', abs_path], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open:\n{path}\n\n{e}")

    def log(self, message):
        if getattr(self, 'console_widget', None):
            ts = datetime.now().strftime("%H:%M:%S")
            self.console_widget.config(state='normal')
            self.console_widget.insert('end', f"[{ts}] {message}\n")
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')


def main():
    root = tk.Tk()
    PICALauncherV2(root)
    root.mainloop()


if __name__ == '__main__':
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main()
