"""
Module: PID_Simulator_GUI.py
Purpose: Multi-PID Setpoint Simulator for Cryogenic Setups (Part of PICA suite).
Models: CCR / LN2 immersion probe with variable cooling power & vacuum level.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib as mpl


class PIDSimulatorGUI:
    PROGRAM_VERSION = "2.0"

    # --- PICA Styling ---
    CLR_BG = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    FONT_BASE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 12, 'bold')

    PRESETS = {
        "Slow (P=0.5, I=4, D=0)":   (0.5, 4.0, 0.0),
        "Medium (P=20, I=15, D=0)": (20.0, 15.0, 0.0),
        "Fast (P=50, I=20, D=0)":   (50.0, 20.0, 0.0),
    }

    PLOT_COLORS = ['#2C2825', '#BA6B5E', '#4A6B8A', '#5E8A5E',
                   '#8A5E8A', '#B68B6E', '#C44536', '#3D6B6B']

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Multi-PID Simulator v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.pid_entries = {}   # {id: {'var': BoolVar, 'frame':, 'params': (kp,ki,kd), 'label': str}}
        self._pid_counter = 0

        self.setup_styles()
        self.create_widgets()
        self.log("Welcome to the PICA Multi-PID Simulator (Cryogenic Model).")
        self.log("Add PID sets via presets or custom values, adjust environment, then Run.")

        # Load the three presets by default
        for name, (kp, ki, kd) in self.PRESETS.items():
            self._add_pid_entry(kp, ki, kd, name)
        self.run_simulation()

    def log(self, msg: str) -> None:
        """Append a timestamped line to the console ScrolledText."""
        ts = datetime.now().strftime("[%H:%M:%S]")
        if not hasattr(self, 'console'):
            print(f"{ts} {msg}") # fallback before UI is built
            return
        self.console.configure(state='normal')
        self.console.insert('end', f"{ts} {msg}\n")
        self.console.see('end')
        self.console.configure(state='disabled')
    def setup_styles(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('.', background=self.CLR_BG, foreground=self.CLR_FG, font=self.FONT_BASE)
        self.style.configure('TFrame', background=self.CLR_BG)
        self.style.configure('TPanedWindow', background=self.CLR_BG)
        self.style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        self.style.configure('Header.TLabel', background=self.CLR_HEADER)
        self.style.configure('TButton', font=self.FONT_BASE, padding=(8, 5),
                             foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        self.style.map('TButton',
                       background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                       foreground=[('active', self.CLR_BG), ('hover', self.CLR_BG)])
        self.style.map('TCombobox', fieldbackground=[('readonly', self.CLR_INPUT_BG)])
        self.style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG, font=self.FONT_TITLE)
        self.style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG)
        self.style.configure('Input.TFrame', background=self.CLR_INPUT_BG)
        self.style.configure('Input.TCheckbutton', background=self.CLR_INPUT_BG)
        self.style.configure('Input.TLabel', background=self.CLR_INPUT_BG)
        self.style.configure('TScale', background=self.CLR_FRAME_BG)

        mpl.rcParams.update({
            'font.family': 'Segoe UI', 'font.size': 11,
            'axes.titlesize': 14, 'axes.labelsize': 12,
            'figure.facecolor': self.CLR_BG, 'axes.facecolor': '#F4EFEA',
            'axes.edgecolor': self.CLR_FG, 'axes.labelcolor': self.CLR_FG,
            'text.color': self.CLR_FG, 'xtick.color': self.CLR_FG, 'ytick.color': self.CLR_FG,
            'mathtext.fontset': 'cm'
        })

    def create_widgets(self):
        # --- Header ---
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)
        left_hdr = tk.Frame(header, bg=self.CLR_HEADER)
        left_hdr.pack(side='left')
        ttk.Label(left_hdr, text="Multi-PID Setpoint Simulator", style='Header.TLabel',
                  font=('Segoe UI', 14, 'bold'), foreground=self.CLR_ACCENT_GOLD
                  ).pack(anchor='w', padx=20, pady=(10, 0))
        ttk.Label(left_hdr, text="Cryogenic Model — CCR / LN2 Probe (PICA Suite)",
                  style='Header.TLabel', font=('Segoe UI', 10, 'italic')
                  ).pack(anchor='w', padx=20, pady=(0, 10))
        ctr = tk.Frame(header, bg=self.CLR_HEADER)
        ctr.pack(side='left', padx=40)
        ttk.Label(ctr, text="UGC-DAE Consortium for Scientific Research",
                  style='Header.TLabel', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(ctr, text="Mumbai Centre", style='Header.TLabel',
                  font=('Segoe UI', 14)).pack(anchor='w')

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        main_pane.add(self._create_left_panel(main_pane), weight=1)
        main_pane.add(self._create_right_panel(main_pane), weight=4)

    # ------------------------------------------------------------------ LEFT
    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=380)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        # --- PID Sets (multi-select, like Data Sources) ---
        pid_frame = ttk.LabelFrame(panel, text="PID Sets (check to plot)")
        pid_frame.grid(row=0, column=0, sticky='new', pady=5)
        pid_frame.grid_columnconfigure(0, weight=1)

        preset_row = ttk.Frame(pid_frame)
        preset_row.grid(row=0, column=0, sticky='ew', padx=10, pady=5)
        ttk.Label(preset_row, text="Preset:").pack(side='left')
        self.preset_cb = ttk.Combobox(preset_row, state='readonly',
                                      values=list(self.PRESETS.keys()), width=24)
        self.preset_cb.pack(side='left', fill='x', expand=True, padx=5)
        ttk.Button(preset_row, text="Add", width=5,
                   command=self.add_preset).pack(side='left')

        custom_row = ttk.Frame(pid_frame)
        custom_row.grid(row=1, column=0, sticky='ew', padx=10, pady=5)
        self.custom_vars = {}
        for key, default in [('P', '10'), ('I', '5'), ('D', '0')]:
            ttk.Label(custom_row, text=f"{key}:").pack(side='left', padx=(4, 1))
            var = tk.StringVar(value=default)
            ttk.Entry(custom_row, textvariable=var, width=6).pack(side='left')
            self.custom_vars[key] = var
        ttk.Button(custom_row, text="Add Custom",
                   command=self.add_custom).pack(side='left', padx=6)

        list_container = ttk.Frame(pid_frame)
        list_container.grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 5))
        pid_canvas = tk.Canvas(list_container, bg=self.CLR_INPUT_BG,
                               highlightthickness=0, height=130)
        sb = ttk.Scrollbar(list_container, orient='vertical', command=pid_canvas.yview)
        self.pid_list_frame = ttk.Frame(pid_canvas, style='Input.TFrame')
        pid_canvas.create_window((0, 0), window=self.pid_list_frame, anchor='nw')
        pid_canvas.configure(yscrollcommand=sb.set)
        pid_canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.pid_list_frame.bind(
            "<Configure>", lambda e: pid_canvas.configure(scrollregion=pid_canvas.bbox("all")))

        ttk.Button(pid_frame, text="Remove Checked PID Sets",
                   command=self.remove_checked).grid(row=3, column=0, sticky='ew',
                                                     padx=10, pady=(0, 8))

        # --- Environment / Cryostat Conditions ---
        env_frame = ttk.LabelFrame(panel, text="Cryostat Environment")
        env_frame.grid(row=1, column=0, sticky='new', pady=5)
        env_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(env_frame, text="Cooling Mode:").grid(row=0, column=0, sticky='w', padx=10, pady=4)
        self.cool_mode_cb = ttk.Combobox(env_frame, state='readonly', width=22,
                                         values=["CCR (Closed Cycle)",
                                                 "LN2 Immersion Probe"])
        self.cool_mode_cb.current(0)
        self.cool_mode_cb.grid(row=0, column=1, columnspan=2, sticky='ew', padx=10, pady=4)
        self.cool_mode_cb.bind("<<ComboboxSelected>>", self._on_mode_change)

        ttk.Label(env_frame, text="Cooling Power (%):").grid(row=1, column=0, sticky='w', padx=10, pady=4)
        self.cool_power = tk.DoubleVar(value=100.0)
        ttk.Scale(env_frame, from_=20, to=200, variable=self.cool_power,
                  command=lambda v: self.cool_lbl.config(
                      text=f"{self.cool_power.get():.0f}%")).grid(
            row=1, column=1, sticky='ew', padx=(10, 2), pady=4)
        self.cool_lbl = ttk.Label(env_frame, text="100%")
        self.cool_lbl.grid(row=1, column=2, sticky='w', padx=(0, 10))

        ttk.Label(env_frame, text="Vacuum (mbar):").grid(row=2, column=0, sticky='w', padx=10, pady=4)
        self.vacuum_var = tk.StringVar(value="1e-3")
        ttk.Entry(env_frame, textvariable=self.vacuum_var, width=10).grid(
            row=2, column=1, sticky='w', padx=10, pady=4)
        ttk.Label(env_frame, text="(1e-6 – 1000)").grid(row=2, column=2, sticky='w', padx=(0, 10))

        entries = [("Start Temp (K)", "t_start", "300"),
                   ("Setpoint (K)", "sp", "77"),
                   ("Max Heater (W)", "u_max", "50"),
                   ("Sim Time (s)", "t_end", "600")]
        self.env_vars = {}
        for i, (label, key, default) in enumerate(entries, start=3):
            ttk.Label(env_frame, text=label + ":").grid(row=i, column=0, sticky='w', padx=10, pady=3)
            var = tk.StringVar(value=default)
            ttk.Entry(env_frame, textvariable=var, width=10).grid(
                row=i, column=1, sticky='w', padx=10, pady=3)
            self.env_vars[key] = var

        ttk.Button(env_frame, text="Run Simulation", command=self.run_simulation
                   ).grid(row=7, column=0, columnspan=3, sticky='ew', padx=10, pady=8)

        # --- Formula ---
        formula_frame = ttk.LabelFrame(panel, text="Model Equations")
        formula_frame.grid(row=2, column=0, sticky='new', pady=5)
        ffig = Figure(figsize=(3.4, 1.6), dpi=100)
        ffig.patch.set_facecolor(self.CLR_FRAME_BG)
        ax = ffig.add_subplot(111)
        ax.axis('off')
        ax.text(0.5, 0.78, r"$u(t) = K_p e + K_i \int e\,d\tau + K_d \frac{de}{dt}$",
                ha='center', fontsize=12)
        ax.text(0.5, 0.45, r"$C\frac{dT}{dt} = u_{heater} - k_{cool}(P_{cool}, vac)\,(T - T_{bath})$",
                ha='center', fontsize=10.5)
        ax.text(0.5, 0.12, r"$e(t) = T_{set} - T(t)$", ha='center', fontsize=10.5)
        FigureCanvasTkAgg(ffig, formula_frame).get_tk_widget().pack(fill='x', padx=5, pady=5)

        # --- Console ---
        console_frame = ttk.LabelFrame(panel, text="Simulation Log")
        console_frame.grid(row=3, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(console_frame, state='disabled',
                                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG,
                                                 font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    # ----------------------------------------------------------------- RIGHT
    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        container = ttk.LabelFrame(panel, text='Visualization')
        container.pack(fill='both', expand=True)

        self.figure = Figure(dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        toolbar_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        toolbar_frame.pack(fill='x', side='bottom', pady=(0, 5))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.configure(background=self.CLR_FRAME_BG)
        for b in toolbar.winfo_children():
            b.config(background=self.CLR_FRAME_BG)
        toolbar.update()
        return panel

    # ------------------------------------------------------------- PID list
    def _add_pid_entry(self, kp, ki, kd, label=None):
        label = label or f"Custom (P={kp:g}, I={ki:g}, D={kd:g})"
        var = tk.BooleanVar(value=True)
        frame = ttk.Frame(self.pid_list_frame, style='Input.TFrame')
        frame.pack(fill='x', expand=True, pady=1)
        ttk.Checkbutton(frame, variable=var, style='Input.TCheckbutton',
                        command=self.run_simulation).pack(side='left', padx=(5, 0))
        ttk.Label(frame, text=label, style='Input.TLabel').pack(side='left', padx=5)
        self._pid_counter += 1
        self.pid_entries[self._pid_counter] = {
            'var': var, 'frame': frame, 'params': (kp, ki, kd), 'label': label}

    def add_preset(self):
        name = self.preset_cb.get()
        if not name:
            self.log("Select a preset first.")
            return
        if any(e['label'] == name for e in self.pid_entries.values()):
            self.log(f"Preset already added: {name}")
            return
        self._add_pid_entry(*self.PRESETS[name], label=name)
        self.log(f"Added preset: {name}")
        self.run_simulation()

    def add_custom(self):
        try:
            kp = float(self.custom_vars['P'].get())
            ki = float(self.custom_vars['I'].get())
            kd = float(self.custom_vars['D'].get())
        except ValueError:
            self.log("Invalid custom PID values.")
            return
        self._add_pid_entry(kp, ki, kd)
        self.log(f"Added custom PID: P={kp:g}, I={ki:g}, D={kd:g}")
        self.run_simulation()

    def remove_checked(self):
        for pid_id in [k for k, e in self.pid_entries.items() if e['var'].get()]:
            self.log(f"Removed: {self.pid_entries[pid_id]['label']}")
            self.pid_entries[pid_id]['frame'].destroy()
            del self.pid_entries[pid_id]
        self.run_simulation()

    def _on_mode_change(self, event=None):
        # LN2 immersion: much stronger coupling, bath fixed at 77 K
        if "LN2" in self.cool_mode_cb.get():
            self.log("Mode: LN2 immersion — bath at 77 K, strong coupling.")
        else:
            self.log("Mode: CCR — cold head ~10 K, weaker exchange-gas coupling.")
        self.run_simulation()

    # ------------------------------------------------------------ SIMULATION
    def run_simulation(self, event=None):
        selected = [(e['label'], e['params']) for e in self.pid_entries.values()
                    if e['var'].get()]

        self.ax_main.clear()
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        try:
            sp = float(self.env_vars['sp'].get())
            t_start = float(self.env_vars['t_start'].get())
            u_max = float(self.env_vars['u_max'].get())
            t_end = float(self.env_vars['t_end'].get())
            vac = float(self.vacuum_var.get())
            cool_pct = self.cool_power.get() / 100.0
            if t_end <= 0 or u_max <= 0 or vac <= 0:
                raise ValueError("Time, heater power and vacuum must be positive.")
        except ValueError as e:
            self.log(f"Input error: {e}")
            self.canvas.draw_idle()
            return

        if not selected:
            self.ax_main.set_title("No PID set selected")
            self.canvas.draw_idle()
            return

        # --- Cryogenic plant parameters ---
        ln2_mode = "LN2" in self.cool_mode_cb.get()
        T_bath = 77.0 if ln2_mode else 10.0
        C = 200.0                      # heat capacity J/K (sample stage + probe)
        k_base = 2.0 if ln2_mode else 0.4   # base thermal conductance W/K

        # Vacuum effect: residual gas adds conduction. Higher pressure => more
        # parasitic coupling to bath (log scale between 1e-6 and 1000 mbar).
        vac_factor = 1.0 + 0.15 * np.log10(np.clip(vac, 1e-6, 1e3) / 1e-6)
        k_cool = k_base * cool_pct * vac_factor

        dt = 0.05
        n = int(t_end / dt)
        t = np.linspace(0, t_end, n)

        self.log(f"--- Run: SP={sp} K | Bath={T_bath} K | k_cool={k_cool:.3f} W/K "
                 f"(cooling {cool_pct*100:.0f}%, vac={vac:g} mbar) ---")

        for idx, (label, (kp, ki, kd)) in enumerate(selected):
            T = np.zeros(n)
            T[0] = t_start
            integral, prev_err = 0.0, sp - t_start
            for i in range(1, n):
                err = sp - T[i - 1]
                deriv = (err - prev_err) / dt
                u = kp * err + ki * integral + kd * deriv
                u_clamped = np.clip(u, 0.0, u_max)   # heater: 0..max W
                if u == u_clamped:                    # anti-windup
                    integral += err * dt
                prev_err = err
                T[i] = T[i - 1] + dt * (u_clamped - k_cool * (T[i - 1] - T_bath)) / C

            color = self.PLOT_COLORS[idx % len(self.PLOT_COLORS)]
            self.ax_main.plot(t, T, linewidth=1.6, color=color, label=label)

            overshoot = sp - T.min() if t_start > sp else T.max() - sp
            tol = max(0.02 * abs(sp), 0.1)
            outside = np.where(np.abs(T - sp) > tol)[0]
            t_settle = (f"{t[outside[-1]]:.0f} s"
                        if len(outside) and outside[-1] < n - 1 else "not settled")
            self.log(f"  {label}: final={T[-1]:.2f} K | "
                     f"over/undershoot={overshoot:+.2f} K | settle(2%)={t_settle}")

        self.ax_main.axhline(sp, color=self.CLR_ACCENT_GOLD, linestyle='--',
                             linewidth=1.5, label=f"Setpoint = {sp:g} K")
        self.ax_main.set_xlabel("Time (s)", fontweight='bold')
        self.ax_main.set_ylabel("Temperature (K)", fontweight='bold')
        mode_str = "LN2 Immersion" if ln2_mode else "CCR"
        self.ax_main.set_title(
            f"PID Comparison — {mode_str} | Cooling {cool_pct*100:.0f}% | "
            f"Vac {vac:g} mbar", fontweight='bold')
        self.ax_main.legend(title="PID Sets", loc='best')
        self.figure.tight_layout()
        self.canvas.draw_idle()


if __name__ == '__main__':
    root = tk.Tk()
    app = PIDSimulatorGUI(root)
    root.mainloop()