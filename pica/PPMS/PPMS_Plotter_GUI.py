"""
Module: PPMS_Plotter_GUI.py
Purpose: Plotter utility for Quantum Design PPMS/DynaCool .DAT files.
         Auto-detects M vs T branches (ZFC / FCC / FCW) and M vs H
         isotherms, and plots them with meaningful colors and legends.
"""

# -------------------------------------------------------------------------------
# Name:         PICA PPMS Plotter Utility
# Purpose:      Plot M-T (ZFC/FCC/FCW), M-H, AC chi and sequence overview
#               data from Quantum Design .DAT files with automatic
#               measurement segmentation.
# Author:       Prathamesh Deshmukh
# Created:      11/07/2026
# Version:      1.0
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib as mpl
import numpy as np

# --- Multi-instance support ---
import sys
from multiprocessing import Process
import multiprocessing
import subprocess

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def run_script_process(script_path):
    """
    Wrapper function to execute a script in a new process.
    `script_path` can be a list (e.g., ['python', 'script.py']) or a string (path to .exe).
    """
    try:
        subprocess.run(script_path, check=True)
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(str(script_path))} ---")
        print(e)
        print("-------------------------")


# ==============================================================================
# Quantum Design VSM .DAT parsing and measurement segmentation
# (kept as module-level functions so they can be tested headless)
# ==============================================================================

# Header INFO tags worth showing to the user, in display order.
_META_DISPLAY = [
    ('Title', 'Title'),
    ('File opened', 'File opened'),
    ('APPNAME', 'Application'),
    ('SAMPLE_MATERIAL', 'Sample material'),
    ('SAMPLE_COMMENT', 'Sample comment'),
    ('SAMPLE_MASS', 'Sample mass'),
    ('SAMPLE_SIZE', 'Sample size'),
    ('SAMPLE_SHAPE', 'Sample shape'),
    ('SAMPLE_VOLUME', 'Sample volume'),
    ('SAMPLE_MOLECULAR_WEIGHT', 'Molecular weight'),
    ('COIL_SERIAL_NUMBER', 'Coil serial no.'),
    ('MOTOR_SERIAL_NUMBER', 'Motor serial no.'),
    ('VSM_SERIAL_NUMBER', 'VSM module serial no.'),
]


def find_column(headers, name):
    """Exact (case-insensitive) match first, then substring match."""
    for h in headers:
        if h.lower() == name.lower():
            return h
    for h in headers:
        if name.lower() in h.lower():
            return h
    return None


def find_ac_columns(headers):
    """Best-effort lookup of AC susceptibility columns (ACMS/MPMS files).

    Returns (chi_prime, chi_double_prime) header names; either may be None.
    """
    def pick(patterns):
        for h in headers:
            hl = h.lower()
            if any(bad in hl for bad in ('raw', 'coil', 'quad', 'std')):
                continue
            for p in patterns:
                if p in hl:
                    return h
        return None
    chi_p = pick(("ac x' ", "ac x'(", "x' (emu", "m' (emu", "ac moment (emu"))
    chi_pp = pick(("ac x'' ", "ac x''(", "x'' (emu", "m'' (emu"))
    return chi_p, chi_pp


def parse_qd_dat(filepath):
    """Parse a Quantum Design .DAT file (VSM / ACMS style).

    Returns (meta, data) where meta is a dict of header fields and data has:
      'headers': column names, 'cols': {name: array} for every column
      (NaN where blank/non-numeric, full row count),
      'T', 'H', 'M', 'Merr': arrays restricted to rows with a valid moment
      (used for measurement segmentation),
      'n_raw', 'n_dropped': row counters.
    """
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    meta = {}
    data_start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.lower() == '[data]':
            data_start = i
            break
        if ',' in s:
            parts = [p.strip() for p in s.split(',')]
            if parts[0] == 'TITLE':
                meta['Title'] = ','.join(parts[1:])
            elif parts[0] == 'FILEOPENTIME' and len(parts) >= 4:
                meta['File opened'] = f"{parts[2]} {parts[3]}"
            elif parts[0] == 'INFO' and len(parts) >= 3:
                # Format: INFO,<value possibly containing commas>,<TAG>
                meta[parts[-1]] = ','.join(parts[1:-1])

    if data_start is None or data_start + 1 >= len(lines):
        raise ValueError("No [Data] section found. Is this a Quantum Design .DAT file?")

    headers = [h.strip() for h in lines[data_start + 1].strip().split(',')]
    ncol = len(headers)

    rows = []
    n_raw = 0
    for line in lines[data_start + 2:]:
        parts = line.rstrip('\n').split(',')
        if len(parts) < ncol:
            continue
        if len(parts) > ncol:
            # Extra commas live in the leading Comment field.
            extra = len(parts) - ncol
            parts = [','.join(parts[:extra + 1])] + parts[extra + 1:]
        n_raw += 1
        row = np.empty(ncol)
        for j, p in enumerate(parts):
            try:
                row[j] = float(p)
            except ValueError:
                row[j] = np.nan
        rows.append(row)

    matrix = np.array(rows) if rows else np.empty((0, ncol))
    cols = {h: matrix[:, j] for j, h in enumerate(headers)}

    def series(name):
        h = find_column(headers, name)
        return cols[h] if h else np.full(len(matrix), np.nan)

    T = series('Temperature (K)')
    H = series('Magnetic Field (Oe)')
    M = series('Moment (emu)')
    Merr = series('M. Std. Err. (emu)')
    if not any(np.isfinite(c).any() for c in cols.values()):
        raise ValueError("No numeric data columns found in the [Data] section.")

    # Rows without a valid moment are touchdowns / centering scans, not data.
    ok = np.isfinite(T) & np.isfinite(H) & np.isfinite(M)
    return meta, {
        'headers': headers, 'cols': cols,
        'T': T[ok], 'H': H[ok], 'M': M[ok], 'Merr': Merr[ok],
        'n_raw': n_raw, 'n_dropped': int(n_raw - ok.sum()),
    }


def _smooth(a, w=5):
    if len(a) < w:
        return a.copy()
    return np.convolve(a, np.ones(w) / w, mode='same')


def _split_by_direction(T, s, e, hyst=2.0):
    """Split rows [s:e) into monotonic warming/cooling runs.

    Direction only flips once T retreats more than `hyst` K from the running
    extremum, so servo noise around a setpoint does not create segments.
    """
    idx = [s]
    direction = 0
    ext = T[s]
    for i in range(s + 1, e):
        if direction >= 0 and T[i] > ext:
            ext = T[i]
            direction = 1
        elif direction <= 0 and T[i] < ext:
            ext = T[i]
            direction = -1
        elif direction == 1 and T[i] < ext - hyst:
            idx.append(i)
            direction = -1
            ext = T[i]
        elif direction == -1 and T[i] > ext + hyst:
            idx.append(i)
            direction = 1
            ext = T[i]
    idx.append(e)
    return [(idx[k], idx[k + 1]) for k in range(len(idx) - 1)]


def segment_measurements(T, H, min_pts=10):
    """Classify a VSM dataset into measurement segments.

    Returns a list of dicts:
      {'mode': 'MT', 'branch': 'ZFC'|'FCC'|'FCW', 'rep': n, 'field': Oe,
       's': start, 'e': end}
      {'mode': 'MH', 'temp': K, 's': start, 'e': end}

    MT: temperature sweeps at a (servoed) constant field. Within each field
    plateau, the first warming run is labeled ZFC, any cooling run FCC, and
    warming runs after the first FCW — the standard protocol ordering.
    MH: field sweeps at constant temperature (one segment per isotherm/loop).
    """
    n = len(T)
    if n < 3:
        return []
    dT = np.gradient(_smooth(T))
    dH = np.gradient(_smooth(H))

    # Per-point class: 1 = T sweeping, 2 = H sweeping (wins if both move,
    # e.g. during field ramps between plateaus), 0 = idle/settling.
    cls = np.zeros(n, dtype=int)
    cls[np.abs(dT) > 0.05] = 1
    cls[np.abs(dH) > 2.0] = 2

    # Majority filter so single-point glitches don't split segments.
    w = 7
    sm = cls.copy()
    for i in range(n):
        lo, hi = max(0, i - w // 2), min(n, i + w // 2 + 1)
        vals, cnts = np.unique(cls[lo:hi], return_counts=True)
        sm[i] = vals[np.argmax(cnts)]

    runs = []
    s = 0
    for i in range(1, n + 1):
        if i == n or sm[i] != sm[s]:
            runs.append((s, i, sm[s]))
            s = i

    segments = []
    field_groups = []          # representative field of each MT plateau group
    branch_state = {}          # group index -> {'w': count, 'c': count}
    for s, e, c in runs:
        if c == 0 or e - s < min_pts:
            continue
        if c == 1:
            fmed = float(np.median(H[s:e]))
            gi = None
            for k, fv in enumerate(field_groups):
                if abs(fv - fmed) < max(5.0, 0.02 * abs(fv)):
                    gi = k
                    break
            if gi is None:
                field_groups.append(fmed)
                gi = len(field_groups) - 1
                branch_state[gi] = {'zfc': False, 'fcw': 0, 'fcc': 0}
            st = branch_state[gi]
            for bs, be in _split_by_direction(T, s, e):
                if be - bs < min_pts:
                    continue
                warming = T[be - 1] > T[bs]
                if warming:
                    if not st['zfc'] and st['fcw'] == 0 and st['fcc'] == 0:
                        st['zfc'] = True
                        branch, rep = 'ZFC', 1
                    else:
                        st['fcw'] += 1
                        branch, rep = 'FCW', st['fcw']
                else:
                    st['fcc'] += 1
                    branch, rep = 'FCC', st['fcc']
                segments.append({'mode': 'MT', 'branch': branch, 'rep': rep,
                                 'field': field_groups[gi], 's': bs, 'e': be})
        else:
            # An H-sweep run can hold several isotherms back to back (the
            # temperature step between loops leaves no measured rows), so
            # split whenever T steps away from the current isotherm.
            sub_s = s
            tref = T[s]
            for i in range(s + 1, e + 1):
                if i == e or abs(T[i] - tref) > max(1.0, 0.02 * abs(tref)):
                    if i - sub_s >= min_pts:
                        segments.append(
                            {'mode': 'MH',
                             'temp': float(np.median(T[sub_s:i])),
                             's': sub_s, 'e': i})
                    if i < e:
                        sub_s = i
                        tref = T[i]
    return segments


def fmt_field(oe):
    """50.2 -> '50 Oe', 2500.1 -> '2.5 kOe'."""
    if abs(oe) >= 10000:
        return f"{oe / 10000:.3g} T"
    if abs(oe) >= 1000:
        return f"{oe / 1000:.3g} kOe"
    return f"{oe:.0f} Oe"


def fmt_temp(kelvin):
    return f"{kelvin:.0f} K" if kelvin >= 10 else f"{kelvin:.1f} K"


# ==============================================================================
# GUI
# ==============================================================================

class PPMSPlotterGUI:
    PROGRAM_VERSION = "1.0"
    CLR_BG = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')

    # Branch colors: fixed role assignment (colorblind-safe blue/red/amber
    # trio); different fields are distinguished by marker shape + legend.
    BRANCH_COLORS = {'ZFC': '#2a78d6', 'FCC': '#e34948', 'FCW': '#eda100'}
    FIELD_MARKERS = ['o', 's', '^', 'D', 'v', 'P', 'X', '*']
    # Linestyle distinguishes files when several are overlaid.
    FILE_LINESTYLES = ['-', '--', ':', '-.']
    # Categorical palette (fixed order, colorblind-validated) for manual mode.
    CAT_PALETTE = ['#2a78d6', '#1baf7a', '#eda100', '#008300',
                   '#4a3aa7', '#e34948', '#e87ba4', '#eb6834']
    # One-hue sequential ramp (light -> dark blue) for M-H isotherm
    # temperatures: coldest = lightest, hottest = darkest.
    MH_RAMP = ['#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6',
               '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b']

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA PPMS Plotter Utility v{self.PROGRAM_VERSION}")
        self.root.geometry("1450x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.active_filepath = None
        # {filepath: {'meta': {...}, 'data': {...}, 'segments': [...]}}
        self.file_data_cache = {}
        # {filepath: {'var': BooleanVar, 'chk': ..., 'lbl': ..., 'frame': ...}}
        self.file_ui_elements = {}
        self.logo_image = None

        self.setup_styles()
        self.create_widgets()
        self.log("Welcome to the PICA PPMS VSM Plotter. Add a .DAT file to begin.")

    # ------------------------------------------------------------------ styles
    def setup_styles(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('.', background=self.CLR_BG,
                             foreground=self.CLR_FG, font=self.FONT_BASE)
        self.style.configure('TFrame', background=self.CLR_BG)
        self.style.configure('TPanedWindow', background=self.CLR_BG)
        self.style.configure('TLabel', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG)
        self.style.configure('Header.TLabel', background=self.CLR_HEADER)
        self.style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG,
                             foreground=self.CLR_FG, insertcolor=self.CLR_FG)
        self.style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                             foreground=self.CLR_ACCENT_GOLD,
                             background=self.CLR_HEADER)
        self.style.map('TButton',
                       background=[('active', self.CLR_ACCENT_GOLD),
                                   ('hover', self.CLR_ACCENT_GOLD)],
                       foreground=[('active', self.CLR_BG),
                                   ('hover', self.CLR_BG)])
        self.style.configure('Plot.TButton', background=self.CLR_ACCENT_GREEN,
                             foreground=self.CLR_BG)
        self.style.map('Plot.TButton',
                       background=[('active', '#8AB845'), ('hover', '#8AB845')])
        self.style.map('TCombobox',
                       fieldbackground=[('readonly', self.CLR_INPUT_BG)],
                       selectbackground=[('readonly', self.CLR_ACCENT_BLUE)],
                       selectforeground=[('readonly', self.CLR_FG)],
                       foreground=[('readonly', self.CLR_FG)])
        self.style.configure('TCombobox', arrowcolor=self.CLR_FG)
        self.style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                             bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG, font=self.FONT_TITLE)
        self.style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG)
        self.style.map('TCheckbutton',
                       background=[('active', self.CLR_FRAME_BG)],
                       indicatorcolor=[('selected', self.CLR_ACCENT_GREEN),
                                       ('!selected', self.CLR_FG)])
        self.style.configure('Input.TFrame', background=self.CLR_INPUT_BG)
        mpl.rcParams.update({
            'font.family': 'Segoe UI', 'font.size': 11,
            'axes.titlesize': 15, 'axes.labelsize': 13,
            'figure.facecolor': self.CLR_BG, 'axes.facecolor': '#F4EFEA',
            'axes.edgecolor': self.CLR_FG, 'axes.labelcolor': self.CLR_FG,
            'xtick.color': self.CLR_FG, 'ytick.color': self.CLR_FG,
            'text.color': self.CLR_FG,
        })

    # ----------------------------------------------------------------- widgets
    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)
        header.grid_columnconfigure(1, weight=1)

        left_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        left_header_frame.grid(row=0, column=0, sticky='w')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(left_header_frame, text="PICA PPMS VSM Plotter",
                  style='Header.TLabel', font=font_title_main,
                  foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)

        center_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        center_header_frame.grid(row=0, column=1, sticky='ew')
        logo_canvas = Canvas(center_header_frame, width=60, height=60,
                             bg=self.CLR_HEADER, highlightthickness=0)
        logo_canvas.pack(side='left', pady=10)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (60, 60), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(30, 30, image=self.logo_image)
            except Exception as e:
                self.log(f"Warning: Could not load logo. {e}")
        institute_frame = tk.Frame(center_header_frame, bg=self.CLR_HEADER)
        institute_frame.pack(side='left', padx=15)
        ttk.Label(institute_frame,
                  text="UGC-DAE Consortium for Scientific Research",
                  style='Header.TLabel',
                  font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(institute_frame, text="Mumbai Centre", style='Header.TLabel',
                  font=('Segoe UI', 14)).pack(anchor='w')

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        main_pane.add(self._create_left_panel(main_pane), weight=1)
        main_pane.add(self._create_right_panel(main_pane), weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=420)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        # --- File Management ---
        file_frame = ttk.LabelFrame(panel, text="Data Source")
        file_frame.grid(row=0, column=0, sticky='new', pady=5)
        file_frame.grid_columnconfigure(0, weight=1)

        file_buttons_frame = ttk.Frame(file_frame)
        file_buttons_frame.grid(row=0, column=0, sticky='ew', padx=10, pady=5)
        file_buttons_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(file_buttons_frame, text="Add File(s)...",
                   command=self.browse_files).grid(row=0, column=0,
                                                   sticky='ew', padx=(0, 5))
        ttk.Button(file_buttons_frame, text="Remove Selected",
                   command=self.remove_selected_file).grid(row=0, column=1,
                                                           sticky='ew', padx=(5, 0))

        list_container = ttk.Frame(file_frame, style='TFrame')
        list_container.grid(row=1, column=0, sticky='nsew', padx=10, pady=(0, 5))
        list_container.rowconfigure(0, weight=1)
        list_container.columnconfigure(0, weight=1)
        file_canvas = tk.Canvas(list_container, bg=self.CLR_INPUT_BG,
                                highlightthickness=0, height=80)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical",
                                  command=file_canvas.yview)
        self.file_list_frame = ttk.Frame(file_canvas, style='Input.TFrame')
        file_canvas.create_window((0, 0), window=self.file_list_frame, anchor="nw")
        file_canvas.configure(yscrollcommand=scrollbar.set)
        file_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_list_frame.bind(
            "<Configure>",
            lambda e: file_canvas.configure(scrollregion=file_canvas.bbox("all")))

        ttk.Button(file_frame, text="Open New Plotter Window",
                   command=self.launch_new_instance_handler).grid(
            row=2, column=0, sticky='ew', padx=10, pady=(5, 10))

        # --- Plot Options ---
        params_frame = ttk.LabelFrame(panel, text="Plot Options")
        params_frame.grid(row=1, column=0, sticky='new', pady=5)
        params_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(params_frame, text="Plot Mode:").grid(row=0, column=0,
                                                        sticky='w', padx=10, pady=5)
        self.mode_cb = ttk.Combobox(params_frame, state='readonly',
                                    values=["Auto", "M vs T (ZFC/FCC/FCW)",
                                            "M vs H (isotherms)",
                                            "AC χ vs T",
                                            "Sequence Overview (T, H vs time)",
                                            "Manual (X/Y)"])
        self.mode_cb.set("Auto")
        self.mode_cb.bind("<<ComboboxSelected>>", self._on_mode_change)
        self.mode_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        # Manual mode: pick any two columns from the data file.
        ttk.Label(params_frame, text="X-Axis Column:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.x_col_cb = ttk.Combobox(params_frame, state='disabled')
        self.x_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.x_col_cb.grid(row=1, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Y-Axis Column:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.y_col_cb = ttk.Combobox(params_frame, state='disabled')
        self.y_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.y_col_cb.grid(row=2, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Moment Units:").grid(row=3, column=0,
                                                           sticky='w', padx=10, pady=5)
        self.units_cb = ttk.Combobox(params_frame, state='readonly',
                                     values=["emu", "emu/g"])
        self.units_cb.set("emu")
        self.units_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.units_cb.grid(row=3, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Sample Mass:").grid(
            row=4, column=0, sticky='w', padx=10, pady=5)
        self.mass_var = tk.StringVar(value="—")
        ttk.Label(params_frame, textvariable=self.mass_var).grid(
            row=4, column=1, sticky='w', padx=10, pady=5)

        ttk.Button(params_frame, text="Reload & Plot", style="Plot.TButton",
                   command=self.reload_all).grid(row=5, column=0, columnspan=2,
                                                 sticky='ew', padx=10, pady=10)

        # --- Sample / File Information ---
        info_frame = ttk.LabelFrame(panel, text="File && Sample Information")
        info_frame.grid(row=2, column=0, sticky='new', pady=5)
        info_frame.grid_columnconfigure(0, weight=1)
        self.info_text = scrolledtext.ScrolledText(
            info_frame, state='disabled', bg=self.CLR_INPUT_BG, fg=self.CLR_FG,
            font=('Consolas', 9), wrap='word', borderwidth=0, height=13)
        self.info_text.pack(fill='both', expand=True, padx=5, pady=5)

        # --- Console ---
        console_frame = ttk.LabelFrame(panel, text="Console")
        console_frame.grid(row=3, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            console_frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG, font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        container = ttk.LabelFrame(panel, text='Plot')
        container.pack(fill='both', expand=True)

        self.figure = Figure(dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.set_title("Add a PPMS VSM .DAT file to plot", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Moment (emu)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        toolbar_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        toolbar_frame.pack(fill='x', side='bottom', pady=(0, 5))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.configure(background=self.CLR_FRAME_BG)
        toolbar._message_label.config(background=self.CLR_FRAME_BG,
                                      foreground=self.CLR_FG)
        for button in toolbar.winfo_children():
            button.config(background=self.CLR_FRAME_BG)
        toolbar.update()

        return panel

    # ----------------------------------------------------------------- helpers
    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def launch_new_instance_handler(self):
        try:
            if getattr(sys, 'frozen', False):
                args = [sys.executable]
            else:
                args = [sys.executable, __file__]
            Process(target=run_script_process, args=(args,)).start()
        except Exception as e:
            messagebox.showerror(
                "Launch Error",
                f"Could not open a new plotter instance.\n\nError: {e}")

    @staticmethod
    def _shorten_label(filename, max_len=28):
        if len(filename) <= max_len:
            return filename
        stem, ext = os.path.splitext(filename)
        keep = max(max_len - len(ext) - 1, 8)
        head = (keep * 2) // 3
        tail = keep - head
        return f"{stem[:head]}…{stem[-tail:]}{ext}"

    # ------------------------------------------------------------ file loading
    def browse_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Select PPMS VSM data file(s)",
            filetypes=(("QD Data Files", "*.dat *.DAT"), ("All files", "*.*")))
        if not filepaths:
            return
        for fp in filepaths:
            if fp not in self.file_data_cache:
                if self._load_file(fp):
                    self._add_file_to_ui(fp)
        if filepaths and self.active_filepath is None:
            existing = [fp for fp in filepaths if fp in self.file_data_cache]
            if existing:
                self._set_active_file(existing[0])
        self.plot_data()

    def _load_file(self, filepath):
        try:
            meta, data = parse_qd_dat(filepath)
            segments = segment_measurements(data['T'], data['H'])
            self.file_data_cache[filepath] = {
                'meta': meta, 'data': data, 'segments': segments}
            self.log(f"Loaded '{os.path.basename(filepath)}': "
                     f"{len(data['T'])} points, {len(segments)} segment(s) detected.")
            for seg in segments:
                self.log(f"    {self._segment_label(seg)}: "
                         f"{seg['e'] - seg['s']} pts")
            if data['n_dropped']:
                self.log(f"    ({data['n_dropped']} rows without a valid moment "
                         "were skipped — touchdown/centering entries.)")
            return True
        except Exception as e:
            self.log(f"Error loading '{os.path.basename(filepath)}': {e}")
            messagebox.showerror(
                "File Load Error",
                f"Could not read '{os.path.basename(filepath)}'.\n\nDetails: {e}")
            return False

    def _segment_label(self, seg, with_range=False):
        if seg['mode'] == 'MT':
            lbl = seg['branch']
            if seg['rep'] > 1:
                lbl += f" ({seg['rep']})"
            return f"{lbl} @ {fmt_field(seg['field'])}"
        return f"M-H @ {fmt_temp(seg['temp'])}"

    def _add_file_to_ui(self, filepath):
        var = tk.BooleanVar(value=True)
        entry_frame = ttk.Frame(self.file_list_frame, style='Input.TFrame')
        entry_frame.pack(fill='x', expand=True)
        chk = ttk.Checkbutton(entry_frame, variable=var, command=self.plot_data)
        chk.pack(side='left', padx=(5, 0))
        lbl = ttk.Label(entry_frame, text=os.path.basename(filepath),
                        style='TLabel', anchor='w', background=self.CLR_INPUT_BG)
        lbl.pack(side='left', fill='x', expand=True, padx=5)
        lbl.bind("<Button-1>", lambda e, fp=filepath: self._set_active_file(fp))
        self.file_ui_elements[filepath] = {
            'var': var, 'chk': chk, 'lbl': lbl, 'frame': entry_frame}

    def remove_selected_file(self):
        paths_to_remove = [fp for fp, ui in self.file_ui_elements.items()
                           if ui['var'].get()]
        if not paths_to_remove:
            messagebox.showinfo("Remove Files",
                                "No files are selected (checked) to be removed.")
            return
        for path in paths_to_remove:
            self.file_ui_elements[path]['frame'].destroy()
            del self.file_ui_elements[path]
            self.file_data_cache.pop(path, None)
            self.log(f"Removed file: {os.path.basename(path)}")
            if self.active_filepath == path:
                self.active_filepath = None
        if self.active_filepath is None:
            remaining = list(self.file_ui_elements.keys())
            self._set_active_file(remaining[0] if remaining else None)
        self.plot_data()

    def _set_active_file(self, filepath):
        for ui in self.file_ui_elements.values():
            ui['lbl'].configure(background=self.CLR_INPUT_BG)
        self.active_filepath = filepath
        if filepath is None:
            self._show_info(None)
            self.mass_var.set("—")
            return
        if filepath in self.file_ui_elements:
            self.file_ui_elements[filepath]['lbl'].configure(
                background=self.CLR_ACCENT_BLUE)
        info = self.file_data_cache.get(filepath)
        if info:
            mass_g = self._mass_grams(filepath)
            self.mass_var.set(f"{mass_g * 1000:g} mg" if mass_g else
                              "not in file header")
            self._populate_column_pickers(info)
            self._show_info(filepath)

    def _populate_column_pickers(self, info):
        """Offer every column that holds at least one numeric value."""
        headers = info['data']['headers']
        cols = info['data']['cols']
        numeric = [h for h in headers if np.isfinite(cols[h]).any()]
        self.x_col_cb['values'] = numeric
        self.y_col_cb['values'] = numeric
        if self.x_col_cb.get() not in numeric:
            default_x = find_column(numeric, 'Temperature (K)')
            self.x_col_cb.set(default_x or (numeric[0] if numeric else ''))
        if self.y_col_cb.get() not in numeric:
            default_y = find_column(numeric, 'Moment (emu)')
            self.y_col_cb.set(default_y or (numeric[1] if len(numeric) > 1 else ''))

    def _on_mode_change(self, event=None):
        manual = self.mode_cb.get().startswith("Manual")
        state = 'readonly' if manual else 'disabled'
        self.x_col_cb.configure(state=state)
        self.y_col_cb.configure(state=state)
        self.plot_data()

    def reload_all(self):
        for fp in list(self.file_data_cache.keys()):
            self._load_file(fp)
        if self.active_filepath:
            self._show_info(self.active_filepath)
        self.plot_data()

    # ------------------------------------------------------------- info panel
    def _show_info(self, filepath):
        self.info_text.config(state='normal')
        self.info_text.delete('1.0', 'end')
        if filepath and filepath in self.file_data_cache:
            info = self.file_data_cache[filepath]
            meta, data, segs = info['meta'], info['data'], info['segments']
            lines = [f"File: {os.path.basename(filepath)}"]
            for tag, label in _META_DISPLAY:
                if tag in meta and meta[tag] not in ('', 'arb', '1'):
                    value = meta[tag]
                    if tag == 'SAMPLE_MASS':
                        value = f"{value} mg"
                    lines.append(f"{label}: {value}")
            T, H = data['T'], data['H']
            if len(T):
                lines.append("")
                lines.append(f"Data points: {len(T)} "
                             f"({data['n_dropped']} non-measurement rows skipped)")
                lines.append(f"Temperature range: {T.min():.2f} – {T.max():.2f} K")
                lines.append(f"Field range: {H.min():.1f} – {H.max():.1f} Oe")
            if segs:
                lines.append("")
                lines.append("Detected segments:")
                for seg in segs:
                    n = seg['e'] - seg['s']
                    if seg['mode'] == 'MT':
                        t0, t1 = T[seg['s']], T[seg['e'] - 1]
                        lines.append(f"  {self._segment_label(seg)}: "
                                     f"{t0:.1f} → {t1:.1f} K  ({n} pts)")
                    else:
                        h = H[seg['s']:seg['e']]
                        lines.append(f"  {self._segment_label(seg)}: "
                                     f"{h.min():.0f} → {h.max():.0f} Oe  ({n} pts)")
            self.info_text.insert('1.0', "\n".join(lines))
        else:
            self.info_text.insert('1.0', "No file selected.")
        self.info_text.config(state='disabled')

    # ---------------------------------------------------------------- plotting
    def _selected_filepaths(self):
        return [fp for fp, ui in self.file_ui_elements.items() if ui['var'].get()]

    MODE_NAMES = {'MT': 'M vs T', 'MH': 'M vs H', 'AC': 'AC χ vs T',
                  'SEQ': 'Sequence Overview', 'MANUAL': 'Manual X/Y'}

    def _resolve_mode(self, selected):
        choice = self.mode_cb.get()
        if choice.startswith("M vs T"):
            return 'MT'
        if choice.startswith("M vs H"):
            return 'MH'
        if choice.startswith("AC"):
            return 'AC'
        if choice.startswith("Sequence"):
            return 'SEQ'
        if choice.startswith("Manual"):
            return 'MANUAL'
        # Auto: mode with the most data points among selected files
        counts = {'MT': 0, 'MH': 0}
        for fp in selected:
            for seg in self.file_data_cache.get(fp, {}).get('segments', []):
                counts[seg['mode']] += seg['e'] - seg['s']
        if counts['MT'] == 0 and counts['MH'] == 0:
            # No DC moment segments: an ACMS file, most likely.
            for fp in selected:
                headers = self.file_data_cache[fp]['data']['headers']
                if find_ac_columns(headers)[0]:
                    return 'AC'
            return 'MANUAL'
        return 'MT' if counts['MT'] >= counts['MH'] else 'MH'

    def _mass_grams(self, filepath):
        """Sample mass in grams (from the file header) for emu/g, or None."""
        info = self.file_data_cache.get(filepath, {})
        raw = info.get('meta', {}).get('SAMPLE_MASS', '')
        try:
            mg = float(raw)
            return mg / 1000.0 if mg > 0 else None
        except (TypeError, ValueError):
            return None

    def _reset_figure(self):
        """Single empty axes; Sequence mode replaces this with its own grid."""
        self.figure.clf()
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

    def plot_data(self, event=None):
        selected = [fp for fp in self._selected_filepaths()
                    if fp in self.file_data_cache]

        if not selected:
            self._reset_figure()
            self.ax_main.set_title("Add a PPMS VSM .DAT file to plot",
                                   fontweight='bold')
            self.ax_main.set_xlabel("Temperature (K)")
            self.ax_main.set_ylabel("Moment (emu)")
            self.canvas.draw_idle()
            return

        try:
            mode = self._resolve_mode(selected)
            per_gram = self.units_cb.get() == 'emu/g'
            if mode == 'SEQ':
                self.figure.clf()
                plotted = self._plot_sequence(selected)
            else:
                self._reset_figure()
                plotters = {'MT': self._plot_mt, 'MH': self._plot_mh,
                            'AC': self._plot_ac, 'MANUAL': self._plot_manual}
                plotted = plotters[mode](selected, per_gram)
            if plotted:
                if mode != 'SEQ':
                    self._finalize_plot(mode, per_gram, selected)
                self.log(f"Plot updated: {plotted} trace(s), "
                         f"{len(selected)} file(s), mode "
                         f"{self.MODE_NAMES[mode]}.")
            else:
                self._reset_figure()
                self.ax_main.set_title(
                    f"Nothing to plot in {self.MODE_NAMES[mode]} mode "
                    "for the selected file(s)", fontweight='bold')
                self.log("Nothing to plot for the selected mode — "
                         "try switching Plot Mode.")
        except Exception as e:
            self.log(f"Error plotting data: {traceback.format_exc()}")
            messagebox.showerror("Plotting Error",
                                 f"An error occurred while plotting.\n\n{e}")
        finally:
            self.canvas.draw_idle()

    # Columns offered as stacked panels in Sequence Overview mode, in order.
    SEQ_PANELS = [('Temperature (K)', 'Temperature (K)'),
                  ('Magnetic Field (Oe)', 'Field (Oe)'),
                  ('Pressure (Torr)', 'Pressure (Torr)')]

    def _plot_sequence(self, selected, per_gram=False):
        """Stacked panels vs elapsed time to visualize the measurement
        sequence: what the cryostat and magnet did, and when."""
        avail = []
        for name, ylab in self.SEQ_PANELS:
            for fp in selected:
                data = self.file_data_cache[fp]['data']
                h = find_column(data['headers'], name)
                if h and np.isfinite(data['cols'][h]).any():
                    avail.append((name, ylab))
                    break
        if not avail:
            self.log("No system columns (T, H, pressure) found for the "
                     "Sequence Overview.")
            return 0

        axes = np.atleast_1d(self.figure.subplots(len(avail), 1, sharex=True))
        plotted = 0
        for fi, fp in enumerate(selected):
            data = self.file_data_cache[fp]['data']
            headers, cols = data['headers'], data['cols']
            t_col = find_column(headers, 'Time Stamp (sec)')
            if t_col is None:
                self.log(f"'{os.path.basename(fp)}': no Time Stamp column — "
                         "skipped in Sequence Overview.")
                continue
            t_h = (cols[t_col] - np.nanmin(cols[t_col])) / 3600.0
            stem = self._shorten_label(
                os.path.splitext(os.path.basename(fp))[0], 24)
            color = self.CAT_PALETTE[fi % len(self.CAT_PALETTE)]
            for ax, (name, _) in zip(axes, avail):
                h = find_column(headers, name)
                if not h:
                    continue
                y = cols[h]
                mask = np.isfinite(t_h) & np.isfinite(y)
                if not mask.any():
                    continue
                ax.plot(t_h[mask], y[mask], linewidth=1.2, color=color,
                        label=stem if len(selected) > 1 else None)
                plotted += 1

        for ax, (_, ylab) in zip(axes, avail):
            ax.set_ylabel(ylab, fontsize=11)
            ax.grid(True, linestyle='--', alpha=0.6)
        axes[-1].set_xlabel("Elapsed Time (h)")
        if len(selected) == 1:
            title = self.file_data_cache[selected[0]]['meta'].get(
                'Title', os.path.basename(selected[0]))
        else:
            title = "Sequence Overview"
            leg = axes[0].legend(fontsize=9, framealpha=0.7,
                                 labelcolor=self.CLR_FG)
            leg.set_draggable(True)
        axes[0].set_title(title, fontweight='bold')
        self.ax_main = axes[0]
        self.figure.tight_layout()
        return plotted

    def _y_values(self, filepath, seg, per_gram):
        data = self.file_data_cache[filepath]['data']
        M = data['M'][seg['s']:seg['e']]
        if per_gram:
            mass_g = self._mass_grams(filepath)
            if mass_g is None:
                return None
            return M / mass_g
        return M

    def _plot_mt(self, selected, per_gram):
        """ZFC/FCC/FCW branches: fixed role colors, marker per field."""
        multi_file = len(selected) > 1
        # Stable marker per field value across all selected files
        fields = sorted({round(seg['field'])
                         for fp in selected
                         for seg in self.file_data_cache[fp]['segments']
                         if seg['mode'] == 'MT'})
        marker_of = {f: self.FIELD_MARKERS[i % len(self.FIELD_MARKERS)]
                     for i, f in enumerate(fields)}
        plotted = 0
        for fi, fp in enumerate(selected):
            info = self.file_data_cache[fp]
            T = info['data']['T']
            stem = self._shorten_label(
                os.path.splitext(os.path.basename(fp))[0], 20)
            ls = self.FILE_LINESTYLES[fi % len(self.FILE_LINESTYLES)] \
                if multi_file else '-'
            for seg in info['segments']:
                if seg['mode'] != 'MT':
                    continue
                y = self._y_values(fp, seg, per_gram)
                if y is None:
                    self.log(f"Skipping '{os.path.basename(fp)}': no valid "
                             "sample mass in file header for emu/g.")
                    break
                x = T[seg['s']:seg['e']]
                label = self._segment_label(seg)
                if multi_file:
                    label = f"{stem} · {label}"
                self.ax_main.plot(
                    x, y, marker=marker_of[round(seg['field'])], markersize=3.5,
                    linestyle=ls, linewidth=1.2,
                    color=self.BRANCH_COLORS[seg['branch']],
                    markevery=max(1, len(x) // 60), label=label)
                plotted += 1
        return plotted

    def _plot_mh(self, selected, per_gram):
        """M-H isotherms: one-hue ramp, coldest = lightest, legend shows T."""
        multi_file = len(selected) > 1
        segs = [(fp, seg) for fp in selected
                for seg in self.file_data_cache[fp]['segments']
                if seg['mode'] == 'MH']
        if not segs:
            return 0
        temps = sorted({round(seg['temp'], 1) for _, seg in segs})
        # Position each temperature on the ramp by rank (evenly spread).
        if len(temps) == 1:
            color_of = {temps[0]: self.MH_RAMP[5]}
        else:
            color_of = {
                t: self.MH_RAMP[round(i * (len(self.MH_RAMP) - 1)
                                      / (len(temps) - 1))]
                for i, t in enumerate(temps)}
        plotted = 0
        for fp, seg in segs:
            y = self._y_values(fp, seg, per_gram)
            if y is None:
                self.log(f"Skipping '{os.path.basename(fp)}': no valid "
                         "sample mass for emu/g (set Sample Mass in mg).")
                continue
            H = self.file_data_cache[fp]['data']['H']
            x = H[seg['s']:seg['e']]
            label = fmt_temp(seg['temp'])
            if multi_file:
                label = f"{self._shorten_label(os.path.basename(fp))} · {label}"
            self.ax_main.plot(
                x, y, marker='o', markersize=3, linestyle='-', linewidth=1.2,
                color=color_of[round(seg['temp'], 1)],
                markevery=max(1, len(x) // 80), label=label)
            plotted += 1
        return plotted

    def _plot_ac(self, selected, per_gram):
        """AC susceptibility vs T: χ' solid / χ'' dashed, color per frequency
        (one-hue ramp, lowest frequency lightest)."""
        multi_file = len(selected) > 1
        plotted = 0
        self._ac_ylabel = "AC susceptibility"
        for fp in selected:
            data = self.file_data_cache[fp]['data']
            headers, cols = data['headers'], data['cols']
            chi_p, chi_pp = find_ac_columns(headers)
            t_col = find_column(headers, 'Temperature (K)')
            if not chi_p or not t_col:
                self.log(f"'{os.path.basename(fp)}': no AC susceptibility "
                         "columns found — skipped in AC χ mode.")
                continue
            self._ac_ylabel = chi_p + (" / g" if per_gram else "")
            stem = self._shorten_label(
                os.path.splitext(os.path.basename(fp))[0], 20)
            mass_g = self._mass_grams(fp) if per_gram else 1.0
            if per_gram and mass_g is None:
                self.log(f"Skipping '{os.path.basename(fp)}': no valid "
                         "sample mass in file header for emu/g.")
                continue
            T = cols[t_col]
            f_col = find_column(headers, 'Frequency (Hz)')
            F = cols[f_col] if f_col else np.full(len(T), np.nan)
            freqs = np.unique(np.round(F[np.isfinite(F)], 1))
            groups = [(f, np.isfinite(F) & (np.round(F, 1) == f))
                      for f in freqs] if len(freqs) > 1 else \
                     [(freqs[0] if len(freqs) else None, np.full(len(T), True))]
            for gi, (freq, gmask) in enumerate(groups):
                if len(groups) == 1:
                    color = self.MH_RAMP[5]
                else:
                    color = self.MH_RAMP[round(gi * (len(self.MH_RAMP) - 1)
                                               / (len(groups) - 1))]
                ftxt = f" {freq:g} Hz" if freq is not None else ""
                prefix = f"{stem} · " if multi_file else ""
                for col_name, style, sym in ((chi_p, '-', "χ′"),
                                             (chi_pp, '--', "χ″")):
                    if not col_name:
                        continue
                    y = cols[col_name]
                    mask = gmask & np.isfinite(T) & np.isfinite(y)
                    if not mask.any():
                        continue
                    self.ax_main.plot(
                        T[mask], y[mask] / mass_g, marker='o', markersize=3,
                        linestyle=style, linewidth=1.2, color=color,
                        markevery=max(1, int(mask.sum()) // 60),
                        label=f"{prefix}{sym}{ftxt}")
                    plotted += 1
        return plotted

    def _plot_manual(self, selected, per_gram=False):
        """Plot any two columns of the file(s); one categorical color per file."""
        x_name, y_name = self.x_col_cb.get(), self.y_col_cb.get()
        if not x_name or not y_name:
            self.log("Manual mode: choose X and Y columns first.")
            return 0
        plotted = 0
        for fi, fp in enumerate(selected):
            data = self.file_data_cache[fp]['data']
            cols = data['cols']
            if x_name not in cols or y_name not in cols:
                self.log(f"'{os.path.basename(fp)}' has no "
                         f"'{x_name}' / '{y_name}' columns — skipped.")
                continue
            x, y = cols[x_name], cols[y_name]
            mask = np.isfinite(x) & np.isfinite(y)
            if not mask.any():
                continue
            stem = self._shorten_label(
                os.path.splitext(os.path.basename(fp))[0], 24)
            self.ax_main.plot(
                x[mask], y[mask], marker='o', markersize=3.5,
                linestyle='-', linewidth=1.2,
                color=self.CAT_PALETTE[fi % len(self.CAT_PALETTE)],
                markevery=max(1, int(mask.sum()) // 100),
                label=stem if len(selected) > 1 else f"{y_name} vs {x_name}")
            plotted += 1
        return plotted

    def _finalize_plot(self, mode, per_gram, selected):
        unit = "emu/g" if per_gram else "emu"
        if mode == 'MT':
            self.ax_main.set_xlabel("Temperature (K)")
            self.ax_main.set_ylabel(f"Moment ({unit})")
            legend_title = "Branch @ Field"
        elif mode == 'MH':
            self.ax_main.set_xlabel("Magnetic Field (Oe)")
            self.ax_main.set_ylabel(f"Moment ({unit})")
            legend_title = "Isotherm"
            self.ax_main.axhline(0, color=self.CLR_FG, linewidth=0.6, alpha=0.4)
            self.ax_main.axvline(0, color=self.CLR_FG, linewidth=0.6, alpha=0.4)
        elif mode == 'AC':
            self.ax_main.set_xlabel("Temperature (K)")
            self.ax_main.set_ylabel(self._ac_ylabel)
            legend_title = "AC χ"
        else:
            self.ax_main.set_xlabel(self.x_col_cb.get())
            self.ax_main.set_ylabel(self.y_col_cb.get())
            legend_title = "Data"

        if len(selected) == 1:
            title = self.file_data_cache[selected[0]]['meta'].get(
                'Title', os.path.basename(selected[0]))
        else:
            title = "M vs T" if mode == 'MT' else "M vs H"
        self.ax_main.set_title(title, fontweight='bold')

        handles = self.ax_main.get_legend_handles_labels()[0]
        if handles:
            leg = self.ax_main.legend(title=legend_title,
                                      labelcolor=self.CLR_FG,
                                      fontsize=9, title_fontsize=10,
                                      framealpha=0.7,
                                      ncol=2 if len(handles) > 8 else 1)
            leg.get_title().set_color(self.CLR_FG)
            leg.set_draggable(True)
        self.figure.tight_layout()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = PPMSPlotterGUI(root)
    root.mainloop()
