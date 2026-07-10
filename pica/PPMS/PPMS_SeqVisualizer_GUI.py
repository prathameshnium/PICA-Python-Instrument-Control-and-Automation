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
        panel = ttk.Frame(parent, width=440)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

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

        ttk.Button(src, text="Parse & Visualize", style='Plot.TButton',
                   command=self.parse_and_plot).grid(
            row=7, column=0, columnspan=2, sticky='ew', padx=10, pady=10)

        # --- Step table ---
        steps = ttk.LabelFrame(panel, text="Protocol Steps")
        steps.grid(row=1, column=0, sticky='nsew', pady=5)
        steps.grid_columnconfigure(0, weight=1)
        steps.grid_rowconfigure(0, weight=1)
        cols = ('n', 'cmd', 'desc', 'start', 'end', 'dur')
        self.tree = ttk.Treeview(steps, columns=cols, show='headings',
                                 height=12)
        for c, w, t in [('n', 30, '#'), ('cmd', 55, 'Cmd'),
                        ('desc', 150, 'Description'), ('start', 55, 'Start'),
                        ('end', 55, 'End'), ('dur', 60, 'Duration')]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w')
        vsb = ttk.Scrollbar(steps, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(5, 0), pady=5)
        vsb.grid(row=0, column=1, sticky='ns', pady=5)

        # --- Console ---
        cons = ttk.LabelFrame(panel, text="Console")
        cons.grid(row=2, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            cons, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG,
            font=('Consolas', 9), wrap='word', borderwidth=0, height=6)
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
        t = 0.0  # elapsed seconds
        self.segments = []   # dicts: t0,t1,T0,T1,H0,H1,cmd,desc,kind
        self.annotations = []  # (t, text) from REM lines

        def add(dur, T1, H1, cmd, desc, kind='op'):
            nonlocal t, T, H
            self.segments.append(dict(t0=t, t1=t + dur, T0=T, T1=T1,
                                      H0=H, H1=H1, cmd=cmd, desc=desc,
                                      kind=kind))
            t += dur
            T, H = T1, H1

        with open(self.filepath, 'r', encoding='utf-8',
                  errors='ignore') as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                tok = s.split()
                cmd = tok[0].upper()

                if cmd == 'REM':
                    self.annotations.append(
                        (t, s[4:].strip(' -.')))
                elif cmd == 'TMP':
                    # TMP TEMP <target K> <rate K/min> <mode>
                    target, rate = float(tok[2]), float(tok[3])
                    dur = abs(target - T) / rate * 60.0 if rate > 0 else 0.0
                    add(dur, target, H, 'TMP',
                        f"T → {target:g} K @ {rate:g} K/min")
                elif cmd == 'FLD':
                    # FLD FIELD <target Oe> <rate Oe/s> <approach> <mode>
                    # approach: 0=Linear, 1=No overshoot, 2=Oscillate
                    target, rate = float(tok[2]), float(tok[3])
                    dur = abs(target - H) / rate if rate > 0 else 0.0
                    approach = tok[4] if len(tok) > 4 else '0'
                    note = {'1': ', no o/s', '2': ', oscillate'}.get(
                        approach, '')
                    add(dur, T, target, 'FLD',
                        f"H → {target:g} Oe @ {rate:g} Oe/s{note}")
                elif cmd == 'WAI':
                    # WAI WAITFOR <delay s> <T> <H> <pos> ... stability flags
                    # delay starts only AFTER the flagged items are stable,
                    # so add a user-adjustable settle estimate per flag set.
                    delay = float(tok[2])
                    flags = []
                    if len(tok) > 3 and tok[3] == '1':
                        flags.append('T stable')
                    if len(tok) > 4 and tok[4] == '1':
                        flags.append('H stable')
                    if len(tok) > 5 and tok[5] == '1':
                        flags.append('pos.')
                    dur = delay + (settle if flags else 0.0)
                    extra = (f" after {', '.join(flags)} "
                             f"(+{settle:g} s est.)" if flags else "")
                    add(dur, T, H, 'WAI', f"Wait {delay:g} s{extra}",
                        kind='wait')
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
                            f"M(T) {T_start:g} → {T_end:g} K "
                            f"@ {rate:g} K/min", kind='meas')
                    else:
                        add(0, T, H, 'VSMMT',
                            "M(T) (unrecognized params!)", kind='warn')
                        self.log("WARNING: could not parse VSMMT sweep "
                                 "parameters — duration not included.")
                elif cmd == 'VSMMH':
                    # M(H) loop at fixed T; duration from user estimate
                    add(mh_est, T, H, 'VSMMH',
                        f"M(H) loop @ {T:g} K (est.)", kind='meas')
                elif cmd == 'CALL':
                    add(0, T, H, 'CALL', "Sub-sequence (not expanded!)",
                        kind='warn')
                    self.log("WARNING: CALL to sub-sequence found — its "
                             "duration is NOT included.")
                elif cmd == 'VSMDF':
                    m = re.search(r'"([^"]+)"', s)
                    fname = os.path.basename(m.group(1)) if m else '?'
                    add(0, T, H, 'VSMDF', f"Data file: {fname}")
                elif cmd in ('VSMLS', 'VSMCM'):
                    add(0, T, H, cmd, "VSM config/centering")
                else:
                    self.log(f"Unknown command skipped: {cmd}")

        total = timedelta(seconds=t)
        self.log(f"Parsed {len(self.segments)} steps. "
                 f"Estimated total duration: {total} "
                 f"(ends {start_dt + total:%Y-%m-%d %H:%M}).")

    # ------------------------------------------------------------- Plotting
    def _plot_timeline(self, start_dt):
        self.ax_T.clear()
        self.ax_H.clear()
        for ax in (self.ax_T, self.ax_H):
            ax.grid(True, linestyle='--', alpha=0.6)

        def dt(sec):
            return start_dt + timedelta(seconds=sec)

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

        for tsec, text in self.annotations:
            self.ax_T.axvline(dt(tsec), color=self.CLR_FG, ls=':', alpha=0.5)
            self.ax_T.annotate(text, (dt(tsec), 1.02),
                               xycoords=('data', 'axes fraction'),
                               rotation=20, fontsize=8, ha='left')

        self.ax_T.set_ylabel("Temperature (K)")
        self.ax_H.set_ylabel("Field (Oe)")
        self.ax_H.set_xlabel("Time")
        self.ax_H.xaxis.set_major_formatter(
            mdates.DateFormatter('%d %b\n%H:%M'))
        total = self.segments[-1]['t1'] if self.segments else 0
        self.figure.suptitle(
            f"{os.path.basename(self.filepath)}  —  est. "
            f"{timedelta(seconds=int(total))}", fontweight='bold')
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _populate_table(self):
        self.tree.delete(*self.tree.get_children())
        start_dt = datetime.strptime(self.start_time_var.get().strip(),
                                     "%Y-%m-%d %H:%M")
        def clock(sec):
            dt = start_dt + timedelta(seconds=sec)
            days = (dt.date() - start_dt.date()).days
            return dt.strftime('%H:%M') + (f" +{days}d" if days else "")

        for i, sg in enumerate(self.segments, 1):
            dur = sg['t1'] - sg['t0']
            self.tree.insert('', 'end', values=(
                i, sg['cmd'], sg['desc'],
                clock(sg['t0']), clock(sg['t1']),
                str(timedelta(seconds=int(dur)))))


if __name__ == '__main__':
    root = tk.Tk()
    app = SeqVisualizerGUI(root)
    root.mainloop()