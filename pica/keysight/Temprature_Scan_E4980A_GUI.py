"""
PROGRAM:       Keysight E4980A + Lakeshore 350 Temperature Dependent Dielectric GUI
PURPOSE:       Combines continuous hardware temperature ramping (Lakeshore) with 
               repeated dynamic frequency sweeps (Keysight LCR).
"""

import tkinter as tk
from tkinter import (
    ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
)
import os
import time
import math
import traceback
from datetime import datetime
import numpy as np
import threading
import queue
import atexit

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Backend ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# BACKEND CLASSES
# ===============================================================================

class Lakeshore350_Backend:
    """Controls the Lakeshore Model 350 Temperature Controller."""
    def __init__(self, visa_address, rm):
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        
    def reset_and_clear(self):
        self.instrument.write('*RST')
        time.sleep(0.5)
        self.instrument.write('*CLS')
        time.sleep(1)

    def setup_heater(self, output, resistance_code, max_current_code):
        self.instrument.write(f'HTRSET {output},{resistance_code},{max_current_code},0,1')
        time.sleep(0.5)

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        self.instrument.write(f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)

    def set_setpoint(self, output, temperature_k):
        self.instrument.write(f'SETP {output},{temperature_k}')

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower(), 0)
        self.instrument.write(f'RANGE {output},{range_code}')

    def get_temperature(self, sensor):
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def get_heater_output(self, output):
        return float(self.instrument.query(f'HTR? {output}').strip())

    def close(self):
        if self.instrument:
            try:
                self.set_heater_range(1, 'off')
                time.sleep(0.5)
                self.instrument.close()
            except Exception:
                pass


class LCR_Backend:
    """Controls the Keysight E4980A LCR Meter."""
    def __init__(self, rm):
        self.instrument = None
        self.has_opt001 = False
        self.rm = rm

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        current_v = float(self.instrument.query(":BIAS:VOLT?"))
        if abs(target_v - current_v) < 0.01: return
        if step <= 0:
            self.instrument.write(f":BIAS:VOLT {target_v:.3f}")
            return
        direction = 1 if target_v > current_v else -1
        ramp_points = np.arange(current_v, target_v, direction * step)
        ramp_points = np.append(ramp_points, target_v)
        for v in ramp_points:
            self.instrument.write(f":BIAS:VOLT {v:.3f}")
            time.sleep(dwell)

    def initialize_instrument(self, p):
        inst = self.rm.open_resource(p["lcr_visa"])
        inst.timeout = 60000 
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.instrument = inst

        idn = inst.query("*IDN?").strip()
        if "E4980" not in idn:
            inst.close()
            raise ConnectionError(f"Not an E4980A: {idn}")

        self.has_opt001 = "001" in inst.query("*OPT?")
        v_bias_max = min(2.0, 40.0 if self.has_opt001 else 2.0)
        v_ac_max = min(2.0, 20.0 if self.has_opt001 else 2.0)
        
        if abs(p["dc_bias"]) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V safety limit.")
        if not (0 < p["ac_bias"] <= v_ac_max):
            raise ValueError(f"AC level outside 0-{v_ac_max} Vrms safety limit.")

        inst.write("*RST; *CLS")
        time.sleep(1.0)
        inst.write(":DISP:ENAB ON")
        time.sleep(0.2)
        inst.write(":FUNC:IMP RX")
        inst.write(f":APER {p['aper']}")
        inst.write(":FUNC:IMP:RANG:AUTO ON")
        inst.write(":FORM ASC")
        inst.write(":FUNC:SMON:VAC ON")
        inst.write(":FUNC:SMON:IAC ON")
        time.sleep(0.2)

        inst.write(":AMPL:ALC ON" if p["alc_enabled"] else ":AMPL:ALC OFF")
        inst.write(f":CORR:LENG {p['cable_len']}")
        
        if p["corr_enabled"]:
            inst.write(":CORR:OPEN:STAT ON")
            inst.write(":CORR:SHOR:STAT ON")
        else:
            inst.write(":CORR:OPEN:STAT OFF")
            inst.write(":CORR:SHOR:STAT OFF")
            
        inst.write(f":VOLT {p['ac_bias']}")
        time.sleep(0.5)

        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON")
        
        if abs(p["dc_bias"]) < 1e-9:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT OFF")
        else:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT ON")
            time.sleep(0.5)
            self.safe_ramp_dc_bias(p["dc_bias"])

    def perform_measurement(self, freq, delay):
        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)
        self.instrument.write(":TRIG:IMM")
        vals = self.instrument.query_ascii_values(":FETC?")
        R, X = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return R, X, status
        
    def calculate_impedance_parameters(self, f, R, X):
        omega = 2 * np.pi * f
        omega_safe = omega if omega != 0 else 1e-20
        Z_mag = np.sqrt(R ** 2 + X ** 2)
        Z_mag_safe = Z_mag if Z_mag != 0 else 1e-20
        Z_mag_sq = Z_mag_safe ** 2

        G = R / Z_mag_sq
        B = -X / Z_mag_sq
        G_safe = G if G != 0 else 1e-20
        B_safe = B if B != 0 else 1e-20
        X_safe = X if X != 0 else 1e-20

        Rp = 1.0 / G_safe
        Cp = B / omega_safe
        Cs = -1.0 / (omega_safe * X_safe)
        Ls = X / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        D = G_safe / B_safe
        D_safe = D if D != 0 else 1e-20
        Q = 1.0 / D_safe
        theta_rad = math.atan2(X, R)
        theta_deg = math.degrees(theta_rad)
        Y_mag = 1.0 / Z_mag_safe

        Cp_dp = G / omega_safe
        Cs_dp = Cp_dp

        return [Q, D, G, B, Cp, Lp, Cs, Ls, Z_mag, theta_rad, X, R, theta_deg, Rp, Y_mag, omega, Cp_dp, Cs_dp]

    def close_instrument(self):
        if not self.instrument: return
        try:
            self.safe_ramp_dc_bias(0.0)
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
            time.sleep(0.2)
            self.instrument.close()
        except Exception:
            pass


class Combined_Backend:
    def __init__(self):
        self.lakeshore = None
        self.lcr = None
        self.params = {}
        self.rm = pyvisa.ResourceManager() if pyvisa else None

    def initialize_instruments(self, params):
        if not self.rm: raise ConnectionError("VISA Resource Manager unavailable.")
        self.params = params
        
        self.lakeshore = Lakeshore350_Backend(params['lakeshore_visa'], self.rm)
        self.lakeshore.reset_and_clear()
        self.lakeshore.setup_heater(1, 1, 2)
        
        self.lcr = LCR_Backend(self.rm)
        self.lcr.initialize_instrument(params)

    def close_instruments(self):
        if self.lakeshore: self.lakeshore.close()
        if self.lcr: self.lcr.close_instrument()


# ===============================================================================
# FRONTEND CLASS
# ===============================================================================

class TD_Dielectric_GUI:
    LOGO_SIZE = 110
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_ACCENT_GOLD = "#BA6B5E"
    CLR_ACCENT_GREEN = "#B68B6E"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    
    FONT_SIZE_BASE = 11
    FONT_BASE = ("Segoe UI", FONT_SIZE_BASE)
    FONT_TITLE = ("Segoe UI", FONT_SIZE_BASE + 2, "bold")
    FONT_CONSOLE = ("Consolas", 10)
    
    DATA_HEADER = (
        "Timestamp\tElapsed Time (s)\tTemperature (K)\tFrequency (Hz)\t"
        "Q\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta(rad)\tchi\t"
        "Rs\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''"
    )

    def __init__(self, root):
        self.root = root
        self.root.title("Temperature Dependent Dielectric Measurement")
        self.root.geometry("1650x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1400, 850)

        self.backend = Combined_Backend()
        atexit.register(self.backend.close_instruments)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self.file_location_path = ""
        self.data_queue = queue.Queue()
        self.worker_thread = None
        self.stop_event = threading.Event()
        
        self._last_draw_time = 0.0
        self._redraw_interval = 0.5 

        # Segregated plotting data
        self.data_storage = {
            'time': [], 'temp': [],
            'ref_temp': [], 'ref_cp': [], 'ref_g': [],
            'current_sweep_f': [], 'current_sweep_cp': []
        }
        
        self.log_scale_var = tk.BooleanVar(value=True)
        
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
        style.configure("TLabel", background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure("TCheckbutton", background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure("TLabelframe", background=self.CLR_BG_DARK, bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure("TLabelframe.Label", background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)
        style.configure("TButton", font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER, borderwidth=0)
        style.map("TButton", background=[("active", self.CLR_ACCENT_GOLD)], foreground=[("active", self.CLR_TEXT_DARK)])
        style.configure("Start.TButton", background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.configure("Stop.TButton", background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)

        mpl.rcParams.update({
            "font.family": "Segoe UI", "font.size": self.FONT_SIZE_BASE - 1,
            "axes.titlesize": self.FONT_SIZE_BASE + 1, "axes.labelsize": self.FONT_SIZE_BASE,
            "figure.facecolor": self.CLR_GRAPH_BG,
        })

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side="top", fill="x")
        Label(header, text="Temperature Dependent Dielectric Measurement (R-X)", bg=self.CLR_HEADER, 
              fg=self.CLR_FG_LIGHT, font=("Segoe UI", self.FONT_SIZE_BASE + 4, "bold", "italic")).pack(side="left", padx=20, pady=10)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left_container = ttk.Frame(main_pane)
        main_pane.add(left_container, weight=0)

        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        # Scrollable Left Panel
        canvas = Canvas(left_container, bg=self.CLR_BG_DARK, highlightthickness=0, width=540)
        scrollbar = ttk.Scrollbar(left_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=520)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)
        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill="x", expand=True, padx=10, pady=5)
        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Information")
        ttk.Label(frame, text="Instruments: Lakeshore 350 & Keysight E4980A\n"
                              "Procedure: Hardware T-Ramp w/ Continuous Freq Sweeps", 
                  font=("Segoe UI", self.FONT_SIZE_BASE, "bold")).pack(padx=10, pady=10, anchor='w')
        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Experiment Parameters")
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pad = (2, 5)

        self._add_entry(frame, "Sample Name", "sample_name", 0, 0, 2, "Sample_TD_Dielectric")
        self._add_entry(frame, "Start Temp (K)", "start_temp", 2, 0, 1, "300")
        self._add_entry(frame, "End Temp (K)", "end_temp", 2, 1, 1, "400")
        self._add_entry(frame, "Ramp Rate (K/min)", "rate", 4, 0, 1, "2.0")
        self._add_entry(frame, "Safety Cutoff (K)", "cutoff", 4, 1, 1, "420")
        
        self._add_entry(frame, "AC Bias Voltage (V)", "ac_bias", 6, 0, 1, "1.0")
        self._add_entry(frame, "DC Bias Voltage (V)", "dc_bias", 6, 1, 1, "0.0")
        
        Label(frame, text="Frequencies to Measure (Hz, CSV):", font=self.FONT_BASE).grid(row=8, column=0, columnspan=2, padx=10, pady=(10, 0), sticky="w")
        self.entries["frequencies"] = Entry(frame, font=self.FONT_BASE)
        self.entries["frequencies"].insert(0, "1000, 2000, 3000, 5000, 7000, 10000, 25000, 50000, 70000, 90000, 100000, 120000, 150000, 170000, 200000, 250000, 500000, 1000000, 1500000, 2000000")
        self.entries["frequencies"].grid(row=9, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")

        self._add_entry(frame, "Delay per point (s)", "delay", 10, 0, 1, "0.2")
        
        Label(frame, text="Aperture (:APER):", font=self.FONT_BASE).grid(row=10, column=1, padx=10, pady=(2, 0), sticky="w")
        self.aper_cb = ttk.Combobox(frame, font=self.FONT_BASE, state="readonly", values=["SHOR", "MED", "LONG"])
        self.aper_cb.set("MED")
        self.aper_cb.grid(row=11, column=1, padx=10, pady=(0, 10), sticky="ew")

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Enable ALC", variable=self.var_alc).grid(row=12, column=0, padx=10, sticky="w")
        ttk.Checkbutton(frame, text="Enable Open/Short Corr.", variable=self.var_corr).grid(row=12, column=1, padx=10, sticky="w")

        Label(frame, text="Cable Length (m):", font=self.FONT_BASE).grid(row=13, column=0, padx=10, pady=pad, sticky="w")
        self.cable_cb = ttk.Combobox(frame, font=self.FONT_BASE, state="readonly", values=["0", "1", "2", "4"])
        self.cable_cb.set("1")
        self.cable_cb.grid(row=13, column=1, padx=10, pady=pad, sticky="ew")

        Label(frame, text="Lakeshore VISA:", font=self.FONT_BASE).grid(row=14, column=0, padx=10, pady=pad, sticky="w")
        Label(frame, text="LCR Meter VISA:", font=self.FONT_BASE).grid(row=14, column=1, padx=10, pady=pad, sticky="w")
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state="readonly")
        self.lakeshore_cb.grid(row=15, column=0, padx=10, pady=(0,10), sticky="ew")
        self.lcr_cb = ttk.Combobox(frame, font=self.FONT_BASE, state="readonly")
        self.lcr_cb.grid(row=15, column=1, padx=10, pady=(0,10), sticky="ew")

        self.scan_btn = ttk.Button(frame, text="Scan Instruments", command=self._scan_for_visa)
        self.scan_btn.grid(row=16, column=0, padx=10, pady=5, sticky="ew")
        ttk.Button(frame, text="Browse Save Loc...", command=self._browse).grid(row=16, column=1, padx=10, pady=5, sticky="ew")

        self.start_btn = ttk.Button(frame, text="Start Measurement", command=self.start_measurement, style="Start.TButton")
        self.start_btn.grid(row=17, column=0, padx=10, pady=15, sticky="ew")
        self.stop_btn = ttk.Button(frame, text="Stop", command=self.stop_measurement, style="Stop.TButton", state="disabled")
        self.stop_btn.grid(row=17, column=1, padx=10, pady=15, sticky="ew")

        return frame

    def _add_entry(self, parent, text, key, r, c, colsp=1, default=""):
        Label(parent, text=f"{text}:", font=self.FONT_BASE).grid(row=r, column=c, padx=10, pady=(2, 0), sticky="w")
        ent = Entry(parent, font=self.FONT_BASE)
        ent.grid(row=r + 1, column=c, columnspan=colsp, padx=10, pady=(0, 10), sticky="ew")
        ent.insert(0, default)
        self.entries[key] = ent

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text="Console Output", bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console = scrolledtext.ScrolledText(frame, state="disabled", bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap="word", height=8, bd=0)
        self.console.pack(pady=5, padx=5, fill="both", expand=True)
        return frame

    def create_graph_frame(self, parent):
        top_bar = tk.Frame(parent, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x')
        ttk.Checkbutton(top_bar, text="Logarithmic Y-Axis (Cp, G)", variable=self.log_scale_var, command=self._update_y_scale).pack(side='right', padx=5, pady=2)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        
        # Plot 1: Cp vs Temp (Ref Freq)
        self.ax_cp_t = self.figure.add_subplot(gs[0, 0])
        self.line_cp_t, = self.ax_cp_t.plot([], [], color="#C00000", marker="o", markersize=3, linestyle="-")
        self.ax_cp_t.set_title("Capacitance vs. Temperature (Ref Freq)", fontweight='bold')
        self.ax_cp_t.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp_t.set_xlabel("Temperature (K)")
        self.ax_cp_t.grid(True, linestyle="--", alpha=0.6)

        # Plot 2: G vs Temp (Ref Freq)
        self.ax_g_t = self.figure.add_subplot(gs[0, 1])
        self.line_g_t, = self.ax_g_t.plot([], [], color="#2A6B3A", marker="s", markersize=3, linestyle="-")
        self.ax_g_t.set_title("Conductance vs. Temperature (Ref Freq)", fontweight='bold')
        self.ax_g_t.set_ylabel("Conductance, G (S)")
        self.ax_g_t.set_xlabel("Temperature (K)")
        self.ax_g_t.grid(True, linestyle="--", alpha=0.6)

        # Plot 3: Cp vs Freq (Current Sweep)
        self.ax_cp_f = self.figure.add_subplot(gs[1, 0])
        self.line_cp_f, = self.ax_cp_f.plot([], [], color="#005A9C", marker="D", markersize=3, linestyle="-")
        self.ax_cp_f.set_title("Latest Sweep: Cp vs Frequency", fontweight='bold')
        self.ax_cp_f.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp_f.set_xlabel("Frequency (Hz)")
        self.ax_cp_f.set_xscale("log")
        self.ax_cp_f.grid(True, linestyle="--", alpha=0.6)

        # Plot 4: Temp vs Time (Ramp Monitoring)
        self.ax_t_time = self.figure.add_subplot(gs[1, 1])
        self.line_t_time, = self.ax_t_time.plot([], [], color="#BA6B5E", linestyle="-")
        self.ax_t_time.set_title("Temperature Ramp Monitoring", fontweight='bold')
        self.ax_t_time.set_ylabel("Temperature (K)")
        self.ax_t_time.set_xlabel("Time (s)")
        self.ax_t_time.grid(True, linestyle="--", alpha=0.6)

        self.figure.subplots_adjust(left=0.08, right=0.96, top=0.92, bottom=0.08, hspace=0.35, wspace=0.25)
        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state="normal")
        self.console.insert("end", f"[{ts}] {msg}\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _scan_for_visa(self):
        if not PYVISA_AVAILABLE or not self.backend.rm:
            self.log("ERROR: PyVISA unavailable.")
            return
        self.log("Scanning VISA resources...")
        try:
            resources = self.backend.rm.list_resources()
            found = []
            ls_lbl, lcr_lbl = None, None
            for res in resources:
                idn = "Unknown"
                try:
                    with self.backend.rm.open_resource(res) as dev:
                        dev.timeout = 1000
                        idn = dev.query("*IDN?").strip()
                except Exception: pass
                label = f"{res}  ->  {idn}"
                found.append(label)
                if ("LSCI" in idn or "MODEL350" in idn.upper()) and not ls_lbl: ls_lbl = label
                if "E4980" in idn and not lcr_lbl: lcr_lbl = label
                
            self.lakeshore_cb['values'] = found
            self.lcr_cb['values'] = found
            if ls_lbl: self.lakeshore_cb.set(ls_lbl)
            if lcr_lbl: self.lcr_cb.set(lcr_lbl)
            self.log("Scan complete.")
        except Exception as e:
            self.log(f"Scan error: {e}")

    def _browse(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location: {path}")

    def _update_y_scale(self):
        scale = 'log' if self.log_scale_var.get() else 'linear'
        self.ax_cp_t.set_yscale(scale)
        self.ax_g_t.set_yscale(scale)
        self.ax_cp_f.set_yscale(scale)
        self._update_live_plots(force=True)

    def start_measurement(self):
        try:
            freq_str = self.entries["frequencies"].get()
            freq_list = [float(f.strip()) for f in freq_str.split(',') if f.strip()]
            if not freq_list: raise ValueError("Frequency list cannot be empty.")

            params = {
                'sample_name': self.entries["sample_name"].get(),
                'start_temp': float(self.entries["start_temp"].get()),
                'end_temp': float(self.entries["end_temp"].get()),
                'rate': float(self.entries["rate"].get()),
                'cutoff': float(self.entries["cutoff"].get()),
                'ac_bias': float(self.entries["ac_bias"].get()),
                'dc_bias': float(self.entries["dc_bias"].get()),
                'delay': float(self.entries["delay"].get()),
                'aper': self.aper_cb.get(),
                'alc_enabled': self.var_alc.get(),
                'corr_enabled': self.var_corr.get(),
                'cable_len': self.cable_cb.get(),
                'freq_list': freq_list,
                'lakeshore_visa': self.lakeshore_cb.get().split("  ->  ")[0].strip(),
                'lcr_visa': self.lcr_cb.get().split("  ->  ")[0].strip()
            }
            if not params['lakeshore_visa'] or not params['lcr_visa'] or not self.file_location_path:
                raise ValueError("VISA addresses and Save Location are required.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"{params['sample_name']}_{ts}_TD_Dielectric.txt"
            self.data_filepath = os.path.join(self.file_location_path, fname)
            
            with open(self.data_filepath, "w", encoding="utf-8") as f:
                f.write(f"# Sample: {params['sample_name']} | AC: {params['ac_bias']}V | DC: {params['dc_bias']}V\n")
                f.write(f"# T-Range: {params['start_temp']}K to {params['end_temp']}K at {params['rate']} K/min\n")
                f.write(self.DATA_HEADER + "\n")

            self.backend.initialize_instruments(params)
            
            # Setup plots for reference frequency
            ref_f = freq_list[0]
            self.ax_cp_t.set_title(f"Capacitance vs. Temp (@ {ref_f} Hz)", fontweight='bold')
            self.ax_g_t.set_title(f"Conductance vs. Temp (@ {ref_f} Hz)", fontweight='bold')
            
            for key in self.data_storage: self.data_storage[key].clear()
            self._update_live_plots(force=True)

            self.is_stabilizing, self.is_running = True, False
            self.start_btn.config(state='disabled')
            self.stop_btn.config(state='normal')
            self.scan_btn.config(state='disabled')
            
            self.stop_event.clear()
            self.worker_thread = threading.Thread(target=self._measurement_worker, daemon=True)
            self.worker_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Init Error", str(e))

    def stop_measurement(self):
        if self.is_running or self.is_stabilizing:
            self.is_running = self.is_stabilizing = False
            self.stop_event.set()
            self.backend.close_instruments()
            self.log("Sweep stopped and instruments disconnected.")
            self.start_btn.config(state='normal')
            self.stop_btn.config(state='disabled')
            self.scan_btn.config(state='normal')

    def _measurement_worker(self):
        params = self.backend.params
        try:
            # --- 1. STABILIZATION ---
            while self.is_stabilizing:
                if self.stop_event.is_set(): return
                cur_t = self.backend.lakeshore.get_temperature('A')
                self.data_queue.put(f"LOG:Stabilizing... T={cur_t:.3f} K (Target: {params['start_temp']} K)")
                
                if cur_t > params['start_temp'] + 5.0:
                    self.backend.lakeshore.set_heater_range(1, 'off')
                else:
                    self.backend.lakeshore.set_heater_range(1, 'high')
                    self.backend.lakeshore.set_setpoint(1, params['start_temp'])

                if abs(cur_t - params['start_temp']) < 2.0:
                    self.data_queue.put(f"LOG:Stabilized at {cur_t:.3f} K. Starting measurements...")
                    time.sleep(5)
                    self.is_stabilizing, self.is_running = False, True
                    break
                time.sleep(2)

            if not self.is_running: return

            # --- 2. RAMP & MEASURE ---
            self.backend.lakeshore.set_setpoint(1, params['end_temp'])
            self.backend.lakeshore.setup_ramp(1, params['rate'])
            self.backend.lakeshore.set_heater_range(1, 'high')
            self.data_queue.put(f"LOG:Ramp started to {params['end_temp']} K at {params['rate']} K/min.")
            
            self.start_time = time.time()
            
            while self.is_running:
                if self.stop_event.is_set(): break
                
                base_t = self.backend.lakeshore.get_temperature('A')
                if base_t >= params['cutoff']:
                    self.data_queue.put("CUTOFF")
                    break
                if base_t >= params['end_temp']:
                    self.data_queue.put("COMPLETE")
                    break

                # Freq Sweep
                for i, f in enumerate(params['freq_list']):
                    if self.stop_event.is_set(): break
                    t_val = self.backend.lakeshore.get_temperature('A')
                    R, X, status = self.backend.lcr.perform_measurement(f, params['delay'])
                    elap = time.time() - self.start_time
                    self.data_queue.put((elap, t_val, f, R, X, status, i == 0))
                    
        except Exception as e:
            self.data_queue.put(e)

    def _process_data_queue(self):
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()
                if isinstance(item, str) and item.startswith("LOG:"):
                    self.log(item[4:])
                elif item == "CUTOFF":
                    self.log("SAFETY CUTOFF REACHED.")
                    self.stop_measurement()
                    return
                elif item == "COMPLETE":
                    self.log("Target temperature reached successfully.")
                    self.stop_measurement()
                    return
                elif isinstance(item, Exception):
                    self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
                    self.stop_measurement()
                    return
                else:
                    self._handle_measurement(item)
        except queue.Empty: pass
        if self.is_running or self.is_stabilizing:
            self.root.after(100, self._process_data_queue)

    def _handle_measurement(self, data):
        elap, t, f, R, X, stat, is_first = data
        calc = self.backend.lcr.calculate_impedance_parameters(f, R, X)
        
        # Save to disk
        row = [datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elap:.2f}", f"{t:.4f}", f"{f}"] + [f"{v:.6E}" for v in calc]
        with open(self.data_filepath, "a", encoding="utf-8") as file:
            file.write("\t".join(row) + "\n")
            file.flush()

        # GUI Ploting Logic
        Cp = calc[4]
        G = calc[2]
        
        self.data_storage['time'].append(elap)
        self.data_storage['temp'].append(t)
        
        if is_first:
            self.data_storage['ref_temp'].append(t)
            self.data_storage['ref_cp'].append(Cp)
            self.data_storage['ref_g'].append(G)
            self.data_storage['current_sweep_f'].clear()
            self.data_storage['current_sweep_cp'].clear()
            self.log(f"Sweep starting @ T={t:.2f}K")
            
        self.data_storage['current_sweep_f'].append(f)
        self.data_storage['current_sweep_cp'].append(Cp)
        self._update_live_plots()

    def _update_live_plots(self, force=False):
        now = time.time()
        if not force and (now - self._last_draw_time) < self._redraw_interval: return
        self._last_draw_time = now

        self.line_cp_t.set_data(self.data_storage['ref_temp'], self.data_storage['ref_cp'])
        self.line_g_t.set_data(self.data_storage['ref_temp'], self.data_storage['ref_g'])
        self.line_cp_f.set_data(self.data_storage['current_sweep_f'], self.data_storage['current_sweep_cp'])
        self.line_t_time.set_data(self.data_storage['time'], self.data_storage['temp'])

        for ax in [self.ax_cp_t, self.ax_g_t, self.ax_cp_f, self.ax_t_time]:
            ax.relim()
            ax.autoscale_view()
        self.canvas.draw_idle()

    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno("Exit", "Measurement running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else: self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = TD_Dielectric_GUI(root)
    root.mainloop()

