'''
PROGRAM:      Integrated L350 & E4980A: Temperature Scan (Cp-G)
PURPOSE:      Automated temperature sweeps with multi-frequency LCR measurements.
              Outputs individual data files for each measured frequency.
VERSION:      2.1 (Cp-G)
FIXES:        Colormap API, E4980A BUS trigger + FETCH, SCPI sync delays,
              thread-safe shutdown, np.linspace DC ramp, file handle leaks,
              Cs'' sign convention, full SCPI command forms
V2.1 CHANGES:  - Trigger fix: :INIT:CONT ON (was OFF, caused dashes)
              - Cs'' formula corrected to G/omega (model-independent)
              - Added 0.5s delays after :BIAS:STAT ON and after DC ramp
              - :DISP:PAGE BLAN → :DISP:PAGE MEAS
              - Log-scale plot filtering for non-positive values
              - :SYST:ERR? check after E4980A setup
              - try/except in safe_ramp_dc_bias initial query
              - Heater range 4 added to range_map
'''

import tkinter as tk
from tkinter import (
    ttk,
    Label,
    Entry,
    LabelFrame,
    filedialog,
    messagebox,
    scrolledtext,
    Canvas,
)
import threading
import queue
import os
import time
import math
import traceback
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import runpy
from multiprocessing import Process

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


def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its
    own directory. This becomes the target for the new process.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(
            f"--- Sub-process Error in "
            f"{os.path.basename(script_path)} ---"
        )
        print(e)
        print("-------------------------")


def launch_plotter_utility():
    """Finds and launches the plotter utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(
            script_dir, "..", "utils", "PlotterUtil_GUI.py"
        )
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at:\n{plotter_path}",
            )
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}"
        )


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py"
        )
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at:\n{scanner_path}",
            )
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}"
        )


# ===============================================================================
# BACKEND CLASS - Instrument Control Logic
# ===============================================================================

class Lakeshore350_Backend:
    """
    LakeShore Model 350 Temperature Controller backend.

    Heater range codes (Output 1, 25Ω setting, 75W max):
      0=Off, 1=7.5mW, 2=75mW, 3=750mW, 4=7.5W, 5=75W
    (decade steps in power)

    References:
      - LakeShore 350 datasheet:
        https://www.lakeshore.com/docs/default-source/product-downloads/lstc_350_l.pdf
    """

    def __init__(self, rm, visa_address):
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        self.instrument.read_termination = "\r\n"
        self.instrument.write_termination = "\r\n"
        print(
            f"Lakeshore Connected: "
            f"{self.instrument.query('*IDN?').strip()}"
        )

    def _write_and_sync(self, command, delay=0.5):
        """Write a command and wait for a fixed settling delay."""
        self.instrument.write(command)
        time.sleep(delay)

    def reset_and_clear(self):
        self.instrument.write("*RST")
        self.instrument.query("*OPC?")  # Block until reset completes
        time.sleep(0.5)
        self.instrument.write("*CLS")
        time.sleep(0.5)

    def setup_heater(self, output, resistance_code, max_current_code):
        # Output 1, 25 Ohm (code 1), max current code 2 = 75 W max
        self._write_and_sync(
            f"HTRSET {output},{resistance_code},"
            f"{max_current_code},0,1"
        )

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        self._write_and_sync(
            f"RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}"
        )

    def set_setpoint(self, output, temperature_k):
        self._write_and_sync(f"SETP {output},{temperature_k}")

    def set_heater_range(self, output, heater_range):
        # 0=Off, 1-5 = decade power steps
        # FIX v2.1: Added range 4 (very_high = 7.5W)
        range_map = {
            "off": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "very_high": 4,
            "max": 5,
        }
        range_code = range_map.get(heater_range.lower(), 0)
        self._write_and_sync(f"RANGE {output},{range_code}")

    def get_temperature(self, sensor):
        return float(
            self.instrument.query(f"KRDG? {sensor}").strip()
        )

    def close(self):
        if self.instrument:
            try:
                self.set_heater_range(1, "off")
                time.sleep(0.5)
                self.instrument.close()
            except Exception as e:
                print(f"Warning during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


class KeysightE4980A_Backend:
    """
    Keysight E4980A Precision LCR Meter backend.

    Trigger model (FIXED in v2.1):
      - :TRIG:SOUR BUS  → trigger from GPIB bus
      - :INIT:CONT ON   → auto-arm after each measurement
                           (FIX: was OFF, which left instrument
                           idle and *TRG was ignored → dashes)
      - *TRG            → fire one measurement
      - :FETCH?         → retrieve result (blocks until done)

    References:
      - PyMeasure E4980 driver:
        https://pymeasure.readthedocs.io/en/latest/api/instruments/agilent/agilentE4980.html
      - QCoDeS E4980A driver:
        http://microsoft.github.io/Qcodes/_modules/qcodes/instrument_drivers/Keysight/keysight_e4980a.html
      - E4980A datasheet:
        https://www.cmc.ca/wp-content/uploads/2019/07/Keysight-E4980A-Datasheet.pdf
      - 4284A→E4980A Migration Guide:
        https://kirkbymicrowave.co.uk/Support/Links/applications/Dielectric_Measurements/documents/Migrating-from-a-Keysight-4284A-LCR-Meter-to-a-Keysight-E4980A-Precision-LCR-Meter.pdf
    """

    def __init__(self, rm, visa_address):
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 15000
        self.instrument.read_termination = "\n"
        self.instrument.write_termination = "\n"
        self.has_opt001 = "001" in self.instrument.query("*OPT?")
        print(
            f"E4980A Connected: "
            f"{self.instrument.query('*IDN?').strip()}"
        )

    def _write_and_sync(self, command, delay=0.5):
        """
        Write a SCPI command and wait a fixed settling delay so the
        instrument can process it and move past transients.
        """
        self.instrument.write(command)
        time.sleep(delay)

    def _check_errors(self, context=""):
        """Query the E4980A error queue and print any errors."""
        err = self.instrument.query(":SYST:ERR?")
        if err and not err.startswith('0,"No error"'):
            print(f"  [E4980A ERROR] {context}: {err}")

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        """Ramp DC bias from current to target using np.linspace."""
        # FIX v2.1: try/except around initial query in case bias
        #   is not yet enabled or query fails
        try:
            current_v = float(
                self.instrument.query(":BIAS:VOLT:LEV?")
            )
        except Exception:
            print(
                "  [safe_ramp] Could not read current DC bias, "
                "assuming 0 V."
            )
            current_v = 0.0

        if abs(target_v - current_v) < 0.01:
            return

        n_steps = int(np.ceil(abs(target_v - current_v) / step))
        if n_steps == 0:
            n_steps = 1
        ramp_points = np.linspace(
            current_v, target_v, n_steps + 1
        )

        for v in ramp_points:
            self.instrument.write(f":BIAS:VOLT:LEV {v:.3f}")
            time.sleep(dwell)

    def setup_measurement(
        self, ac_bias, dc_bias, aper, alc_on, corr_on
    ):
        """
        Configure the E4980A for Cp-G measurement with BUS trigger.

        Command order follows the legacy configuration:
        Function → Level → Range → Meas Time → ALC → Corr →
        Trigger → Bias

        FIX v2.1: :INIT:CONT ON (was OFF) — with OFF the instrument
        stayed idle and ignored *TRG, producing dashes on the display.
        With ON, the instrument auto-rearms after each measurement.
        """
        # --- Reset and clear ---
        self.instrument.write("*RST")
        self.instrument.query("*OPC?")  # Block until reset done
        time.sleep(0.5)
        self.instrument.write("*CLS")
        time.sleep(0.5)

        # --- Data format and display ---
        # FIX v2.1: BLAN not a valid page; MEAS is the measurement
        #   display page
        self._write_and_sync(":FORM ASC")
        self._write_and_sync(":DISP:PAGE MEAS")

        # --- Measurement function: Cp-G (parallel C, parallel G) ---
        self._write_and_sync(":FUNC:IMP CPG")

        # --- Aperture (integration time) ---
        self._write_and_sync(f":APER {aper}")

        # --- Auto impedance range ---
        self._write_and_sync(":FUNC:IMP:RANG:AUTO ON")

        # --- ALC (Auto Level Control) ---
        if alc_on:
            self._write_and_sync(":AMPL:ALC ON")
        else:
            self._write_and_sync(":AMPL:ALC OFF")

        # --- Open/Short correction ---
        corr_state = "ON" if corr_on else "OFF"
        self._write_and_sync(f":CORR:OPEN:STAT {corr_state}")
        self._write_and_sync(f":CORR:SHOR:STAT {corr_state}")

        # --- AC test signal voltage ---
        self._write_and_sync(f":VOLT:LEV {ac_bias}")

        # --- Trigger: BUS source, continuous init ON ---
        # FIX v2.1: INIT:CONT ON (was OFF)
        #   With :INIT:CONT OFF, the instrument remained in idle
        #   state after *RST. *TRG was silently ignored because the
        #   trigger was never armed. :FETCH? then returned stale/empty
        #   data, which appeared as dashes on the front-panel display.
        #   With :INIT:CONT ON, the instrument auto-rearms after each
        #   measurement completion, so every *TRG fires exactly one
        #   new measurement.
        self._write_and_sync(":TRIG:SOUR BUS")
        self._write_and_sync(":INIT:CONT ON")

        # --- DC bias setup ---
        self._write_and_sync(":BIAS:VOLT:LEV 0")
        self._write_and_sync(":BIAS:STAT ON")
        # FIX v2.1: Add 0.5s settling delay after enabling bias
        #   output before ramping
        time.sleep(0.5)
        self.safe_ramp_dc_bias(dc_bias)
        # FIX v2.1: Add 0.5s settling delay after DC ramp completes
        time.sleep(0.5)

        # FIX v2.1: Check for any SCPI errors accumulated during setup
        self._check_errors("setup_measurement")

    def measure_freq(self, freq, delay):
        """
        Measure Cp and G at the given frequency.

        With :INIT:CONT ON, the instrument is always armed.
        *TRG fires one measurement; :FETCH? blocks until the
        result is ready and returns [Cp, G, status].
        """
        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)
        # Trigger one measurement via bus trigger
        self.instrument.write("*TRG")
        # Retrieve the result from reading memory
        # (:FETCH? blocks until measurement is complete)
        vals = self.instrument.query_ascii_values(":FETCH?")
        if len(vals) < 2:
            raise ValueError(
                f"Unexpected data from :FETCH?: {vals}"
            )
        cp, g = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return cp, g, status

    def close(self):
        if self.instrument:
            try:
                self.safe_ramp_dc_bias(0.0)
                self.instrument.write(":BIAS:STAT OFF")
                self.instrument.write(":DISP:PAGE MEAS")
                self.instrument.close()
            except Exception as e:
                print(f"Warning during E4980A shutdown: {e}")
            finally:
                self.instrument = None


class Combined_Backend:
    """Manages both the Lakeshore 350 and Keysight E4980A."""

    def __init__(self):
        self.lakeshore = None
        self.lcr = None
        self.params = {}
        self.rm = (
            pyvisa.ResourceManager() if PYVISA_AVAILABLE else None
        )

    def initialize_instruments(self, parameters):
        self.params = parameters
        if not self.rm:
            raise ConnectionError(
                "VISA Resource Manager unavailable."
            )

        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore350_Backend(
            self.rm, self.params["lakeshore_visa"]
        )
        self.lakeshore.reset_and_clear()
        self.lakeshore.setup_heater(1, 1, 2)

        self.lcr = KeysightE4980A_Backend(
            self.rm, self.params["lcr_visa"]
        )
        self.lcr.setup_measurement(
            self.params["ac_bias"],
            self.params["dc_bias"],
            self.params["aper"],
            self.params["alc_enabled"],
            self.params["corr_enabled"],
        )

    def get_temperature(self):
        return self.lakeshore.get_temperature("A")

    def measure_lcr_array(self, freqs, delay):
        """Measure Cp-G at each frequency, reading T per-point."""
        results = {}
        for f in freqs:
            t_point = self.get_temperature()
            cp, g, status = self.lcr.measure_freq(f, delay)
            results[f] = (t_point, cp, g, status)
        return results

    def close_instruments(self):
        print(
            "\n--- [Backend] Closing all instrument connections. ---"
        )
        if self.lcr:
            self.lcr.close()
            print("  E4980A closed, bias → 0 V.")
        if self.lakeshore:
            self.lakeshore.close()
            print("  Lakeshore closed, heater OFF.")


# ===============================================================================
# FRONTEND CLASS - GUI Application
# ===============================================================================

class Temperature_Scan_GUI:
    PROGRAM_VERSION = "2.1 (Cp-G)"
    LOGO_SIZE = 110

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg",
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # Standard UI Colors
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_ACCENT_GOLD = "#BA6B5E"
    CLR_ACCENT_GREEN = "#B68B6E"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    FONT_SIZE_BASE = 10
    FONT_BASE = ("Segoe UI", FONT_SIZE_BASE)
    FONT_TITLE = ("Segoe UI", FONT_SIZE_BASE + 2, "bold")
    FONT_CONSOLE = ("Consolas", 9)

    DATA_HEADER = (
        "Temperature\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\t"
        "lZl\ttheta\tchi\tR(Rs)\ttheta(deg.)\tRp\t"
        "1/lZl\tOmega\tCp''\tCs''"
    )

    def __init__(self, root):
        self.root = root
        self.root.title(
            "Integrated L350 & E4980A: Temperature Scan (Cp-G)"
        )
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self.backend = Combined_Backend()

        self.save_directory = ""
        self.run_folder = ""
        self.file_handles = {}

        self.logo_image = None
        self.freq_list = []
        self.data_storage = {"temp": []}

        self.data_queue = queue.Queue()
        self.measurement_thread = None
        # FIX: Thread-safe shutdown via Event instead of direct
        #   close_instruments() call from GUI thread
        self.stop_event = threading.Event()

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "TFrame", background=self.CLR_BG_DARK
        )
        style.configure(
            "TLabel",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure(
            "TLabelframe",
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
        )
        style.configure(
            "TLabelframe.Label",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE,
        )
        style.configure(
            "TCheckbutton",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure(
            "TButton",
            font=self.FONT_BASE,
            padding=5,
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
        )
        style.configure(
            "Start.TButton",
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK,
        )
        style.configure(
            "Stop.TButton",
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT,
        )

    def create_widgets(self):
        # Header
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side="top", fill="x")
        Label(
            header_frame,
            text="Temperature-Dependent Frequency Scan (Cp-G)",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=("Segoe UI", 14, "bold", "italic"),
        ).pack(side="left", padx=20, pady=10)

        ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3,
        ).pack(side="right", padx=10, pady=5)
        ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3,
        ).pack(side="right", padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        # Left Panel (Controls + Console)
        left_panel = ttk.Frame(main_pane, width=450)
        main_pane.add(left_panel, weight=0)

        canvas = Canvas(
            left_panel,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            left_panel, orient="vertical", command=canvas.yview
        )
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            ),
        )
        canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw", width=430
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="top", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)

        self.create_input_frame(scrollable_frame)

        console_frame = LabelFrame(
            left_panel,
            text="Console",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        console_frame.pack(
            side="bottom", fill="both", expand=False, pady=5
        )
        self.console_widget = scrolledtext.ScrolledText(
            console_frame,
            state="disabled",
            bg=self.CLR_CONSOLE_BG,
            font=self.FONT_CONSOLE,
            height=8,
        )
        self.console_widget.pack(
            fill="both", expand=True, padx=5, pady=5
        )

        # Right Panel (Graphs)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)
        self.create_graph_frame(right_panel)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        logo_canvas.grid(
            row=0, column=0, rowspan=3, padx=(15, 10), pady=10
        )

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE, self.LOGO_SIZE),
                    RESAMPLE_FILTER,
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image,
                )
            except Exception:
                pass

        institute_font = (
            "Segoe UI",
            self.FONT_SIZE_BASE + 2,
            "bold",
        )
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky="sw"
        )
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=1, column=1, padx=10, sticky="nw")

        return frame

    def create_input_frame(self, parent):
        f_temp = LabelFrame(
            parent,
            text="Temperature & Instrument Parameters",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        f_temp.pack(fill="x", pady=5, padx=5)

        self.entries = {}

        def add_entry(
            frame, label, key, r, c, default=""
        ):
            Label(frame, text=f"{label}:").grid(
                row=r, column=c, padx=5, pady=2, sticky="w"
            )
            e = Entry(frame, font=self.FONT_BASE, width=16)
            e.grid(
                row=r + 1,
                column=c,
                padx=5,
                pady=(0, 5),
                sticky="w",
            )
            e.insert(0, default)
            self.entries[key] = e

        add_entry(f_temp, "Sample Name", "sample", 0, 0, "Sample_01")
        add_entry(f_temp, "Delay/freq (s)", "delay", 0, 1, "0.2")
        add_entry(f_temp, "Start Temp (K)", "t_start", 2, 0, "300")
        add_entry(f_temp, "End Temp (K)", "t_end", 2, 1, "350")
        add_entry(f_temp, "Ramp Rate (K/min)", "rate", 4, 0, "2.0")
        add_entry(f_temp, "Safety Cutoff (K)", "cutoff", 4, 1, "380")
        add_entry(f_temp, "AC Bias (V)", "ac_bias", 6, 0, "1.0")
        add_entry(f_temp, "DC Bias (V)", "dc_bias", 6, 1, "0.0")

        Label(f_temp, text="Aperture:").grid(
            row=8, column=0, padx=5, sticky="w"
        )
        self.aper_cb = ttk.Combobox(
            f_temp,
            values=["SHOR", "MED", "LONG"],
            state="readonly",
            width=14,
        )
        self.aper_cb.set("MED")
        self.aper_cb.grid(
            row=9, column=0, padx=5, pady=(0, 5), sticky="w"
        )

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f_temp, text="ALC ON", variable=self.var_alc
        ).grid(row=8, column=1, sticky="w")
        ttk.Checkbutton(
            f_temp, text="Open/Short Corr", variable=self.var_corr
        ).grid(row=9, column=1, sticky="w")

        f_freq = LabelFrame(
            parent,
            text="Frequency Selection (Hz)",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        f_freq.pack(fill="x", pady=5, padx=5)

        Label(f_freq, text="Comma-separated Frequencies:").pack(
            anchor="w", padx=5
        )
        self.freq_text = scrolledtext.ScrolledText(
            f_freq, height=4, width=40, font=self.FONT_BASE
        )
        self.freq_text.pack(padx=5, pady=5, fill="x")
        default_freqs = (
            "1000, 2000, 3000, 5000, 7000, 10000, 25000, "
            "50000, 70000, 90000, 100000, 120000, 150000, "
            "170000, 200000, 250000, 500000, 1000000, "
            "1500000, 2000000"
        )
        self.freq_text.insert("end", default_freqs)
        self.freq_text.bind(
            "<KeyRelease>", self._update_freq_count
        )

        frame_count = tk.Frame(f_freq, bg=self.CLR_BG_DARK)
        frame_count.pack(fill="x", padx=5, pady=2)
        Label(frame_count, text="Total Frequencies:").pack(
            side="left"
        )
        self.lbl_fcount = Label(
            frame_count,
            text="20",
            fg=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE,
        )
        self.lbl_fcount.pack(side="left", padx=5)

        f_hw = LabelFrame(
            parent,
            text="Hardware & Execution",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        f_hw.pack(fill="x", pady=5, padx=5)

        Label(f_hw, text="Lakeshore VISA:").grid(
            row=0, column=0, sticky="w", padx=5
        )
        self.ls_visa = ttk.Combobox(
            f_hw, state="readonly", width=18
        )
        self.ls_visa.grid(row=1, column=0, padx=5, pady=(0, 5))

        Label(f_hw, text="E4980A VISA:").grid(
            row=0, column=1, sticky="w", padx=5
        )
        self.lcr_visa = ttk.Combobox(
            f_hw, state="readonly", width=18
        )
        self.lcr_visa.grid(row=1, column=1, padx=5, pady=(0, 5))

        ttk.Button(
            f_hw,
            text="Scan Instruments",
            command=self._scan_visa,
        ).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5
        )
        ttk.Button(
            f_hw,
            text="Browse Save Folder",
            command=self._browse_dir,
        ).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=5
        )

        self.start_btn = ttk.Button(
            f_hw,
            text="Start Measurement",
            command=self.start_measurement,
            style="Start.TButton",
        )
        self.start_btn.grid(
            row=4, column=0, padx=5, pady=10, sticky="ew"
        )
        self.stop_btn = ttk.Button(
            f_hw,
            text="Stop",
            command=self.stop_measurement,
            style="Stop.TButton",
            state="disabled",
        )
        self.stop_btn.grid(
            row=4, column=1, padx=5, pady=10, sticky="ew"
        )

    def create_graph_frame(self, parent):
        self.figure = Figure(
            dpi=100, facecolor=self.CLR_GRAPH_BG, layout="tight"
        )

        self.ax_cp = self.figure.add_subplot(2, 1, 1)
        self.ax_cp.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp.set_title("Cp vs. Temperature", fontweight="bold")
        self.ax_cp.set_yscale("log")
        self.ax_cp.grid(True, linestyle="--", alpha=0.6)

        self.ax_g = self.figure.add_subplot(2, 1, 2)
        self.ax_g.set_xlabel("Temperature (K)")
        self.ax_g.set_ylabel("Conductance, G (S)")
        self.ax_g.set_title("G vs. Temperature", fontweight="bold")
        self.ax_g.set_yscale("log")
        self.ax_g.grid(True, linestyle="--", alpha=0.6)

        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5
        )
        self.lines_cp = {}
        self.lines_g = {}

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state="normal")
        self.console_widget.insert("end", f"[{ts}] {message}\n")
        self.console_widget.see("end")
        self.console_widget.config(state="disabled")

    def _update_freq_count(self, event=None):
        raw = self.freq_text.get("1.0", "end").strip()
        if not raw:
            self.lbl_fcount.config(text="0")
            return
        try:
            lst = [
                int(float(x.strip()))
                for x in raw.split(",")
                if x.strip()
            ]
            self.lbl_fcount.config(text=str(len(lst)))
        except ValueError:
            self.lbl_fcount.config(text="Err")

    def _scan_visa(self):
        if not PYVISA_AVAILABLE:
            self.log("PyVISA not installed.")
            return
        if not self.backend.rm:
            self.backend.rm = pyvisa.ResourceManager()
        res = self.backend.rm.list_resources()
        self.ls_visa["values"] = res
        self.lcr_visa["values"] = res
        self.log(f"VISA Scan found: {res}")

    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.save_directory = d
            self.log(f"Save directory: {d}")

    # ===================================================================
    # CALCULATION & DATA HANDLING
    # ===================================================================

    def calculate_impedance_parameters(self, f, cp, g):
        """
        Calculate all 18 impedance parameters from Cp, G, and
        frequency.

        Conventions:
          - Complex capacitance: C* = C' - jC''
          - Cp'' = G/omega  (parallel loss, positive)
          - Cs'' = G/omega  (series loss, positive)
            FIX v2.1: Was 1/(omega*Rs), corrected to G/omega.
            The complex capacitance C* is a physical property of
            the device and is model-independent. Both Cp'' and Cs''
            must yield the same value.
            Source: Agilent Impedance Parameter Application Note,
            Table 1 confirms C* is the same in series and parallel
            models.

        References:
          - Agilent AN on series/parallel impedance parameters:
            https://idm-instrumentos.es/wp-content/uploads/2014/09/AN-Series_and_Parallel_Impedance_Parameters_and_Equivalent_Circuits_-092007_1.pdf
        """
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
        Rs_safe = Rs if Rs != 0 else 1e-20
        Xs = -B / (Y_mag_safe**2)
        Xs_safe = Xs if Xs != 0 else 1e-20

        theta_rad = math.atan2(Xs, Rs)
        theta_deg = math.degrees(theta_rad)

        chi = Xs
        Cs = -1.0 / (omega_safe * Xs_safe)
        Ls = Xs / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        # Complex capacitance C* = C' - jC''
        # Both Cp'' and Cs'' equal G/omega (model-independent)
        Cp_double_prime = g / omega_safe
        # FIX v2.1: Was 1/(omega*Rs) — incorrect. Now G/omega,
        #   matching Cp'' as required for model independence.
        Cs_double_prime = g / omega_safe

        return [
            Q,
            D,
            g,
            B,
            cp,
            Lp,
            Cs,
            Ls,
            Z_mag,
            theta_rad,
            chi,
            Rs,
            theta_deg,
            Rp,
            Y_mag,
            omega,
            Cp_double_prime,
            Cs_double_prime,
        ]

    def _open_data_files(self, sample_name, timestamp):
        self.run_folder = os.path.join(
            self.save_directory, f"{sample_name}_{timestamp}"
        )
        os.makedirs(self.run_folder, exist_ok=True)
        self.file_handles = {}

        for f in self.freq_list:
            fname = os.path.join(
                self.run_folder, f"{sample_name}_{f}Hz.txt"
            )
            fh = open(fname, "w", encoding="utf-8")
            fh.write(
                f"# Sample: {sample_name} | Freq: {f} Hz | "
                f"AC: {self.entries['ac_bias'].get()}V | "
                f"DC: {self.entries['dc_bias'].get()}V\n"
            )
            fh.write(self.DATA_HEADER + "\n")
            self.file_handles[f] = fh
        self.log(
            f"Created {len(self.freq_list)} files in "
            f"{os.path.basename(self.run_folder)}."
        )

    def _close_data_files(self):
        for fh in self.file_handles.values():
            try:
                fh.close()
            except Exception:
                pass
        self.file_handles.clear()

    # ===================================================================
    # MEASUREMENT EXECUTION
    # ===================================================================

    def start_measurement(self):
        try:
            # Parse Frequencies
            raw = self.freq_text.get("1.0", "end").strip()
            self.freq_list = [
                int(float(x.strip()))
                for x in raw.split(",")
                if x.strip()
            ]
            if not self.freq_list:
                raise ValueError(
                    "Frequency list is empty or invalid."
                )

            params = {
                "sample": self.entries["sample"].get(),
                "t_start": float(
                    self.entries["t_start"].get()
                ),
                "t_end": float(
                    self.entries["t_end"].get()
                ),
                "rate": float(self.entries["rate"].get()),
                "cutoff": float(
                    self.entries["cutoff"].get()
                ),
                "ac_bias": float(
                    self.entries["ac_bias"].get()
                ),
                "dc_bias": float(
                    self.entries["dc_bias"].get()
                ),
                "delay": float(
                    self.entries["delay"].get()
                ),
                "aper": self.aper_cb.get(),
                "alc_enabled": self.var_alc.get(),
                "corr_enabled": self.var_corr.get(),
                "lakeshore_visa": self.ls_visa.get(),
                "lcr_visa": self.lcr_visa.get(),
            }

            if not all(
                [
                    params["sample"],
                    params["lakeshore_visa"],
                    params["lcr_visa"],
                    self.save_directory,
                ]
            ):
                raise ValueError(
                    "Missing VISA addresses or Save Directory."
                )

            self.backend.initialize_instruments(params)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._open_data_files(params["sample"], ts)

            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")

            # Setup Plotting arrays & lines
            self.ax_cp.clear()
            self.ax_g.clear()
            self.ax_cp.set_yscale("log")
            self.ax_g.set_yscale("log")
            self.ax_cp.grid(True, linestyle="--", alpha=0.6)
            self.ax_g.grid(True, linestyle="--", alpha=0.6)
            self.ax_cp.set_ylabel("Capacitance, Cp (F)")
            self.ax_cp.set_title(
                "Cp vs. Temperature", fontweight="bold"
            )
            self.ax_g.set_xlabel("Temperature (K)")
            self.ax_g.set_ylabel("Conductance, G (S)")
            self.ax_g.set_title(
                "G vs. Temperature", fontweight="bold"
            )

            self.data_storage = {"temp": []}
            self.lines_cp = {}
            self.lines_g = {}

            # FIX: mpl.colormaps['viridis'] instead of
            #   mpl.colormaps.get_cmap('viridis') which was removed
            #   in matplotlib >= 3.9
            cmap = mpl.colormaps["viridis"]
            for i, f in enumerate(self.freq_list):
                self.data_storage[f] = {"cp": [], "g": []}
                c = cmap(i / len(self.freq_list))
                self.lines_cp[f], = self.ax_cp.plot(
                    [],
                    [],
                    color=c,
                    marker=".",
                    markersize=2,
                    linestyle="-",
                    linewidth=1,
                )
                self.lines_g[f], = self.ax_g.plot(
                    [],
                    [],
                    color=c,
                    marker=".",
                    markersize=2,
                    linestyle="-",
                    linewidth=1,
                )

            self.canvas.draw()

            # FIX: Clear stop_event before starting
            self.stop_event.clear()
            self.is_stabilizing = True
            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True
            )
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"Startup Error: {e}")
            # FIX: Clean up instruments and files on startup error
            try:
                self.backend.close_instruments()
            except Exception:
                pass
            self._close_data_files()
            messagebox.showerror("Error", str(e))

    def stop_measurement(self):
        """
        User-initiated stop. Sets the stop_event flag; the worker
        thread detects it, exits its loop, closes instruments, and
        sends "DONE" to the queue. The GUI then closes files and
        resets UI.
        """
        if self.is_running or self.is_stabilizing:
            self.is_running = False
            self.is_stabilizing = False
            self.stop_event.set()
            self.log("Measurement stopping...")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            # Worker thread will close instruments and send "DONE"

    def _measurement_worker(self):
        """
        Worker thread: stabilization → ramp → measurement loop.
        Checks stop_event at every iteration. Always cleans up
        instruments in finally block.
        """
        p = self.backend.params
        try:
            # --- 1. Stabilization Phase ---
            stab_start = time.time()
            STAB_TIMEOUT_S = 1800  # 30 min safety cutoff

            while not self.stop_event.is_set():
                current_t = self.backend.get_temperature()

                if time.time() - stab_start > STAB_TIMEOUT_S:
                    self.data_queue.put(
                        "LOG:Stabilization timeout "
                        "— check cooling/setpoint."
                    )
                    self.data_queue.put("CUTOFF")
                    break

                self.data_queue.put(
                    f"LOG:Stabilizing... T={current_t:.2f}K "
                    f"(Target={p['t_start']}K)"
                )

                if current_t > p["t_start"] + 5.0:
                    # Above target — turn off heater, wait for
                    # passive cooling
                    self.backend.lakeshore.set_heater_range(
                        1, "off"
                    )
                else:
                    # Below target — set setpoint, turn on heater
                    self.backend.lakeshore.setup_ramp(
                        1, 0, ramp_on=False
                    )
                    self.backend.lakeshore.set_setpoint(
                        1, p["t_start"]
                    )
                    self.backend.lakeshore.set_heater_range(
                        1, "high"
                    )

                if abs(current_t - p["t_start"]) < 2.0:
                    self.data_queue.put(
                        "LOG:Stabilized. Wait 5s before sweep..."
                    )
                    time.sleep(5)
                    self.is_stabilizing = False
                    self.is_running = True
                    break
                time.sleep(2)

            # --- 2. Ramp Phase ---
            if self.is_running and not self.stop_event.is_set():
                # Ramp ON → Range → Setpoint
                self.backend.lakeshore.setup_ramp(
                    1, p["rate"], ramp_on=True
                )
                self.backend.lakeshore.set_heater_range(
                    1, "high"
                )
                self.backend.lakeshore.set_setpoint(
                    1, p["t_end"]
                )
                self.data_queue.put(
                    f"LOG:Ramp started to {p['t_end']}K at "
                    f"{p['rate']} K/min"
                )

            # --- 3. Measurement Loop ---
            while self.is_running and not self.stop_event.is_set():
                t_monitor = self.backend.get_temperature()

                if t_monitor >= p["cutoff"]:
                    self.data_queue.put("CUTOFF")
                    break
                if t_monitor >= p["t_end"]:
                    self.data_queue.put("COMPLETE")
                    break

                freq_results = self.backend.measure_lcr_array(
                    self.freq_list, p["delay"]
                )
                self.data_queue.put(("DATA", freq_results))

        except Exception as e:
            self.data_queue.put(e)
            self.data_queue.put(
                "LOG:Hard crash in measurement thread."
            )
        finally:
            # FIX: Always close instruments from the worker thread
            #   (never from GUI thread) to avoid race conditions
            try:
                self.backend.close_instruments()
            except Exception:
                pass
            self.data_queue.put("DONE")

    def _process_data_queue(self):
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()

                if isinstance(item, str):
                    if item.startswith("LOG:"):
                        self.log(item[4:])
                    elif item == "CUTOFF":
                        self.log("SAFETY CUTOFF REACHED.")
                        self.is_running = False
                        # Worker will send DONE after cleanup
                    elif item == "COMPLETE":
                        self.log(
                            "Target temperature reached "
                            "successfully."
                        )
                        self.is_running = False
                        # Worker will send DONE after cleanup
                    elif item == "DONE":
                        self._close_data_files()
                        self.start_btn.config(state="normal")
                        self.stop_btn.config(state="disabled")
                        self.is_running = False
                        self.is_stabilizing = False
                        self.log(
                            "Measurement thread finished. "
                            "Instruments secured."
                        )

                elif isinstance(item, Exception):
                    self.log(f"RUNTIME ERROR: {item}")
                    # Worker will send DONE after cleanup

                elif (
                    isinstance(item, tuple)
                    and item[0] == "DATA"
                ):
                    _, results = item

                    base_t = results[self.freq_list[0]][0]
                    self.data_storage["temp"].append(base_t)

                    self.log(
                        f"T (Start) = {base_t:.2f} K | "
                        f"Freq scan block completed."
                    )

                    for f in self.freq_list:
                        t_point, cp, g, status = results[f]

                        if status != 0:
                            self.log(
                                f"  WARNING: f={f}Hz status="
                                f"{status} (overload/invalid) "
                                f"at T={t_point:.2f}K"
                            )

                        self.data_storage[f]["cp"].append(cp)
                        self.data_storage[f]["g"].append(g)

                        calc_vals = (
                            self.calculate_impedance_parameters(
                                f, cp, g
                            )
                        )

                        row_vals = [t_point] + calc_vals
                        row_str = "\t".join(
                            [f"{v:.6E}" for v in row_vals]
                        )

                        fh = self.file_handles.get(f)
                        if fh:
                            fh.write(row_str + "\n")
                            fh.flush()

                    self._update_plots()

        except queue.Empty:
            pass

        # FIX: Keep polling while thread is alive or queue has items
        if (
            self.measurement_thread
            and self.measurement_thread.is_alive()
        ) or not self.data_queue.empty():
            self.root.after(200, self._process_data_queue)

    def _update_plots(self):
        temps = np.array(self.data_storage["temp"])

        for f in self.freq_list:
            cp_arr = np.array(self.data_storage[f]["cp"])
            g_arr = np.array(self.data_storage[f]["g"])

            # FIX v2.1: Filter non-positive values for log-scale
            #   plotting. Mask points where cp <= 0 or g <= 0 to
            #   prevent matplotlib log-scale errors and broken
            #   plots. Invalid points are masked (shown as gaps
            #   in the line), which is the correct scientific
            #   representation.
            cp_masked = np.ma.masked_where(
                cp_arr <= 0, cp_arr
            )
            g_masked = np.ma.masked_where(
                g_arr <= 0, g_arr
            )

            self.lines_cp[f].set_data(temps, cp_masked)
            self.lines_g[f].set_data(temps, g_masked)

        self.ax_cp.relim()
        self.ax_cp.autoscale_view()
        self.ax_g.relim()
        self.ax_g.autoscale_view()
        self.canvas.draw_idle()

    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno(
                "Exit", "Measurement is running. Stop and exit?"
            ):
                self.stop_event.set()
                if (
                    self.measurement_thread
                    and self.measurement_thread.is_alive()
                ):
                    self.measurement_thread.join(timeout=10)
                try:
                    self.backend.close_instruments()
                except Exception:
                    pass
                self._close_data_files()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    Temperature_Scan_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()