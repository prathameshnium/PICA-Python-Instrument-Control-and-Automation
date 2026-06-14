"""
PROGRAM:       Master Temp-Dependent LCR Frequency Scan GUI
PURPOSE:       Automates Temperature steps (Lakeshore 350) and performs a 
               Full Frequency Sweep (Keysight E4980A) only when temperature is stable.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas, filedialog
import os
import time
import math
import traceback
import threading
import queue
from datetime import datetime
import numpy as np

import runpy
from multiprocessing import Process

def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")

def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")

def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

# --- Optional Packages ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# BACKEND: TEMPERATURE CONTROLLER (Lakeshore 350)
# ===============================================================================
class Lakeshore_Backend:
    def __init__(self, resource_manager):
        self.rm = resource_manager
        self.instrument = None

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        self.instrument = self.rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        idn = self.instrument.query('*IDN?').strip()
        return idn

    def configure_ramp(self, setpoint, rate, heater_range):
        self.instrument.write('*RST')
        time.sleep(0.5)
        self.instrument.write('*CLS')
        self.set_heater_range(1, heater_range)
        self.instrument.write(f'SETP 1,{setpoint}')
        self.instrument.write(f'RAMP 1,1,{rate}')

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        self.instrument.write(f'RANGE {output},{range_code}')

    def get_status(self):
        temp = float(self.instrument.query('KRDG? A').strip())
        htr_output = float(self.instrument.query('HTR? 1').strip())
        return temp, htr_output

    def stop_ramp(self):
        if self.instrument:
            try:
                self.instrument.write('RAMP 1,0,0')
                self.set_heater_range(1, 'off')
            except Exception:
                pass

    def shutdown(self):
        if self.instrument:
            self.stop_ramp()
            self.instrument.close()
            self.instrument = None


# ===============================================================================
# BACKEND: LCR METER (Keysight E4980A)
# ===============================================================================
class LCR_Backend:
    def __init__(self, resource_manager):
        self.rm = resource_manager
        self.instrument = None
        self.has_opt001 = False

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        inst = self.rm.open_resource(visa_address)
        inst.timeout = 15000
        inst.read_termination = '\n'
        inst.write_termination = '\n'
        self.instrument = inst
        idn = inst.query('*IDN?').strip()
        self.has_opt001 = '001' in inst.query('*OPT?')
        return idn

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        current_v = float(self.instrument.query(':BIAS:VOLT?'))
        if abs(target_v - current_v) < 0.01:
            return
        direction = 1 if target_v > current_v else -1
        ramp_points = np.arange(current_v, target_v, direction * step)
        ramp_points = np.append(ramp_points, target_v)
        for v in ramp_points:
            self.instrument.write(f':BIAS:VOLT {v:.3f}')
            time.sleep(dwell)

    def initialize_sweep_params(self, p):
        v_bias_max = 40.0 if self.has_opt001 else 2.0
        v_ac_max = 20.0 if self.has_opt001 else 2.0
        if abs(p['dc_bias']) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V limit.")
        if not (0 < p['ac_bias'] <= v_ac_max):
            raise ValueError(f"AC level outside 0–{v_ac_max} Vrms.")

        self.instrument.write('*RST; *CLS')
        self.instrument.write(':DISP:ENAB ON')
        self.instrument.write(':FUNC:IMP CPG')
        self.instrument.write(f":APER {p['aper']}")
        self.instrument.write(':FUNC:IMP:RANG:AUTO ON')
        
        self.instrument.write(':AMPL:ALC ON' if p['alc_enabled'] else ':AMPL:ALC OFF')
        
        if p['corr_enabled']:
            self.instrument.write(':CORR:OPEN:STAT ON')
            self.instrument.write(':CORR:SHOR:STAT ON')
        else:
            self.instrument.write(':CORR:OPEN:STAT OFF')
            self.instrument.write(':CORR:SHOR:STAT OFF')

        self.instrument.write(f":VOLT {p['ac_bias']}")
        self.instrument.write(':TRIG:SOUR BUS')
        self.instrument.write(':INIT:CONT ON')
        self.instrument.write(':BIAS:VOLT 0')
        self.instrument.write(':BIAS:STAT ON')
        
        self.safe_ramp_dc_bias(p['dc_bias'])

    def perform_measurement(self, freq, delay):
        self.instrument.write(f':FREQ {freq}')
        time.sleep(delay)
        vals = self.instrument.query_ascii_values('*TRG')
        return vals[0], vals[1]  # Cp, G

    def turn_off_bias(self):
        if self.instrument:
            self.safe_ramp_dc_bias(0.0)
            self.instrument.write(':BIAS:STAT OFF')

    def shutdown(self):
        if self.instrument:
            try:
                self.turn_off_bias()
                self.instrument.write(':DISP:PAGE MEAS')
                self.instrument.close()
            except Exception:
                pass
            finally:
                self.instrument = None


# ===============================================================================
# FRONTEND: MASTER GUI
# ===============================================================================
class MasterControlGUI:
    PROGRAM_VERSION = "1.0-Master"
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#8AB845'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_GOLD = '#B68B6E'
    CLR_STABLE_WAIT = '#D4A373'
    
    FONT_BASE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 12, 'bold')
    FONT_CONSOLE = ('Consolas', 9)

    DATA_HEADER = "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''"

    def __init__(self, root):
        self.root = root
        self.root.title(f"Master Automated LCR-Temp Scan v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x900")
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.gui_queue = queue.Queue()
        
        # Shared Resource Manager
        self.rm = pyvisa.ResourceManager() if PYVISA_AVAILABLE else None
        
        self.tc_backend = Lakeshore_Backend(self.rm)
        self.lcr_backend = LCR_Backend(self.rm)

        self.file_location_path = ""
        self.data_temp = {'time': [], 'temperature': [], 'target': [], 'heater': []}
        self.data_lcr = {'freq': [], 'cp': [], 'g': []}
        
        self.sweep_frequencies = np.concatenate([
            np.arange(40, 1000, 10),
            np.arange(1000, 10000, 100),
            np.arange(10000, 100000, 1000),
            np.arange(100000, 1000000, 10000),
            np.arange(1000000, 2000001, 100000)
        ])

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TNotebook', background=self.CLR_BG_DARK)
        style.configure('TNotebook.Tab', font=self.FONT_TITLE, padding=[10, 5])
        style.map('TNotebook.Tab', background=[('selected', self.CLR_HEADER)])
        style.configure('TLabel', background=self.CLR_FRAME_BG)
        style.configure('TButton', font=self.FONT_BASE, padding=5, background=self.CLR_HEADER)
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FRAME_BG)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, font=self.FONT_TITLE)
        
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 9, 'axes.titlesize': 11})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(header, text="Automated T-Dependent Impedance Spectroscopy", 
                  font=('Segoe UI', 14, 'bold'), background=self.CLR_HEADER, foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)

        ttk.Button(header, text="📈", command=launch_plotter_utility, width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner, width=3).pack(side='right', padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(main_pane, width=450)
        main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(main_pane)
        main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)

    def _populate_left_panel(self, panel):
        notebook = ttk.Notebook(panel)
        notebook.pack(fill='both', expand=True)

        # Tab 1: Temperature Controls
        tab_temp = ttk.Frame(notebook)
        notebook.add(tab_temp, text='1. Temperature Setup')
        self._build_sequence_panel(tab_temp)
        self._build_temp_settings(tab_temp)

        # Tab 2: LCR Controls
        tab_lcr = ttk.Frame(notebook)
        notebook.add(tab_lcr, text='2. LCR Settings')
        self._build_lcr_settings(tab_lcr)

        # Bottom Frame: Execution & Console
        exec_frame = ttk.Frame(panel)
        exec_frame.pack(fill='x', pady=5)
        
        btn_frame = ttk.Frame(exec_frame)
        btn_frame.pack(fill='x', pady=5)
        self.start_button = ttk.Button(btn_frame, text="START MASTER SEQUENCE", style='Start.TButton', command=self.start_sequence)
        self.start_button.pack(side='left', fill='x', expand=True, padx=2)
        self.stop_button = ttk.Button(btn_frame, text="STOP/ABORT", style='Stop.TButton', state='disabled', command=self.stop_sequence)
        self.stop_button.pack(side='left', fill='x', expand=True, padx=2)

        self.lbl_status = tk.Label(exec_frame, text="READY", font=('Segoe UI', 14, 'bold'), bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK, pady=5)
        self.lbl_status.pack(fill='x', pady=5)

        self.console = scrolledtext.ScrolledText(exec_frame, state='disabled', bg=self.CLR_HEADER, fg=self.CLR_TEXT_DARK, font=self.FONT_CONSOLE, height=12)
        self.console.pack(fill='both', expand=True)
        self.log("System Ready. Please configure Temperature and LCR tabs.")

    def _build_sequence_panel(self, parent):
        frame = ttk.LabelFrame(parent, text='Temperature Steps (K)')
        frame.pack(fill='x', padx=5, pady=5)
        
        self.listbox = tk.Listbox(frame, height=6, font=self.FONT_BASE)
        self.listbox.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        ctrl_frame = ttk.Frame(frame)
        ctrl_frame.pack(side="right", fill="y", padx=5, pady=5)
        
        ttk.Label(ctrl_frame, text="Manual Add (K):").pack()
        self.entry_manual_t = ttk.Entry(ctrl_frame, width=10)
        self.entry_manual_t.pack(pady=2)
        ttk.Button(ctrl_frame, text="Add", command=lambda: self.listbox.insert(tk.END, f"{float(self.entry_manual_t.get()):.2f}")).pack(fill='x')
        ttk.Button(ctrl_frame, text="Remove Sel.", command=lambda: [self.listbox.delete(i) for i in reversed(self.listbox.curselection())]).pack(fill='x', pady=2)
        ttk.Button(ctrl_frame, text="Clear All", command=lambda: self.listbox.delete(0, tk.END)).pack(fill='x')

    def _build_temp_settings(self, parent):
        frame = ttk.LabelFrame(parent, text='Lakeshore Settings')
        frame.pack(fill='x', padx=5, pady=5)
        
        self.t_entries = {}
        def add_t_entry(label, default, r, c):
            ttk.Label(frame, text=label).grid(row=r, column=c, padx=5, pady=2, sticky='e')
            e = ttk.Entry(frame, width=8)
            e.insert(0, default)
            e.grid(row=r, column=c+1, padx=5, pady=2, sticky='w')
            self.t_entries[label] = e

        add_t_entry("Tolerance (±K):", "0.5", 0, 0)
        add_t_entry("Soak Time (s):", "60", 0, 2)
        add_t_entry("Ramp (K/min):", "2.0", 1, 0)
        add_t_entry("Poll Delay (s):", "1.0", 1, 2)

        ttk.Label(frame, text="Heater Range:").grid(row=2, column=0, sticky='e', padx=5)
        self.heater_var = tk.StringVar(value='High')
        ttk.Combobox(frame, textvariable=self.heater_var, values=['Off', 'Low', 'Medium', 'High'], state='readonly', width=8).grid(row=2, column=1, sticky='w', padx=5)

        ttk.Label(frame, text="Lakeshore VISA:").grid(row=3, column=0, sticky='e', padx=5, pady=5)
        self.ls_cb = ttk.Combobox(frame, state='readonly', width=15)
        self.ls_cb.grid(row=3, column=1, columnspan=2, sticky='we', padx=5)
        ttk.Button(frame, text="Scan VISA", command=self._scan_for_visa).grid(row=3, column=3, padx=5)

    def _build_lcr_settings(self, parent):
        frame = ttk.LabelFrame(parent, text='E4980A Parameters')
        frame.pack(fill='x', padx=5, pady=5)

        self.lcr_entries = {}
        def add_lcr_entry(label, default, r, c):
            ttk.Label(frame, text=label).grid(row=r, column=c, padx=5, pady=2, sticky='e')
            e = ttk.Entry(frame, width=10)
            e.insert(0, default)
            e.grid(row=r, column=c+1, padx=5, pady=2, sticky='w')
            self.lcr_entries[label] = e

        add_lcr_entry("Sample Name:", "Sample1", 0, 0)
        add_lcr_entry("AC Bias (V):", "1.0", 1, 0)
        add_lcr_entry("DC Bias (V):", "0.0", 1, 2)
        add_lcr_entry("Meas. Delay (s):", "0.2", 2, 0)

        ttk.Label(frame, text="Aperture:").grid(row=2, column=2, sticky='e', padx=5)
        self.aper_var = tk.StringVar(value='MED')
        ttk.Combobox(frame, textvariable=self.aper_var, values=['SHOR', 'MED', 'LONG'], state='readonly', width=8).grid(row=2, column=3, sticky='w', padx=5)

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Enable ALC", variable=self.var_alc).grid(row=3, column=0, columnspan=2, sticky='w', padx=5)
        ttk.Checkbutton(frame, text="Open/Short Corr.", variable=self.var_corr).grid(row=3, column=2, columnspan=2, sticky='w', padx=5)

        ttk.Label(frame, text="LCR VISA:").grid(row=4, column=0, sticky='e', padx=5, pady=5)
        self.lcr_cb = ttk.Combobox(frame, state='readonly', width=15)
        self.lcr_cb.grid(row=4, column=1, columnspan=2, sticky='we', padx=5)

        ttk.Button(frame, text="Browse Save Directory...", command=self._browse_dir).grid(row=5, column=0, columnspan=4, sticky='we', padx=5, pady=5)

    def _populate_right_panel(self, panel):
        # Top: Temp Graph
        t_frame = ttk.LabelFrame(panel, text="Temperature Profile")
        t_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        self.fig_t = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_t = self.fig_t.add_subplot(111)
        self.line_t_target, = self.ax_t.plot([], [], '--', color=self.CLR_ACCENT_GREEN, label='Target')
        self.line_t_actual, = self.ax_t.plot([], [], '-', color=self.CLR_ACCENT_RED, label='Actual T')
        self.ax_t.set_ylabel("Temp (K)")
        self.ax_t.legend()
        self.ax_t.grid(True, linestyle=':')
        self.fig_t.tight_layout()
        
        self.canvas_t = FigureCanvasTkAgg(self.fig_t, t_frame)
        self.canvas_t.get_tk_widget().pack(fill='both', expand=True)

        # Bottom: LCR Graphs
        lcr_frame = ttk.LabelFrame(panel, text="Live LCR Sweep (Current Temp)")
        lcr_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        self.fig_lcr = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_cp = self.fig_lcr.add_subplot(121)
        self.line_cp, = self.ax_cp.plot([], [], 'o-', color='#C00000', markersize=3)
        self.ax_cp.set_xscale('log')
        self.ax_cp.set_xlabel("Frequency (Hz)")
        self.ax_cp.set_ylabel("Cp (F)")
        self.ax_cp.grid(True, linestyle=':')
        
        self.ax_g = self.fig_lcr.add_subplot(122)
        self.line_g, = self.ax_g.plot([], [], 's-', color='#2A6B3A', markersize=3)
        self.ax_g.set_xscale('log')
        self.ax_g.set_xlabel("Frequency (Hz)")
        self.ax_g.set_ylabel("Conductance G (S)")
        self.ax_g.grid(True, linestyle=':')

        self.fig_lcr.tight_layout()
        self.canvas_lcr = FigureCanvasTkAgg(self.fig_lcr, lcr_frame)
        self.canvas_lcr.get_tk_widget().pack(fill='both', expand=True)

    # --- UI HELPERS ---
    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _scan_for_visa(self):
        if not self.rm: return
        self.log("Scanning VISA...")
        res = self.rm.list_resources()
        self.ls_cb['values'] = res
        self.lcr_cb['values'] = res
        self.log(f"Found: {res}")

    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.file_location_path = d
            self.log(f"Save dir: {d}")

    def _update_status(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    # --- MATH ENGINE ---
    def calculate_impedance(self, f, cp, g):
        omega = 2 * np.pi * f
        G_safe = g if g != 0 else 1e-20
        omega_safe = omega if omega != 0 else 1e-20

        Rp = 1.0 / G_safe
        B = omega * cp
        B_safe = B if B != 0 else 1e-20
        
        D = G_safe / B_safe
        Q = 1.0 / D if D != 0 else 0.0

        Y_mag = np.sqrt(g**2 + B**2)
        Y_mag_safe = Y_mag if Y_mag != 0 else 1e-20
        Z_mag = 1.0 / Y_mag_safe

        Rs = g / (Y_mag_safe**2)
        Xs = -B / (Y_mag_safe**2)
        Xs_safe = Xs if Xs != 0 else 1e-20

        theta_rad = math.atan2(Xs, Rs)
        theta_deg = math.degrees(theta_rad)

        chi = Xs
        Cs = -1.0 / (omega_safe * Xs_safe)
        Ls = Xs / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        Cp_double_prime = g / omega_safe
        Cs_double_prime = Cp_double_prime

        return [Q, D, g, B, cp, Lp, Cs, Ls, Z_mag, theta_rad, chi, Rs, theta_deg, Rp, Y_mag, omega, Cp_double_prime, Cs_double_prime]

    # --- EXECUTION LOGIC ---
    def start_sequence(self):
        setpoints = list(self.listbox.get(0, tk.END))
        if not setpoints:
            messagebox.showerror("Error", "Temperature list is empty.")
            return
        if not self.file_location_path:
            messagebox.showerror("Error", "Please select a save directory.")
            return

        try:
            self.t_params = {
                'tol': float(self.t_entries["Tolerance (±K):"].get()),
                'soak': float(self.t_entries["Soak Time (s):"].get()),
                'rate': float(self.t_entries["Ramp (K/min):"].get()),
                'delay': float(self.t_entries["Poll Delay (s):"].get()),
                'htr': self.heater_var.get(),
                'visa': self.ls_cb.get()
            }
            self.lcr_params = {
                'sample': self.lcr_entries["Sample Name:"].get(),
                'ac_bias': float(self.lcr_entries["AC Bias (V):"].get()),
                'dc_bias': float(self.lcr_entries["DC Bias (V):"].get()),
                'delay': float(self.lcr_entries["Meas. Delay (s):"].get()),
                'aper': self.aper_var.get(),
                'alc_enabled': self.var_alc.get(),
                'corr_enabled': self.var_corr.get(),
                'visa': self.lcr_cb.get()
            }
            if not self.t_params['visa'] or not self.lcr_params['visa']:
                raise ValueError("Both VISA addresses must be selected.")
            self.setpoints_f = [float(x) for x in setpoints]
        except Exception as e:
            messagebox.showerror("Config Error", str(e))
            return

        self.is_running = True
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        
        self.data_temp = {'time': [], 'temperature': [], 'target': []}
        self.start_time = time.time()

        self.root.after(100, self._process_gui_queue)
        threading.Thread(target=self._master_hardware_loop, daemon=True).start()

    def stop_sequence(self):
        self.is_running = False
        self.log("ABORT COMMAND SENT. Waiting for threads to yield...")
        self.stop_button.config(state='disabled')

    def _process_gui_queue(self):
        try:
            while True:
                msg = self.gui_queue.get_nowait()
                m_type = msg['type']
                
                if m_type == 'log':
                    self.log(msg['text'])
                elif m_type == 'status':
                    self._update_status(msg['text'], msg['color'])
                elif m_type == 'plot_t':
                    self.line_t_target.set_data(self.data_temp['time'], self.data_temp['target'])
                    self.line_t_actual.set_data(self.data_temp['time'], self.data_temp['temperature'])
                    self.ax_t.relim()
                    self.ax_t.autoscale_view()
                    self.canvas_t.draw_idle()
                elif m_type == 'plot_lcr':
                    self.line_cp.set_data(self.data_lcr['freq'], self.data_lcr['cp'])
                    self.line_g.set_data(self.data_lcr['freq'], self.data_lcr['g'])
                    self.ax_cp.relim(); self.ax_cp.autoscale_view()
                    self.ax_g.relim(); self.ax_g.autoscale_view()
                    self.canvas_lcr.draw_idle()
                elif m_type == 'finish':
                    self.start_button.config(state='normal')
                    self.stop_button.config(state='disabled')
                    self._update_status("SEQUENCE COMPLETE", self.CLR_HEADER)
                    messagebox.showinfo("Done", "All steps completed.")
        except queue.Empty:
            pass
        if self.is_running:
            self.root.after(100, self._process_gui_queue)

    def _put(self, msg_type, **kwargs):
        payload = {'type': msg_type}
        payload.update(kwargs)
        self.gui_queue.put(payload)

    def _master_hardware_loop(self):
        try:
            self._put('log', text="Connecting to instruments...")
            self.tc_backend.connect(self.t_params['visa'])
            self.lcr_backend.connect(self.lcr_params['visa'])
            self._put('log', text="Instruments connected successfully.")

            for i, target in enumerate(self.setpoints_f):
                if not self.is_running: break

                # --- 1. TEMPERATURE RAMP & STABILIZE ---
                self._put('log', text=f"== STEP {i+1}/{len(self.setpoints_f)}: Ramping to {target} K ==")
                self._put('status', text=f"RAMPING T TO {target} K", color=self.CLR_ACCENT_RED)
                
                self.tc_backend.configure_ramp(target, self.t_params['rate'], self.t_params['htr'])
                stable_start = None

                while self.is_running:
                    t_act, _ = self.tc_backend.get_status()
                    self.data_temp['time'].append((time.time() - self.start_time)/60.0)
                    self.data_temp['temperature'].append(t_act)
                    self.data_temp['target'].append(target)
                    self._put('plot_t')

                    if abs(t_act - target) <= self.t_params['tol']:
                        if stable_start is None:
                            stable_start = time.time()
                            self._put('status', text=f"STABILIZING AT {target} K", color=self.CLR_STABLE_WAIT)
                        elif time.time() - stable_start >= self.t_params['soak']:
                            self._put('log', text="Temperature stabilized.")
                            break
                    else:
                        stable_start = None
                        self._put('status', text=f"RAMPING T TO {target} K", color=self.CLR_ACCENT_RED)
                    
                    time.sleep(self.t_params['delay'])

                if not self.is_running: break

                # --- 2. LCR FREQUENCY SWEEP ---
                self._put('status', text=f"MEASURING LCR AT {target} K", color=self.CLR_ACCENT_GREEN)
                
                # Setup specific file for this temperature
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{self.lcr_params['sample']}_{target}K_{ts}.txt"
                filepath = os.path.join(self.file_location_path, filename)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(f"# Sample: {self.lcr_params['sample']} | Temp: {target}K | AC: {self.lcr_params['ac_bias']}V | DC: {self.lcr_params['dc_bias']}V\n")
                    f.write(self.DATA_HEADER + "\n")

                self.data_lcr = {'freq': [], 'cp': [], 'g': []}
                self._put('plot_lcr')

                self.lcr_backend.initialize_sweep_params(self.lcr_params)

                for freq in self.sweep_frequencies:
                    if not self.is_running: break
                    
                    cp, g = self.lcr_backend.perform_measurement(freq, self.lcr_params['delay'])
                    
                    self.data_lcr['freq'].append(freq)
                    self.data_lcr['cp'].append(cp)
                    self.data_lcr['g'].append(g)
                    
                    calc_vals = self.calculate_impedance(freq, cp, g)
                    row_str = "\t".join([f"{v:.6E}" for v in ([freq] + calc_vals)])
                    
                    with open(filepath, 'a', encoding='utf-8') as file:
                        file.write(row_str + "\n")
                    
                    self._put('plot_lcr')

                self._put('log', text=f"Sweep at {target} K saved to {filename}")
                self.lcr_backend.turn_off_bias()

        except Exception as e:
            self._put('log', text=f"HARDWARE ERROR: {e}\n{traceback.format_exc()}")
        finally:
            self.is_running = False
            self.tc_backend.shutdown()
            self.lcr_backend.shutdown()
            self._put('finish')

    def _on_closing(self):
        if self.is_running and messagebox.askyesno("Exit", "Sequence active. Stop hardware and exit?"):
            self.stop_sequence()
            time.sleep(1)
            self.root.destroy()
        elif not self.is_running:
            self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = MasterControlGUI(root)
    root.mainloop()