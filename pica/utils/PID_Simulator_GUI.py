"""
Module: PID_Simulator_GUI.py
Purpose: PID Setpoint Simulator Utility (Part of PICA suite).
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib as mpl


class PIDSimulatorGUI:
    PROGRAM_VERSION = "1.0"

    # --- PICA Styling ---
    CLR_BG = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    FONT_BASE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 12, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA PID Setpoint Simulator v{self.PROGRAM_VERSION}")
        self.root.geometry("1300x850")
        self.root.minsize(1000, 650)
        self.root.configure(bg=self.CLR_BG)

        self.setup_styles()
        self.create_widgets()
        self.log("Welcome to the PICA PID Setpoint Simulator.")
        self.log("Enter Kp, Ki, Kd and Setpoint, then click 'Run Simulation'.")
        self.run_simulation()

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
        self.style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                             foreground=self.CLR_FG, font=self.FONT_TITLE)
        self.style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG)

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
        ttk.Label(left_hdr, text="PID Setpoint Simulator", style='Header.TLabel',
                  font=('Segoe UI', 14, 'bold'), foreground=self.CLR_ACCENT_GOLD
                  ).pack(anchor='w', padx=20, pady=(10, 0))
        ttk.Label(left_hdr, text="(Part of the PICA Suite)", style='Header.TLabel',
                  font=('Segoe UI', 10, 'italic')).pack(anchor='w', padx=20, pady=(0, 10))

        ctr = tk.Frame(header, bg=self.CLR_HEADER)
        ctr.pack(side='left', padx=40)
        ttk.Label(ctr, text="UGC-DAE Consortium for Scientific Research",
                  style='Header.TLabel', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(ctr, text="Mumbai Centre",
                  style='Header.TLabel', font=('Segoe UI', 14)).pack(anchor='w')

        # --- Main Layout ---
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        main_pane.add(self._create_left_panel(main_pane), weight=1)
        main_pane.add(self._create_right_panel(main_pane), weight=4)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=340)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        # --- PID Parameters ---
        params = ttk.LabelFrame(panel, text="PID Parameters")
        params.grid(row=0, column=0, sticky='new', pady=5)
        params.grid_columnconfigure(1, weight=1)

        self.vars = {}
        fields = [("Kp (Proportional)", "kp", "2.0"),
                  ("Ki (Integral)", "ki", "1.0"),
                  ("Kd (Derivative)", "kd", "0.5"),
                  ("Setpoint", "sp", "100.0"),
                  ("Sim Time (s)", "t_end", "20"),
                  ("Time Step dt (s)", "dt", "0.01")]
        for i, (label, key, default) in enumerate(fields):
            ttk.Label(params, text=label + ":").grid(row=i, column=0, sticky='w', padx=10, pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(params, textvariable=var, width=12).grid(row=i, column=1, sticky='ew', padx=10, pady=4)
            self.vars[key] = var

        ttk.Button(params, text="Run Simulation", command=self.run_simulation
                   ).grid(row=len(fields), column=0, columnspan=2, sticky='ew', padx=10, pady=10)

        # --- Formula Display ---
        formula_frame = ttk.LabelFrame(panel, text="PID Control Law")
        formula_frame.grid(row=1, column=0, sticky='new', pady=5)
        self.formula_fig = Figure(figsize=(3.2, 1.4), dpi=100)
        self.formula_fig.patch.set_facecolor(self.CLR_FRAME_BG)
        ax = self.formula_fig.add_subplot(111)
        ax.axis('off')
        ax.text(0.5, 0.65,
                r"$u(t) = K_p e(t) + K_i \int_0^t e(\tau)\,d\tau + K_d \frac{de(t)}{dt}$",
                ha='center', va='center', fontsize=13)
        ax.text(0.5, 0.15, r"$e(t) = \mathrm{Setpoint} - \mathrm{Process\ Value}$",
                ha='center', va='center', fontsize=11)
        self.formula_canvas = FigureCanvasTkAgg(self.formula_fig, formula_frame)
        self.formula_canvas.get_tk_widget().pack(fill='x', padx=5, pady=5)

        # --- Console ---
        console_frame = ttk.LabelFrame(panel, text="Simulation Log")
        console_frame.grid(row=2, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(console_frame, state='disabled',
                                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG,
                                                 font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        return panel

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
        for button in toolbar.winfo_children():
            button.config(background=self.CLR_FRAME_BG)
        toolbar.update()
        return panel

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def run_simulation(self):
        try:
            kp = float(self.vars['kp'].get())
            ki = float(self.vars['ki'].get())
            kd = float(self.vars['kd'].get())
            sp = float(self.vars['sp'].get())
            t_end = float(self.vars['t_end'].get())
            dt = float(self.vars['dt'].get())
            if dt <= 0 or t_end <= 0:
                raise ValueError("Time values must be positive.")
        except ValueError as e:
            self.log(f"Input error: {e}")
            return

        # Simple first-order plant: dy/dt = (-y + u) / tau
        tau = 1.5
        n = int(t_end / dt)
        t = np.linspace(0, t_end, n)
        y = np.zeros(n)
        u_arr = np.zeros(n)
        integral, prev_err = 0.0, sp - 0.0

        for i in range(1, n):
            err = sp - y[i - 1]
            integral += err * dt
            deriv = (err - prev_err) / dt
            u = kp * err + ki * integral + kd * deriv
            prev_err = err
            u_arr[i] = u
            y[i] = y[i - 1] + dt * (-y[i - 1] + u) / tau

        # Metrics
        final = y[-1]
        overshoot = max(0.0, (y.max() - sp) / sp * 100) if sp != 0 else 0.0
        tol = 0.02 * abs(sp) if sp != 0 else 0.02
        settled = np.where(np.abs(y - sp) > tol)[0]
        t_settle = t[settled[-1]] if len(settled) and settled[-1] < n - 1 else None

        self.ax_main.clear()
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.ax_main.axhline(sp, color=self.CLR_ACCENT_GOLD, linestyle='--',
                             linewidth=1.5, label=f"Setpoint = {sp:g}")
        self.ax_main.plot(t, y, color=self.CLR_FG, linewidth=1.8,
                          label=f"Response (Kp={kp:g}, Ki={ki:g}, Kd={kd:g})")
        self.ax_main.set_xlabel("Time (s)", fontweight='bold')
        self.ax_main.set_ylabel("Process Value", fontweight='bold')
        self.ax_main.set_title("PID Setpoint Response", fontweight='bold')
        self.ax_main.legend(loc='best')
        self.figure.tight_layout()
        self.canvas.draw_idle()

        self.log(f"Simulated: Kp={kp:g}, Ki={ki:g}, Kd={kd:g}, SP={sp:g}")
        self.log(f"  Final value: {final:.3f} | Overshoot: {overshoot:.1f}%")
        self.log(f"  Settling time (2%): {t_settle:.2f} s\n" if t_settle
                 else "  Did not settle within 2% band.\n")


if __name__ == '__main__':
    root = tk.Tk()
    app = PIDSimulatorGUI(root)
    root.mainloop()