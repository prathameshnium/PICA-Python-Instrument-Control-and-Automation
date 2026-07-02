"""
Module:             CT_E4980A_L350_T_Control_GUI.py
Purpose:            GUI module for Temperature-Dependent Dielectric
                    Measurement (Keysight E4980A + Lakeshore 350).
Original Authors:   Prathamesh Deshmukh (template programs)
Integrated by:      AI-assisted merge per design specification
Version:            V: 1.0
"""

# ===============================================================================
# IMPORTS  (union of both source programs)
# ===============================================================================

import tkinter as tk
from tkinter import (
    ttk, Label, Entry, LabelFrame, filedialog, messagebox,
    scrolledtext, Canvas,
)
import threading
import queue
import os
import time
import math
import traceback
import atexit
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
import runpy
from multiprocessing import Process

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

# --- PyVISA for instrument communication ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# UTILITY LAUNCH FUNCTIONS  (verbatim from source programs)
# ===============================================================================

def run_script_process(script_path):
    """Wrapper to execute a script using runpy in its own directory."""
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
        plotter_path = os.path.join(
            script_dir, "..", "..", "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "..", "..",
            "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}")


# ===============================================================================
# BACKEND: LAKESHORE 350  (verbatim from R-T template)
# ===============================================================================

class Lakeshore350_Backend:
    """A class to control the Lakeshore Model 350 Temperature Controller."""

    def __init__(self, visa_address):
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        print(f"Lakeshore Connected: "
              f"{self.instrument.query('*IDN?').strip()}")

    def reset_and_clear(self):
        self.instrument.write('*RST')
        time.sleep(0.5)
        self.instrument.write('*CLS')
        time.sleep(1)

    def setup_heater(self, output, resistance_code, max_current_code):
        self.instrument.write(
            f'HTRSET {output},{resistance_code},{max_current_code},0,1')
        time.sleep(0.5)

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        """Configures the instrument's internal ramp generator."""
        self.instrument.write(
            f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)

    def set_setpoint(self, output, temperature_k):
        self.instrument.write(f'SETP {output},{temperature_k}')

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None:
            raise ValueError("Invalid heater range.")
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
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


# ===============================================================================
# BACKEND: KEYSIGHT E4980A LCR  (verbatim from freq-scan program)
# ===============================================================================

class LCR_Backend:
    """Handles all SCPI communication with the Keysight E4980A."""

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.has_opt001 = False
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def _check_errors(self, context=""):
        """Drain SCPI error queue; raise on any error."""
        errors = []
        for _ in range(20):
            err = self.instrument.query(":SYST:ERR?").strip()
            if err.startswith("0,") or err.startswith("+0,"):
                break
            errors.append(err)
        if errors:
            raise RuntimeError(f"SCPI errors after {context}: {errors}")

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        """Safely ramps the DC bias to the target voltage."""
        current_v = float(self.instrument.query(":BIAS:VOLT?"))
        if abs(target_v - current_v) < 0.01:
            return
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
        """Configures the instrument for a multi-frequency R-X measurement."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

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

        # Strict Safety Ceilings: Hard cap at 2.0 V regardless of options
        v_bias_max = min(2.0, 40.0 if self.has_opt001 else 2.0)
        v_ac_max   = min(2.0, 20.0 if self.has_opt001 else 2.0)

        if abs(p["dc_bias"]) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V safety limit.")
        if not (0 < p["ac_bias"] <= v_ac_max):
            raise ValueError(
                f"AC level outside 0-{v_ac_max} Vrms safety limit.")

        # --- Instrument configuration ---
        inst.write("*RST; *CLS")
        time.sleep(1.0)
        inst.write(":DISP:ENAB ON")
        time.sleep(0.2)

        inst.write(":FUNC:IMP RX")
        inst.write(f":APER {p['aper']}")
        inst.write(":FUNC:IMP:RANG:AUTO ON")
        time.sleep(0.2)

        inst.write(":FORM ASC")

        inst.write(":FUNC:SMON:VAC ON")
        inst.write(":FUNC:SMON:IAC ON")
        inst.write(":FUNC:SMON:VDC OFF")
        inst.write(":FUNC:SMON:IDC OFF")
        time.sleep(0.2)

        if p["alc_enabled"]:
            inst.write(":AMPL:ALC ON")
        else:
            inst.write(":AMPL:ALC OFF")
        time.sleep(0.2)

        inst.write(f":CORR:LENG {p['cable_len']}")
        if p["corr_enabled"]:
            inst.write(":CORR:OPEN:STAT ON")
            inst.write(":CORR:SHOR:STAT ON")
        else:
            inst.write(":CORR:OPEN:STAT OFF")
            inst.write(":CORR:SHOR:STAT OFF")
        time.sleep(0.2)

        inst.write(f":VOLT {p['ac_bias']}")
        time.sleep(0.5)

        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON")
        time.sleep(0.2)

        # --- Conditional DC Bias Handling ---
        if abs(p["dc_bias"]) < 1e-9:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT OFF")
        else:
            inst.write(":BIAS:VOLT 0")
            inst.write(":BIAS:STAT ON")
            time.sleep(0.5)
            if self.has_opt001:
                print(f"  Ramping DC Bias to {p['dc_bias']} V...")
                self.safe_ramp_dc_bias(p["dc_bias"])
            else:
                if p["dc_bias"] not in (1.5, 2.0):
                    raise ValueError(
                        "Without Option 001, DC bias must be 0, 1.5 or 2 V.")
                inst.write(f":BIAS:VOLT {p['dc_bias']}")
                time.sleep(1.0)

        self._check_errors("configuration")
        print(f"  Connected & configured (RX mode): {idn}")

    def perform_measurement(self, freq, delay):
        """Set frequency, settle, trigger one measurement, fetch R, X, status."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)
        self.instrument.write(":TRIG:IMM")
        vals = self.instrument.query_ascii_values(":FETC?")
        R, X = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return R, X, status

    def close_instrument(self):
        print("--- [Backend] Closing LCR instrument connection. ---")
        if not self.instrument:
            return
        try:
            if self.has_opt001:
                print("  Ramping bias to zero and turning off...")
                self.safe_ramp_dc_bias(0.0)
            else:
                self.instrument.write(":BIAS:VOLT 0")
                time.sleep(0.5)
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
            time.sleep(0.2)
        except Exception as e:
            print(f"  Warning during LCR shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  E4980A connection closed.")
            finally:
                self.instrument = None


# ===============================================================================
# BACKEND: COMBINED  (Appendix A.1 — per-point temperature binding)
# ===============================================================================

class Combined_Backend:
    """Manages the Lakeshore 350 and the Keysight E4980A together."""

    def __init__(self):
        self.lakeshore = None
        self.lcr = LCR_Backend()
        self.params = {}

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore350_Backend(parameters['lakeshore_visa'])
        self.lakeshore.reset_and_clear()
        self.lakeshore.setup_heater(1, 1, 2)
        # LCR_Backend.initialize_instrument expects keys:
        #   lcr_visa, ac_bias, dc_bias, aper, alc_enabled,
        #   corr_enabled, cable_len
        self.lcr.initialize_instrument(parameters)

    def measure_frequency_sweep(self, frequencies, delay):
        """
        Appendix A.1 — CRITICAL:
        Reads Lakeshore temperature INSIDE the frequency loop so every
        single data point is bound to the exact temperature at which it
        was measured.  No cycle-average temperature is computed.
        """
        htr = self.lakeshore.get_heater_output(1)
        points = []
        for f in frequencies:
            temp = self.lakeshore.get_temperature('A')   # T for THIS point
            R, X, status = self.lcr.perform_measurement(f, delay)
            points.append((temp, f, R, X, status))
        return {'heater': htr, 'points': points}

    def close_instruments(self):
        print("\n--- [Backend] Closing all instrument connections. ---")
        try:
            self.lcr.close_instrument()
        finally:
            if self.lakeshore:
                self.lakeshore.close()


# ===============================================================================
# FRONT END (GUI)
# ===============================================================================

class Integrated_CT_GUI:
    """
    Main GUI application for Temperature-Dependent Dielectric Measurement.
    Combines Lakeshore 350 temperature control with E4980A multi-frequency
    LCR measurement.
    """

    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110

    # --- Default frequency list (Section 4 of original instructions) ---
    DEFAULT_FREQS = (
        "1000, 2000, 3000, 5000, 7000, 10000, 25000, 50000, "
        "70000, 90000, 100000, 120000, 150000, 170000, 200000, "
        "250000, 500000, 1000000, 1500000, 2000000"
    )

    # --- Appendix A.2: exact 19-column header (Temperature + 18 derived) ---
    DATA_HEADER = (
        "Temperature\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\t"
        "theta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\t"
        "Cp''\tCs''"
    )

    # --- Robust logo path ---
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "..", "..",
            "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme constants (identical to both source programs) ---
    CLR_BG_DARK     = '#B8A392'
    CLR_HEADER      = '#E5DCD3'
    CLR_FG_LIGHT    = '#2C2825'
    CLR_TEXT_DARK   = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN= '#B68B6E'
    CLR_ACCENT_RED  = '#BA6B5E'
    CLR_CONSOLE_BG  = '#E5DCD3'
    CLR_GRAPH_BG    = '#F4EFEA'
    FONT_SIZE_BASE  = 11
    FONT_BASE       = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL  = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE      = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE    = ('Consolas', 10)

    # ------------------------------------------------------------------
    def __init__(self, root):
        self.root = root
        self.root.title(
            "E4980A & L350: Dielectric vs. Temperature (T-Control)")
        self.root.geometry("1600x980")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 880)

        # --- State flags ---
        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self._last_draw_time = 0.0
        self._redraw_interval = 0.25   # seconds; redraw at most ~4×/sec

        # --- Backend ---
        self.backend = Combined_Backend()
        atexit.register(self.backend.close_instruments)

        # --- File / frequency state ---
        self.file_location_path = ""
        self.frequencies = []
        self.freq_filepaths = {}
        self.plot_freq = None

        # --- Data storage (Appendix A.4: keyed per frequency) ---
        self.data_storage = {
            'time': [],
            'temperature': [],
            'cp': {},   # {freq: {'T': [...], 'v': [...]}}
            'g':  {},   # {freq: {'T': [...], 'v': [...]}}
        }

        # --- UI variables ---
        self.log_scale_var = tk.BooleanVar(value=False)
        self.logo_image = None

        # --- Threading ---
        self.data_queue = queue.Queue()
        self.measurement_thread = None

        # --- Build the GUI ---
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ==================================================================
    # STYLES
    # ==================================================================
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TCheckbutton', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TLabelframe', background=self.CLR_BG_DARK,
                        bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK,
                        foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER,
                        borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover',  self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover',  self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', font=self.FONT_BASE,
                        padding=(10, 9),
                        background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton',
                  background=[('active', '#8AB845'),
                              ('hover',  '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE,
                        padding=(10, 9),
                        background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton',
                  background=[('active', '#D63C2A'),
                              ('hover',  '#D63C2A')])

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    # ==================================================================
    # LAYOUT
    # ==================================================================
    def create_widgets(self):
        self.create_header()

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # --- Left panel (scrollable) ---
        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK,
                        highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container,
                                  orient="vertical",
                                  command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame,
                             anchor="nw", width=500)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame).pack(
            fill='x', padx=10, pady=5)
        self.create_input_frame(scrollable_frame).pack(
            fill='x', padx=10, pady=5)
        self.create_console_frame(scrollable_frame).pack(
            fill='both', expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

    # ------------------------------------------------------------------
    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        Label(header_frame,
              text="E4980A & L350: Dielectric vs. Temperature (T-Control)",
              bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD,
              font=font_title_main).pack(side='left', padx=20, pady=10)

        ttk.Button(header_frame, text="📈",
                   command=launch_plotter_utility,
                   width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header_frame, text="📟",
                   command=launch_gpib_scanner,
                   width=3).pack(side='right', padx=(0, 5), pady=5)

        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}",
              bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
              font=self.FONT_SUB_LABEL).pack(
                  side='right', padx=20, pady=10)

    # ------------------------------------------------------------------
    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE,
                             height=self.LOGO_SIZE,
                             bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3,
                         padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception:
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    text="LOGO\nERROR", font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT, justify='center')
        else:
            logo_canvas.create_text(
                self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                text="LOGO\nMISSING", font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT, justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 6, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font,
                  background=self.CLR_BG_DARK).grid(
                      row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre",
                  font=institute_font,
                  background=self.CLR_BG_DARK).grid(
                      row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = (
            "Program Name: Dielectric vs. Temperature (T-Control)\n"
            "Instruments: Lakeshore 350, Keysight E4980A\n"
            "Function: FUNC:IMP RX, multi-frequency scan per T point")
        ttk.Label(frame, text=details_text, justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10),
            sticky='w')

        return frame

    # ------------------------------------------------------------------
    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters',
                           relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)
        padx_val = 10
        r = 0

        # --- Sample Name (span 2) ---
        Label(frame, text="Sample Name:").grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=pady_val, sticky='w')
        r += 1
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(0, 10), sticky='ew')
        r += 1

        # --- Start Temp | End Temp ---
        Label(frame, text="Start Temp (K):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="End Temp (K):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Start Temp"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 5), sticky='ew')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["End Temp"].grid(
            row=r, column=1, padx=(5, 10), pady=(0, 5), sticky='ew')
        r += 1

        # --- Ramp Rate | Safety Cutoff ---
        Label(frame, text="Ramp Rate (K/min):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="Safety Cutoff (K):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Rate"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.entries["Cutoff"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Cutoff"].grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- AC Bias | DC Bias ---
        Label(frame, text="AC Bias Voltage (V):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="DC Bias Voltage (V):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["AC Bias"] = Entry(frame, font=self.FONT_BASE)
        self.entries["AC Bias"].insert(0, "1.0")
        self.entries["AC Bias"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.entries["DC Bias"] = Entry(frame, font=self.FONT_BASE)
        self.entries["DC Bias"].insert(0, "0.0")
        self.entries["DC Bias"].grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Delay | Aperture ---
        Label(frame, text="Delay per Freq (s):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="Aperture (:APER):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].insert(0, "0.2")
        self.entries["Delay"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.aper_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=["SHOR", "MED", "LONG"])
        self.aper_combobox.set("MED")
        self.aper_combobox.grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Checkbuttons: ALC, Corrections ---
        self.var_alc  = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Enable Auto Level Control (ALC)",
                        variable=self.var_alc).grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=2, sticky='w')
        r += 1
        ttk.Checkbutton(frame, text="Enable Open/Short Corrections",
                        variable=self.var_corr).grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=2, sticky='w')
        r += 1

        # --- Cable Length ---
        Label(frame, text="Cable Length (m):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.cable_len_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=["0", "1", "2", "4"])
        self.cable_len_combobox.set("1")
        self.cable_len_combobox.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        r += 1

        # --- Frequency list text box (Section 4) ---
        Label(frame, text="Frequencies (Hz, comma-separated):").grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(5, 0), sticky='w')
        r += 1
        self.freq_text = tk.Text(frame, font=self.FONT_BASE,
                                 height=3, wrap='word')
        self.freq_text.insert('1.0', self.DEFAULT_FREQS)
        self.freq_text.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(0, 10), sticky='ew')
        r += 1

        # --- Plot Frequency dropdown (Appendix A.4) ---
        Label(frame, text="Live Plot Frequency:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.plot_freq_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='disabled')
        self.plot_freq_cb.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=(0, 10), sticky='ew')
        self.plot_freq_cb.bind(
            "<<ComboboxSelected>>", self._on_plot_freq_change)
        r += 1

        # --- Lakeshore VISA | LCR VISA ---
        Label(frame, text="Lakeshore VISA:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="LCR (E4980A) VISA:").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.lakeshore_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.lcr_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lcr_cb.grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Scan for Instruments ---
        self.scan_button = ttk.Button(
            frame, text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=4, sticky='ew')
        r += 1

        # --- Browse Destination Folder ---
        self.file_button = ttk.Button(
            frame, text="Browse Destination Folder...",
            command=self._browse_file_location)
        self.file_button.grid(
            row=r, column=0, columnspan=2, padx=padx_val,
            pady=4, sticky='ew')
        r += 1

        # --- Start | Stop ---
        self.start_button = ttk.Button(
            frame, text="Start Measurement",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=r, column=0, padx=padx_val, pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame, text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton', state='disabled')
        self.stop_button.grid(
            row=r, column=1, padx=padx_val, pady=(10, 10), sticky='ew')

        return frame

    # ------------------------------------------------------------------
    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove',
                           bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                           font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled',
            bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', bd=0, height=10)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan "
                 "for instruments.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    # ------------------------------------------------------------------
    def create_graph_frame(self, parent):
        graph_container = LabelFrame(
            parent, text='Live Graphs', relief='groove',
            bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK,
            font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        # --- Log-scale checkbox ---
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        self.log_scale_cb = ttk.Checkbutton(
            top_bar, text="Logarithmic Cp Axis",
            variable=self.log_scale_var,
            command=self._update_y_scale)
        self.log_scale_cb.pack(side='right', padx=5)

        # --- Matplotlib figure with 3 axes ---
        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG,
                             layout='constrained')
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        # Main: Cp vs Temperature
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED,
            marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Cp vs. Temperature", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Capacitance, Cp (F)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        # Sub 1: G vs Temperature
        self.line_sub1, = self.ax_sub1.plot(
            [], [], color=self.CLR_ACCENT_GOLD,
            marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Conductance, G (S)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        # Sub 2: Temperature vs Time
        self.line_sub2, = self.ax_sub2.plot(
            [], [], color=self.CLR_ACCENT_GREEN,
            marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)

        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ==================================================================
    # IMPEDANCE CALCULATIONS  (verbatim from freq-scan program)
    # ==================================================================
    def calculate_impedance_parameters(self, f, R, X):
        """
        Calculates all 18 parameters from measured R (series resistance)
        and X (reactance).

        Complex impedance:  Z = R + jX
        Complex admittance: Y = 1/Z = (R - jX) / |Z|^2
                           G = Re(Y) = R / |Z|^2
                           B = Im(Y) = -X / |Z|^2

        Returns list of 18 values in DATA_HEADER column order:
            Q, D, G, B, Cp, Lp, Cs, Ls, |Z|, theta(rad), chi, Rs,
            theta(deg), Rp, 1/|Z|, omega, Cp'', Cs''
        """
        omega = 2 * np.pi * f
        omega_safe = omega if omega != 0 else 1e-20

        Z_mag = np.sqrt(R ** 2 + X ** 2)
        Z_mag_safe = Z_mag if Z_mag != 0 else 1e-20
        Z_mag_sq = Z_mag_safe ** 2

        # Admittance components
        G = R / Z_mag_sq    # conductance (real part of Y)
        B = -X / Z_mag_sq   # susceptance (imaginary part of Y)

        G_safe = G if G != 0 else 1e-20
        B_safe = B if B != 0 else 1e-20
        X_safe = X if X != 0 else 1e-20

        # Derived parameters
        Rp = 1.0 / G_safe
        Cp = B / omega_safe
        Cs = -1.0 / (omega_safe * X_safe)
        Ls = X / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        D = G_safe / B_safe   # dissipation factor
        D_safe = D if D != 0 else 1e-20
        Q = 1.0 / D_safe

        theta_rad = math.atan2(X, R)
        theta_deg = math.degrees(theta_rad)

        chi = X       # reactance
        Rs = R        # series resistance (directly measured)

        Y_mag = 1.0 / Z_mag_safe

        # Complex capacitance C* = C' - jC''
        Cp_double_prime = G / omega_safe
        Cs_double_prime = Cp_double_prime

        return [
            Q,                  # 0
            D,                  # 1
            G,                  # 2
            B,                  # 3
            Cp,                 # 4
            Lp,                 # 5
            Cs,                 # 6
            Ls,                 # 7
            Z_mag,              # 8
            theta_rad,          # 9
            chi,                # 10
            Rs,                 # 11
            theta_deg,          # 12
            Rp,                 # 13
            Y_mag,              # 14
            omega,              # 15
            Cp_double_prime,    # 16
            Cs_double_prime,    # 17
        ]

    # ==================================================================
    # FREQUENCY PARSING  (Section 4)
    # ==================================================================
    def _parse_frequencies(self):
        raw = self.freq_text.get('1.0', 'end').replace('\n', ' ')
        freqs = []
        for tok in raw.split(','):
            tok = tok.strip()
            if not tok:
                continue
            f = float(tok)
            if not (20 <= f <= 2e6):
                raise ValueError(
                    f"Frequency {f} Hz outside E4980A range "
                    f"(20 Hz - 2 MHz).")
            freqs.append(f)
        if not freqs:
            raise ValueError("Frequency list is empty.")
        return sorted(set(freqs))

    # ==================================================================
    # LOGGING
    # ==================================================================
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _handle_log_message(self, message):
        self.log(message)

    # ==================================================================
    # START / STOP
    # ==================================================================
    def start_measurement(self):
        try:
            # --- Parse frequencies ---
            self.frequencies = self._parse_frequencies()

            # --- Gather parameters ---
            params = {
                'sample_name':   self.entries["Sample Name"].get(),
                'start_temp':    float(self.entries["Start Temp"].get()),
                'end_temp':      float(self.entries["End Temp"].get()),
                'rate':          float(self.entries["Rate"].get()),
                'cutoff':        float(self.entries["Cutoff"].get()),
                'ac_bias':       float(self.entries["AC Bias"].get()),
                'dc_bias':       float(self.entries["DC Bias"].get()),
                'delay':         float(self.entries["Delay"].get()),
                'aper':          self.aper_combobox.get(),
                'alc_enabled':   self.var_alc.get(),
                'corr_enabled':  self.var_corr.get(),
                'cable_len':     self.cable_len_combobox.get(),
                'lakeshore_visa': self._extract_visa(
                                      self.lakeshore_cb.get()),
                'lcr_visa':      self._extract_visa(
                                      self.lcr_cb.get()),
            }

            # --- Validation ---
            if not all([params['sample_name'],
                        params['lakeshore_visa'],
                        params['lcr_visa']]) or not self.file_location_path:
                raise ValueError(
                    "Sample Name, both VISA addresses, and a destination "
                    "folder are required.")
            if not (params['start_temp'] <
                    params['end_temp'] < params['cutoff']):
                raise ValueError(
                    "Temperatures must be in order: start < end < cutoff.")

            if params['corr_enabled']:
                self.log(
                    f"WARNING: Ensure physical Open/Short calibration was "
                    f"performed with {params['cable_len']} m cable before "
                    f"enabling corrections!")

            # --- Initialize hardware ---
            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: "
                     f"{params['sample_name']}")

            # --- Appendix A.2: create one file per frequency ---
            self._create_per_frequency_files(params['sample_name'])
            self.log(
                f"Number of frequencies entered: {len(self.frequencies)}. "
                f"{len(self.frequencies)} output files created in "
                f"{self.file_location_path}")

            # --- Appendix A.4: populate plot-frequency dropdown ---
            self._populate_plot_freq_dropdown()

            # --- Reset state ---
            self.is_stabilizing, self.is_running = True, False
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.scan_button.config(state='disabled')

            self.data_storage['time'].clear()
            self.data_storage['temperature'].clear()
            self.data_storage['cp'] = {
                f: {'T': [], 'v': []} for f in self.frequencies}
            self.data_storage['g'] = {
                f: {'T': [], 'v': []} for f in self.frequencies}

            for line in (self.line_main, self.line_sub1, self.line_sub2):
                line.set_data([], [])
            self.ax_main.set_title(
                f"Cp vs. T @ {int(self.plot_freq)} Hz", fontweight='bold')
            self.canvas.draw()

            self.log("Starting stabilization process...")

            # --- Launch worker thread ---
            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start measurement.\n{e}")

    # ------------------------------------------------------------------
    def _extract_visa(self, combo_val):
        """Strips the '  ->  IDN' suffix added by the identity-aware scan."""
        if "  ->  " in combo_val:
            return combo_val.split("  ->  ")[0].strip()
        return combo_val.strip()

    # ------------------------------------------------------------------
    def _create_per_frequency_files(self, sample_name):
        """
        Appendix A.2 / A.3:
        One .txt file per frequency, header pre-written.
        If any target file already exists, appends a timestamp to the
        sample name to avoid silently overwriting previous data.
        """
        candidate_paths = {
            f: os.path.join(self.file_location_path,
                            f"{sample_name}-{int(f)}Hz.txt")
            for f in self.frequencies
        }
        if any(os.path.exists(p) for p in candidate_paths.values()):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sample_name = f"{sample_name}_{ts}"
            candidate_paths = {
                f: os.path.join(self.file_location_path,
                                f"{sample_name}-{int(f)}Hz.txt")
                for f in self.frequencies
            }
            self.log(f"Existing files detected. Using unique sample "
                     f"tag: {sample_name}")

        self.freq_filepaths = candidate_paths
        for f, path in self.freq_filepaths.items():
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(self.DATA_HEADER + "\n")

    # ------------------------------------------------------------------
    def _populate_plot_freq_dropdown(self):
        """
        Appendix A.4:
        Populate the plot-frequency dropdown.  Default selection is the
        middle frequency: (len - 1) // 2  →  for 20 freqs this is index 9,
        i.e. the 10th frequency.
        """
        vals = [f"{int(f)} Hz" for f in self.frequencies]
        self.plot_freq_cb.config(state='readonly')
        self.plot_freq_cb['values'] = vals
        mid_index = (len(self.frequencies) - 1) // 2
        self.plot_freq_cb.current(mid_index)
        self.plot_freq = self.frequencies[mid_index]
        self.log(
            f"Default live-plot frequency: {int(self.plot_freq)} Hz "
            f"(index {mid_index + 1} of {len(self.frequencies)}).")

    # ------------------------------------------------------------------
    def _on_plot_freq_change(self, event=None):
        """
        Appendix A.4:
        Main-thread-only redraw.  Never touches the worker thread or the
        instruments, so switching frequency cannot cause measurement lag,
        errors, or a hang.
        """
        sel = self.plot_freq_cb.get().replace(" Hz", "")
        self.plot_freq = float(sel)
        self.ax_main.set_title(
            f"Cp vs. T @ {int(self.plot_freq)} Hz", fontweight='bold')
        self._update_live_plots(force=True)

    # ------------------------------------------------------------------
    def stop_measurement(self, from_user=True):
        if self.is_running or self.is_stabilizing:
            self.is_running, self.is_stabilizing = False, False
            if from_user:
                self.log("Measurement stopped by user.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.scan_button.config(state='normal')
            self.backend.close_instruments()
            if from_user:
                messagebox.showinfo(
                    "Info",
                    "Measurement stopped and instruments disconnected.")

    # ==================================================================
    # WORKER THREAD  (Section 7, amended by Appendix A.1)
    # ==================================================================
    def _measurement_worker(self):
        params = self.backend.params
        try:
            # --- Stabilization Phase (verbatim from R-T template) ---
            while self.is_stabilizing:
                current_temp = self.backend.lakeshore.get_temperature('A')
                self.data_queue.put(
                    f"LOG:Stabilizing... Current: {current_temp:.4f} K "
                    f"(Target: {params['start_temp']} K)")

                if current_temp > params['start_temp'] + 5.0:
                    self.backend.lakeshore.set_heater_range(1, 'off')
                else:
                    self.backend.lakeshore.set_heater_range(1, 'high')
                    self.backend.lakeshore.set_setpoint(
                        1, params['start_temp'])

                if abs(current_temp - params['start_temp']) < 5.0:
                    self.data_queue.put(
                        f"LOG:Stabilized at {current_temp:.4f} K. "
                        f"Waiting 5s before ramp...")
                    time.sleep(5)
                    self.is_stabilizing = False
                    self.is_running = True
                    break
                time.sleep(2)

            # --- Ramp Phase ---
            if self.is_running:
                self.backend.lakeshore.set_setpoint(1, params['end_temp'])
                self.backend.lakeshore.setup_ramp(1, params['rate'])
                self.backend.lakeshore.set_heater_range(1, 'high')
                self.data_queue.put(
                    f"LOG:Hardware ramp started towards "
                    f"{params['end_temp']} K at {params['rate']} K/min.")
                self.start_time = time.time()

            # --- Measurement Loop ---
            # Appendix A.1: measure_frequency_sweep reads T inside the
            # frequency loop so every data point is bound to its own T.
            while self.is_running:
                cycle = self.backend.measure_frequency_sweep(
                    self.frequencies, params['delay'])
                elapsed = time.time() - self.start_time
                self.data_queue.put(('CYCLE', cycle, elapsed))

                # Termination check uses the temperature of the LAST
                # point measured in this cycle.
                last_temp = cycle['points'][-1][0]
                if last_temp >= params['cutoff']:
                    self.data_queue.put("CUTOFF")
                    break
                elif last_temp >= params['end_temp']:
                    self.data_queue.put("COMPLETE")
                    break

        except Exception as e:
            self.data_queue.put(e)

    # ==================================================================
    # QUEUE PROCESSING (main thread)
    # ==================================================================
    def _process_data_queue(self):
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()

                if isinstance(data, str) and data.startswith("LOG:"):
                    self._handle_log_message(data[4:])
                elif isinstance(data, str) and data == "CUTOFF":
                    self._handle_cutoff_event()
                    return
                elif isinstance(data, str) and data == "COMPLETE":
                    self._handle_complete_event()
                    return
                elif isinstance(data, Exception):
                    self._handle_runtime_error(data)
                    return
                elif isinstance(data, tuple) and data[0] == 'CYCLE':
                    _, cycle, elapsed = data
                    self._process_cycle(cycle, elapsed)
        except queue.Empty:
            pass

        if self.is_running or self.is_stabilizing:
            self.root.after(200, self._process_data_queue)

    # ------------------------------------------------------------------
    def _process_cycle(self, cycle, elapsed):
        # Log non-zero status warnings
        for (temp, f, R, X, status) in cycle['points']:
            if status != 0:
                self.log(
                    f"WARNING: f={int(f)} Hz status={status} "
                    f"(non-zero = overload/ALC issue) "
                    f"at T={temp:.3f} K")

        last_temp = cycle['points'][-1][0]
        self.log(
            f"Cycle @ t={elapsed:.1f}s | last T={last_temp:.3f} K | "
            f"Htr={cycle['heater']:.1f}%")

        # Appendix A.3: safe per-point file writing
        self._save_cycle_to_files(cycle)

        # Update in-memory storage for plotting
        self._update_data_storage(cycle, elapsed)

        # Redraw plots (throttled)
        self._update_live_plots()

    # ==================================================================
    # Appendix A.3: Safe per-point file writing (open / append / close)
    # ==================================================================
    def _save_cycle_to_files(self, cycle):
        for (temp, f, R, X, status) in cycle['points']:
            try:
                calc = self.calculate_impedance_parameters(f, R, X)
            except Exception as calc_err:
                self.log(f"Calc error at {int(f)} Hz: {calc_err}. "
                         f"Writing NaNs.")
                calc = [float('nan')] * 18

            # Every value — including temperature — formatted as %.6E
            row_vals = [temp] + calc
            row_str = "\t".join("{:.6E}".format(v) for v in row_vals)

            # Open, append, close for every single point: if the program
            # crashes mid-experiment, no data already written is lost.
            with open(self.freq_filepaths[f], 'a',
                      encoding='utf-8') as fh:
                fh.write(row_str + "\n")

    # ==================================================================
    # DATA STORAGE UPDATE (Appendix A.4: per-frequency keyed)
    # ==================================================================
    def _update_data_storage(self, cycle, elapsed):
        for (temp, f, R, X, status) in cycle['points']:
            try:
                calc = self.calculate_impedance_parameters(f, R, X)
                cp, g = calc[4], calc[2]
            except Exception:
                cp, g = float('nan'), float('nan')

            self.data_storage['cp'][f]['T'].append(temp)
            self.data_storage['cp'][f]['v'].append(cp)
            self.data_storage['g'][f]['T'].append(temp)
            self.data_storage['g'][f]['v'].append(g)

        self.data_storage['time'].append(elapsed)
        self.data_storage['temperature'].append(cycle['points'][-1][0])

    # ==================================================================
    # PLOTTING (Appendix A.4: decoupled from acquisition)
    # ==================================================================
    def _update_y_scale(self):
        if self.log_scale_var.get():
            self.ax_main.set_yscale('log')
        else:
            self.ax_main.set_yscale('linear')
        self._rescale_main_axis()
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _update_live_plots(self, force=False):
        fq = self.plot_freq
        if fq is None or fq not in self.data_storage['cp']:
            return

        # Update line data for the currently selected frequency
        self.line_main.set_data(
            self.data_storage['cp'][fq]['T'],
            self.data_storage['cp'][fq]['v'])
        self.line_sub1.set_data(
            self.data_storage['g'][fq]['T'],
            self.data_storage['g'][fq]['v'])
        self.line_sub2.set_data(
            self.data_storage['time'],
            self.data_storage['temperature'])

        # Throttle expensive redraws
        now = time.time()
        if not force and (now - self._last_draw_time) < self._redraw_interval:
            return
        self._last_draw_time = now

        self._rescale_main_axis()
        for ax in (self.ax_sub1, self.ax_sub2):
            ax.relim()
            ax.autoscale_view()

        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _rescale_main_axis(self):
        """Autoscale the main Cp axis, safely handling log y-scale."""
        fq = self.plot_freq
        if fq is None or fq not in self.data_storage['cp']:
            return
        vals = self.data_storage['cp'][fq]['v']
        temps = self.data_storage['cp'][fq]['T']
        if not vals:
            return

        if self.log_scale_var.get():
            valid = [v for v in vals
                     if v > 0 and v == v and v != float('inf')]
            if valid:
                lo, hi = min(valid), max(valid)
                self.ax_main.set_ylim(lo * 0.5, hi * 2.0)
        else:
            self.ax_main.relim()
            self.ax_main.autoscale_view(scaley=True)

        if temps:
            xlo, xhi = min(temps), max(temps)
            if xhi > xlo:
                pad = (xhi - xlo) * 0.05
                self.ax_main.set_xlim(xlo - pad, xhi + pad)

    # ==================================================================
    # EVENT HANDLERS  (identical logic to R-T template)
    # ==================================================================
    def _handle_cutoff_event(self):
        self.log("!!! SAFETY CUTOFF REACHED !!!")
        self._update_live_plots(force=True)
        self.stop_measurement(False)
        messagebox.showwarning(
            "Cutoff", "Safety cutoff temperature reached.")

    # ------------------------------------------------------------------
    def _handle_complete_event(self):
        self.log("Target temperature reached.")
        self._update_live_plots(force=True)
        self.stop_measurement(False)
        messagebox.showinfo("Finished", "Measurement complete.")

    # ------------------------------------------------------------------
    def _handle_runtime_error(self, exception):
        self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        self.stop_measurement(False)
        messagebox.showerror(
            "Runtime Error",
            f"A critical error occurred: {exception}")

    # ==================================================================
    # VISA SCAN (identity-aware, for BOTH instruments)
    # ==================================================================
    def _scan_for_visa_instruments(self):
        """
        Identity-aware instrument scan (from freq-scan program, extended
        to auto-select both the Lakeshore 350 and the E4980A).
        Queries *IDN? on every discovered VISA resource, then auto-selects
        the device reporting 'E4980' → LCR combobox and the device
        reporting 'LSCI' or 'MODEL350' → Lakeshore combobox.
        Displays 'address  ->  IDN' in both comboboxes.
        """
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed.")
            return

        try:
            rm = pyvisa.ResourceManager()
        except Exception as e:
            self.log(f"ERROR: Cannot create VISA Resource Manager: {e}")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        resources = rm.list_resources()
        if not resources:
            self.log("No VISA instruments found.")
            return

        found = []
        lakeshore_label = None
        lcr_label = None

        for res in resources:
            idn = "Unknown / no response"
            try:
                with rm.open_resource(res) as dev:
                    dev.timeout = 2000
                    dev.read_termination = "\n"
                    dev.write_termination = "\n"
                    idn = dev.query("*IDN?").strip()
            except Exception:
                pass  # busy / non-SCPI / timeout — skip silently

            label = f"{res}  ->  {idn}"
            found.append(label)
            self.log(f"  {label}")

            # Auto-select Lakeshore 350
            if (lakeshore_label is None and
                ("LSCI" in idn or "MODEL350" in idn.upper()
                 or "MODEL 350" in idn.upper())):
                lakeshore_label = label

            # Auto-select E4980A
            if lcr_label is None and "E4980" in idn:
                lcr_label = label

        self.lakeshore_cb['values'] = found
        self.lcr_cb['values'] = found

        if lakeshore_label:
            self.lakeshore_cb.set(lakeshore_label)
            self.log("Lakeshore 350 auto-selected.")
        elif found:
            self.lakeshore_cb.set(found[0])
            self.log("WARNING: No Lakeshore 350 found; "
                     "defaulted to first device.")

        if lcr_label:
            self.lcr_cb.set(lcr_label)
            self.log("E4980A auto-selected.")
        elif found:
            self.lcr_cb.set(found[0])
            self.log("WARNING: No E4980A found; "
                     "defaulted to first device.")

    # ==================================================================
    # FILE BROWSE  (Appendix A.2: askdirectory)
    # ==================================================================
    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Destination folder set to: {path}")

    # ==================================================================
    # WINDOW CLOSE
    # ==================================================================
    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno(
                    "Exit",
                    "Measurement is running. Stop and exit?"):
                self.stop_measurement(from_user=False)
                self.root.destroy()
        else:
            self.root.destroy()


# ===============================================================================
# MAIN ENTRY POINT
# ===============================================================================

def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\nPlease run:\n"
            "pip install pyvisa")
        return

    root = tk.Tk()
    Integrated_CT_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()