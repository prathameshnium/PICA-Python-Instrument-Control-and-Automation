"""
Module: MD_Ratio_Calculator_GUI.py
Purpose: Magnetodielectric (MD) ratio calculator for the continuous
         passive dielectric protocol.

The passive scan (Temprature_Scan_Passive_E4980A_GUI.py) runs
CONTINUOUSLY through the whole PPMS protocol, so one per-frequency file
({sample}-{freq}Hz.txt, legacy 19-column format) contains everything:
cooldown -> warming ramp #1 (0 Oe) -> cooldown -> warming ramp #2 (H)...

This tool:
  - loads one such file (or a run folder with a frequency dropdown),
  - auto-detects the WARMING RAMPS (base sits, top holds and cooldowns
    are excluded — only ramp data is ever used),
  - assigns ramp #1 = 0 Oe and ramp #2 = field by run order (editable),
  - interpolates the field ramp onto the 0 Oe temperature grid BEFORE
    subtraction (the two ramps never sample identical temperatures),
  - computes MD = (Cp(H) - Cp(0)) / Cp(0) x 100 % and the analogous
    magneto-loss ratio from G (or tan-delta), and
  - plots Cp overlay + MD % + loss % on a shared, zoomable T axis.

THE ORIGINAL DATA IS IMMUTABLE: source files are opened strictly
read-only; the only write this program can ever perform is "Save MD
data…" to a NEW file, and it refuses a path equal to any loaded source.

Self-contained by design: PICA programs never import from each other.
"""

import os
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import traceback
from datetime import datetime

import numpy as np
import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)


# ======================================================================
# Pure logic (no Tk) — unit-tested in tests/test_md_ratio_calculator.py
# ======================================================================
def load_tscan_file(path):
    """Read a legacy 19-column Tscan file STRICTLY read-only.

    Columns (tab-separated): 0 Temperature, 1 Q, 2 D, 3 G(1/Rp), 4 B,
    5 Cp, ... — only T, D, G and Cp are kept. Header and malformed
    lines are skipped; NaN rows are kept (masked out downstream).
    Returns {"T", "Cp", "G", "D"} as float arrays.
    """
    T, Cp, G, D = [], [], [], []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            try:
                t = float(parts[0])
                d = float(parts[2])
                g = float(parts[3])
                cp = float(parts[5])
            except ValueError:
                continue          # header or malformed line
            T.append(t); D.append(d); G.append(g); Cp.append(cp)
    if not T:
        raise ValueError(f"No data rows found in {os.path.basename(path)} "
                         "(expected the 19-column Tscan format).")
    return {"T": np.asarray(T, float), "Cp": np.asarray(Cp, float),
            "G": np.asarray(G, float), "D": np.asarray(D, float)}


def _moving_median(x, k):
    """Odd-window moving median with edge padding."""
    k = max(1, int(k))
    if k % 2 == 0:
        k += 1
    if k == 1 or len(x) < k:
        return np.asarray(x, float)
    pad = k // 2
    xp = np.pad(np.asarray(x, float), (pad, pad), mode="edge")
    return np.array([np.median(xp[i:i + k]) for i in range(len(x))])


def find_warming_ramps(T, min_span_k=30.0, smooth_n=15):
    """Detect warming-ramp segments in a continuous temperature trace.

    The trace is median-smoothed, each point classified rising/not by a
    coarse two-sided slope (window = smooth_n points, so noise and brief
    plateaus cannot split a ramp), and contiguous rising runs whose
    temperature span is >= min_span_k are returned in file order as
    (start, stop) python slice indices. Base sits, top holds and
    cooldowns never classify as rising — the "only use ramps" guarantee.
    """
    T = np.asarray(T, float)
    n = len(T)
    if n < 5:
        return []
    sm = _moving_median(T, smooth_n)
    w = max(2, int(smooth_n))
    hi = np.minimum(np.arange(n) + w, n - 1)
    lo = np.maximum(np.arange(n) - w, 0)
    rising = (sm[hi] - sm[lo]) > 0.0

    ramps = []
    start = None
    for i in range(n):
        if rising[i] and start is None:
            start = i
        elif not rising[i] and start is not None:
            ramps.append((start, i))
            start = None
    if start is not None:
        ramps.append((start, n))

    return [(a, b) for (a, b) in ramps
            if b - a >= 3 and (sm[b - 1] - sm[a]) >= float(min_span_k)]


def interp_onto(T_ref, T_other, y_other):
    """Interpolate y_other(T_other) onto T_ref, restricted to the
    overlapping temperature range.

    Returns (mask, y_interp): `mask` selects the T_ref points inside the
    overlap; `y_interp` is y_other evaluated at T_ref[mask]. The other
    dataset is sorted by temperature first (np.interp needs
    non-decreasing x); NaNs are dropped from it.
    """
    T_ref = np.asarray(T_ref, float)
    T_other = np.asarray(T_other, float)
    y_other = np.asarray(y_other, float)
    ok = np.isfinite(T_other) & np.isfinite(y_other)
    T_o, y_o = T_other[ok], y_other[ok]
    if len(T_o) < 2:
        raise ValueError("Too few valid points to interpolate.")
    order = np.argsort(T_o, kind="stable")
    T_o, y_o = T_o[order], y_o[order]
    lo = max(np.nanmin(T_ref), T_o[0])
    hi = min(np.nanmax(T_ref), T_o[-1])
    mask = np.isfinite(T_ref) & (T_ref >= lo) & (T_ref <= hi)
    if not mask.any():
        raise ValueError("The two ramps have no overlapping "
                         "temperature range.")
    return mask, np.interp(T_ref[mask], T_o, y_o)


def compute_ratio_pct(y0, yh):
    """(y(H) - y(0)) / y(0) x 100, elementwise; NaN where y(0) ~ 0."""
    y0 = np.asarray(y0, float)
    yh = np.asarray(yh, float)
    out = np.full_like(y0, np.nan)
    ok = np.isfinite(y0) & np.isfinite(yh) & (np.abs(y0) > 0)
    out[ok] = (yh[ok] - y0[ok]) / y0[ok] * 100.0
    return out


FREQ_FILE_RE = re.compile(r"-(\d+(?:\.\d+)?)Hz\.txt$", re.IGNORECASE)


def list_freq_files(folder):
    """{frequency_Hz: path} for every *-<freq>Hz.txt in a run folder."""
    out = {}
    for fn in sorted(os.listdir(folder)):
        mobj = FREQ_FILE_RE.search(fn)
        if mobj:
            out[float(mobj.group(1))] = os.path.join(folder, fn)
    return out


# ======================================================================
# GUI
# ======================================================================
class MDRatioCalculatorGUI:
    PROGRAM_VERSION = "1.0"

    CLR_BG = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG = "#2C2825"
    CLR_FRAME_BG = "#E5DCD3"
    CLR_INPUT_BG = "#F4EFEA"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_ACCENT_GOLD = "#B68B6E"
    CLR_GRAPH_BG = "#F4EFEA"
    FONT_BASE = ("Segoe UI", 11)
    FONT_TITLE = ("Segoe UI", 13, "bold")

    CLR_ZERO = "#4A6B8A"    # 0 Oe curve
    CLR_FIELD = "#BA3B2E"   # field curve
    CLR_MD = "#2A6B3A"      # MD ratio
    CLR_ML = "#8A5A3B"      # magneto-loss ratio

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"PICA MD Ratio Calculator v{self.PROGRAM_VERSION} — "
            "(Cp(H) − Cp(0)) / Cp(0) from continuous passive Tscans")
        self.root.geometry("1500x900")
        self.root.minsize(1150, 720)
        self.root.configure(bg=self.CLR_BG)

        # Data state (source files are NEVER written)
        self.data = None            # arrays of the loaded file
        self.source_path = None     # currently loaded file
        self.folder_files = {}      # freq -> path (folder mode)
        self.ramps = []             # [(start, stop), ...]
        self.result = None          # last computed table (for save)
        self._recalc_job = None

        self.setup_styles()
        self.create_widgets()
        self._attach_traces()
        self.log(f"MD Ratio Calculator v{self.PROGRAM_VERSION}. Load one "
                 "continuous Tscan file (or its run folder): warming "
                 "ramp #1 is taken as 0 Oe, ramp #2 as the field run — "
                 "editable below. Source data is opened read-only and "
                 "never modified.")

    # ------------------------------------------------------------- style
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=self.CLR_BG,
                        foreground=self.CLR_FG, font=self.FONT_BASE)
        style.configure("TFrame", background=self.CLR_FRAME_BG)
        style.configure("Outer.TFrame", background=self.CLR_BG)
        style.configure("TLabel", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG)
        style.configure("Header.TLabel", background=self.CLR_HEADER)
        style.configure("Big.TLabel", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_ACCENT_RED,
                        font=("Segoe UI", 19, "bold"))
        style.configure("Hint.TLabel", background=self.CLR_FRAME_BG,
                        foreground="#6B655F", font=("Segoe UI", 9))
        style.configure("TEntry", fieldbackground=self.CLR_INPUT_BG,
                        foreground=self.CLR_FG, insertcolor=self.CLR_FG)
        style.configure("TButton", font=self.FONT_BASE, padding=(8, 5),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER)
        style.map("TButton",
                  background=[("active", self.CLR_ACCENT_GOLD),
                              ("hover", self.CLR_ACCENT_GOLD)],
                  foreground=[("active", self.CLR_BG),
                              ("hover", self.CLR_BG)])
        style.configure("TCheckbutton", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG)
        style.map("TCheckbutton",
                  background=[("active", self.CLR_FRAME_BG)])
        style.configure("Section.TCheckbutton",
                        background=self.CLR_FRAME_BG,
                        font=("Segoe UI", 12, "bold"))
        style.configure("TLabelframe", background=self.CLR_FRAME_BG,
                        bordercolor=self.CLR_ACCENT_RED)
        style.configure("TLabelframe.Label", background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG, font=self.FONT_TITLE)
        mpl.rcParams.update({
            "font.family": "Segoe UI", "font.size": 10,
            "axes.titlesize": 12, "axes.labelsize": 11,
            "figure.facecolor": self.CLR_BG,
            "axes.facecolor": self.CLR_GRAPH_BG,
            "axes.edgecolor": self.CLR_FG, "axes.labelcolor": self.CLR_FG,
            "xtick.color": self.CLR_FG, "ytick.color": self.CLR_FG,
            "text.color": self.CLR_FG,
        })

    # ----------------------------------------------------------- widgets
    def _sentence(self, parent, parts, pady=2, padx=12):
        """parts: str labels or (var, width) entry tuples on one row."""
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=padx, pady=pady, anchor="w")
        for p in parts:
            if isinstance(p, str):
                ttk.Label(row, text=p).pack(side="left")
            else:
                var, width = p
                ttk.Entry(row, textvariable=var, width=width,
                          justify="center").pack(side="left", padx=3)
        return row

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side="top", fill="x", padx=1, pady=1)
        ttk.Label(header, text="PICA MD Ratio Calculator",
                  style="Header.TLabel", font=("Segoe UI", 15, "bold"),
                  foreground=self.CLR_ACCENT_GOLD
                  ).pack(side="left", padx=20, pady=10)
        ttk.Label(header,
                  text="ΔCp/Cp from one continuous run — ramps only, "
                       "originals untouched",
                  style="Header.TLabel",
                  font=("Segoe UI", 12, "italic")).pack(side="left", padx=15)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)
        main_pane.add(self._create_left_panel(main_pane), weight=1)
        main_pane.add(self._create_right_panel(main_pane), weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=470, style="Outer.TFrame")

        # ---------------- 1 · data ----------------
        lf = ttk.LabelFrame(panel, text="1 ·  Data (read-only)")
        lf.pack(fill="x", pady=4)
        row = ttk.Frame(lf); row.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Button(row, text="Open data file…",
                   command=self._open_file).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Open run folder…",
                   command=self._open_folder).pack(side="left")
        frow = ttk.Frame(lf); frow.pack(fill="x", padx=10, pady=2)
        ttk.Label(frow, text="frequency:").pack(side="left")
        self.freq_cb = ttk.Combobox(frow, state="disabled", width=14)
        self.freq_cb.pack(side="left", padx=6)
        self.freq_cb.bind("<<ComboboxSelected>>", self._on_freq_change)
        self.file_lbl = ttk.Label(lf, text="(no file loaded)",
                                  style="Hint.TLabel", wraplength=430)
        self.file_lbl.pack(anchor="w", padx=12, pady=(2, 8))

        # ---------------- 2 · ramps ----------------
        rf = ttk.LabelFrame(panel, text="2 ·  Warming ramps  (auto-"
                                        "detected — only ramps are used)")
        rf.pack(fill="x", pady=4)
        self.ramp_list_lbl = ttk.Label(rf, text="—", justify="left",
                                       wraplength=430)
        self.ramp_list_lbl.pack(anchor="w", padx=12, pady=(6, 4))
        arow = ttk.Frame(rf); arow.pack(fill="x", padx=12, pady=2)
        ttk.Label(arow, text="0 Oe data is").pack(side="left")
        self.zero_cb = ttk.Combobox(arow, state="disabled", width=22)
        self.zero_cb.pack(side="left", padx=4)
        arow2 = ttk.Frame(rf); arow2.pack(fill="x", padx=12, pady=2)
        ttk.Label(arow2, text="field data is").pack(side="left")
        self.field_cb = ttk.Combobox(arow2, state="disabled", width=22)
        self.field_cb.pack(side="left", padx=4)
        ttk.Label(arow2, text="at").pack(side="left", padx=(6, 0))
        self.field_label_var = tk.StringVar(value="5000 Oe")
        ttk.Entry(arow2, textvariable=self.field_label_var, width=9,
                  justify="center").pack(side="left", padx=3)
        for cb in (self.zero_cb, self.field_cb):
            cb.bind("<<ComboboxSelected>>", self._schedule_recalc)

        self.tmin_var = tk.StringVar(value="")
        self.tmax_var = tk.StringVar(value="")
        self._sentence(rf, ["restrict to", (self.tmin_var, 6), "…",
                            (self.tmax_var, 6),
                            "K   (blank = full overlap)"])
        lrow = ttk.Frame(rf); lrow.pack(fill="x", padx=12, pady=(2, 8))
        ttk.Label(lrow, text="loss ratio from:").pack(side="left")
        self.loss_cb = ttk.Combobox(
            lrow, state="readonly", width=12,
            values=["G (1/Rp)", "tanδ (D)"])
        self.loss_cb.set("G (1/Rp)")
        self.loss_cb.pack(side="left", padx=6)
        self.loss_cb.bind("<<ComboboxSelected>>", self._schedule_recalc)

        # ---------------- 3 · advanced ----------------
        self.adv_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel,
                        text="3 ·  Ramp detection details  (defaults are "
                             "fine — click to adjust)",
                        variable=self.adv_open,
                        style="Section.TCheckbutton",
                        command=self._toggle_advanced).pack(anchor="w",
                                                            pady=(8, 0))
        self.adv = ttk.LabelFrame(panel, text="Detection")
        self.min_span_var = tk.StringVar(value="30")
        self.smooth_var = tk.StringVar(value="15")
        self._sentence(self.adv, ["a ramp must span at least",
                                  (self.min_span_var, 5), "K"])
        self._sentence(self.adv, ["smooth over", (self.smooth_var, 5),
                                  "points (median filter)"])
        ttk.Frame(self.adv, height=4).pack()

        # ---------------- answer ----------------
        ans = ttk.LabelFrame(panel, text="Answer")
        ans.pack(fill="x", pady=4)
        self.big_var = tk.StringVar(value="…")
        ttk.Label(ans, textvariable=self.big_var,
                  style="Big.TLabel").pack(fill="x", padx=14, pady=(8, 0))
        self.detail_var = tk.StringVar(value="Load a data file to begin.")
        ttk.Label(ans, textvariable=self.detail_var, wraplength=430,
                  justify="left").pack(fill="x", padx=14, pady=(2, 8))
        ttk.Button(ans, text="Save MD data…  (writes a NEW file)",
                   command=self._save_result).pack(fill="x", padx=14,
                                                   pady=(0, 10))

        # ---------------- console ----------------
        cons = ttk.LabelFrame(panel, text="Console")
        cons.pack(fill="both", expand=True, pady=4)
        self.console = scrolledtext.ScrolledText(
            cons, state="disabled", bg=self.CLR_HEADER, fg=self.CLR_FG,
            font=("Consolas", 9), wrap="word", borderwidth=0, height=3)
        self.console.pack(fill="both", expand=True, padx=5, pady=5)
        return panel

    def _toggle_advanced(self):
        if self.adv_open.get():
            self.adv.pack(fill="x", pady=2)
        else:
            self.adv.pack_forget()

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, style="Outer.TFrame")
        container = ttk.LabelFrame(
            panel, text="Cp overlay  ·  MD ratio  ·  loss ratio   "
                        "(shared T axis — zoom/pan with the toolbar)")
        container.pack(fill="both", expand=True)

        self.figure = Figure(dpi=100)
        self.ax_cp = self.figure.add_subplot(311)
        self.ax_md = self.figure.add_subplot(312, sharex=self.ax_cp)
        self.ax_ml = self.figure.add_subplot(313, sharex=self.ax_cp)
        self.ax_cp.set_ylabel("Cp (F)")
        self.ax_md.set_ylabel("MD (%)")
        self.ax_ml.set_ylabel("Loss ratio (%)")
        self.ax_ml.set_xlabel("Temperature (K)")
        for ax in (self.ax_cp, self.ax_md, self.ax_ml):
            ax.grid(True, linestyle="--", alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill="both", expand=True,
                                         padx=5, pady=5)
        tb_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        tb_frame.pack(fill="x", side="bottom", pady=(0, 5))
        tb = NavigationToolbar2Tk(self.canvas, tb_frame)
        tb.configure(background=self.CLR_FRAME_BG)
        try:
            tb._message_label.config(background=self.CLR_FRAME_BG,
                                     foreground=self.CLR_FG)
            for b in tb.winfo_children():
                b.config(background=self.CLR_FRAME_BG)
        except Exception:
            pass
        tb.update()
        return panel

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state="normal")
        self.console.insert("end", f"[{ts}] {msg}\n")
        self.console.see("end")
        self.console.config(state="disabled")

    # ------------------------------------------------------ live recalc
    def _attach_traces(self):
        for var in (self.tmin_var, self.tmax_var, self.field_label_var):
            var.trace_add("write", self._schedule_recalc)
        # Detection parameters re-run the ramp detection itself.
        for var in (self.min_span_var, self.smooth_var):
            var.trace_add("write", self._schedule_redetect)

    def _schedule_recalc(self, *_):
        if self._recalc_job is not None:
            self.root.after_cancel(self._recalc_job)
        self._recalc_job = self.root.after(400, self._recalc_now)

    def _schedule_redetect(self, *_):
        if self._recalc_job is not None:
            self.root.after_cancel(self._recalc_job)
        self._recalc_job = self.root.after(400, self._detect_ramps)

    # ------------------------------------------------------ data loading
    def _open_file(self):
        path = filedialog.askopenfilename(
            title="Open a continuous Tscan data file (read-only)",
            filetypes=[("Tscan data", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        self.folder_files = {}
        self.freq_cb.config(state="disabled", values=[])
        self.freq_cb.set("")
        self._load_path(path)

    def _open_folder(self):
        folder = filedialog.askdirectory(
            title="Open a passive-run folder (read-only)")
        if not folder:
            return
        files = list_freq_files(folder)
        if not files:
            messagebox.showwarning(
                "No data files",
                "No *-<freq>Hz.txt files found in that folder.")
            return
        self.folder_files = files
        freqs = sorted(files)
        self.freq_cb.config(state="readonly",
                            values=[f"{f:g} Hz" for f in freqs])
        self.freq_cb.set(f"{freqs[0]:g} Hz")
        self.log(f"Folder loaded: {len(files)} frequency files.")
        self._load_path(files[freqs[0]])

    def _on_freq_change(self, event=None):
        sel = self.freq_cb.get().replace(" Hz", "").strip()
        try:
            f = float(sel)
        except ValueError:
            return
        path = self.folder_files.get(f)
        if path:
            self._load_path(path)

    def _load_path(self, path):
        try:
            self.data = load_tscan_file(path)
        except Exception as e:
            self.data = None
            self.source_path = None
            messagebox.showerror("Load failed", str(e))
            return
        self.source_path = os.path.abspath(path)
        self.file_lbl.config(
            text=f"{os.path.basename(path)}   "
                 f"({len(self.data['T'])} rows, read-only)")
        self.log(f"Loaded (read-only): {path}")
        self._detect_ramps()

    # --------------------------------------------------- ramp detection
    def _detect_ramps(self):
        if self.data is None:
            return
        try:
            min_span = float(self.min_span_var.get() or 30)
            smooth_n = int(float(self.smooth_var.get() or 15))
        except ValueError:
            self.big_var.set("…")
            self.detail_var.set("Check the ramp-detection numbers.")
            return
        self.ramps = find_warming_ramps(self.data["T"], min_span,
                                        smooth_n)
        T = self.data["T"]
        names = []
        lines = []
        for k, (a, b) in enumerate(self.ramps, 1):
            span = f"{T[a]:.1f} → {T[b - 1]:.1f} K"
            names.append(f"Ramp {k}  ({span})")
            lines.append(f"Ramp {k}:  {span},  {b - a} rows")
        self.ramp_list_lbl.config(
            text="\n".join(lines) if lines
            else "No warming ramps found — lower the minimum span "
                 "under section 3?")
        for cb in (self.zero_cb, self.field_cb):
            cb.config(state="readonly" if names else "disabled",
                      values=names)
        if len(names) >= 2:
            self.zero_cb.set(names[0])      # run order: 1st ramp = 0 Oe
            self.field_cb.set(names[1])     # 2nd ramp = field
        elif names:
            self.zero_cb.set(names[0])
            self.field_cb.set(names[0])
        self.log(f"Detected {len(self.ramps)} warming ramp(s).")
        self._recalc_now()

    def _ramp_index(self, combobox):
        txt = combobox.get()
        mobj = re.match(r"Ramp (\d+)", txt)
        if not mobj:
            return None
        idx = int(mobj.group(1)) - 1
        return idx if 0 <= idx < len(self.ramps) else None

    # ------------------------------------------------------ computation
    def _recalc_now(self):
        self._recalc_job = None
        if self.data is None or not self.ramps:
            return
        try:
            self._compute_and_plot()
        except ValueError as e:
            self.big_var.set("…")
            self.detail_var.set(str(e))
            self.result = None
        except Exception:
            self.big_var.set("…")
            self.detail_var.set("Computation failed — see console.")
            self.result = None
            self.log(traceback.format_exc())

    def _ramp_arrays(self, idx, key):
        a, b = self.ramps[idx]
        T = self.data["T"][a:b]
        y = self.data[key][a:b]
        tmin = self.tmin_var.get().strip()
        tmax = self.tmax_var.get().strip()
        mask = np.isfinite(T)
        if tmin:
            mask &= T >= float(tmin)
        if tmax:
            mask &= T <= float(tmax)
        return T[mask], y[mask]

    def _compute_and_plot(self):
        i0 = self._ramp_index(self.zero_cb)
        ih = self._ramp_index(self.field_cb)
        if i0 is None or ih is None:
            raise ValueError("Assign both ramps in section 2.")
        # A user typing the T-range should get an error message, not a crash
        try:
            float(self.tmin_var.get()) if self.tmin_var.get().strip() else 0
            float(self.tmax_var.get()) if self.tmax_var.get().strip() else 0
        except ValueError:
            raise ValueError("The T-range values must be numbers (K).")

        loss_key = "G" if self.loss_cb.get().startswith("G") else "D"
        loss_name = ("G" if loss_key == "G" else "tanδ")

        T0, Cp0 = self._ramp_arrays(i0, "Cp")
        TH, CpH = self._ramp_arrays(ih, "Cp")
        _, L0 = self._ramp_arrays(i0, loss_key)
        _, LH = self._ramp_arrays(ih, loss_key)
        if len(T0) < 3 or len(TH) < 3:
            raise ValueError("Too few points in the selected range.")

        # Interpolate the FIELD ramp onto the 0 Oe temperature grid,
        # restricted to the overlap — then subtract.
        mask, CpH_i = interp_onto(T0, TH, CpH)
        _, LH_i = interp_onto(T0, TH, LH)
        Tc = T0[mask]
        Cp0_c, L0_c = Cp0[mask], L0[mask]
        md = compute_ratio_pct(Cp0_c, CpH_i)
        ml = compute_ratio_pct(L0_c, LH_i)

        fl = self.field_label_var.get().strip() or "H"
        if i0 == ih:
            self.log("NOTE: both dropdowns point at the same ramp — "
                     "MD is identically zero.")

        # Headline: the extremum of |MD|
        if np.isfinite(md).any():
            k = int(np.nanargmax(np.abs(md)))
            self.big_var.set(f"Peak MD:  {md[k]:+.3f} %  at {Tc[k]:.1f} K")
        else:
            self.big_var.set("MD: no finite values")
        self.detail_var.set(
            f"{len(Tc)} points over {Tc.min():.1f}–{Tc.max():.1f} K.  "
            f"MD = (Cp({fl}) − Cp(0 Oe)) / Cp(0 Oe) × 100.  Field ramp "
            f"interpolated onto the 0 Oe grid. Loss ratio from "
            f"{loss_name}. Sources untouched.")

        self.result = {
            "T": Tc, "Cp0": Cp0_c, "CpH": CpH_i, "MD_pct": md,
            "L0": L0_c, "LH": LH_i, "ML_pct": ml,
            "loss_name": loss_name, "field_label": fl,
            "ramp0": self.ramps[i0], "rampH": self.ramps[ih],
        }
        self._plot(T0, Cp0, TH, CpH, Tc, md, ml, fl, loss_name)

    def _plot(self, T0, Cp0, TH, CpH, Tc, md, ml, fl, loss_name):
        for ax in (self.ax_cp, self.ax_md, self.ax_ml):
            ax.clear()
            ax.grid(True, linestyle="--", alpha=0.6)
        self.ax_cp.plot(T0, Cp0, color=self.CLR_ZERO, lw=1.2,
                        label="Cp(0 Oe) ramp")
        self.ax_cp.plot(TH, CpH, color=self.CLR_FIELD, lw=1.2,
                        label=f"Cp({fl}) ramp")
        self.ax_cp.legend(loc="best", fontsize=9, framealpha=0.7)
        self.ax_cp.set_ylabel("Cp (F)")

        self.ax_md.plot(Tc, md, color=self.CLR_MD, lw=1.4)
        self.ax_md.axhline(0, color=self.CLR_FG, lw=0.8, alpha=0.5)
        self.ax_md.set_ylabel("MD (%)")
        if np.isfinite(md).any():
            k = int(np.nanargmax(np.abs(md)))
            self.ax_md.plot([Tc[k]], [md[k]], "o", color=self.CLR_ACCENT_RED,
                            ms=7)
            self.ax_md.annotate(f"{md[k]:+.2f}% @ {Tc[k]:.1f} K",
                                (Tc[k], md[k]), xytext=(8, 6),
                                textcoords="offset points", fontsize=9,
                                fontweight="bold",
                                color=self.CLR_ACCENT_RED)

        self.ax_ml.plot(Tc, ml, color=self.CLR_ML, lw=1.4)
        self.ax_ml.axhline(0, color=self.CLR_FG, lw=0.8, alpha=0.5)
        self.ax_ml.set_ylabel(f"Δ{loss_name}/{loss_name} (%)")
        self.ax_ml.set_xlabel("Temperature (K)")
        self.figure.tight_layout()
        self.canvas.draw_idle()

    # ------------------------------------------------------------- save
    def _save_result(self):
        """The ONLY write in this program — always to a NEW file, never
        to a loaded source (originals are immutable)."""
        if self.result is None:
            messagebox.showinfo("Nothing to save",
                                "Load data and compute the MD first.")
            return
        base = os.path.splitext(
            os.path.basename(self.source_path or "MD"))[0]
        path = filedialog.asksaveasfilename(
            title="Save MD result to a NEW file",
            defaultextension=".txt",
            initialfile=f"{base}_MDratio.txt",
            filetypes=[("Tab-separated text", "*.txt"),
                       ("All files", "*.*")])
        if not path:
            return
        # Immutability guard: refuse any loaded source path.
        chosen = os.path.abspath(path)
        protected = {os.path.abspath(p) for p in
                     list(self.folder_files.values())
                     + ([self.source_path] if self.source_path else [])}
        if chosen in protected:
            messagebox.showerror(
                "Refused",
                "That is a SOURCE data file. Original data is immutable "
                "— choose a different file name.")
            return
        r = self.result
        fl, ln = r["field_label"], r["loss_name"]
        try:
            with open(chosen, "w", encoding="utf-8") as fh:
                fh.write(f"# MD ratio — generated "
                         f"{datetime.now():%Y-%m-%d %H:%M:%S} by PICA "
                         f"MD Ratio Calculator v{self.PROGRAM_VERSION}\n")
                fh.write(f"# Source (read-only): {self.source_path}\n")
                fh.write(f"# 0 Oe ramp rows: {r['ramp0'][0]}–"
                         f"{r['ramp0'][1]}   {fl} ramp rows: "
                         f"{r['rampH'][0]}–{r['rampH'][1]} "
                         f"(field ramp interpolated onto the 0 Oe grid)\n")
                fh.write(f"# MD_pct = (Cp({fl}) - Cp(0)) / Cp(0) * 100   "
                         f"ML_pct = ({ln}({fl}) - {ln}(0)) / {ln}(0) * "
                         f"100\n")
                fh.write(f"Temperature\tCp0\tCp{fl.replace(' ', '')}"
                         f"_interp\tMD_pct\t{ln}0\t"
                         f"{ln}{fl.replace(' ', '')}_interp\tML_pct\n")
                for i in range(len(r["T"])):
                    fh.write(f"{r['T'][i]:.6E}\t{r['Cp0'][i]:.6E}\t"
                             f"{r['CpH'][i]:.6E}\t{r['MD_pct'][i]:.6E}\t"
                             f"{r['L0'][i]:.6E}\t{r['LH'][i]:.6E}\t"
                             f"{r['ML_pct'][i]:.6E}\n")
            self.log(f"MD result saved to NEW file: {chosen}")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))


def main():
    root = tk.Tk()
    MDRatioCalculatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
