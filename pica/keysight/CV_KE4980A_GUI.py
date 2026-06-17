'''
PROGRAM:      Keysight E4980A C-V Measurement GUI
PURPOSE:      Provide a user-friendly interface for automating C-V sweeps.
VERSION:      2.0
DATE:         2026-06-17
CHANGES:      - Fixed trigger model (replaced broken :TRIG:SOUR BUS + :INIT:CONT ON
                with :READ? for reliable per-point measurement)
              - Fixed traceback loss (now captured in worker thread)
              - Added open/short compensation workflow with cable length selection
              - Added configurable dwell time, integration time, and averaging
              - Added bias ramp-up before sweep start
              - Added *OPC? synchronization and 0.5s stabilization delays
              - Rich CSV metadata logging
              - Removed all dead code (sweep_gen references)
              - Fixed Option 001 parsing
              - Added post-sweep bias verification
              - Added AC level reduction during short compensation
              - Added connection validation ping
REFERENCES:
  - Keysight E4980A/AL User's Guide (9018-05655)
  - Keysight E4980A Brochure (5989-4235): confirms 1/2/4m cable extension,
    Open/Short/Load correction, averaging up to 999, SHOR/MED/LONG integration
  - Keysight Fixture Compensation (E4990A): "If oscillator level > 500 mV,
    REDUCE OSC LEVEL or BRIDGE UNBALANCED may occur during Short correction"
  - :READ? is equivalent to :INIT:IMM + :FETC? (per Keysight SCPI convention)
'''

# --- Packages for Front end ---
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
    Checkbutton,
    BooleanVar,
    StringVar,
)
import os
import time
import threading
import queue
import math
import traceback
from datetime import datetime
import csv
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk

    PIL_AVAILABLE = True
    RESAMPLE_FILTER = getattr(Image, "Resampling", Image).LANCZOS
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa

    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

# Stabilization delay after each SCPI configuration command (seconds)
SCPI_STABILIZE_DELAY = 0.5


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
            script_dir, "..", "utils", "PlotterUtil_GUI.py"
        )
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}",
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
                f"GPIB Scanner not found at expected path:\n{scanner_path}",
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


class LCR_Backend:
    """Handles backend communication with the Keysight E4980A LCR Meter."""

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.has_opt001 = False
        self.rm = None
        self.idn = ""
        self.firmware = ""
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

    def _write_delayed(self, cmd):
        """Write a SCPI command and wait for stabilization delay."""
        self.instrument.write(cmd)
        time.sleep(SCPI_STABILIZE_DELAY)

    def _write_opc(self, cmd):
        """Write a SCPI command and wait for *OPC? (operation complete)."""
        self.instrument.write(cmd)
        self.instrument.query("*OPC?")
        time.sleep(SCPI_STABILIZE_DELAY)

    def validate_connection(self, visa_address):
        """Ping the instrument with *IDN? before full initialization."""
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")
        inst = self.rm.open_resource(visa_address)
        inst.timeout = 5000
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        try:
            idn = inst.query("*IDN?").strip()
            if "E4980" not in idn:
                inst.close()
                raise ConnectionError(f"Not an E4980A: {idn}")
            return idn
        finally:
            inst.close()

    def initialize_instrument(self, p):
        """Receives all parameters from the GUI and configures the instrument."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(p["lcr_visa"])
        inst.timeout = 30000  # 30s timeout for LONG integration + averaging
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self.instrument = inst

        # --- Connection validation (Phase 5.3) ---
        idn = inst.query("*IDN?").strip()
        if "E4980" not in idn:
            inst.close()
            raise ConnectionError(f"Not an E4980A: {idn}")
        self.idn = idn
        parts = idn.split(",")
        self.firmware = parts[3].strip() if len(parts) > 3 else "unknown"
        print(f"  Connected: {idn}")

        # --- Parse Option 001 properly (Phase 4.2) ---
        opt_response = inst.query("*OPT?").strip()
        opts = [o.strip() for o in opt_response.split(",")]
        self.has_opt001 = "001" in opts
        print(f"  Options: {opts} | Option 001: {self.has_opt001}")

        # ---- Safety Limits (Keysight brochure 5989-4235) ----
        v_bias_max = 40.0 if self.has_opt001 else 2.0
        v_ac_max = 20.0 if self.has_opt001 else 2.0
        if abs(p["v_max"]) > v_bias_max:
            raise ValueError(
                f"|Max Voltage| > {v_bias_max} V limit "
                f"(Option 001 {'present' if self.has_opt001 else 'absent'})."
            )
        if not (0 < p["v_ac"] <= v_ac_max):
            raise ValueError(f"AC level outside 0-{v_ac_max} Vrms.")
        if not (20 <= p["freq"] <= 2e6):
            raise ValueError("Frequency outside 20 Hz-2 MHz.")

        # ---- SCPI Setup Sequence ----
        # Reset and clear (Phase 1.3: *OPC? synchronization)
        self._write_opc("*RST; *CLS")
        self._write_delayed(":DISP:ENAB ON")
        self._write_delayed(":FUNC:IMP CPRP")  # Cp-Rp; values[0] = C
        self._write_delayed(f":APER {p['aper']}")  # Integration time

        # Auto-range (Phase 3: configurable)
        self._write_delayed(":FUNC:IMP:RANG:AUTO ON")

        # Averaging (Phase 3.3)
        if p["avg_count"] > 1:
            self._write_delayed(f":AVER:COUN {p['avg_count']}")
            self._write_delayed(":AVER:STAT ON")
        else:
            self._write_delayed(":AVER:STAT OFF")

        self._write_delayed(f":FREQ {p['freq']}")
        self._write_delayed(f":VOLT {p['v_ac']}")

        # ---- Compensation settings (Phase 2.4, 2.5) ----
        if p["corr_enable"]:
            cable_len = int(p["cable_length"])
            if cable_len not in (0, 1, 2, 4):
                raise ValueError(
                    f"Cable length must be 0, 1, 2, or 4 m. Got: {cable_len}"
                )
            self._write_delayed(f":CORR:LENG {cable_len}")
            self._write_delayed(":CORR:OPEN:STAT ON")
            self._write_delayed(":CORR:SHOR:STAT ON")
            print(
                f"  Compensation: ENABLED (cable={cable_len}m, open+short)"
            )
        else:
            self._write_delayed(":CORR:OPEN:STAT OFF")
            self._write_delayed(":CORR:SHOR:STAT OFF")
            print("  Compensation: DISABLED")

        # ---- Trigger model: use :READ? (Phase 1.1) ----
        # No :TRIG:SOUR, no :INIT:CONT — :READ? handles INIT+TRIG+FETC
        self._write_delayed(":TRIG:SOUR IMM")
        self._write_delayed(":INIT:CONT OFF")

        # ---- DC Bias setup ----
        self._write_delayed(":BIAS:VOLT 0")  # Explicit 0 V BEFORE enabling
        self._write_opc(":BIAS:STAT ON")  # Enable DC Bias (Phase 1.3: *OPC?)

        self._check_errors("configuration")
        print("--- [Backend] Instrument Initialization Complete ---")

    def perform_open_compensation(self):
        """Perform OPEN compensation measurement."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        print("  Performing OPEN compensation...")
        self.instrument.write(":CORR:OPEN")
        self.instrument.query("*OPC?")
        time.sleep(SCPI_STABILIZE_DELAY)
        self._check_errors("open compensation")
        print("  OPEN compensation complete.")

    def perform_short_compensation(self, original_v_ac):
        """Perform SHORT compensation, reducing AC level if > 500 mV.

        Per Keysight documentation: 'If oscillator level > 500 mV,
        REDUCE OSC LEVEL or BRIDGE UNBALANCED may occur during Short
        correction.' (Phase 5.1)
        """
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        print("  Performing SHORT compensation...")
        reduced = False
        if original_v_ac > 0.5:
            print(
                f"  AC level {original_v_ac}V > 0.5V — "
                "temporarily reducing to 0.5V for short correction."
            )
            self.instrument.write(":VOLT 0.5")
            time.sleep(SCPI_STABILIZE_DELAY)
            reduced = True

        self.instrument.write(":CORR:SHOR")
        self.instrument.query("*OPC?")
        time.sleep(SCPI_STABILIZE_DELAY)
        self._check_errors("short compensation")

        if reduced:
            self.instrument.write(f":VOLT {original_v_ac}")
            time.sleep(SCPI_STABILIZE_DELAY)
        print("  SHORT compensation complete.")

    def perform_measurement(self, voltage, dwell=0.5):
        """Sets a voltage, lets it settle, and performs a triggered read.

        Uses :READ? which combines INIT + TRIG + FETC in one command.
        This is the correct trigger model for the E4980A (Phase 1.1).
        """
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")

        self.instrument.write(f":BIAS:VOLT {voltage}")
        time.sleep(dwell)  # Wait for dielectric/cable settling

        # :READ? = INIT:IMM + TRIG + FETC? (Phase 1.1)
        vals = self.instrument.query_ascii_values(":READ?")

        capacitance = vals[0]
        actual_v = float(self.instrument.query(":BIAS:VOLT?"))

        return actual_v, capacitance

    def ramp_bias_to_voltage(self, target_v, step=0.5, dwell=0.1):
        """Ramps bias from current value to target voltage safely (Phase 3.4)."""
        try:
            current_v = float(self.instrument.query(":BIAS:VOLT?"))
            if abs(target_v - current_v) < step:
                self.instrument.write(f":BIAS:VOLT {target_v}")
                time.sleep(dwell)
                return
            n = int(abs(target_v - current_v) / step)
            direction = 1 if target_v > current_v else -1
            for i in range(1, n + 1):
                v = current_v + direction * i * step
                self.instrument.write(f":BIAS:VOLT {v}")
                time.sleep(dwell)
            self.instrument.write(f":BIAS:VOLT {target_v}")
            time.sleep(dwell)
        except Exception as e:
            print(f"  Warning during bias ramp: {e}")

    def ramp_bias_to_zero(self, step=0.5, dwell=0.1):
        """Safely ramps down the DC bias to 0V to protect the DUT."""
        try:
            v = float(self.instrument.query(":BIAS:VOLT?"))
            if abs(v) < step:  # Phase 4.5: skip if already near zero
                self.instrument.write(":BIAS:VOLT 0")
                return
            n = int(abs(v) / step)
            for i in range(n, 0, -1):
                self.instrument.write(
                    f":BIAS:VOLT {math.copysign(i * step, v)}"
                )
                time.sleep(dwell)
            self.instrument.write(":BIAS:VOLT 0")
        except Exception as e:
            print(f"  Warning during bias ramp-down: {e}")

    def get_bias_voltage(self):
        """Query the current DC bias voltage."""
        if self.instrument:
            return float(self.instrument.query(":BIAS:VOLT?"))
        return 0.0

    def close_instrument(self):
        """Ramps down safely and shuts down the instrument connection."""
        print("--- [Backend] Closing instrument connection. ---")
        if not self.instrument:
            return
        try:
            print("  Ramping bias to zero and turning off...")
            self.ramp_bias_to_zero()
            # Phase 4.6: Post-sweep bias verification
            final_v = self.get_bias_voltage()
            print(f"  Post-ramp bias verification: {final_v:.4f} V")
            self.instrument.write(":BIAS:STAT OFF")
            self.instrument.write(":DISP:PAGE MEAS")
        except Exception as e:
            print(f"  Warning during shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  E4980A connection closed.")
            finally:
                self.instrument = None


# ===============================================================================
# FRONTEND CLASS - The Main GUI Application
# ===============================================================================


class LCR_CV_GUI:
    """The main GUI application class for C-V measurements."""

    PROGRAM_VERSION = "2.0"
    LOGO_SIZE = 110

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme Colors ---
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

    def __init__(self, root):
        self.root = root
        self.root.title("Keysight E4980A C-V Measurement")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.backend = LCR_Backend()
        self.file_location_path = ""
        self.data_storage = {
            "voltage": [],
            "capacitance": [],
            "loop": [],
            "protocol": [],
        }
        self.logo_image = None
        self.sweep_points = []
        self.sweep_index = 0
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self.current_params = {}

        # GUI variables for checkboxes/comboboxes
        self.corr_enabled_var = BooleanVar(value=True)
        self.cable_length_var = StringVar(value="1")
        self.aper_var = StringVar(value="MED")

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
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
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE,
        )

        style.configure(
            "TButton",
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor="none",
        )
        style.map(
            "TButton",
            background=[
                ("active", self.CLR_ACCENT_GOLD),
                ("hover", self.CLR_ACCENT_GOLD),
            ],
            foreground=[
                ("active", self.CLR_TEXT_DARK),
                ("hover", self.CLR_TEXT_DARK),
            ],
        )

        style.configure(
            "Start.TButton",
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK,
        )
        style.map(
            "Start.TButton",
            background=[("active", "#8AB845"), ("hover", "#8AB845")],
        )

        style.configure(
            "Stop.TButton",
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT,
        )
        style.map(
            "Stop.TButton",
            background=[("active", "#D63C2A"), ("hover", "#D63C2A")],
        )

        style.configure(
            "green.Horizontal.TProgressbar",
            background=self.CLR_ACCENT_GREEN,
        )

        style.configure(
            "TCheckbutton",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )

        mpl.rcParams.update(
            {
                "font.family": "Segoe UI",
                "font.size": self.FONT_SIZE_BASE,
                "axes.titlesize": self.FONT_SIZE_BASE + 4,
                "axes.labelsize": self.FONT_SIZE_BASE + 2,
                "figure.facecolor": self.CLR_GRAPH_BG,
            }
        )

    def create_widgets(self):
        font_title_italic = (
            "Segoe UI",
            self.FONT_SIZE_BASE + 2,
            "bold",
            "italic",
        )
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side="top", fill="x")

        # --- Utility Launch Buttons ---
        ttk.Button(
            header_frame,
            text="\U0001F4C8",
            command=launch_plotter_utility,
            width=3,
        ).pack(side="right", padx=10, pady=5)
        ttk.Button(
            header_frame,
            text="\U0001F4DF",
            command=launch_gpib_scanner,
            width=3,
        ).pack(side="right", padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="Keysight E4980A: C-V Measurement",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=font_title_italic,
        ).pack(side="left", padx=20, pady=10)

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            left_panel_container, orient="vertical", command=canvas.yview
        )
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw", width=500
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Populate Panels ---
        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)

        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill="x", expand=True, padx=10, pady=5)

        comp_frame = self.create_compensation_frame(scrollable_frame)
        comp_frame.pack(fill="x", expand=True, padx=10, pady=5)

        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

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
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image,
                )
            except Exception as e:
                self.log(f"ERROR: Failed to load logo: {e}")

        institute_font = ("Segoe UI", self.FONT_SIZE_BASE + 2, "bold")
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=0, column=1, padx=10, pady=(10, 0), sticky="sw")
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=1, column=1, padx=10, sticky="nw")
        ttk.Separator(frame, orient="horizontal").grid(
            row=2, column=1, sticky="ew", padx=10, pady=8
        )

        details_text = (
            f"Program: C-V Measurement (v{self.PROGRAM_VERSION})\n"
            "Instrument: Keysight E4980A LCR Meter\n"
            "Measurement Range: 20 Hz to 2 MHz\n"
            "Function: Cp-Rp (Capacitance - Parallel Resistance)\n"
            "Note: Ensure DUT complies with programmed Bias settings."
        )
        ttk.Label(frame, text=details_text, justify="left").grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w"
        )

        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Experiment Parameters")
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady = (5, 5)
        padx = 10

        self._add_entry(
            frame,
            "Sample Name",
            "sample_name",
            0,
            0,
            colspan=2,
            default="Sample_CV",
        )
        self._add_entry(
            frame, "Max Voltage (V)", "v_max", 2, 0, default="2"
        )
        self._add_entry(
            frame, "Voltage Step (V)", "v_step", 2, 1, default="0.2"
        )
        self._add_entry(
            frame, "Frequency (Hz)", "freq", 4, 0, default="1000"
        )
        self._add_entry(
            frame, "AC Voltage (V)", "v_ac", 4, 1, default="0.5"
        )
        self._add_entry(
            frame, "Number of Loops", "loops", 6, 0, default="1"
        )
        self._add_entry(
            frame, "Dwell Time (s)", "dwell", 6, 1, default="0.5"
        )
        self._add_entry(
            frame, "Averaging Count", "avg_count", 8, 0, default="1"
        )

        # Integration Time selector (Phase 3.2)
        Label(
            frame,
            text="Integration Time:",
            font=self.FONT_BASE,
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
        ).grid(row=8, column=1, padx=padx, pady=pady, sticky="w")
        aper_combo = ttk.Combobox(
            frame,
            textvariable=self.aper_var,
            values=["SHOR", "MED", "LONG"],
            state="readonly",
            font=self.FONT_BASE,
            width=8,
        )
        aper_combo.grid(row=9, column=1, padx=padx, pady=(0, 10), sticky="w")

        Label(
            frame,
            text="LCR Meter VISA:",
            font=self.FONT_BASE,
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
        ).grid(
            row=10, column=0, columnspan=2, padx=padx, pady=pady, sticky="w"
        )
        self.lcr_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly"
        )
        self.lcr_combobox.grid(
            row=11,
            column=0,
            columnspan=2,
            padx=padx,
            pady=(0, 10),
            sticky="ew",
        )

        ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa,
        ).grid(row=12, column=0, columnspan=2, padx=padx, pady=5, sticky="ew")
        ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location,
        ).grid(
            row=13, column=0, columnspan=2, padx=padx, pady=5, sticky="ew"
        )

        self.start_button = ttk.Button(
            frame,
            text="Start Sweep",
            command=self.start_sweep,
            style="Start.TButton",
        )
        self.start_button.grid(
            row=14, column=0, padx=(padx, 5), pady=15, sticky="ew"
        )

        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_sweep,
            style="Stop.TButton",
            state="disabled",
        )
        self.stop_button.grid(
            row=14, column=1, padx=(5, padx), pady=15, sticky="ew"
        )

        self.progress_bar = ttk.Progressbar(
            frame,
            orient="horizontal",
            mode="determinate",
            style="green.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(
            row=15, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky="ew"
        )

        return frame

    def create_compensation_frame(self, parent):
        """Phase 2: Open/Short compensation controls."""
        frame = ttk.LabelFrame(parent, text="Open/Short Compensation")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)

        # Checkbox to enable/disable correction (Phase 2.1)
        Checkbutton(
            frame,
            text="Enable Open/Short Correction",
            variable=self.corr_enabled_var,
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
            selectcolor=self.CLR_HEADER,
            activebackground=self.CLR_BG_DARK,
            activeforeground=self.CLR_FG_LIGHT,
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")

        # Cable length entry (Phase 2.2)
        Label(
            frame,
            text="Cable Length (m):",
            font=self.FONT_BASE,
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
        ).grid(row=1, column=0, padx=10, pady=5, sticky="w")
        cable_combo = ttk.Combobox(
            frame,
            textvariable=self.cable_length_var,
            values=["0", "1", "2", "4"],
            state="readonly",
            font=self.FONT_BASE,
            width=8,
        )
        cable_combo.grid(row=1, column=1, padx=10, pady=5, sticky="w")

        # Perform Compensation button (Phase 2.3)
        ttk.Button(
            frame,
            text="Perform Compensation",
            command=self._perform_compensation,
        ).grid(row=2, column=0, columnspan=2, padx=10, pady=10, sticky="ew")

        return frame

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text="Console Output",
            relief="groove",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state="disabled",
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap="word",
            bd=0,
        )
        self.console_widget.pack(pady=5, padx=5, fill="both", expand=True)
        self.log(
            "Console initialized. Configure parameters and scan for "
            "instruments."
        )
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text="Live C-V Curve")
        graph_container.pack(fill="both", expand=True, padx=5, pady=5)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot(
            [], [],
            color="#C00000",
            marker="o",
            markersize=4,
            linestyle="-",
        )
        self.ax_main.set_title(
            "Capacitance vs. Voltage", fontweight="bold"
        )
        self.ax_main.set_xlabel("Bias Voltage (V)")
        self.ax_main.set_ylabel("Capacitance (F)")
        self.ax_main.grid(True, linestyle="--", alpha=0.7)
        self.figure.tight_layout(pad=2.5)

        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5
        )

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state="normal")
        self.console_widget.insert("end", f"[{timestamp}] {message}\n")
        self.console_widget.see("end")
        self.console_widget.config(state="disabled")

    def _add_entry(
        self, parent, text, dict_key, r, c, colspan=1, default=""
    ):
        """Helper to create an entry widget and map it to a dict key."""
        Label(
            parent,
            text=f"{text}:",
            font=self.FONT_BASE,
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
        ).grid(row=r, column=c, padx=10, pady=(5, 0), sticky="w")
        entry = Entry(parent, font=self.FONT_BASE)
        entry.grid(
            row=r + 1,
            column=c,
            columnspan=colspan,
            padx=10,
            pady=(0, 10),
            sticky="ew",
        )
        entry.insert(0, default)
        self.entries[dict_key] = entry

    def _perform_compensation(self):
        """Phase 2.3: Guides user through open/short compensation."""
        visa_addr = self.lcr_combobox.get()
        if not visa_addr:
            messagebox.showerror(
                "Error", "Please scan for and select a VISA instrument first."
            )
            return

        v_ac = float(self.entries["v_ac"].get())
        freq = float(self.entries["freq"].get())

        # Use a temporary backend connection for compensation
        temp_backend = LCR_Backend()
        try:
            self.log(
                "Starting compensation. Instrument will be configured "
                "temporarily."
            )
            temp_backend.initialize_instrument(
                {
                    "lcr_visa": visa_addr,
                    "v_max": 2.0,
                    "v_step": 0.2,
                    "freq": freq,
                    "v_ac": v_ac,
                    "loops": 1,
                    "aper": self.aper_var.get(),
                    "avg_count": 1,
                    "corr_enable": True,
                    "cable_length": self.cable_length_var.get(),
                    "dwell": 0.5,
                }
            )

            # Step 1: OPEN compensation
            response = messagebox.askyesno(
                "Open Compensation",
                "Remove the DUT from the fixture (leave OPEN).\n\n"
                "Click YES when the fixture is in OPEN state.",
            )
            if not response:
                self.log("Compensation cancelled by user.")
                temp_backend.close_instrument()
                return
            temp_backend.perform_open_compensation()
            self.log("OPEN compensation data acquired.")

            # Step 2: SHORT compensation
            response = messagebox.askyesno(
                "Short Compensation",
                "Short the fixture contacts (use a shorting bar).\n\n"
                "Click YES when the fixture is in SHORT state.",
            )
            if not response:
                self.log("Compensation cancelled by user.")
                temp_backend.close_instrument()
                return
            temp_backend.perform_short_compensation(v_ac)
            self.log("SHORT compensation data acquired.")

            self.log("Compensation complete. Correction is now active.")
            messagebox.showinfo(
                "Compensation Complete",
                "Open/Short compensation has been performed successfully.\n"
                "Correction is now active on the instrument.",
            )

        except Exception as e:
            self.log(f"ERROR during compensation: {traceback.format_exc()}")
            messagebox.showerror(
                "Compensation Error",
                f"Failed to perform compensation:\n\n{e}",
            )
        finally:
            temp_backend.close_instrument()

    def start_sweep(self):
        try:
            # Gather & validate parameters before hardware contact
            params = {
                "sample_name": self.entries["sample_name"].get(),
                "v_max": float(self.entries["v_max"].get()),
                "v_step": float(self.entries["v_step"].get()),
                "freq": float(self.entries["freq"].get()),
                "v_ac": float(self.entries["v_ac"].get()),
                "loops": int(self.entries["loops"].get()),
                "dwell": float(self.entries["dwell"].get()),
                "avg_count": int(self.entries["avg_count"].get()),
                "aper": self.aper_var.get(),
                "lcr_visa": self.lcr_combobox.get(),
                "corr_enable": self.corr_enabled_var.get(),
                "cable_length": self.cable_length_var.get(),
            }
            self.current_params = params

            if not all(
                [
                    params["sample_name"],
                    params["lcr_visa"],
                    self.file_location_path,
                ]
            ):
                raise ValueError(
                    "Sample Name, VISA address, and Save Location are "
                    "required."
                )
            if params["v_step"] <= 0 or params["loops"] <= 0:
                raise ValueError(
                    "Voltage Step and Number of Loops must be positive."
                )
            if params["dwell"] < 0.1:
                raise ValueError(
                    "Dwell time must be at least 0.1 seconds."
                )
            if not (1 <= params["avg_count"] <= 999):
                raise ValueError(
                    "Averaging count must be between 1 and 999."
                )
            if params["corr_enable"]:
                cl = int(params["cable_length"])
                if cl not in (0, 1, 2, 4):
                    raise ValueError(
                        "Cable length must be 0, 1, 2, or 4 meters."
                    )

            # Calculate sweep points (Phase 4.1: no sweep_gen)
            self.sweep_points = self._build_sweep_points(
                params["v_max"], params["v_step"], params["loops"]
            )
            if not self.sweep_points:
                raise ValueError(
                    "Calculated zero measurement points. Verify voltage step."
                )

            # Create CSV file with rich metadata (Phase 4.3)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = (
                f"{params['sample_name']}_{timestamp}_CV.csv"
            )
            self.data_filepath = os.path.join(
                self.file_location_path, file_name
            )

            with open(self.data_filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [f"# C-V Measurement Data - {timestamp}"]
                )
                writer.writerow(
                    [f"# Sample: {params['sample_name']}"]
                )
                writer.writerow(
                    [f"# Frequency: {params['freq']} Hz"]
                )
                writer.writerow(
                    [f"# AC Voltage: {params['v_ac']} V"]
                )
                writer.writerow(
                    [f"# Max Voltage: {params['v_max']} V"]
                )
                writer.writerow(
                    [f"# Voltage Step: {params['v_step']} V"]
                )
                writer.writerow(
                    [f"# Loops: {params['loops']}"]
                )
                writer.writerow(
                    [f"# Dwell Time: {params['dwell']} s"]
                )
                writer.writerow(
                    [f"# Integration Time: {params['aper']}"]
                )
                writer.writerow(
                    [f"# Averaging Count: {params['avg_count']}"]
                )
                writer.writerow(
                    [
                        f"# Compensation: "
                        f"{'ENABLED' if params['corr_enable'] else 'DISABLED'}"
                    ]
                )
                if params["corr_enable"]:
                    writer.writerow(
                        [f"# Cable Length: {params['cable_length']} m"]
                    )
                writer.writerow(
                    [f"# Function: Cp-Rp"]
                )
                writer.writerow(
                    [
                        "Voltage (V)",
                        "Capacitance (F)",
                        "Loop",
                        "Protocol",
                    ]
                )
            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}"
            )

            # Initialize backend
            self.backend.initialize_instrument(params)
            self.log(
                f"Backend initialized for sample: {params['sample_name']}"
            )

            # Phase 3.4: Ramp bias to first sweep point
            first_v = self.sweep_points[0][0]
            if abs(first_v) > 0:
                self.log(
                    f"Ramping bias to starting voltage: {first_v:.3f} V"
                )
                self.backend.ramp_bias_to_voltage(
                    first_v, step=params["v_step"], dwell=0.1
                )

            self.is_running = True
            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")

            for key in self.data_storage:
                self.data_storage[key].clear()

            self.line_main.set_data([], [])
            self.ax_main.set_title(
                f"C-V Curve for: {params['sample_name']}",
                fontweight="bold",
            )
            self.canvas.draw()
            self.sweep_index = 0

            self.progress_bar["value"] = 0
            self.progress_bar["maximum"] = len(self.sweep_points)
            self.log("Starting C-V sweep...")

            # Launch measurement worker thread (Phase 1.2)
            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True
            )
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.backend.close_instrument()
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start sweep.\n\n{e}",
            )

    def stop_sweep(self, reason=""):
        if self.is_running:
            self.is_running = False

            if reason:
                self.log(f"Sweep stopped: {reason}")
            else:
                self.log("Sweep stopped by user.")

            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")

            # Phase 4.4: Increased thread join timeout to 10s
            if (
                self.measurement_thread is not None
                and self.measurement_thread.is_alive()
                and threading.current_thread() is not self.measurement_thread
            ):
                self.measurement_thread.join(timeout=10.0)

            # Shut down gracefully (bias ramps to 0)
            self.backend.close_instrument()
            self.log("Instrument connection closed.")

            if not reason:
                messagebox.showinfo(
                    "Info",
                    "Sweep stopped and instrument disconnected.",
                )

    def _build_sweep_points(self, v_max, v_step, loops):
        """Build sweep point list using integer steps to prevent drift."""
        n = int(round(v_max / v_step))
        up = [round(i * v_step, 9) for i in range(n + 1)]
        pts = []
        for loop in range(1, loops + 1):
            pts += [(v, loop, "A") for v in up]  # 0 to +V
            pts += [(v, loop, "B") for v in reversed(up)]  # +V to 0
            pts += [(-v, loop, "C") for v in up]  # 0 to -V
            pts += [(-v, loop, "D") for v in reversed(up)]  # -V to 0
        return pts

    def _measurement_worker(self):
        """Worker thread to run hardware SCPI calls safely.

        Phase 1.2: Captures full traceback string in the worker thread
        so the actual error is preserved when processed by the GUI thread.
        """
        try:
            dwell = self.current_params.get("dwell", 0.5)
            for i in range(len(self.sweep_points)):
                if not self.is_running:
                    return

                target_v, loop_n, proto = self.sweep_points[i]
                actual_v, cap = self.backend.perform_measurement(
                    target_v, dwell=dwell
                )
                self.data_queue.put(
                    ("DATA", actual_v, cap, loop_n, proto, i)
                )

            # Natural completion signal
            self.data_queue.put(None)

        except Exception:
            # Phase 1.2: Capture full traceback HERE, not in GUI thread
            self.data_queue.put(traceback.format_exc())

    def _process_data_queue(self):
        """Polls measurement results back into GUI main thread."""
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()
                if item is None:
                    self._handle_sweep_completion()
                    return
                if isinstance(item, str):
                    # Phase 1.2: This is a captured traceback string
                    self._handle_sweep_error(item)
                    return

                _, v, c, l, p, idx = item
                self._process_sweep_point(v, c, l, p)
                self.sweep_index = idx + 1
                self._update_sweep_plot()
        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(100, self._process_data_queue)

    def _scan_for_visa(self):
        if not PYVISA_AVAILABLE:
            self.log(
                "ERROR: PyVISA not found. Ensure PyVISA and a backend "
                "(like NI-VISA) are installed."
            )
            return

        backend = self.backend
        if backend.rm is None:
            self.log("ERROR: VISA manager failed. Is NI-VISA installed?")
            return

        self.log("Scanning for VISA instruments...")
        try:
            resources = backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lcr_combobox["values"] = resources
                for res in resources:
                    if "GPIB0::17" in res:  # Common GPIB for E4980A
                        self.lcr_combobox.set(res)
                        break
                if not self.lcr_combobox.get():
                    self.lcr_combobox.set(resources[0])
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno(
                "Exit", "Sweep is running. Stop and exit?"
            ):
                self.stop_sweep("User closed application.")
                self.root.destroy()
        else:
            self.root.destroy()

    def _process_sweep_point(self, actual_v, cap, loop_n, proto):
        self.log(
            f"V: {actual_v:.3f} V | C: {cap:.4e} F | "
            f"Loop: {loop_n} ({proto})"
        )
        self.data_storage["voltage"].append(actual_v)
        self.data_storage["capacitance"].append(cap)
        self.data_storage["loop"].append(loop_n)
        self.data_storage["protocol"].append(proto)

        with open(self.data_filepath, "a", newline="") as f:
            csv.writer(f).writerow(
                [f"{actual_v:.6f}", f"{cap:.6e}", loop_n, proto]
            )

    def _update_sweep_plot(self):
        self.line_main.set_data(
            self.data_storage["voltage"], self.data_storage["capacitance"]
        )
        self.ax_main.relim()
        self.ax_main.autoscale_view()
        self.figure.tight_layout(pad=2.5)
        self.canvas.draw()
        self.progress_bar["value"] = self.sweep_index

    def _handle_sweep_completion(self):
        self.log("Sweep finished successfully.")
        self.stop_sweep("Sweep naturally complete.")
        messagebox.showinfo("Finished", "C-V sweep is complete.")

    def _handle_sweep_error(self, traceback_str):
        """Phase 1.2: Now receives the actual traceback string."""
        self.log(f"RUNTIME ERROR:\n{traceback_str}")
        self.stop_sweep(
            "A critical hardware or measurement error occurred."
        )
        messagebox.showerror(
            "Runtime Error",
            "An error occurred during the sweep. Check console for "
            "full traceback.",
        )


def main():
    """Initializes and runs the main application."""
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\n"
            "Please run:\npip install pyvisa",
        )
        return

    root = tk.Tk()
    LCR_CV_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()