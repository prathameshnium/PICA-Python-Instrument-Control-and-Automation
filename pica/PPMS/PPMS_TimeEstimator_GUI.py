"""
Module: PPMS_TimeEstimator_GUI.py
Purpose: Answer "how long will my PPMS run take, and when will it finish?"
         BEFORE writing the sequence. Describe the plan in plain terms —
         M(T) as ZFC / FCC / FCW at several fields, M(H) loops at several
         temperatures, and/or the dielectric protocol (ε(T) warming runs
         at several fields, optionally followed by the temperature-step
         frequency scan) — and the estimate updates live as you type.
Timing model calibrated against real MultiVu .seq/.DAT pairs (see
PPMS_SeqVisualizer_GUI.py and pica/PPMS/data_file_for_ref).

v2.1: dielectric section added — mirrors the PPMS Dielectric Master
protocol (per field: cooldown at the sequence cool rate + probe soak,
field set at base, continuous warming measurement, top hold; the
optional "Full Master" Fscan adds, per setpoint: step ramp +
stabilization wait + a frequency sweep estimated from the E4980A
per-point timing model).
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import traceback
from datetime import datetime, timedelta
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, \
    NavigationToolbar2Tk
import matplotlib.dates as mdates
import matplotlib as mpl


# E4980A per-point timing model for the dielectric Fscan estimate
# (embedded copy from PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py —
# PICA programs never import from each other).
# t_meas = max(base, cycles/f): apertures have a fixed floor but are
# period-limited at low frequency (dominates < ~1 kHz).
APER_MEAS_MODEL = {          # aperture -> (base_s, cycles)
    "SHOR": (0.02, 1.0),
    "MED": (0.09, 4.0),
    "LONG": (0.85, 32.0),
}
VISA_OVERHEAD_S = 0.25       # :FREQ + :TRIG:IMM + *OPC? + :FETC? round trips
TEMP_LOG_S = 0.10            # one interleaved KRDG? per frequency point


def estimate_fscan_sweep_seconds(n_points, aper, freq_delay,
                                 f_lo=40.0, f_hi=2e6):
    """One dielectric frequency sweep, in seconds: n_points log-spaced
    between f_lo and f_hi through the per-point timing model."""
    n = max(1, int(n_points))
    base, cycles = APER_MEAS_MODEL.get(aper, APER_MEAS_MODEL["MED"])
    if n == 1:
        freqs = [f_lo]
    else:
        ratio = (f_hi / f_lo) ** (1.0 / (n - 1))
        freqs = [f_lo * ratio ** i for i in range(n)]
    total = 0.0
    for f in freqs:
        total += (freq_delay + max(base, cycles / max(f, 1.0))
                  + VISA_OVERHEAD_S + TEMP_LOG_S)
    return total


class TimeEstimatorGUI:
    PROGRAM_VERSION = "2.1"   # + dielectric ε(T)/Fscan estimator
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

    CLR_TEMP = '#8A5A3B'   # temperature trace
    CLR_FIELD = '#4A6B8A'  # field trace
    CLR_MEAS = '#BA6B5E'   # measurement shading

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"PICA PPMS Measurement Time Estimator v{self.PROGRAM_VERSION}")
        self.root.geometry("1500x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.segments = []
        self._blocks = {}      # tree parent iid -> (t0, t1) seconds
        self._plot_start = None
        self._hl_spans = []
        self._recalc_job = None

        self.setup_styles()
        self.create_widgets()
        self._attach_traces()
        self.root.after(100, self._recalc_now)

    # ------------------------------------------------------------------ UI
    def setup_styles(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('.', background=self.CLR_BG,
                             foreground=self.CLR_FG, font=self.FONT_BASE)
        self.style.configure('TFrame', background=self.CLR_FRAME_BG)
        self.style.configure('Outer.TFrame', background=self.CLR_BG)
        self.style.configure('TLabel', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG)
        self.style.configure('Header.TLabel', background=self.CLR_HEADER)
        self.style.configure('Big.TLabel', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_ACCENT_RED,
                             font=('Segoe UI', 19, 'bold'))
        self.style.configure('Hint.TLabel', background=self.CLR_FRAME_BG,
                             foreground='#6B655F', font=('Segoe UI', 9))
        self.style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG,
                             foreground=self.CLR_FG, insertcolor=self.CLR_FG)
        self.style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG)
        self.style.map('TCheckbutton',
                       background=[('active', self.CLR_FRAME_BG)])
        self.style.configure('Section.TCheckbutton',
                             background=self.CLR_FRAME_BG,
                             font=('Segoe UI', 12, 'bold'))
        self.style.map('Section.TCheckbutton',
                       background=[('active', self.CLR_FRAME_BG)])
        self.style.configure('TButton', font=self.FONT_BASE, padding=(8, 5),
                             foreground=self.CLR_ACCENT_GOLD,
                             background=self.CLR_HEADER)
        self.style.map('TButton',
                       background=[('active', self.CLR_ACCENT_GOLD),
                                   ('hover', self.CLR_ACCENT_GOLD)],
                       foreground=[('active', self.CLR_BG),
                                   ('hover', self.CLR_BG)])
        self.style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                             bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure('TLabelframe.Label',
                             background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG, font=self.FONT_TITLE)
        self.style.configure('Treeview', background=self.CLR_INPUT_BG,
                             fieldbackground=self.CLR_INPUT_BG,
                             foreground=self.CLR_FG, rowheight=24,
                             font=('Segoe UI', 10))
        self.style.configure('Treeview.Heading', background=self.CLR_HEADER,
                             foreground=self.CLR_FG,
                             font=('Segoe UI', 10, 'bold'))
        mpl.rcParams.update({
            'font.family': 'Segoe UI', 'font.size': 10,
            'axes.titlesize': 14, 'axes.labelsize': 12,
            'figure.facecolor': self.CLR_BG, 'axes.facecolor': '#F4EFEA',
            'axes.edgecolor': self.CLR_FG, 'axes.labelcolor': self.CLR_FG,
            'xtick.color': self.CLR_FG, 'ytick.color': self.CLR_FG,
            'text.color': self.CLR_FG,
        })

    # small helpers for "sentence style" rows -----------------------------
    def _sentence(self, parent, parts, pady=2, padx=12):
        """parts: list of str (label text) or (var, width) tuples."""
        row = ttk.Frame(parent)
        row.pack(fill='x', padx=padx, pady=pady, anchor='w')
        for p in parts:
            if isinstance(p, str):
                ttk.Label(row, text=p).pack(side='left')
            else:
                var, width = p
                ttk.Entry(row, textvariable=var, width=width,
                          justify='center').pack(side='left', padx=3)
        return row

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)
        ttk.Label(header, text="PICA PPMS Measurement Time Estimator",
                  style='Header.TLabel',
                  font=('Segoe UI', 15, 'bold'),
                  foreground=self.CLR_ACCENT_GOLD).pack(side='left',
                                                        padx=20, pady=10)
        ttk.Label(header,
                  text="Describe the plan — the estimate updates as "
                       "you type",
                  style='Header.TLabel',
                  font=('Segoe UI', 12, 'italic')).pack(side='left', padx=15)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        main_pane.add(self._create_left_panel(main_pane), weight=1)
        main_pane.add(self._create_right_panel(main_pane), weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=480, style='Outer.TFrame')

        # ---------------- what to measure ----------------
        what = ttk.LabelFrame(panel, text="1 ·  What will you measure?")
        what.pack(fill='x', pady=4)

        self.mt_on_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(what, text="M(T)  —  moment vs temperature",
                        variable=self.mt_on_var,
                        style='Section.TCheckbutton').pack(
            anchor='w', padx=8, pady=(6, 0))
        self.mt_tlo_var = tk.StringVar(value="5")
        self.mt_thi_var = tk.StringVar(value="350")
        self.mt_rate_var = tk.StringVar(value="2.5")
        self._sentence(what, ["sweep from", (self.mt_tlo_var, 5),
                              "K  to", (self.mt_thi_var, 5),
                              "K  at", (self.mt_rate_var, 5), "K/min"],
                       padx=30)
        self.zfc_var = tk.BooleanVar(value=True)
        self.fcc_var = tk.BooleanVar(value=False)
        self.fcw_var = tk.BooleanVar(value=True)
        prot = ttk.Frame(what)
        prot.pack(fill='x', padx=30, pady=2, anchor='w')
        ttk.Label(prot, text="protocols: ").pack(side='left')
        for txt, v in [("ZFC", self.zfc_var), ("FCC", self.fcc_var),
                       ("FCW", self.fcw_var)]:
            ttk.Checkbutton(prot, text=txt, variable=v).pack(
                side='left', padx=(0, 10))
        self.mt_fields_var = tk.StringVar(value="50, 100, 2500")
        self._sentence(what, ["repeated at each field:",
                              (self.mt_fields_var, 16), "Oe"], padx=30)
        ttk.Label(what,
                  text="ZFC = cool in zero field, measure warming ·  "
                       "FCC = measure cooling in field ·  "
                       "FCW = measure warming after field cool",
                  style='Hint.TLabel', wraplength=430,
                  justify='left').pack(anchor='w', padx=30, pady=(0, 8))

        ttk.Separator(what).pack(fill='x', padx=8, pady=2)

        self.mh_on_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(what, text="M(H)  —  hysteresis loops",
                        variable=self.mh_on_var,
                        style='Section.TCheckbutton').pack(
            anchor='w', padx=8, pady=(4, 0))
        self.mh_hmax_var = tk.StringVar(value="50000")
        self.mh_frate_var = tk.StringVar(value="100")
        self._sentence(what, ["loop to ±", (self.mh_hmax_var, 8),
                              "Oe  at", (self.mh_frate_var, 6), "Oe/s"],
                       padx=30)
        self.mh_pts_var = tk.StringVar(value="0")
        self.mh_spp_var = tk.StringVar(value="10")
        self._sentence(what, ["stopping at", (self.mh_pts_var, 5),
                              "points ×", (self.mh_spp_var, 4),
                              "s   (0 = sweep continuously)"], padx=30)
        self.mh_temps_var = tk.StringVar(value="5, 100, 300")
        self._sentence(what, ["one loop at each temperature:",
                              (self.mh_temps_var, 14), "K"], padx=30)

        ttk.Separator(what).pack(fill='x', padx=8, pady=2)

        # ---------------- dielectric ε(T) / Fscan ----------------
        self.di_on_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(what, text="ε(T)  —  dielectric (PPMS-synced)",
                        variable=self.di_on_var,
                        style='Section.TCheckbutton').pack(
            anchor='w', padx=8, pady=(4, 0))
        self.di_fields_var = tk.StringVar(value="0, 5000")
        self._sentence(what, ["warming runs at fields:",
                              (self.di_fields_var, 12), "Oe"], padx=30)
        self.di_base_var = tk.StringVar(value="10")
        self.di_top_var = tk.StringVar(value="310")
        self.di_warm_var = tk.StringVar(value="1")
        self.di_cool_var = tk.StringVar(value="3")
        self._sentence(what, ["from base", (self.di_base_var, 5),
                              "K  to top", (self.di_top_var, 5),
                              "K  —  warm at", (self.di_warm_var, 4),
                              "K/min, cool at", (self.di_cool_var, 4),
                              "K/min"], padx=30)
        self.di_hold_var = tk.StringVar(value="30")
        self.di_soak_var = tk.StringVar(value="80")
        self._sentence(what, ["hold", (self.di_hold_var, 4),
                              "min at top,  soak", (self.di_soak_var, 4),
                              "min at base before each run"], padx=30)
        self.di_fscan_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(what,
                        text="then step Fscan (Full Master protocol)",
                        variable=self.di_fscan_var).pack(
            anchor='w', padx=30, pady=(2, 0))
        self.di_fscan_temps_var = tk.StringVar(value="15, 25, 50, 100")
        self._sentence(what, ["step scans at:",
                              (self.di_fscan_temps_var, 16), "K"],
                       padx=48)
        self.di_stab_var = tk.StringVar(value="30")
        self.di_pts_var = tk.StringVar(value="377")
        self.di_delay_var = tk.StringVar(value="0.2")
        row = self._sentence(what, ["stabilize", (self.di_stab_var, 4),
                                    "min  +  sweep", (self.di_pts_var, 5),
                                    "points at"], padx=48)
        self.di_aper_cb = ttk.Combobox(row, values=["SHOR", "MED", "LONG"],
                                       state='readonly', width=6)
        self.di_aper_cb.set("MED")
        self.di_aper_cb.pack(side='left', padx=3)
        self.di_aper_cb.bind("<<ComboboxSelected>>", self._schedule_recalc)
        ttk.Label(row, text="aperture,").pack(side='left')
        ttk.Entry(row, textvariable=self.di_delay_var, width=5,
                  justify='center').pack(side='left', padx=3)
        ttk.Label(row, text="s delay").pack(side='left')
        ttk.Label(what,
                  text="Mirrors the PPMS Dielectric Master: per field — "
                       "cooldown at the cool rate + probe soak, field set "
                       "at base, continuous warming measurement, top "
                       "hold. Untick the Fscan for a Tscan-only protocol.",
                  style='Hint.TLabel', wraplength=430,
                  justify='left').pack(anchor='w', padx=30, pady=(0, 8))
        ttk.Frame(what, height=6).pack()

        # ---------------- start / end ----------------
        se = ttk.LabelFrame(panel, text="2 ·  Start & End")
        se.pack(fill='x', pady=4)
        self.start_time_var = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d %I:%M %p"))
        row = self._sentence(se, ["starting at",
                                  (self.start_time_var, 19)])
        ttk.Button(row, text="now", width=5,
                   command=lambda: self.start_time_var.set(
                       datetime.now().strftime("%Y-%m-%d %I:%M %p"))).pack(
            side='left', padx=4)
        self.init_temp_var = tk.StringVar(value="300")
        self.final_temp_var = tk.StringVar(value="300")
        self._sentence(se, ["sample is now at", (self.init_temp_var, 5),
                            "K,  afterwards return it to",
                            (self.final_temp_var, 5), "K"])
        ttk.Frame(se, height=4).pack()

        # ---------------- advanced ----------------
        self.adv_open = tk.BooleanVar(value=False)
        adv_btn = ttk.Checkbutton(
            panel, text="3 ·  Cryostat details  (calibrated defaults — "
                        "click to adjust)",
            variable=self.adv_open, style='Section.TCheckbutton',
            command=self._toggle_advanced)
        adv_btn.pack(anchor='w', pady=(8, 0))
        self.adv = ttk.LabelFrame(panel, text="Cryostat rates & waits")
        self.fast_rate_var = tk.StringVar(value="8")
        self.slow_rate_var = tk.StringVar(value="2")
        self.slow_below_var = tk.StringVar(value="30")
        self._sentence(self.adv, ["move (not measuring) at",
                                  (self.fast_rate_var, 4), "K/min,  but",
                                  (self.slow_rate_var, 4), "K/min below",
                                  (self.slow_below_var, 4), "K"])
        self.field_rate_var = tk.StringVar(value="50")
        self._sentence(self.adv, ["charge the magnet at",
                                  (self.field_rate_var, 5), "Oe/s"])
        self.settle_var = tk.StringVar(value="300")
        self._sentence(self.adv, ["allow", (self.settle_var, 5),
                                  "s extra settling after every ramp"])
        self.mt_soak_var = tk.StringVar(value="1200")
        self._sentence(self.adv, ["soak", (self.mt_soak_var, 5),
                                  "s at base temperature before "
                                  "measuring"])
        ttk.Frame(self.adv, height=4).pack()
        # (self.adv is packed/unpacked by _toggle_advanced)

        # ---------------- console ----------------
        cons = ttk.LabelFrame(panel, text="Console")
        cons.pack(fill='both', expand=True, pady=4)
        self.console = scrolledtext.ScrolledText(
            cons, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG,
            font=('Consolas', 9), wrap='word', borderwidth=0, height=3)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _toggle_advanced(self):
        if self.adv_open.get():
            self.adv.pack(fill='x', pady=2)
        else:
            self.adv.pack_forget()

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, style='Outer.TFrame')
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=2)
        panel.grid_rowconfigure(2, weight=3)

        summ = ttk.LabelFrame(panel, text="Answer")
        summ.grid(row=0, column=0, sticky='new', pady=(0, 4))
        self.big_var = tk.StringVar(value="…")
        ttk.Label(summ, textvariable=self.big_var,
                  style='Big.TLabel').pack(fill='x', padx=14, pady=(8, 0))
        self.detail_var = tk.StringVar(value="")
        ttk.Label(summ, textvariable=self.detail_var, wraplength=950,
                  justify='left',
                  font=('Segoe UI', 11)).pack(fill='x', padx=14,
                                              pady=(2, 8))

        steps = ttk.LabelFrame(
            panel, text="Where the time goes  (expand a block for its "
                        "steps · click to highlight on the plot)")
        steps.grid(row=1, column=0, sticky='nsew', pady=4)
        steps.grid_columnconfigure(0, weight=1)
        steps.grid_rowconfigure(0, weight=1)
        cols = ('desc', 'start', 'end', 'dur')
        self.tree = ttk.Treeview(steps, columns=cols, show='tree headings',
                                 height=8)
        self.tree.heading('#0', text='')
        self.tree.column('#0', width=30, stretch=False)
        for c, w, t in [('desc', 400, 'Step'), ('start', 105, 'Start'),
                        ('end', 105, 'End'), ('dur', 90, 'Duration')]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w')
        vsb = ttk.Scrollbar(steps, orient='vertical',
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(5, 0), pady=5)
        vsb.grid(row=0, column=1, sticky='ns', pady=5)
        self.tree.tag_configure('meas', background='#F3DBD4')
        self.tree.tag_configure('wait', background='#EDEAE6',
                                foreground='#6B655F')
        self.tree.tag_configure('blk', background=self.CLR_HEADER,
                                font=('Segoe UI', 10, 'bold'))
        self.tree.bind('<<TreeviewSelect>>', self._on_step_select)

        container = ttk.LabelFrame(panel, text='Timeline Preview')
        container.grid(row=2, column=0, sticky='nsew', pady=4)
        self.figure = Figure(dpi=100)
        self.ax_T = self.figure.add_subplot(211)
        self.ax_H = self.figure.add_subplot(212, sharex=self.ax_T)
        self.ax_T.set_ylabel("Temperature (K)")
        self.ax_H.set_ylabel("Field (Oe)")
        self.ax_H.set_xlabel("Time")
        for ax in (self.ax_T, self.ax_H):
            ax.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True,
                                         padx=5, pady=5)
        tb_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        tb_frame.pack(fill='x', side='bottom', pady=(0, 5))
        tb = NavigationToolbar2Tk(self.canvas, tb_frame)
        tb.configure(background=self.CLR_FRAME_BG)
        tb._message_label.config(background=self.CLR_FRAME_BG,
                                 foreground=self.CLR_FG)
        for b in tb.winfo_children():
            b.config(background=self.CLR_FRAME_BG)
        tb.update()
        return panel

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {msg}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    # -------------------------------------------------- live recalculation
    def _attach_traces(self):
        for var in (self.mt_on_var, self.mt_tlo_var, self.mt_thi_var,
                    self.mt_rate_var, self.zfc_var, self.fcc_var,
                    self.fcw_var, self.mt_fields_var, self.mt_soak_var,
                    self.mh_on_var, self.mh_hmax_var, self.mh_frate_var,
                    self.mh_pts_var, self.mh_spp_var, self.mh_temps_var,
                    self.di_on_var, self.di_fields_var, self.di_base_var,
                    self.di_top_var, self.di_warm_var, self.di_cool_var,
                    self.di_hold_var, self.di_soak_var, self.di_fscan_var,
                    self.di_fscan_temps_var, self.di_stab_var,
                    self.di_pts_var, self.di_delay_var,
                    self.start_time_var, self.init_temp_var,
                    self.final_temp_var, self.fast_rate_var,
                    self.slow_rate_var, self.slow_below_var,
                    self.field_rate_var, self.settle_var):
            var.trace_add('write', self._schedule_recalc)

    def _schedule_recalc(self, *_):
        if self._recalc_job is not None:
            self.root.after_cancel(self._recalc_job)
        self._recalc_job = self.root.after(500, self._recalc_now)

    def _parse_start(self):
        txt = self.start_time_var.get().strip().upper()
        for fmt in ("%Y-%m-%d %I:%M %p",   # 12-hour with AM/PM
                    "%Y-%m-%d %H:%M"):     # 24-hour still accepted
            try:
                return datetime.strptime(txt, fmt)
            except ValueError:
                continue
        raise ValueError(
            "Start time must be like 2026-07-11 09:30 PM "
            "(or 24-hour 21:30)")

    def _recalc_now(self):
        self._recalc_job = None
        try:
            start_dt = self._parse_start()
        except ValueError as e:
            self.big_var.set("…")
            self.detail_var.set(str(e))
            return
        try:
            self._build_plan()
        except ValueError as e:
            self.big_var.set("…")
            self.detail_var.set(str(e))
            return
        except Exception:
            self.big_var.set("…")
            self.detail_var.set("Estimation failed — see console.")
            self.log(traceback.format_exc())
            return
        self._update_summary(start_dt)
        self._plot_timeline(start_dt)
        self._populate_table(start_dt)

    @staticmethod
    def _fmt_dur(sec):
        sec = int(sec)
        h, m = divmod(sec // 60, 60)
        if h:
            return f"{h}h {m:02d}m"
        if m:
            return f"{m}m {sec % 60:02d}s"
        return f"{sec}s"

    @staticmethod
    def _parse_list(text):
        vals = []
        for part in text.replace(';', ',').split(','):
            part = part.strip()
            if part:
                vals.append(float(part))
        return vals

    # ------------------------------------------------------------ the plan
    def _build_plan(self):
        T = float(self.init_temp_var.get())
        H = 0.0
        fast = float(self.fast_rate_var.get())
        slow = float(self.slow_rate_var.get())
        below = float(self.slow_below_var.get())
        f_rate = float(self.field_rate_var.get())
        settle = float(self.settle_var.get())
        if min(fast, slow, f_rate) <= 0:
            raise ValueError("Cryostat ramp rates must be positive.")

        t = 0.0
        self.segments = []

        def add(dur, T1, H1, desc, kind='op'):
            nonlocal t, T, H
            self.segments.append(dict(t0=t, t1=t + dur, T0=T, T1=T1,
                                      H0=H, H1=H1, desc=desc, kind=kind))
            t += dur
            T, H = T1, H1

        def ramp_secs(T0, T1):
            # non-measuring ramp: slow rate below `below`, fast above
            lo, hi = sorted((T0, T1))
            s = 0.0
            if lo < below:
                s += (min(hi, below) - lo) / slow * 60.0
            if hi > below:
                s += (hi - max(lo, below)) / fast * 60.0
            return s

        def goto_T(target, why):
            if abs(T - target) > 0.5:
                word = "Cool" if target < T else "Warm"
                add(ramp_secs(T, target), target, H,
                    f"{word} {T:g} → {target:g} K ({why})")
                add(settle, T, H, "Wait for T stable (settle est.)",
                    kind='wait')

        def goto_H(target, why):
            if abs(H - target) > 0.5:
                add(abs(target - H) / f_rate, T, target,
                    f"Field {H:g} → {target:g} Oe ({why})")
                add(settle, T, H, "Wait for H stable (settle est.)",
                    kind='wait')

        # ---------------- M(T) blocks ----------------
        if self.mt_on_var.get():
            fields = self._parse_list(self.mt_fields_var.get())
            if not fields:
                raise ValueError("M(T) is ticked but no fields are given.")
            tlo = float(self.mt_tlo_var.get())
            thi = float(self.mt_thi_var.get())
            sweep = float(self.mt_rate_var.get())
            if not (tlo < thi):
                raise ValueError("M(T): the low temperature must be "
                                 "below the high one.")
            if sweep <= 0:
                raise ValueError("M(T): sweep rate must be positive.")
            zfc, fcc, fcw = (self.zfc_var.get(), self.fcc_var.get(),
                             self.fcw_var.get())
            if not (zfc or fcc or fcw):
                raise ValueError("M(T): tick at least one of ZFC/FCC/FCW.")
            soak = float(self.mt_soak_var.get() or 0)
            sweep_s = (thi - tlo) / sweep * 60.0

            def soak_at_base():
                if soak > 0:
                    add(soak, T, H,
                        f"Soak at {T:g} K ({soak:g} s)", kind='wait')

            for F in fields:
                names = [n for n, v in (('ZFC', zfc), ('FCC', fcc),
                                        ('FCW', fcw)) if v]
                add(0, T, H,
                    f"M(T) {'+'.join(names)} at {F:g} Oe", kind='blk')
                if zfc:
                    goto_H(0, "zero-field cool")
                    goto_T(tlo, "ZFC cooldown")
                    soak_at_base()
                    goto_H(F, "apply measuring field")
                    add(sweep_s, thi, H,
                        f"ZFC warming M(T) {tlo:g} → {thi:g} K "
                        f"@ {sweep:g} K/min", kind='meas')
                else:
                    goto_T(thi, "go to start")
                    goto_H(F, "apply measuring field")
                if fcc:
                    add(sweep_s, tlo, H,
                        f"FCC cooling M(T) {thi:g} → {tlo:g} K "
                        f"@ {sweep:g} K/min", kind='meas')
                    if fcw:
                        add(sweep_s, thi, H,
                            f"FCW warming M(T) {tlo:g} → {thi:g} K "
                            f"@ {sweep:g} K/min", kind='meas')
                elif fcw:
                    goto_T(tlo, "field cooldown (no meas.)")
                    soak_at_base()
                    add(sweep_s, thi, H,
                        f"FCW warming M(T) {tlo:g} → {thi:g} K "
                        f"@ {sweep:g} K/min", kind='meas')
                goto_H(0, "remove field")

        # ---------------- M(H) loops ----------------
        if self.mh_on_var.get():
            temps = self._parse_list(self.mh_temps_var.get())
            if not temps:
                raise ValueError(
                    "M(H) is ticked but no temperatures are given.")
            hmax = float(self.mh_hmax_var.get())
            lrate = float(self.mh_frate_var.get())
            npts = int(float(self.mh_pts_var.get() or 0))
            spp = float(self.mh_spp_var.get() or 0)
            if hmax <= 0 or lrate <= 0:
                raise ValueError("M(H): field and rate must be positive.")
            # full loop 0 → +H → −H → +H → 0 travels 6×Hmax
            loop_s = 6.0 * hmax / lrate + max(npts, 0) * spp

            for Ti in temps:
                add(0, T, H, f"M(H) loop at {Ti:g} K", kind='blk')
                goto_T(Ti, "go to loop temperature")
                mode = (f"{npts} stable pts" if npts > 0
                        else "continuous sweep")
                add(loop_s, T, 0,
                    f"M(H) loop ±{hmax:g} Oe @ {Ti:g} K ({mode})",
                    kind='meas')
                add(settle, T, 0, "Wait for H stable (settle est.)",
                    kind='wait')

        # ---------------- dielectric ε(T) runs + optional Fscan ----------
        if self.di_on_var.get():
            fields = self._parse_list(self.di_fields_var.get())
            if not fields:
                raise ValueError(
                    "Dielectric is ticked but no fields are given.")
            base = float(self.di_base_var.get())
            top = float(self.di_top_var.get())
            warm = float(self.di_warm_var.get())
            cool = float(self.di_cool_var.get())
            if not base < top:
                raise ValueError("Dielectric: base must be below top.")
            if warm <= 0 or cool <= 0:
                raise ValueError("Dielectric: warm/cool rates must be "
                                 "positive.")
            hold_s = float(self.di_hold_var.get() or 0) * 60.0
            soak_s = float(self.di_soak_var.get() or 0) * 60.0
            if hold_s < 0 or soak_s < 0:
                raise ValueError("Dielectric: hold/soak must be >= 0.")

            def di_cooldown(why):
                # The PPMS sequence owns this ramp — the dielectric cool
                # rate applies, not the cryostat move rates.
                if abs(T - base) > 0.5:
                    add(abs(T - base) / cool * 60.0, base, H,
                        f"Cool {T:g} → {base:g} K @ {cool:g} K/min "
                        f"({why})")
                if soak_s > 0:
                    add(soak_s, T, H,
                        f"Probe soak at base ({soak_s / 60:g} min)",
                        kind='wait')

            for F in fields:
                add(0, T, H, f"ε(T) warming run at {F:g} Oe", kind='blk')
                di_cooldown("dielectric cooldown")
                goto_H(F, "set measuring field at base")
                add((top - base) / warm * 60.0, top, H,
                    f"ε(T) warming {base:g} → {top:g} K @ {warm:g} K/min",
                    kind='meas')
                if hold_s > 0:
                    add(hold_s, T, H,
                        f"Hold at top ({hold_s / 60:g} min)", kind='wait')
                goto_H(0, "remove field")

            if self.di_fscan_var.get():
                temps = self._parse_list(self.di_fscan_temps_var.get())
                if not temps:
                    raise ValueError("Dielectric Fscan is ticked but no "
                                     "setpoints are given.")
                stab_s = float(self.di_stab_var.get() or 0) * 60.0
                npts = int(float(self.di_pts_var.get() or 377))
                fdelay = float(self.di_delay_var.get() or 0)
                if stab_s < 0 or npts <= 0 or fdelay < 0:
                    raise ValueError("Dielectric Fscan: check stabilize / "
                                     "points / delay.")
                sweep_s = estimate_fscan_sweep_seconds(
                    npts, self.di_aper_cb.get(), fdelay)
                add(0, T, H,
                    f"ε(f) step Fscan ({len(temps)} setpoints)",
                    kind='blk')
                di_cooldown("final cooldown before the Fscan")
                for Ti in temps:
                    if abs(T - Ti) > 0.5:
                        add(abs(Ti - T) / warm * 60.0, Ti, H,
                            f"Step to {Ti:g} K @ {warm:g} K/min")
                    if stab_s > 0:
                        add(stab_s, T, H,
                            f"Stabilize at {Ti:g} K "
                            f"({stab_s / 60:g} min)", kind='wait')
                    add(sweep_s, T, H,
                        f"Frequency sweep at {Ti:g} K ({npts} pts, "
                        f"{self.di_aper_cb.get()})", kind='meas')

        # ---------------- wrap up ----------------
        fin = self.final_temp_var.get().strip()
        if fin and abs(float(fin) - T) > 0.5:
            add(0, T, H, "End of run", kind='blk')
            goto_H(0, "remove field")
            goto_T(float(fin), "return sample")

        if not any(sg['kind'] == 'meas' for sg in self.segments):
            raise ValueError(
                "Nothing to measure yet — tick M(T), M(H) and/or ε(T).")

    # ------------------------------------------------------------- output
    def _update_summary(self, start_dt):
        segs = [sg for sg in self.segments if sg['kind'] != 'blk']
        total = segs[-1]['t1'] if segs else 0
        by = {}
        for sg in segs:
            by[sg['kind']] = by.get(sg['kind'], 0) + (sg['t1'] - sg['t0'])
        meas = by.get('meas', 0)
        n_meas = sum(1 for sg in segs if sg['kind'] == 'meas')
        n_blocks = sum(1 for sg in self.segments
                       if sg['kind'] == 'blk' and 'End of run'
                       not in sg['desc'])
        pct = (100 * meas / total) if total else 0
        finish = start_dt + timedelta(seconds=total)
        self.big_var.set(
            f"≈ {self._fmt_dur(total)}   —   finishes "
            f"{finish:%a %d %b, %I:%M %p}")
        self.detail_var.set(
            f"{n_blocks} block(s), {n_meas} measurement(s):  "
            f"measuring {self._fmt_dur(meas)} ({pct:.0f}%)  ·  "
            f"moving {self._fmt_dur(by.get('op', 0))}  ·  "
            f"waiting {self._fmt_dur(by.get('wait', 0))}.  "
            f"Real runs land within a few % of this "
            f"(calibrated on real .seq/.DAT pairs).")

    def _plot_timeline(self, start_dt):
        self.ax_T.clear()
        self.ax_H.clear()
        for ax in (self.ax_T, self.ax_H):
            ax.grid(True, linestyle='--', alpha=0.6)

        def dt(sec):
            return start_dt + timedelta(seconds=sec)

        self._plot_start = start_dt
        self._hl_spans = []
        total = self.segments[-1]['t1'] if self.segments else 0

        tT, yT, tH, yH = [], [], [], []
        for sg in self.segments:
            tT += [dt(sg['t0']), dt(sg['t1'])]
            yT += [sg['T0'], sg['T1']]
            tH += [dt(sg['t0']), dt(sg['t1'])]
            yH += [sg['H0'], sg['H1']]
            if sg['kind'] == 'meas' and sg['t1'] > sg['t0']:
                for ax in (self.ax_T, self.ax_H):
                    ax.axvspan(dt(sg['t0']), dt(sg['t1']),
                               color=self.CLR_MEAS, alpha=0.25)

        self.ax_T.plot(tT, yT, color=self.CLR_TEMP, lw=2)
        self.ax_H.plot(tH, yH, color=self.CLR_FIELD, lw=2)

        for sg in self.segments:
            w = sg['t1'] - sg['t0']
            if sg['kind'] == 'meas' and total and w / total > 0.04:
                label = sg['desc'].split(' ')[0]
                self.ax_T.annotate(
                    label, (dt((sg['t0'] + sg['t1']) / 2), 0.06),
                    xycoords=('data', 'axes fraction'),
                    ha='center', fontsize=9, fontweight='bold',
                    color=self.CLR_MEAS)
            if sg['H1'] != sg['H0'] and sg['H1']:
                self.ax_H.annotate(
                    f"{sg['H1']:g} Oe", (dt(sg['t1']), sg['H1']),
                    xytext=(4, 5), textcoords='offset points',
                    fontsize=8, color=self.CLR_FIELD)

        blks = [sg for sg in self.segments if sg['kind'] == 'blk']
        for k, sg in enumerate(blks):
            self.ax_T.axvline(dt(sg['t0']), color=self.CLR_FG,
                              ls=':', alpha=0.5)
            self.ax_T.annotate(
                sg['desc'], (dt(sg['t0']), 1.03 + 0.09 * (k % 2)),
                xycoords=('data', 'axes fraction'),
                fontsize=8, ha='left', fontweight='bold')

        from matplotlib.patches import Patch
        self.ax_T.legend(
            handles=[Patch(facecolor=self.CLR_MEAS, alpha=0.25,
                           label='instrument measuring')],
            loc='upper right', fontsize=8, framealpha=0.7)

        self.ax_T.set_ylabel("Temperature (K)")
        self.ax_H.set_ylabel("Field (Oe)")
        self.ax_H.set_xlabel("Time")
        self.ax_H.xaxis.set_major_formatter(
            mdates.DateFormatter('%d %b\n%I:%M %p'))
        self.figure.suptitle(
            f"Planned run  —  est. {self._fmt_dur(total)}",
            fontweight='bold')
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _on_step_select(self, event=None):
        if self._plot_start is None:
            return
        for art in self._hl_spans:
            try:
                art.remove()
            except (ValueError, NotImplementedError):
                pass
        self._hl_spans = []
        sel = self.tree.selection()
        span = None
        if sel:
            iid = sel[0]
            if iid.isdigit():
                sg = self.segments[int(iid)]
                span = (sg['t0'], sg['t1'])
            elif iid in self._blocks:
                span = self._blocks[iid]
        if span:
            d0 = self._plot_start + timedelta(seconds=span[0])
            d1 = self._plot_start + timedelta(seconds=span[1])
            for ax in (self.ax_T, self.ax_H):
                if span[1] > span[0]:
                    self._hl_spans.append(
                        ax.axvspan(d0, d1, color='#4A6B8A', alpha=0.30))
                else:
                    self._hl_spans.append(
                        ax.axvline(d0, color='#4A6B8A', lw=2, alpha=0.8))
        self.canvas.draw_idle()

    def _populate_table(self, start_dt):
        self.tree.delete(*self.tree.get_children())
        self._blocks = {}

        def clock(sec):
            dt = start_dt + timedelta(seconds=sec)
            days = (dt.date() - start_dt.date()).days
            return dt.strftime('%I:%M %p') + (f" +{days}d" if days else "")

        parent = ''
        blk_iid = None
        for idx, sg in enumerate(self.segments):
            if sg['kind'] == 'blk':
                blk_iid = f"blk{idx}"
                # end time is patched once the block's children are known
                parent = self.tree.insert(
                    '', 'end', iid=blk_iid, open=False,
                    values=(sg['desc'], clock(sg['t0']), '', ''),
                    tags=('blk',))
                self._blocks[blk_iid] = [sg['t0'], sg['t0']]
                continue
            dur = sg['t1'] - sg['t0']
            self.tree.insert(parent, 'end', iid=str(idx), values=(
                sg['desc'], clock(sg['t0']), clock(sg['t1']),
                self._fmt_dur(dur) if dur else ''),
                tags=(sg['kind'],))
            if blk_iid:
                self._blocks[blk_iid][1] = sg['t1']

        for iid, (t0, t1) in self._blocks.items():
            self.tree.set(iid, 'end', clock(t1))
            self.tree.set(iid, 'dur', self._fmt_dur(t1 - t0))


if __name__ == '__main__':
    root = tk.Tk()
    app = TimeEstimatorGUI(root)
    root.mainloop()
