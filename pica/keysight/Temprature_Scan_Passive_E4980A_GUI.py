"""
PROGRAM:       Passive Temperature Sensing & Dielectric Scan (E4980A & L350)
PURPOSE:       Continuously monitors temperature (heater OFF) while performing
               multi-frequency dielectric sweeps (Cp-G) and calculating 18
               impedance parameters. Saves a separate file per frequency.

VERSION:       2.1 (Passive) — Robust SCPI, trigger fix, settling delays
"""

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import threading
import queue
import os
import time
import math
from datetime import datetime
import numpy as np

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import runpy
from multiprocessing import Process

# ===============================================================================
# UTILITY LAUNCHERS
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
        plotter_path = os.path.join(script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}",
            )
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")

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
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")

# ===============================================================================
# OPTIONAL DEPENDENCIES
# ===============================================================================

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# SCPI BACKEND: INSTRUMENT CONTROL
# ===============================================================================

class Lakeshore350_PassiveBackend:
    """SCPI Control for Lakeshore 350 — Read Only (Heater explicitly disabled).

    IMPORTANT: *RST is NOT called here because it erases all user calibration
    curves, zone tables, PID settings, and alarm configurations. For a passive
    read-only application we only ensure the heater is off.
    Reference: Lakeshore 350 Manual, "IEEE-488 Commands" section.
    Source: https://www.lakeshore.com/docs/default-source/product-downloads/lstc_350_l.pdf
    """

    def __init__(self, rm, visa_address):
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        self.instrument.read_termination = '\r\n'
        self.instrument.write_termination = '\r\n'

        idn = self.instrument.query('*IDN?').strip()
        print(f"Lakeshore Connected: {idn}")

        # Clear status register only (does NOT reset settings/curves)
        self.instrument.write('*CLS')
        time.sleep(0.5)

        # Ensure heater is off — Output 1 (main heater)
        # RANGE <output>,<range>  where range 0 = OFF
        # Source: Lakeshore 350 Manual, "RANGE" command
        self.instrument.write('RANGE 1,0')
        time.sleep(0.5)

        # Ensure ramp is off for output 1
        # RAMP <output>,<on/off>,<rate>
        # Rate field 1 is valid (1 K/min), range 0.001–100
        self.instrument.write('RAMP 1,0,1')
        time.sleep(0.5)

    def get_temperature(self, sensor='A'):
        """Read Kelvin temperature from specified sensor input.
        KRDG? returns the current reading in Kelvin.
        """
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def close(self):
        if self.instrument:
            try:
                self.instrument.write('RANGE 1,0')
                self.instrument.close()
            except Exception as e:
                print(f"Lakeshore close warning: {e}")
            finally:
                self.instrument = None


class KeysightE4980A_Backend:
    """SCPI Control for Keysight E4980A LCR Meter.

    Trigger Model Reference (E4980A/E4980AL):
    - :TRIG:SOUR INT  — Internal trigger, instrument triggers itself
                        continuously.
    - :INIT:CONT ON   — Continuous activation: after each measurement the
                        trigger system re-arms automatically.
    - :FETCh?         — Returns the latest completed measurement result;
                        blocks until data is available.

    Using INT + INIT:CONT ON + FETCh? is the simplest and most robust approach
    for point-by-point frequency stepping. The previous code used BUS trigger
    with *TRG which caused "dashes" (no measurement data) due to timing
    conflicts.

    Sources:
    - E4980A Datasheet: https://www.cmc.ca/wp-content/uploads/2019/07/Keysight-E4980A-Datasheet.pdf
    - PyMeasure E4980 driver: https://pymeasure.readthedocs.io/en/stable/api/instruments/agilent/agilentE4980.html
    - Keysight Trigger Help: https://helpfiles.keysight.com/csg/e4982a/programming/remote_control/starting_measurement_cycle_(triggering)_and_detecting_end_of_measurement/starting_a_new_measurement_cycle_(triggering).htm
    """

    def __init__(self, rm, visa_address):
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 15000
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'

        # Check for Option 001 (Power and DC Bias Enhancement)
        # Source: E4980A Datasheet — Option E4980A-001 provides built-in 40V DC bias
        opt_response = self.instrument.query('*OPT?').strip()
        self.has_opt001 = '001' in opt_response

        idn = self.instrument.query('*IDN?').strip()
        print(f"E4980A Connected: {idn}")
        print(f"E4980A Options: {opt_response}")
        if self.has_opt001:
            print("  -> Option 001 (DC Bias) detected.")
        else:
            print("  -> Option 001 (DC Bias) NOT detected.")

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    def _write_and_wait(self, cmd, delay=0.5):
        """Send a SCPI command with a guaranteed settling delay.

        This ensures the instrument has processed the command and any
        internal transients have settled before the next command is sent.
        The default 0.5 s delay is conservative but safe for all setup
        commands per the E4980A command reference.
        """
        self.instrument.write(cmd)
        time.sleep(delay)

    def _check_errors(self, context=""):
        """Query the SCPI error queue and print any errors.

        :SYST:ERR? returns the oldest error and removes it from the queue.
        Returns a string like "0,No error" when the queue is empty.
        """
        while True:
            err = self.instrument.query(':SYST:ERR?').strip()
            if err.startswith('0,') or err.startswith('+0,'):
                break
            print(f"[E4980A SCPI Error] {context}: {err}")

    def _has_corr_data(self, corr_type):
        """Check whether OPEN or SHORT correction data is stored.

        :CORR:OPEN:DATA? / :CORR:SHOR:DATA? return the stored correction
        data. If no data has been stored, the instrument returns empty
        or zero values. Enabling correction without stored data causes
        the display to show dashes.

        Returns True if correction data appears to exist.
        """
        if corr_type.upper() == 'OPEN':
            data = self.instrument.query(':CORR:OPEN:DATA?').strip()
        else:
            data = self.instrument.query(':CORR:SHOR:DATA?').strip()

        # Check that data is not empty and contains non-zero values
        if not data:
            return False
        try:
            vals = [float(v) for v in data.split(',')]
            # If all values are zero, no real correction data is stored
            return any(abs(v) > 1e-15 for v in vals)
        except (ValueError, IndexError):
            return False

    # ------------------------------------------------------------------
    # DC Bias Ramp
    # ------------------------------------------------------------------

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        """Gradually ramp DC bias from current value to target.

        Queries the current bias voltage safely (with fallback to 0V if
        the query fails, e.g., when bias is not yet enabled).
        """
        try:
            current_v = float(self.instrument.query(':BIAS:VOLT?'))
        except Exception:
            current_v = 0.0

        if abs(target_v - current_v) < 0.01:
            return

        n = max(int(math.ceil(abs(target_v - current_v) / step)), 1)
        ramp_points = np.linspace(current_v, target_v, n + 1)[1:]

        for v in ramp_points:
            self.instrument.write(f':BIAS:VOLT {v:.3f}')
            time.sleep(dwell)

    # ------------------------------------------------------------------
    # Measurement Setup
    # ------------------------------------------------------------------

    def setup_measurement(self, ac_bias, dc_bias, aper, alc_on, corr_on):
        """Configure the E4980A for Cp-G measurement with proper settling.

        Parameters:
            ac_bias  : AC test signal voltage in Volts (100 µV to 2 Vrms,
                       or up to 20 Vrms with Option 001)
            dc_bias  : DC bias voltage in Volts (requires Option 001)
            aper     : Aperture mode — 'SHOR', 'MED', or 'LONG'
            alc_on   : Enable Auto Level Control (ALC)
            corr_on  : Enable OPEN/SHORT correction (if data exists)
        """
        # ------------------------------------------------------------------
        # 1. Full reset with proper synchronization
        # *RST is an overlapped command — must wait for completion via *OPC?
        # Source: IEEE 488.2 standard, *OPC? blocks until all pending
        #         overlapped commands complete.
        # ------------------------------------------------------------------
        self.instrument.write('*RST')
        self.instrument.query('*OPC?')
        time.sleep(1.0)  # Extra settling after full reset

        self.instrument.write('*CLS')
        time.sleep(0.5)

        # ------------------------------------------------------------------
        # 2. Display and measurement function
        # :FUNC:IMP CPG — Primary: Cp (parallel capacitance, F)
        #                   Secondary: G  (parallel conductance, S)
        # Source: PyMeasure E4980 mode property — CPG is valid
        # ------------------------------------------------------------------
        self._write_and_wait(':DISP:ENAB ON')
        self._write_and_wait(':FUNC:IMP CPG')

        # ------------------------------------------------------------------
        # 3. Aperture (integration time)
        # SHOR = 5.6 ms, MED = 88 ms, LONG = 220 ms (at 1 MHz)
        # Source: E4980A Datasheet
        # ------------------------------------------------------------------
        self._write_and_wait(f':APER {aper}')

        # ------------------------------------------------------------------
        # 4. Impedance range — auto
        # ------------------------------------------------------------------
        self._write_and_wait(':FUNC:IMP:RANG:AUTO ON')

        # ------------------------------------------------------------------
        # 5. Auto Level Control (ALC)
        # ALC maintains the programmed AC voltage at the DUT terminals
        # ------------------------------------------------------------------
        self._write_and_wait(':AMPL:ALC ON' if alc_on else ':AMPL:ALC OFF')

        # ------------------------------------------------------------------
        # 6. Open/Short correction
        # Only enable if calibration data is actually stored on the
        # instrument. Enabling without data causes dashes.
        # ------------------------------------------------------------------
        if corr_on:
            if self._has_corr_data('OPEN'):
                self._write_and_wait(':CORR:OPEN:STAT ON')
                print("  -> OPEN correction enabled (data found).")
            else:
                print("  -> WARNING: OPEN correction requested but no data "
                      "stored. Skipping.")
            if self._has_corr_data('SHOR'):
                self._write_and_wait(':CORR:SHOR:STAT ON')
                print("  -> SHORT correction enabled (data found).")
            else:
                print("  -> WARNING: SHORT correction requested but no data "
                      "stored. Skipping.")

        # ------------------------------------------------------------------
        # 7. AC test signal level
        # :VOLT sets the oscillator voltage in Vrms
        # ------------------------------------------------------------------
        self._write_and_wait(f':VOLT {ac_bias}')

        # ------------------------------------------------------------------
        # 8. Trigger configuration — INTERNAL trigger + continuous init
        #
        # This is the critical fix for the "dashes" issue.
        # :TRIG:SOUR INT  — Instrument triggers itself automatically
        # :INIT:CONT ON   — Trigger system re-arms after each measurement
        #
        # With this configuration, the instrument continuously measures at
        # the set frequency. We simply change frequency, wait for settling,
        # and fetch the latest result with :FETCh?
        #
        # Source: E4980A/E4982A Trigger Model documentation
        # ------------------------------------------------------------------
        self._write_and_wait(':TRIG:SOUR INT')
        self._write_and_wait(':INIT:CONT ON')

        # ------------------------------------------------------------------
        # 9. DC bias (requires Option 001)
        # If DC bias != 0 and Option 001 is not installed, raise an error
        # before attempting any bias commands.
        # Source: E4980A Datasheet — Option E4980A-001 provides DC bias
        # ------------------------------------------------------------------
        if dc_bias != 0:
            if not self.has_opt001:
                raise RuntimeError(
                    "DC bias requires Option 001 (Power and DC Bias "
                    "Enhancement), which is not installed on this unit."
                )
            # Enable DC bias output, starting from 0V, then ramp to target
            self.instrument.write(':BIAS:VOLT 0')
            time.sleep(0.5)
            self.instrument.write(':BIAS:STAT ON')
            time.sleep(0.5)
            self.safe_ramp_dc_bias(dc_bias)

        # ------------------------------------------------------------------
        # 10. Final error check
        # ------------------------------------------------------------------
        self._check_errors("setup_measurement")

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def measure_freq(self, freq, delay):
        """Measure Cp and G at the specified frequency.

        With :TRIG:SOUR INT and :INIT:CONT ON, the instrument is
        continuously measuring. We set the frequency, wait for the
        measurement to settle, then fetch the latest result.

        :FETCh? blocks until a completed measurement is available and
        returns: <primary>,<secondary>[,<status>]
        For Cp-G mode: returns Cp(F), G(S), [status word]

        Parameters:
            freq  : Measurement frequency in Hz
            delay : Settling time in seconds after frequency change

        Returns:
            tuple (cp, g, status)
        """
        self.instrument.write(f':FREQ {freq}')
        time.sleep(delay)

        # :FETCh? returns the latest completed measurement
        vals = self.instrument.query_ascii_values(':FETCh?')

        if len(vals) < 2:
            raise IOError(
                f"E4980A returned malformed data at {freq} Hz: {vals}"
            )

        cp = vals[0]
        g = vals[1]
        # Status word: 0 = normal, non-zero indicates measurement issues
        status = vals[2] if len(vals) > 2 else 0

        return cp, g, status

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        if self.instrument:
            try:
                # Ramp DC bias back to 0 before disabling
                self.safe_ramp_dc_bias(0.0)
                self.instrument.write(':BIAS:STAT OFF')
                time.sleep(0.3)
                # Return to measurement display page
                self.instrument.write(':DISP:PAGE MEAS')
                self.instrument.close()
            except Exception as e:
                print(f"E4980A close warning: {e}")
            finally:
                self.instrument = None


class Combined_Passive_Backend:
    """Combines Lakeshore 350 and E4980A backends for passive measurement."""

    def __init__(self):
        self.lakeshore = None
        self.lcr = None
        self.params = {}
        self.rm = pyvisa.ResourceManager() if PYVISA_AVAILABLE else None

    def initialize_instruments(self, parameters):
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        self.lakeshore = Lakeshore350_PassiveBackend(
            self.rm, self.params['lakeshore_visa']
        )

        self.lcr = KeysightE4980A_Backend(self.rm, self.params['lcr_visa'])
        self.lcr.setup_measurement(
            self.params['ac_bias'],
            self.params['dc_bias'],
            self.params['aper'],
            self.params['alc_enabled'],
            self.params['corr_enabled']
        )

    def get_temperature(self):
        return self.lakeshore.get_temperature('A')

    def measure_lcr_array(self, freqs, delay):
        results = {}
        for f in freqs:
            cp, g, status = self.lcr.measure_freq(f, delay)
            results[f] = (cp, g)
            # Log status if non-zero (informational)
            if status != 0:
                print(f"[E4980A] Measurement status at {f} Hz: {status}")
        return results

    def close_instruments(self):
        if self.lcr:
            self.lcr.close()
        if self.lakeshore:
            self.lakeshore.close()


# ===============================================================================
# FRONTEND GUI
# ===============================================================================

class Passive_Dielectric_GUI:
    PROGRAM_VERSION = "2.1 (Passive)"
    LOGO_SIZE = 110

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # Color scheme
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#8AB845'
    CLR_ACCENT_RED = '#D63C2A'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'

    # Fonts
    FONT_BASE = ('Segoe UI', 10)
    FONT_SIZE_BASE = 10
    FONT_TITLE = ('Segoe UI', 12, 'bold')
    FONT_CONSOLE = ('Consolas', 9)

    # Data header — clarified labels
    # 20 columns: Time, Temp, + 18 calculated parameters
    DATA_HEADER = (
        "Time(s)\tTemperature\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\t"
        "|Z|\ttheta(rad)\tXs\tRs\ttheta(deg)\tRp\t|Y|\tOmega\t"
        "Cp''\tCs''"
    )

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Passive Temp Sensing & Dielectric Scan v{self.PROGRAM_VERSION}"
        )
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self._stopping = False  # Guard against double-stop
        self.worker_thread = None
        self.logo_image = None
        self.start_time = None
        self.backend = Combined_Passive_Backend()

        self.save_directory = ""
        self.run_folder = ""
        self.file_handles = {}

        self.freq_list = []
        self.data_storage = {'time': [], 'temp': []}
        self.lines_cp = {}
        self.lines_g = {}

        self.data_queue = queue.Queue()

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel', background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE
        )
        style.configure(
            'TLabelframe', background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER
        )
        style.configure(
            'TLabelframe.Label', background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE
        )
        style.configure(
            'TCheckbutton', background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE
        )
        style.configure(
            'TButton', font=self.FONT_BASE, padding=5,
            background=self.CLR_HEADER
        )
        style.configure(
            'Start.TButton', background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK
        )
        style.configure(
            'Stop.TButton', background=self.CLR_ACCENT_RED,
            foreground=self.CLR_GRAPH_BG
        )

    def create_widgets(self):
        # Header
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(
            header_frame, text="Passive T-Sensing & Impedance Spectroscopy",
            bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD,
            font=('Segoe UI', 14, 'bold')
        ).pack(side='left', padx=20, pady=10)

        ttk.Button(
            header_frame, text="📈",
            command=launch_plotter_utility, width=3
        ).pack(side='right', padx=10, pady=5)
        ttk.Button(
            header_frame, text="📟",
            command=launch_gpib_scanner, width=3
        ).pack(side='right', padx=(0, 5), pady=5)

        # Main pane
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # Left panel (scrollable)
        left_panel = ttk.Frame(main_pane, width=450)
        main_pane.add(left_panel, weight=0)

        canvas = Canvas(
            left_panel, bg=self.CLR_BG_DARK, highlightthickness=0
        )
        scrollbar = ttk.Scrollbar(
            left_panel, orient="vertical", command=canvas.yview
        )
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw", width=430
        )
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="top", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Info frame
        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill='x', expand=True, padx=10, pady=5)

        # Input frame
        self.create_input_frame(scrollable_frame)

        # Console
        console_frame = LabelFrame(
            left_panel, text='Console', bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE
        )
        console_frame.pack(side="bottom", fill="both", expand=False, pady=5)
        self.console_widget = scrolledtext.ScrolledText(
            console_frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            font=self.FONT_CONSOLE, height=8
        )
        self.console_widget.pack(fill='both', expand=True, padx=5, pady=5)

        # Right panel (graphs)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)
        self.create_graph_frame(right_panel)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK, highlightthickness=0
        )
        logo_canvas.grid(
            row=0, column=0, rowspan=3, padx=(15, 10), pady=10
        )

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    image=self.logo_image
                )
            except Exception:
                pass

        institute_font = (
            'Segoe UI', self.FONT_SIZE_BASE + 2, 'bold'
        )
        ttk.Label(
            frame, text="UGC-DAE Consortium for Scientific Research",
            font=institute_font, background=self.CLR_BG_DARK
        ).grid(row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(
            frame, text="Mumbai Centre", font=institute_font,
            background=self.CLR_BG_DARK
        ).grid(row=1, column=1, padx=10, sticky='nw')

        return frame

    def create_input_frame(self, parent):
        f_params = LabelFrame(
            parent, text='Measurement Parameters', bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE
        )
        f_params.pack(fill='x', pady=5, padx=5)

        self.entries = {}

        def add_entry(frame, label, key, r, c, default=""):
            Label(frame, text=f"{label}:").grid(
                row=r, column=c, padx=5, pady=2, sticky='w'
            )
            e = Entry(frame, font=self.FONT_BASE, width=16)
            e.grid(row=r + 1, column=c, padx=5, pady=(0, 5), sticky='w')
            e.insert(0, default)
            self.entries[key] = e

        add_entry(f_params, "Sample Name", 'sample', 0, 0, "Sample_01")
        add_entry(f_params, "Loop Delay (s)", 'loop_delay', 0, 1, "5.0")

        add_entry(f_params, "AC Bias (V)", 'ac_bias', 2, 0, "1.0")
        add_entry(f_params, "DC Bias (V)", 'dc_bias', 2, 1, "0.0")
        add_entry(f_params, "LCR Meas Delay (s)", 'lcr_delay', 4, 0, "0.2")

        Label(f_params, text="Aperture:").grid(
            row=4, column=1, padx=5, sticky='w'
        )
        self.aper_cb = ttk.Combobox(
            f_params, values=['SHOR', 'MED', 'LONG'],
            state='readonly', width=14
        )
        self.aper_cb.set('MED')
        self.aper_cb.grid(row=5, column=1, padx=5, pady=(0, 5), sticky='w')

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            f_params, text="ALC ON", variable=self.var_alc
        ).grid(row=6, column=0, sticky='w', padx=5)
        ttk.Checkbutton(
            f_params, text="Open/Short Corr", variable=self.var_corr
        ).grid(row=6, column=1, sticky='w', padx=5)

        # Frequency selection
        f_freq = LabelFrame(
            parent, text='Frequency Selection (Hz)', bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE
        )
        f_freq.pack(fill='x', pady=5, padx=5)

        self.freq_text = scrolledtext.ScrolledText(
            f_freq, height=4, width=40, font=self.FONT_BASE
        )
        self.freq_text.pack(padx=5, pady=5, fill='x')
        self.freq_text.insert(
            'end',
            "1000, 5000, 10000, 50000, 100000, 500000, 1000000"
        )

        # Hardware & execution
        f_hw = LabelFrame(
            parent, text='Hardware & Execution', bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE
        )
        f_hw.pack(fill='x', pady=5, padx=5)

        Label(f_hw, text="Lakeshore VISA:").grid(
            row=0, column=0, sticky='w', padx=5
        )
        self.ls_visa = ttk.Combobox(f_hw, state='readonly', width=18)
        self.ls_visa.grid(row=1, column=0, padx=5, pady=(0, 5))

        Label(f_hw, text="E4980A VISA:").grid(
            row=0, column=1, sticky='w', padx=5
        )
        self.lcr_visa = ttk.Combobox(f_hw, state='readonly', width=18)
        self.lcr_visa.grid(row=1, column=1, padx=5, pady=(0, 5))

        ttk.Button(
            f_hw, text="Scan Instruments", command=self._scan_visa
        ).grid(row=2, column=0, columnspan=2, sticky='ew', padx=5, pady=5)
        ttk.Button(
            f_hw, text="Browse Save Folder", command=self._browse_dir
        ).grid(row=3, column=0, columnspan=2, sticky='ew', padx=5, pady=5)

        self.start_btn = ttk.Button(
            f_hw, text="Start Passive Logging",
            command=self.start_measurement, style='Start.TButton'
        )
        self.start_btn.grid(row=4, column=0, padx=5, pady=10, sticky='ew')
        self.stop_btn = ttk.Button(
            f_hw, text="Stop", command=self.stop_measurement,
            style='Stop.TButton', state='disabled'
        )
        self.stop_btn.grid(row=4, column=1, padx=5, pady=10, sticky='ew')

    def create_graph_frame(self, parent):
        self.figure = Figure(
            dpi=100, facecolor=self.CLR_GRAPH_BG, layout='tight'
        )

        self.ax_cp = self.figure.add_subplot(2, 1, 1)
        self.ax_cp.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp.set_title("Cp vs. Temperature", fontweight='bold')
        self.ax_cp.set_yscale('log')
        self.ax_cp.grid(True, linestyle='--', alpha=0.6)

        self.ax_g = self.figure.add_subplot(2, 1, 2)
        self.ax_g.set_xlabel("Temperature (K)")
        self.ax_g.set_ylabel("Conductance, G (S)")
        self.ax_g.set_title("G vs. Temperature", fontweight='bold')
        self.ax_g.set_yscale('log')
        self.ax_g.grid(True, linestyle='--', alpha=0.6)

        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5
        )

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{ts}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _scan_visa(self):
        if not PYVISA_AVAILABLE:
            self.log("PyVISA not installed.")
            return
        rm = pyvisa.ResourceManager()
        res = rm.list_resources()
        self.ls_visa['values'] = res
        self.lcr_visa['values'] = res
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
        """Calculate 18 impedance parameters from Cp and G.

        Given parallel capacitance Cp and parallel conductance G at
        frequency f, computes:

        Admittance: Y = G + jB  where B = omega * Cp
        Impedance:  Z = 1/Y = Rs + jXs

        Parameters derived:
          Q       : Quality factor = 1/D
          D       : Dissipation factor = G/B
          G       : Parallel conductance (S) = 1/Rp
          B       : Susceptance (S) = omega * Cp
          Cp      : Parallel capacitance (F)
          Lp      : Parallel inductance (H) = -1/(omega * B)
          Cs      : Series capacitance (F) = -1/(omega * Xs)
          Ls      : Series inductance (H) = Xs / omega
          |Z|     : Impedance magnitude (Ohm) = 1/|Y|
          theta(rad) : Impedance phase angle (rad) = atan2(Xs, Rs)
          Xs      : Series reactance (Ohm)
          Rs      : Series resistance (Ohm) = G / |Y|^2
          theta(deg) : Impedance phase angle (degrees)
          Rp      : Parallel resistance (Ohm) = 1/G
          |Y|     : Admittance magnitude (S) = sqrt(G^2 + B^2)
          Omega   : Angular frequency (rad/s) = 2*pi*f
          Cp''    : Loss capacitance (parallel) = G / omega
          Cs''    : Loss capacitance (series) = 1 / (omega * Rs)

        Note: Cs'' != Cp'' in general.
              Cp'' = G/omega (parallel loss representation)
              Cs'' = 1/(omega*Rs) (series loss representation)
        """
        omega = 2 * np.pi * f

        # Guard against division by zero
        G_safe = g if g != 0 else 1e-20
        omega_safe = omega if omega != 0 else 1e-20

        Rp = 1.0 / G_safe
        B = omega * cp
        B_safe = B if B != 0 else 1e-20

        D = G_safe / B_safe
        Q = 1.0 / D if D != 0 else 0.0

        Y_mag = np.sqrt(g ** 2 + B ** 2)
        Y_mag_safe = Y_mag if Y_mag != 0 else 1e-20
        Z_mag = 1.0 / Y_mag_safe

        Rs = g / (Y_mag_safe ** 2)
        Xs = -B / (Y_mag_safe ** 2)
        Xs_safe = Xs if Xs != 0 else 1e-20
        Rs_safe = Rs if Rs != 0 else 1e-20

        theta_rad = math.atan2(Xs, Rs)
        theta_deg = math.degrees(theta_rad)

        chi = Xs  # Reactance (labeled Xs in header)
        Cs = -1.0 / (omega_safe * Xs_safe)
        Ls = Xs / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        # Loss capacitances — corrected
        # Cp'' (parallel loss) = G / omega
        Cp_double_prime = g / omega_safe

        # Cs'' (series loss) = 1 / (omega * Rs)
        # This is NOT equal to Cp'' in general.
        Cs_double_prime = 1.0 / (omega_safe * Rs_safe)

        return [
            Q, D, g, B, cp, Lp, Cs, Ls, Z_mag, theta_rad,
            chi, Rs, theta_deg, Rp, Y_mag, omega,
            Cp_double_prime, Cs_double_prime
        ]

    def _open_data_files(self, sample_name, timestamp):
        self.run_folder = os.path.join(
            self.save_directory, f"{sample_name}_{timestamp}_Passive"
        )
        os.makedirs(self.run_folder, exist_ok=True)
        self.file_handles = {}

        for f in self.freq_list:
            fname = os.path.join(
                self.run_folder, f"{sample_name}_{f}Hz.txt"
            )
            fh = open(fname, 'w', encoding='utf-8')
            fh.write(
                f"# PASSIVE SCAN | Sample: {sample_name} | "
                f"Freq: {f} Hz | "
                f"AC: {self.entries['ac_bias'].get()}V | "
                f"DC: {self.entries['dc_bias'].get()}V\n"
            )
            fh.write(self.DATA_HEADER + "\n")
            self.file_handles[f] = fh
        self.log(f"Created {len(self.freq_list)} frequency files in folder.")

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
            raw = self.freq_text.get("1.0", "end").strip()
            self.freq_list = [
                int(float(x.strip()))
                for x in raw.split(',')
                if x.strip()
            ]
            if not self.freq_list:
                raise ValueError("Frequency list is empty or invalid.")

            params = {
                'sample': self.entries['sample'].get(),
                'loop_delay': float(self.entries['loop_delay'].get()),
                'ac_bias': float(self.entries['ac_bias'].get()),
                'dc_bias': float(self.entries['dc_bias'].get()),
                'lcr_delay': float(self.entries['lcr_delay'].get()),
                'aper': self.aper_cb.get(),
                'alc_enabled': self.var_alc.get(),
                'corr_enabled': self.var_corr.get(),
                'lakeshore_visa': self.ls_visa.get(),
                'lcr_visa': self.lcr_visa.get()
            }

            if not all([
                params['sample'],
                params['lakeshore_visa'],
                params['lcr_visa'],
                self.save_directory
            ]):
                raise ValueError(
                    "Missing VISA addresses or Save Directory."
                )

            self.backend.initialize_instruments(params)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._open_data_files(params['sample'], ts)

            self.start_btn.config(state='disabled')
            self.stop_btn.config(state='normal')

            # Setup plotting arrays & lines
            self.ax_cp.clear()
            self.ax_g.clear()
            self.ax_cp.set_yscale('log')
            self.ax_g.set_yscale('log')
            self.ax_cp.grid(True, linestyle='--', alpha=0.6)
            self.ax_g.grid(True, linestyle='--', alpha=0.6)
            self.ax_cp.set_ylabel("Capacitance, Cp (F)")
            self.ax_cp.set_title("Cp vs. Temperature", fontweight='bold')
            self.ax_g.set_ylabel("Conductance, G (S)")
            self.ax_g.set_title("G vs. Temperature", fontweight='bold')
            self.ax_g.set_xlabel("Temperature (K)")

            self.data_storage = {'time': [], 'temp': []}
            self.lines_cp = {}
            self.lines_g = {}

            cmap = mpl.colormaps['viridis']
            for i, f in enumerate(self.freq_list):
                self.data_storage[f] = {'cp': [], 'g': []}
                denom = max(len(self.freq_list) - 1, 1)
                c = cmap(i / denom)
                self.lines_cp[f], = self.ax_cp.plot(
                    [], [], color=c, marker='.', markersize=3,
                    linestyle='-', linewidth=1, label=f"{f} Hz"
                )
                self.lines_g[f], = self.ax_g.plot(
                    [], [], color=c, marker='.', markersize=3,
                    linestyle='-', linewidth=1, label=f"{f} Hz"
                )

            self.ax_cp.legend(fontsize=7, loc='best')
            self.ax_g.legend(fontsize=7, loc='best')

            self.canvas.draw()
            self.is_running = True
            self._stopping = False
            self.start_time = time.time()

            self.worker_thread = threading.Thread(
                target=self._measurement_worker, daemon=True
            )
            self.worker_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"Startup Error: {e}")
            messagebox.showerror("Error", str(e))

    def _flush_queue_to_disk(self):
        """Write any remaining queued measurements to disk before closing."""
        while not self.data_queue.empty():
            try:
                item = self.data_queue.get_nowait()
            except queue.Empty:
                break
            if not (
                isinstance(item, tuple)
                and item
                and item[0] == 'DATA'
            ):
                continue
            _, elapsed, t, results = item
            for f in self.freq_list:
                if f not in results:
                    continue
                cp, g = results[f]
                calc_vals = self.calculate_impedance_parameters(f, cp, g)
                row_str = "\t".join(
                    f"{v:.6E}" for v in ([elapsed, t] + calc_vals)
                )
                fh = self.file_handles.get(f)
                if fh and not fh.closed:
                    fh.write(row_str + "\n")
                    fh.flush()

    def stop_measurement(self, show_info=True):
        """Stop measurement, flush data, and close instruments safely.

        Includes a guard against double-stop calls.
        """
        if not self.is_running or self._stopping:
            return

        self._stopping = True
        self.is_running = False
        self.log("Stopping Passive Measurement...")

        # Wait for the worker to finish its current cycle so we don't
        # tear down VISA mid-transaction.
        if self.worker_thread is not None:
            self.worker_thread.join(
                timeout=self.backend.params.get('loop_delay', 5) + 20
            )
            self.worker_thread = None

        # Persist anything still queued BEFORE closing files.
        self._flush_queue_to_disk()

        self.start_btn.config(state='normal')
        self.stop_btn.config(state='disabled')

        self.backend.close_instruments()
        self._close_data_files()

        self._stopping = False

        if show_info:
            messagebox.showinfo(
                "Stopped",
                "Measurement interrupted and files closed safely."
            )

    def _measurement_worker(self):
        p = self.backend.params
        loop_delay = p['loop_delay']

        while self.is_running:
            try:
                t_start_loop = time.time()
                elapsed = t_start_loop - self.start_time

                # 1. Read passive temperature
                current_t = self.backend.get_temperature()

                # 2. Sweep frequencies
                freq_results = self.backend.measure_lcr_array(
                    self.freq_list, p['lcr_delay']
                )

                # 3. Queue results for GUI thread
                self.data_queue.put(
                    ('DATA', elapsed, current_t, freq_results)
                )

                # 4. Enforce loop delay
                elapsed_in_loop = time.time() - t_start_loop
                sleep_time = loop_delay - elapsed_in_loop
                if sleep_time > 0:
                    time.sleep(sleep_time)

            except Exception as e:
                self.data_queue.put(e)
                break

    def _process_data_queue(self):
        try:
            while not self.data_queue.empty():
                item = self.data_queue.get_nowait()

                if isinstance(item, Exception):
                    self.log(f"RUNTIME ERROR: {item}")
                    self.stop_measurement(show_info=False)
                    messagebox.showerror("Runtime Error", str(item))

                elif isinstance(item, tuple) and item[0] == 'DATA':
                    _, elapsed, t, results = item

                    self.data_storage['time'].append(elapsed)
                    self.data_storage['temp'].append(t)
                    self.log(
                        f"Elapsed: {elapsed:.1f}s | "
                        f"Temp: {t:.2f}K logged."
                    )

                    for f in self.freq_list:
                        cp, g = results[f]
                        self.data_storage[f]['cp'].append(cp)
                        self.data_storage[f]['g'].append(g)

                        calc_vals = self.calculate_impedance_parameters(
                            f, cp, g
                        )
                        row_vals = [elapsed, t] + calc_vals
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

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _update_plots(self):
        temps = self.data_storage['temp']
        for f in self.freq_list:
            cp = [
                v if v > 0 else np.nan
                for v in self.data_storage[f]['cp']
            ]
            g = [
                v if v > 0 else np.nan
                for v in self.data_storage[f]['g']
            ]
            self.lines_cp[f].set_data(temps, cp)
            self.lines_g[f].set_data(temps, g)

        self.ax_cp.relim()
        self.ax_cp.autoscale_view()
        self.ax_g.relim()
        self.ax_g.autoscale_view()
        self.canvas.draw_idle()

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno(
                "Exit", "Measurement is running. Stop and exit?"
            ):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    Passive_Dielectric_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()