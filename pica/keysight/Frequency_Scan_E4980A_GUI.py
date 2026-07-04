'''
 PROGRAM:      Keysight E4980A Frequency Scan (R-X) GUI
 PURPOSE:      Provide a robust interface for automating Frequency sweeps with ALC,
               Aperture control, Open/Short corrections, Cable Length correction,
               and Full Impedance calculations derived from measured R-X.
               Mirrors legacy LabVIEW panel: Funct R-X, Level 1V, Range auto,
               Bias 0V, Meas Time Med, V_ac on, V_dc off, Corr 1m open short.
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
import os
import time
import math
import traceback
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
from multiprocessing import Process
import runpy
import threading
import queue
import atexit


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

# --- Packages for Back end ---
try:
    import pyvisa

    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# BACKEND CLASS - Instrument Control Logic
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
        """Configures the instrument for a Frequency sweep using R-X function."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(p["lcr_visa"])
        inst.timeout = 60000  # 60 s; low-freq + LONG + autorange can be slow
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
        v_ac_max = min(2.0, 20.0 if self.has_opt001 else 2.0)
        
        if abs(p["dc_bias"]) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V safety limit.")
        if not (0 < p["ac_bias"] <= v_ac_max):
            raise ValueError(f"AC level outside 0-{v_ac_max} Vrms safety limit.")

        # Instrument configuration
        inst.write("*RST; *CLS")
        time.sleep(1.0)  # Graceful reset
        inst.write(":DISP:ENAB ON")
        time.sleep(0.2)

        inst.write(":FUNC:IMP RX")
        inst.write(f":APER {p['aper']}")
        inst.write(":FUNC:IMP:RANG:AUTO ON")
        time.sleep(0.2)

        # Fix: :FORM ASC (ASC is a parameter, not a node)
        inst.write(":FORM ASC")

        inst.write(":FUNC:SMON:VAC ON")
        inst.write(":FUNC:SMON:IAC ON")
        # Fix: V_dc/I_dc monitors are valid for all models, removed Option 001 gate
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
        time.sleep(0.5)  # Let AC level settle

        inst.write(":TRIG:SOUR BUS")
        inst.write(":INIT:CONT ON")
        time.sleep(0.2)

        # Conditional DC Bias Handling
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
                        "Without Option 001, DC bias must be 0, 1.5 or 2 V."
                    )
                inst.write(f":BIAS:VOLT {p['dc_bias']}")
                time.sleep(1.0)  # Graceful settle for discrete bias step

        self._check_errors("configuration")
        print(f"  Connected & configured (RX mode): {idn}")

    def perform_measurement(self, freq, delay):
        """Set frequency, settle, trigger one measurement, fetch R, X, status."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")

        self.instrument.write(f":FREQ {freq}")
        time.sleep(delay)  # settle at the new frequency

        self.instrument.write(":TRIG:IMM")  # trigger one measurement (BUS armed)
        self.instrument.query("*OPC?")  # Wait for operation complete
        vals = self.instrument.query_ascii_values(":FETC?")

        R, X = vals[0], vals[1]
        status = int(vals[2]) if len(vals) > 2 else 0
        return R, X, status

    def close_instrument(self):
        print("--- [Backend] Closing instrument connection. ---")
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

class LCR_Freq_GUI:
    """The main GUI application class for Frequency measurements."""

    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme Colours ---
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

    # Required output format string
    DATA_HEADER = (
        "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\t"
        "R(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''"
    )

    def __init__(self, root):
        self.root = root
        self.root.title("Keysight E4980A Frequency Scan (R-X)")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self._stopping = False          # re-entrancy guard for stop_sweep
        self._close_after_stop = False  # destroy window once worker exits
        self.backend = LCR_Backend()
        self.file_location_path = ""

        # Threading components
        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None

        # Forced Cleanup: register backend close on crash/exit
        atexit.register(self.backend.close_instrument)

        self.data_storage = {
            "freq": [],
            "cp": [],
            "g": [],
            "rs": [],
            "rp": [],
            "status": [],
        }
        self.logo_image = None
        self.sweep_index = 0

        # Decade log autoscale state (LabVIEW-style): current snapped
        # y-limits per axis key; log-Y enabled by default since Cp/G
        # span several decades over a frequency sweep.
        self.log_y_var = tk.BooleanVar(value=True)
        self._decade_ylims = {}

        # Frequency array — UNCHANGED per user instruction (40 Hz to 2 MHz)
        self.sweep_frequencies = np.concatenate(
            [
                np.arange(40, 1000, 10),
                np.arange(1000, 10000, 100),
                np.arange(10000, 100000, 1000),
                np.arange(100000, 1000000, 10000),
                np.arange(1000000, 2000001, 100000),
            ]
        )

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
            "TCheckbutton",
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
        style.configure(
            "Stop.TButton",
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT,
        )
        style.configure(
            "green.Horizontal.TProgressbar",
            background=self.CLR_ACCENT_GREEN,
        )

        mpl.rcParams.update(
            {
                "font.family": "Segoe UI",
                "font.size": self.FONT_SIZE_BASE,
                "axes.titlesize": self.FONT_SIZE_BASE + 2,
                "axes.labelsize": self.FONT_SIZE_BASE,
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

        Label(
            header_frame,
            text="Keysight E4980A: Frequency Scan (R-X)",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=font_title_italic,
        ).pack(side="left", padx=20, pady=10)

        # --- Utility Launch Buttons ---
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

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview,
        )
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw"
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width),
        )
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)

        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill="x", expand=True, padx=10, pady=5)

        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

        # sashpos() has no effect until the PanedWindow is actually mapped and
        # laid out — an early call fails SILENTLY. So we (a) wait for the
        # window to be drawn, (b) measure the real required width of the
        # left-panel content instead of guessing, and (c) retry until the
        # sash position verifiably sticks.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()  # force geometry to be computed

            # Measure the actual content width: inner scrollable frame +
            # vertical scrollbar + a little breathing room. Falls back to
            # LEFT_PANEL_WIDTH if measurement isn't ready yet.
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30  # scrollbar (~15px) + padding
            else:
                target = self.LEFT_PANEL_WIDTH

            self.main_pane.sashpos(0, target)

            # Verify it stuck; if not (widget not mapped yet), retry.
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

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

        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Experiment Parameters")
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady = (2, 5)
        padx = 10

        # Row 0-1: Sample Name
        self._add_entry(
            frame,
            "Sample Name",
            "sample_name",
            0,
            0,
            colspan=2,
            default="Sample_FreqScan",
        )
        # Row 2-3: AC Bias | DC Bias
        self._add_entry(
            frame, "AC Bias Voltage (V)", "ac_bias", 2, 0, default="1.0"
        )
        self._add_entry(
            frame, "DC Bias Voltage (V)", "dc_bias", 2, 1, default="0.0"
        )
        # Row 4-5: Delay | Aperture
        self._add_entry(
            frame, "Delay per step (s)", "delay", 4, 0, default="0.2"
        )

        Label(
            frame, text="Aperture (:APER):", font=self.FONT_BASE
        ).grid(row=4, column=1, padx=padx, pady=pady, sticky="w")
        self.aper_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=["SHOR", "MED", "LONG"],
        )
        self.aper_combobox.set("MED")
        self.aper_combobox.grid(
            row=5, column=1, padx=padx, pady=(0, 10), sticky="ew"
        )

        # Row 6: ALC checkbox
        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Enable Auto Level Control (ALC)",
            variable=self.var_alc,
        ).grid(row=6, column=0, columnspan=2, padx=padx, pady=2, sticky="w")

        # Row 7: Corrections checkbox
        ttk.Checkbutton(
            frame,
            text="Enable Open/Short Corrections",
            variable=self.var_corr,
        ).grid(row=7, column=0, columnspan=2, padx=padx, pady=2, sticky="w")

        # Row 8: Cable Length selector
        Label(
            frame, text="Cable Length (m):", font=self.FONT_BASE
        ).grid(row=8, column=0, padx=padx, pady=pady, sticky="w")
        self.cable_len_combobox = ttk.Combobox(
            frame,
            font=self.FONT_BASE,
            state="readonly",
            values=["0", "1", "2", "4"],
        )
        self.cable_len_combobox.set("1")  # default 1 m to match legacy panel
        self.cable_len_combobox.grid(
            row=8, column=1, padx=padx, pady=pady, sticky="ew"
        )

        # Row 9-10: LCR Meter VISA
        Label(
            frame, text="LCR Meter VISA:", font=self.FONT_BASE
        ).grid(
            row=9, column=0, columnspan=2, padx=padx, pady=(10, 2), sticky="w"
        )
        self.lcr_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly"
        )
        self.lcr_combobox.grid(
            row=10, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky="ew"
        )

        # Row 11: Scan + Browse buttons
        # MUST be assigned to self.scan_button to disable during sweeps
        self.scan_button = ttk.Button(
            frame, text="Scan Instruments", command=self._scan_for_visa
        )
        self.scan_button.grid(row=11, column=0, padx=padx, pady=5, sticky="ew")
        
        ttk.Button(
            frame,
            text="Browse Save Loc...",
            command=self._browse_file_location,
        ).grid(row=11, column=1, padx=padx, pady=5, sticky="ew")

        # Row 12: Start + Stop buttons
        self.start_button = ttk.Button(
            frame,
            text="Start Sweep",
            command=self.start_sweep,
            style="Start.TButton",
        )
        self.start_button.grid(
            row=12, column=0, padx=(padx, 5), pady=15, sticky="ew"
        )

        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_sweep,
            style="Stop.TButton",
            state="disabled",
        )
        self.stop_button.grid(
            row=12, column=1, padx=(5, padx), pady=15, sticky="ew"
        )

        # Row 13: Current frequency label
        self.lbl_current_freq = ttk.Label(
            frame,
            text="Measuring: -- Hz",
            font=("Segoe UI", 12, "bold"),
            foreground=self.CLR_ACCENT_RED,
        )
        self.lbl_current_freq.grid(
            row=13, column=0, columnspan=2, pady=5
        )

        # Row 14: Progress bar
        self.progress_bar = ttk.Progressbar(
            frame,
            orient="horizontal",
            mode="determinate",
            style="green.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(
            row=14,
            column=0,
            columnspan=2,
            padx=padx,
            pady=(5, 10),
            sticky="ew",
        )

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
            height=8,
        )
        self.console_widget.pack(pady=5, padx=5, fill="both", expand=True)
        self.log("Frequency Scan Initialized. Spanning 40 Hz to 2 MHz.")
        return frame

    def create_graph_frame(self, parent):
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)

        self.ax_cp = self.figure.add_subplot(2, 1, 1)
        self.line_cp, = self.ax_cp.plot(
            [], [], color="#C00000", marker="o", markersize=3, linestyle="-"
        )
        self.ax_cp.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp.set_xscale("log")
        self.ax_cp.grid(True, linestyle="--", alpha=0.7)

        self.ax_g = self.figure.add_subplot(2, 1, 2)
        self.line_g, = self.ax_g.plot(
            [], [], color="#2A6B3A", marker="s", markersize=3, linestyle="-"
        )
        self.ax_g.set_xlabel("Frequency (Hz)")
        self.ax_g.set_ylabel("Conductance, G (S)")
        self.ax_g.set_xscale("log")
        self.ax_g.grid(True, linestyle="--", alpha=0.7)

        self.figure.subplots_adjust(
            left=0.08, right=0.98, top=0.98, bottom=0.07, hspace=0.15
        )

        ttk.Checkbutton(
            parent,
            text="Log Y scale (decade autoscale)",
            variable=self.log_y_var,
            command=self._on_log_y_toggle,
        ).pack(anchor="w", padx=5, pady=(5, 0))

        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=0, pady=0
        )

    def _on_log_y_toggle(self):
        """Re-snap axes from scratch when the log-Y checkbox flips."""
        self._decade_ylims.clear()
        self._update_sweep_plot()

    def _decade_autoscale_y(self, ax, values, key):
        """LabVIEW-style decade autoscale: snap y-limits to
        [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))]; expand only,
        whole decades at a time, so the scale never jitters per point.
        Linear fallback if no positive finite data."""
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        if not pos:
            ax.set_yscale('linear')
            ax.relim()
            ax.autoscale_view(scaley=True)
            self._decade_ylims.pop(key, None)
            return False
        lo = 10.0 ** math.floor(math.log10(min(pos)))
        hi = 10.0 ** math.ceil(math.log10(max(pos)))
        if hi <= lo:
            hi = lo * 10.0
        cur = self._decade_ylims.get(key)
        if cur is not None:
            lo, hi = min(lo, cur[0]), max(hi, cur[1])  # expand only
        if cur != (lo, hi):
            self._decade_ylims[key] = (lo, hi)
            ax.set_yscale('log')
            ax.set_ylim(lo, hi)
        return True

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state="normal")
        self.console_widget.insert("end", f"[{timestamp}] {message}\n")
        self.console_widget.see("end")
        self.console_widget.config(state="disabled")

    def _add_entry(
        self, parent, text, dict_key, r, c, colspan=1, default=""
    ):
        Label(
            parent, text=f"{text}:", font=self.FONT_BASE
        ).grid(row=r, column=c, padx=10, pady=(2, 0), sticky="w")
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

    def calculate_impedance_parameters(self, f, R, X):
        """Calculates all 18 parameters from measured R (series resistance)
        and X (reactance).

        Complex impedance:  Z = R + jX
        Complex admittance: Y = 1/Z = (R - jX) / |Z|^2
                           G = Re(Y) = R / |Z|^2
                           B = Im(Y) = -X / |Z|^2

        NOTE: Complex capacitance C* = C' - jC''. By this definition,
        Cp'' = Cs'' = G/omega. This equivalence is used here.

        Returns list of 18 values in DATA_HEADER column order:
            Q, D, G, B, Cp, Lp, Cs, Ls, |Z|, theta(rad), chi, Rs,
            theta(deg), Rp, 1/|Z|, omega, Cp'', Cs''
        """
        omega = 2 * np.pi * f

        # Avoid division by zero
        omega_safe = omega if omega != 0 else 1e-20

        Z_mag = np.sqrt(R ** 2 + X ** 2)
        Z_mag_safe = Z_mag if Z_mag != 0 else 1e-20
        Z_mag_sq = Z_mag_safe ** 2

        # Admittance components
        G = R / Z_mag_sq   # conductance (real part of Y)
        B = -X / Z_mag_sq  # susceptance (imaginary part of Y)

        G_safe = G if G != 0 else 1e-20
        B_safe = B if B != 0 else 1e-20
        X_safe = X if X != 0 else 1e-20

        # Derived parameters
        Rp = 1.0 / G_safe
        Cp = B / omega_safe
        Cs = -1.0 / (omega_safe * X_safe)
        Ls = X / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        # ------------------------------------------------------------------
        # LEGACY COMPATIBILITY:
        # The older LabVIEW program reports inductances (Ls and Lp) as
        # absolute (positive) magnitudes only, regardless of whether the
        # DUT is inductive (+X) or capacitive (-X). To keep this Python
        # implementation drop-in compatible with that legacy data format
        # (so downstream plotting/analysis tools can consume both files
        # interchangeably), we force Ls and Lp to be non-negative here.
        # NOTE: This discards the sign information. If signed inductance
        # is ever needed, derive it back from X (Ls_signed = X/omega).
        # ------------------------------------------------------------------
        Ls = abs(Ls)
        Lp = abs(Lp)

        D = G_safe / B_safe  # dissipation factor
        D_safe = D if D != 0 else 1e-20
        Q = 1.0 / D_safe

        theta_rad = math.atan2(X, R)
        theta_deg = math.degrees(theta_rad)

        chi = X       # reactance
        Rs = R        # series resistance (directly measured)

        Y_mag = 1.0 / Z_mag_safe  # |Y| = 1/|Z|

        # Complex capacitance C* = C' - jC''
        Cp_double_prime = G / omega_safe
        Cs_double_prime = Cp_double_prime

        return [
            Q,                  # 0
            D,                  # 1
            G,                  # 2
            B,                  # 3
            Cp,                 # 4
            Lp,                 # 5   (absolute value — legacy convention)
            Cs,                 # 6
            Ls,                 # 7   (absolute value — legacy convention)
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

    def start_sweep(self):
        try:
            # Extract VISA address from combobox (format: "address  ->  IDN")
            visa_val = self.lcr_combobox.get()
            if "  ->  " in visa_val:
                visa_addr = visa_val.split("  ->  ")[0].strip()
            else:
                visa_addr = visa_val.strip()

            params = {
                "sample_name": self.entries["sample_name"].get(),
                "ac_bias": float(self.entries["ac_bias"].get()),
                "dc_bias": float(self.entries["dc_bias"].get()),
                "delay": float(self.entries["delay"].get()),
                "aper": self.aper_combobox.get(),
                "alc_enabled": self.var_alc.get(),
                "corr_enabled": self.var_corr.get(),
                "cable_len": self.cable_len_combobox.get(),
                "lcr_visa": visa_addr,
            }
            if not all(
                [
                    params["sample_name"],
                    params["lcr_visa"],
                    self.file_location_path,
                ]
            ):
                raise ValueError(
                    "Sample Name, VISA address, and Save Location"
                    " are required."
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = (
                f"{params['sample_name']}_{timestamp}_FreqScan.txt"
            )
            self.data_filepath = os.path.join(
                self.file_location_path, file_name
            )

            self.backend.initialize_instrument(params)

            with open(self.data_filepath, "w", encoding="utf-8") as f:
                f.write(
                    f"# Sample: {params['sample_name']} | "
                    f"AC: {params['ac_bias']}V | "
                    f"DC: {params['dc_bias']}V | "
                    f"APER: {params['aper']}\n"
                )
                f.write(
                    f"# ALC: {params['alc_enabled']} | "
                    f"Corrections: {params['corr_enabled']} | "
                    f"Cable: {params['cable_len']} m | "
                    f"Func: RX\n"
                )
                f.write(self.DATA_HEADER + "\n")

            self.log(
                f"Output file created: "
                f"{os.path.basename(self.data_filepath)}"
            )

            if params["corr_enabled"]:
                self.log(
                    f"WARNING: Ensure physical Open/Short calibration was"
                    f" performed with {params['cable_len']} m cable before"
                    f" enabling corrections!"
                )

            try:
                self.is_running = True
                self.start_button.config(state="disabled")
                self.stop_button.config(state="normal")
                self.scan_button.config(state="disabled") # Disable scan during sweep

                for key in self.data_storage:
                    self.data_storage[key].clear()

                self.line_cp.set_data([], [])
                self.line_g.set_data([], [])
                self._decade_ylims.clear()  # fresh dataset re-snaps decades
                self.canvas.draw()

                self.sweep_index = 0
                self.progress_bar["value"] = 0
                self.progress_bar["maximum"] = len(
                    self.sweep_frequencies
                )

                self.log("Starting Frequency sweep (R-X mode)...")
                
                # Read delay from Tk in the main thread before starting worker
                self.sweep_delay = float(self.entries["delay"].get())
                
                # Start worker thread
                self.stop_event.clear()
                self.worker_thread = threading.Thread(
                    target=self._sweep_loop, daemon=True
                )
                self.worker_thread.start()
                
                # Start polling the queue
                self.root.after(100, self._poll_queue)

            except Exception as sweep_err:
                self.backend.close_instrument()
                raise sweep_err

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start sweep.\n\n{e}",
            )

    def stop_sweep(self, reason=""):
        """Signal the worker to stop, then finish cleanup asynchronously.

        The instrument is closed only after the worker thread has exited
        (non-blocking root.after() poll), so the worker never touches a
        closed VISA handle and the GUI never freezes while a slow VISA
        read drains.
        """
        if self._stopping or not self.is_running:
            return
        self._stopping = True
        self.is_running = False
        self.stop_event.set()  # Signal worker thread to stop
        self.lbl_current_freq.config(text="Measuring: STOPPING…")
        self.stop_button.config(state="disabled")

        if reason:
            self.log(f"Sweep stopped: {reason}")
        else:
            self.log("Sweep stopped by user.")
        self.log("Waiting for worker thread to finish...")

        self._stop_deadline = time.time() + 15.0
        self._poll_worker_stopped(reason)

    def _poll_worker_stopped(self, reason):
        t = self.worker_thread
        if (
            t is not None
            and t.is_alive()
            and time.time() < self._stop_deadline
        ):
            self.root.after(200, lambda: self._poll_worker_stopped(reason))
            return
        if t is not None and t.is_alive():
            self.log(
                "WARNING: worker did not exit within timeout; "
                "closing instrument anyway."
            )
        self._finalize_stop(reason)

    def _finalize_stop(self, reason):
        try:
            self.backend.close_instrument()
        except Exception as e:
            self.log(f"WARNING: error closing instrument: {e}")

        self.lbl_current_freq.config(text="Measuring: STOPPED")
        self.start_button.config(state="normal")
        self.scan_button.config(state="normal")  # Re-enable scan
        self._stopping = False

        if self._close_after_stop:
            self.root.destroy()
            return

        if not reason:
            messagebox.showinfo(
                "Info",
                "Sweep stopped and instrument disconnected.",
            )

    def _sweep_loop(self):
        """Worker thread: performs measurements and puts data in queue."""
        try:
            for i, target_f in enumerate(self.sweep_frequencies):
                if self.stop_event.is_set():
                    break
                
                # Use the delay captured from the main thread
                R, X, status = self.backend.perform_measurement(
                    target_f, self.sweep_delay
                )
                self.data_queue.put((target_f, R, X, status, i))
                
            # Signal completion
            if not self.stop_event.is_set():
                self.data_queue.put(("DONE", None, None, None, None))
                
        except Exception as e:
            self.data_queue.put(("ERROR", e, None, None, None))

    def _poll_queue(self):
        """Main thread: processes data from the worker thread."""
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()
                f, R, X, status, idx = item
                
                if f == "DONE":
                    self._handle_sweep_completion()
                    return
                elif f == "ERROR":
                    self._handle_sweep_error(R)
                    return
                else:
                    self.sweep_index = idx
                    self.lbl_current_freq.config(
                        text=f"Measuring: {f:,.0f} Hz"
                    )
                    self._process_sweep_point(f, R, X, status)
                    self._update_sweep_plot()
        except queue.Empty:
            pass
            
        if self.is_running:
            self.root.after(100, self._poll_queue)

    def _scan_for_visa(self):
        """Identity-aware instrument scan.

        Queries *IDN? on every discovered VISA resource, auto-selects the
        one reporting 'E4980', and displays 'address  ->  IDN' in the
        combobox so the user can see what each device is.
        """
        if not PYVISA_AVAILABLE or self.backend.rm is None:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        rm = self.backend.rm
        found = []
        e4980_label = None

        try:
            for res in rm.list_resources():
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
                if "E4980" in idn and e4980_label is None:
                    e4980_label = label
                self.log(f"  {label}")

            self.lcr_combobox["values"] = found

            if e4980_label:
                self.lcr_combobox.set(e4980_label)
                self.log("E4980A auto-selected.")
            elif found:
                self.lcr_combobox.set(found[0])
                self.log(
                    "WARNING: No E4980A found; defaulted to first device."
                )
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
                # Destroy is deferred to _finalize_stop so the worker
                # can exit cleanly without freezing the GUI.
                self._close_after_stop = True
                self.stop_sweep("User closed application.")
        elif self._stopping:
            # Stop already in progress — just close once it finishes.
            self._close_after_stop = True
        else:
            self.root.destroy()

    def _process_sweep_point(self, f, R, X, status=0):
        if status != 0:
            self.log(
                f"WARNING: f={f} Hz returned status {status} "
                f"(0=normal, non-zero=overload/ALC issue)."
            )

        self.log(
            f"f: {f} Hz | R: {R:.4e} | X: {X:.4e} | st: {status}"
        )

        # Compute full parameter set from R, X (guard against math errors)
        try:
            calc_vals = self.calculate_impedance_parameters(f, R, X)
        except Exception as calc_err:
            self.log(
                f"Calc error at {f} Hz: {calc_err}. "
                f"Saving raw R/X with NaN for derived values."
            )
            calc_vals = [float("nan")] * 18

        # Row: Frequency + 18 calculated values
        row_vals = [f] + calc_vals
        row_str = "\t".join(f"{v:.6E}" for v in row_vals)

        # Write to disk immediately and flush so a crash never loses the point
        with open(self.data_filepath, "a", encoding="utf-8") as file:
            file.write(row_str + "\n")
            file.flush()

        # Store in memory for plotting
        # calc_vals indices: Cp=4, G=2, Rs=11, Rp=13
        self.data_storage["freq"].append(f)
        self.data_storage["cp"].append(calc_vals[4])
        self.data_storage["g"].append(calc_vals[2])
        self.data_storage["rs"].append(calc_vals[11])
        self.data_storage["rp"].append(calc_vals[13])
        self.data_storage["status"].append(status)

    def _update_sweep_plot(self):
        self.line_cp.set_data(
            self.data_storage["freq"], self.data_storage["cp"]
        )
        self.line_g.set_data(
            self.data_storage["freq"], self.data_storage["g"]
        )

        for ax, key, data in (
            (self.ax_cp, "cp", self.data_storage["cp"]),
            (self.ax_g, "g", self.data_storage["g"]),
        ):
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
            if self.log_y_var.get():
                self._decade_autoscale_y(ax, data, key)
            else:
                ax.set_yscale('linear')
                ax.autoscale_view(scaley=True)

        self.canvas.draw_idle()
        self.progress_bar["value"] = self.sweep_index

    def _handle_sweep_completion(self):
        self.lbl_current_freq.config(text="Measuring: DONE")
        self.log("Sweep finished successfully.")
        self.stop_sweep("Sweep naturally complete.")
        messagebox.showinfo("Finished", "Frequency sweep is complete.")

    def _handle_sweep_error(self, exception):
        self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        self.stop_sweep(
            "A critical hardware or measurement error occurred."
        )
        messagebox.showerror(
            "Runtime Error",
            "An error occurred during the sweep. Check console.",
        )


def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\nPlease run:\n"
            "pip install pyvisa",
        )
        return

    root = tk.Tk()
    LCR_Freq_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()