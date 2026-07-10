"""
Module: SeqVisualizer_GUI.py
Purpose: PPMS sequence (.seq) protocol visualizer - PICA suite style.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import re
import traceback
from datetime import datetime, timedelta
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.dates as mdates
import matplotlib as mpl


class SeqVisualizerGUI:
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

    CLR_TEMP = '#8A5A3B'   # temperature trace
    CLR_FIELD = '#4A6B8A'  # field trace
    CLR_MEAS = '#BA6B5E'   # measurement shading

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA PPMS Sequence Visualizer v{self.PROGRAM_VERSION}")
        self.root.geometry("1500x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.filepath = None
        self.segments = []   # parsed timeline segments
        self.setup_styles()
        self.create_widgets()
        self.log("Welcome. Select a PPMS .seq file to visualize the protocol.")

    # ------------------------------------------------------------------ UI
    def setup_styles(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('.', background=self.CLR_BG,
                             foreground=self.CLR_FG, font=self.FONT_BASE)
        self.style.configure('TFrame', background=self.CLR_BG)
        self.style.configure('TLabel', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG)
        self.style.configure('Header.TLabel', background=self.CLR_HEADER)
        self.style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG,
                             foreground=self.CLR_FG, insertcolor=self.CLR_FG)
        self.style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG)
        self.style.map('TCheckbutton',
                       background=[('active', self.CLR_FRAME_BG)])
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
        self.style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                             bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG, font=self.FONT_TITLE)
        self.style.configure('Treeview', background=self.CLR_INPUT_BG,
                             fieldbackground=self.CLR_INPUT_BG,
                             foreground=self.CLR_FG, rowheight=22,
                             font=('Segoe UI', 9))
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

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)
        ttk.Label(header, text="PICA PPMS Sequence Visualizer",
                  style='Header.TLabel',
                  font=('Segoe UI', 15, 'bold'),
                  foreground=self.CLR_ACCENT_GOLD).pack(side='left',
                                                        padx=20, pady=10)
        ttk.Label(header, text="UGC-DAE Consortium for Scientific Research, "
                               "Mumbai Centre",
                  style='Header.TLabel',
                  font=('Segoe UI', 13)).pack(side='left', padx=15)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        main_pane.add(self._create_left_panel(main_pane), weight=1)
        main_pane.add(self._create_right_panel(main_pane), weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=520)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=5)
        panel.grid_rowconfigure(3, weight=1)

        # --- Source & timing ---
        src = ttk.LabelFrame(panel, text="Sequence Source & Timing")
        src.grid(row=0, column=0, sticky='new', pady=5)
        src.grid_columnconfigure(1, weight=1)

        ttk.Button(src, text="Select .seq File...",
                   command=self.browse_file).grid(row=0, column=0,
                                                  columnspan=2, sticky='ew',
                                                  padx=10, pady=(8, 4))
        self.file_var = tk.StringVar(value="(no file selected)")
        ttk.Label(src, textvariable=self.file_var, wraplength=380,
                  font=('Segoe UI', 9, 'italic')).grid(
            row=1, column=0, columnspan=2, sticky='w', padx=10)

        ttk.Label(src, text="Start Time:").grid(row=2, column=0, sticky='w',
                                                padx=10, pady=4)
        self.start_time_var = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d %H:%M"))
        ttk.Entry(src, textvariable=self.start_time_var).grid(
            row=2, column=1, sticky='ew', padx=10, pady=4)

        ttk.Label(src, text="Initial Temp (K):").grid(row=3, column=0,
                                                      sticky='w', padx=10)
        self.init_temp_var = tk.StringVar(value="300")
        ttk.Entry(src, textvariable=self.init_temp_var, width=10).grid(
            row=3, column=1, sticky='w', padx=10)

        ttk.Label(src, text="Initial Field (Oe):").grid(row=4, column=0,
                                                        sticky='w', padx=10)
        self.init_field_var = tk.StringVar(value="0")
        ttk.Entry(src, textvariable=self.init_field_var, width=10).grid(
            row=4, column=1, sticky='w', padx=10)

        ttk.Label(src, text="VSM M(H) est. (s):").grid(row=5, column=0,
                                                       sticky='w', padx=10)
        self.mh_time_var = tk.StringVar(value="1800")
        ttk.Entry(src, textvariable=self.mh_time_var, width=10).grid(
            row=5, column=1, sticky='w', padx=10)

        ttk.Label(src, text="Stability settle est. (s):").grid(
            row=6, column=0, sticky='w', padx=10)
        self.settle_var = tk.StringVar(value="300")
        ttk.Entry(src, textvariable=self.settle_var, width=10).grid(
            row=6, column=1, sticky='w', padx=10)

        ttk.Label(src, text="AC meas. per freq. (s):").grid(
            row=7, column=0, sticky='w', padx=10)
        self.ac_freq_var = tk.StringVar(value="40")
        ttk.Entry(src, textvariable=self.ac_freq_var, width=10).grid(
            row=7, column=1, sticky='w', padx=10)

        ttk.Button(src, text="Parse & Visualize", style='Plot.TButton',
                   command=self.parse_and_plot).grid(
            row=8, column=0, columnspan=2, sticky='ew', padx=10, pady=10)

        # --- Plain-language summary ---
        summ = ttk.LabelFrame(panel, text="At a Glance")
        summ.grid(row=1, column=0, sticky='new', pady=5)
        self.summary_var = tk.StringVar(
            value="Load a sequence to see what it does and how long "
                  "it will take.")
        ttk.Label(summ, textvariable=self.summary_var, wraplength=400,
                  justify='left', font=('Segoe UI', 10)).pack(
            fill='x', padx=10, pady=6)

        # --- Step table ---
        steps = ttk.LabelFrame(panel,
                               text="Protocol Steps  (click a row to "
                                    "highlight it on the plot)")
        steps.grid(row=2, column=0, sticky='nsew', pady=5)
        steps.grid_columnconfigure(0, weight=1)
        steps.grid_rowconfigure(0, weight=1)
        self.raw_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(steps,
                        text="Show raw .seq commands (as written in file)",
                        variable=self.raw_var,
                        command=self._refresh_table_view).grid(
            row=1, column=0, columnspan=2, sticky='w', padx=5, pady=(0, 5))
        cols = ('n', 'cmd', 'desc', 'start', 'end', 'dur')
        self.tree = ttk.Treeview(steps, columns=cols, show='headings',
                                 height=22)
        for c, w, t in [('n', 30, '#'), ('cmd', 60, 'Cmd'),
                        ('desc', 260, 'Description'), ('start', 60, 'Start'),
                        ('end', 60, 'End'), ('dur', 65, 'Duration')]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w')
        vsb = ttk.Scrollbar(steps, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(5, 0), pady=5)
        vsb.grid(row=0, column=1, sticky='ns', pady=5)
        # row colors by what the step does
        self.tree.tag_configure('meas', background='#F3DBD4')  # measuring
        self.tree.tag_configure('wait', background='#EDEAE6',
                                foreground='#6B655F')          # waiting
        self.tree.tag_configure('warn', background='#E8C0B8')  # attention
        self.tree.tag_configure('off', foreground='#9A928A')   # disabled
        self.tree.tag_configure('rem', background=self.CLR_HEADER,
                                font=('Segoe UI', 9, 'bold'))  # section
        self.tree.bind('<<TreeviewSelect>>', self._on_step_select)

        # --- Console ---
        cons = ttk.LabelFrame(panel, text="Console")
        cons.grid(row=3, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            cons, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG,
            font=('Consolas', 9), wrap='word', borderwidth=0, height=4)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        container = ttk.LabelFrame(panel, text='Protocol Timeline')
        container.pack(fill='both', expand=True)

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

    # ------------------------------------------------------------- Parsing
    def browse_file(self):
        fp = filedialog.askopenfilename(
            title="Select PPMS sequence file",
            filetypes=(("Sequence Files", "*.seq *.txt *.dat"),
                       ("All files", "*.*")))
        if fp:
            self.filepath = fp
            self.file_var.set(os.path.basename(fp))
            self.parse_and_plot()

    def parse_and_plot(self):
        if not self.filepath:
            messagebox.showinfo("No file", "Please select a .seq file first.")
            return
        try:
            start_dt = datetime.strptime(self.start_time_var.get().strip(),
                                         "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Time Format",
                                 "Start time must be YYYY-MM-DD HH:MM")
            return
        try:
            self._parse_sequence(start_dt)
            self._plot_timeline(start_dt)
            self._populate_table()
        except Exception as e:
            self.log(f"Error: {traceback.format_exc()}")
            messagebox.showerror("Parse Error", str(e))

    def _parse_sequence(self, start_dt):
        T = float(self.init_temp_var.get())
        H = float(self.init_field_var.get())
        mh_est = float(self.mh_time_var.get())
        settle = float(self.settle_var.get())
        ac_per_freq = float(self.ac_freq_var.get())
        t = 0.0  # elapsed seconds
        self.segments = []   # dicts: t0,t1,T0,T1,H0,H1,cmd,desc,kind

        cur_raw = ['']  # raw .seq line for the segment(s) being added

        def add(dur, T1, H1, cmd, desc, kind='op'):
            nonlocal t, T, H
            self.segments.append(dict(t0=t, t1=t + dur, T0=T, T1=T1,
                                      H0=H, H1=H1, cmd=cmd, desc=desc,
                                      kind=kind, raw=cur_raw[0]))
            t += dur
            T, H = T1, H1

        def acms_freqs(line):
            # frequency list is the quoted comma-separated token
            for part in re.findall(r'"([^"]*)"', line):
                if ',' in part:
                    fl = [x.strip() for x in part.split(',') if x.strip()]
                    if fl:
                        return fl
            return []

        with open(self.filepath, 'r', encoding='utf-8',
                  errors='ignore') as f:
            lines = [ln.strip() for ln in f]

        i = 0
        while i < len(lines):
            s = lines[i]
            i += 1
            if not s:
                continue
            cur_raw[0] = s
            if s.startswith('!'):
                # MultiVu saves disabled sequence lines with a leading '!'
                add(0, T, H, 'off',
                    f"(disabled) {s[1:].strip()[:70]}", kind='off')
                continue
            tok = s.split()
            cmd = tok[0].upper()

            if cmd == 'REM':
                add(0, T, H, 'REM', s[4:].strip(' -.'), kind='rem')
            elif cmd == 'TMP':
                # TMP TEMP <target K> <rate K/min> <mode>
                target, rate = float(tok[2]), float(tok[3])
                dur = abs(target - T) / rate * 60.0 if rate > 0 else 0.0
                mode = {'0': 'Fast Settle', '1': "No O'Shoot"}.get(
                    tok[4] if len(tok) > 4 else '0', '?')
                word = "cool" if target < T else "warm"
                add(dur, target, H, 'TMP',
                    f"Set Temperature {target:g}K at {rate:g}K/min, "
                    f"{mode}  ({word} from {T:g}K)")
            elif cmd == 'FLD':
                # FLD FIELD <target Oe> <rate Oe/s> <approach> <mode>
                target, rate = float(tok[2]), float(tok[3])
                dur = abs(target - H) / rate if rate > 0 else 0.0
                appr = {'0': 'Linear', '1': "No O'Shoot",
                        '2': 'Oscillate'}.get(
                    tok[4] if len(tok) > 4 else '0', '?')
                endm = {'0': 'Persistent', '1': 'Driven'}.get(
                    tok[5] if len(tok) > 5 else '0', '?')
                add(dur, T, target, 'FLD',
                    f"Set Magnetic Field {target:g}Oe at {rate:g}Oe/sec, "
                    f"{appr}, {endm}")
            elif cmd == 'WAI':
                # WAI WAITFOR <delay s> <T> <H> <pos> ... stability flags
                # delay starts only AFTER the flagged items are stable,
                # so add a user-adjustable settle estimate per flag set.
                delay = float(tok[2])
                names = {3: 'Temperature', 4: 'Field', 5: 'Position'}
                flags = [names[j] for j in (3, 4, 5)
                         if len(tok) > j and tok[j] == '1']
                dur = delay + (settle if flags else 0.0)
                base = (f"Wait For {', '.join(flags)} — " if flags
                        else "Wait — ")
                extra = f" (+{settle:g}s settle est.)" if flags else ""
                add(dur, T, H, 'WAI',
                    f"{base}Delay {delay:g} secs{extra}", kind='wait')
            elif cmd == 'VSMMT':
                # VSMMT moment-vs-temperature sweep. Validated against
                # MultiVu data files: tok[12]=start K, tok[13]=end K,
                # tok[14]=rate K/min; T ramps start→end during the scan.
                ok = False
                if len(tok) > 14:
                    try:
                        T_start = float(tok[12])
                        T_end = float(tok[13])
                        rate = float(tok[14])
                        ok = (0 < T_start < 1100 and 0 < T_end < 1100
                              and 0 < rate <= 50)
                    except ValueError:
                        ok = False
                if ok:
                    if abs(T - T_start) > 1.0:
                        pre = abs(T_start - T) / rate * 60.0
                        add(pre, T_start, H, 'VSMMT',
                            f"go to M(T) start {T_start:g} K",
                            kind='meas')
                        self.log(f"Note: M(T) starts at {T_start:g} K "
                                 f"but sequence T is {T:g} K — added "
                                 "implicit ramp.")
                    dur = abs(T_end - T_start) / rate * 60.0
                    add(dur, T_end, H, 'VSMMT',
                        f"VSM: Moment vs Temperature {T_start:g}K to "
                        f"{T_end:g}K at {rate:g}K/min", kind='meas')
                else:
                    add(0, T, H, 'VSMMT',
                        "VSM: Moment vs Temperature "
                        "(unrecognized params!)", kind='warn')
                    self.log("WARNING: could not parse VSMMT sweep "
                             "parameters — duration not included.")
            elif cmd == 'VSMMH':
                # M(H) loop at fixed T; duration from user estimate
                add(mh_est, T, H, 'VSMMH',
                    f"VSM: Moment vs Field at {T:g}K (user est.)",
                    kind='meas')
            elif cmd == 'ACMSAC':
                # AC susceptibility at current T, one point per frequency
                fl = acms_freqs(s)
                n = len(fl) if fl else 1
                rng = f" ({fl[0]}–{fl[-1]} Hz)" if fl else ""
                add(n * ac_per_freq, T, H, 'ACMSAC',
                    f"ACMS: AC Susceptibility — {n} frequencies{rng} "
                    f"at {T:g}K", kind='meas')
            elif cmd == 'ACMSLS':
                # ACMSLS <..> <..> <amplitude Oe> <freq Hz>: locate sample
                amp = tok[3] if len(tok) > 3 else '?'
                frq = tok[4] if len(tok) > 4 else '?'
                add(60, T, H, 'ACMSLS',
                    f"ACMS: Locate Sample ({amp}Oe, {frq}Hz, ~60 s)")
            elif cmd == 'LPT' and len(tok) > 7 and \
                    tok[1].upper() in ('SCANT', 'SCANH'):
                # LPT SCANT <start> <end> <rate> <npts> <spacing> <approach>
                # approach 2 = continuous sweep (measure on the fly);
                # 0/1 = stepped scan (settle + measure at each point).
                # Body commands until ENT EOS run at each scan point.
                what = tok[1].upper()
                v0, v1 = float(tok[2]), float(tok[3])
                rate, npts = float(tok[4]), int(float(tok[5]))
                approach = tok[7]
                body = []
                depth = 1
                while i < len(lines):
                    b = lines[i]
                    i += 1
                    if not b or b.startswith('!'):
                        continue
                    bt = b.split()[0].upper()
                    if bt in ('LPT', 'LPB'):
                        depth += 1
                        self.log("WARNING: nested scan loop — inner "
                                 "loop timing not expanded.")
                    elif bt == 'ENT':
                        depth -= 1
                        if depth == 0:
                            break
                    elif depth == 1:
                        body.append(b)
                body_t = 0.0
                for b in body:
                    bt = b.split()[0].upper()
                    if bt == 'ACMSAC':
                        fl = acms_freqs(b)
                        body_t += (len(fl) if fl else 1) * ac_per_freq
                    elif bt == 'VSMMH':
                        body_t += mh_est
                    elif bt == 'WAI':
                        body_t += float(b.split()[2])
                # raw view: show the whole loop incl. consumed body lines
                cur_raw[0] = '  ▸  '.join([s] + body + ['ENT EOS'])
                thing, unit, rate_u = (('Temperature', 'K', 'K/min')
                                       if what == 'SCANT'
                                       else ('Field', 'Oe', 'Oe/s'))
                mode_n = {'0': 'Fast', '1': "No O'Shoot",
                          '2': 'Sweep'}.get(approach, '?')
                span = abs(v1 - v0)
                ramp_s = (span / rate * 60.0 if what == 'SCANT'
                          else span / rate) if rate > 0 else 0.0
                base = (f"Scan {thing} {v0:g}{unit} to {v1:g}{unit} "
                        f"at {rate:g}{rate_u}, {npts} steps, {mode_n}")
                if approach == '2':
                    # continuous sweep: duration set by ramp rate only
                    dur = ramp_s
                    n_est = int(dur / body_t) if body_t > 0 else npts
                    desc = f"{base} — measuring on the fly (~{n_est} pts)"
                else:
                    dur = ramp_s + npts * (settle + body_t)
                    desc = f"{base} — settle + measure at each step"
                if what == 'SCANT':
                    if abs(T - v0) > 1.0 and rate > 0:
                        add(abs(v0 - T) / rate * 60.0, v0, H, 'LPT',
                            f"go to scan start {v0:g} K")
                    add(dur, v1, H, 'LPT', desc, kind='meas')
                else:
                    if abs(H - v0) > 1.0 and rate > 0:
                        add(abs(v0 - H) / rate, T, v0, 'LPT',
                            f"go to scan start {v0:g} Oe")
                    add(dur, T, v1, 'LPT', desc, kind='meas')
            elif cmd == 'ENT':
                pass  # loop terminator without a matching LPT
            elif cmd == 'CALL':
                add(0, T, H, 'CALL',
                    "Call Sequence (not expanded — time NOT included!)",
                    kind='warn')
                self.log("WARNING: CALL to sub-sequence found — its "
                         "duration is NOT included.")
            elif cmd in ('VSMDF', 'ACMSDF'):
                m = re.search(r'"([^"]+)"', s)
                fname = os.path.basename(m.group(1)) if m else '?'
                add(0, T, H, cmd, f"New Datafile: {fname}")
            elif cmd == 'VSMLS':
                add(0, T, H, cmd, "VSM: Locate Sample")
            elif cmd == 'VSMCM':
                add(0, T, H, cmd, "VSM: Center Sample")
            else:
                self.log(f"Unknown command skipped: {cmd}")

        total = timedelta(seconds=t)
        self.log(f"Parsed {len(self.segments)} steps. "
                 f"Estimated total duration: {total} "
                 f"(ends {start_dt + total:%Y-%m-%d %H:%M}).")
        self._update_summary(start_dt)

    @staticmethod
    def _fmt_dur(sec):
        sec = int(sec)
        h, m = divmod(sec // 60, 60)
        if h:
            return f"{h}h {m:02d}m"
        if m:
            return f"{m}m {sec % 60:02d}s"
        return f"{sec}s"

    def _update_summary(self, start_dt):
        segs = [sg for sg in self.segments
                if sg['kind'] not in ('rem', 'off')]
        if not segs:
            self.summary_var.set("No timed steps found in this sequence.")
            return
        total = segs[-1]['t1']
        by = {}
        for sg in segs:
            by[sg['kind']] = by.get(sg['kind'], 0) + (sg['t1'] - sg['t0'])
        meas = by.get('meas', 0)
        ramp = by.get('op', 0)
        wait = by.get('wait', 0)
        n_meas = sum(1 for sg in segs
                     if sg['kind'] == 'meas' and sg['t1'] > sg['t0'])
        temps = [x for sg in segs for x in (sg['T0'], sg['T1'])]
        fields = sorted({sg['H1'] for sg in segs})
        f_txt = ', '.join(f"{f:g}" for f in fields)
        pct = (100 * meas / total) if total else 0
        self.summary_var.set(
            f"Total ≈ {self._fmt_dur(total)}   →   ends "
            f"{start_dt + timedelta(seconds=total):%a %d %b, %H:%M}\n"
            f"Measuring {self._fmt_dur(meas)} ({pct:.0f}%)  ·  "
            f"Ramping {self._fmt_dur(ramp)}  ·  "
            f"Waiting {self._fmt_dur(wait)}\n"
            f"{n_meas} measurement(s)  ·  "
            f"T: {min(temps):g}–{max(temps):g} K  ·  "
            f"Fields: {f_txt} Oe")

    # ------------------------------------------------------------- Plotting
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

        # label wide measurement segments so the shading is self-explaining
        MEAS_LABEL = {'VSMMT': 'M(T)', 'VSMMH': 'M(H)',
                      'ACMSAC': 'AC χ', 'LPT': 'AC χ scan'}
        for sg in self.segments:
            w = sg['t1'] - sg['t0']
            if sg['kind'] == 'meas' and total and w / total > 0.04:
                label = MEAS_LABEL.get(sg['cmd'], 'meas.')
                self.ax_T.annotate(
                    label, (dt((sg['t0'] + sg['t1']) / 2), 0.06),
                    xycoords=('data', 'axes fraction'),
                    ha='center', fontsize=9, fontweight='bold',
                    color=self.CLR_MEAS)

        # label field plateaus at each field change (skip returns to zero)
        for sg in self.segments:
            if sg['cmd'] == 'FLD' and sg['H1'] != sg['H0'] and sg['H1']:
                self.ax_H.annotate(
                    f"{sg['H1']:g} Oe", (dt(sg['t1']), sg['H1']),
                    xytext=(4, 5), textcoords='offset points',
                    fontsize=8, color=self.CLR_FIELD)

        # section banners from REM lines, staggered to avoid overlap
        rems = [sg for sg in self.segments if sg['kind'] == 'rem']
        for k, sg in enumerate(rems):
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
            mdates.DateFormatter('%d %b\n%H:%M'))
        self.figure.suptitle(
            f"{os.path.basename(self.filepath)}  —  est. "
            f"{self._fmt_dur(total)}", fontweight='bold')
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _on_step_select(self, event=None):
        """Highlight the selected table step on the timeline plot."""
        if getattr(self, '_plot_start', None) is None:
            return
        for art in self._hl_spans:
            try:
                art.remove()
            except (ValueError, NotImplementedError):
                pass
        self._hl_spans = []
        sel = self.tree.selection()
        if sel and sel[0].isdigit():
            sg = self.segments[int(sel[0])]
            d0 = self._plot_start + timedelta(seconds=sg['t0'])
            d1 = self._plot_start + timedelta(seconds=sg['t1'])
            for ax in (self.ax_T, self.ax_H):
                if sg['t1'] > sg['t0']:
                    self._hl_spans.append(
                        ax.axvspan(d0, d1, color='#4A6B8A', alpha=0.30))
                else:
                    self._hl_spans.append(
                        ax.axvline(d0, color='#4A6B8A', lw=2, alpha=0.8))
        self.canvas.draw_idle()

    def _refresh_table_view(self):
        if self.segments:
            try:
                self._populate_table()
            except ValueError:
                pass  # invalid start-time entry; table refreshes next parse

    def _populate_table(self):
        self.tree.delete(*self.tree.get_children())
        start_dt = datetime.strptime(self.start_time_var.get().strip(),
                                     "%Y-%m-%d %H:%M")
        raw_mode = self.raw_var.get()

        def clock(sec):
            dt = start_dt + timedelta(seconds=sec)
            days = (dt.date() - start_dt.date()).days
            return dt.strftime('%H:%M') + (f" +{days}d" if days else "")

        def text(sg):
            return sg.get('raw') or sg['desc'] if raw_mode else sg['desc']

        n = 0
        for idx, sg in enumerate(self.segments):
            if sg['kind'] == 'rem':
                desc = (sg['raw'] if raw_mode
                        else f"—— {sg['desc']} ——")
                self.tree.insert('', 'end', iid=f"rem{idx}", values=(
                    '', '', desc, clock(sg['t0']), '', ''),
                    tags=('rem',))
                continue
            n += 1
            dur = sg['t1'] - sg['t0']
            self.tree.insert('', 'end', iid=str(idx), values=(
                n, sg['cmd'] if sg['kind'] != 'off' else '—', text(sg),
                clock(sg['t0']), clock(sg['t1']),
                self._fmt_dur(dur) if dur else ''),
                tags=(sg['kind'],))


if __name__ == '__main__':
    root = tk.Tk()
    app = SeqVisualizerGUI(root)
    root.mainloop()