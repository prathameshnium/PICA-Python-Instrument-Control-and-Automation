"""
Module: RT_K2400_K2182_L340_T_Control_GUI.py
Purpose: R vs T sweep (Keithley 2400 source, 2182 nanovoltmeter) with the
         temperature ramp driven by a Lake Shore Model 340 (heater range 5).
         Port of RT_K2400_K2182_T_Control_GUI.py to the Model 340 command set.

What is different from the Model 350 version, and why
-----------------------------------------------------
  * No *RST at connect.  On a 340 "*RST sets controller parameters to
    power-up settings" (340 manual, printed 9-24): it disables the control
    loop and resets setpoint and ramp.  Only *CLS is sent.
  * Control Loop 1 is enabled explicitly with "CSET 1,<input>,1,1" and
    verified with "CSET? 1" (printed 9-31).  A 340 loop is DISABLED from
    the factory and the heater stays off until CSET turns it on.  The loop
    is put in Manual PID mode with "CMODE 1,1" and verified (printed 9-29).
  * "CLIMIT? 1" is read at start (printed 9-28): setpoint limit, slope
    limits, max current code and max heater range are logged.  A start or
    end temperature above the setpoint limit is refused, and so is the
    module's fixed heater range 5 when the CLIMIT max range is lower.  The
    350's HTRSET (25 ohm / 1 A) does not exist on a 340 and is not sent.
  * RANGE takes no output number: "RANGE 5" / "RANGE 0", read back with
    "RANGE?" and verified after every write (printed 9-40 / 9-41).  The 350
    form "RANGE 1,5" is a syntax error on a 340.
  * HTR? takes no argument and reports Loop 1 in percent (printed 9-33).
  * The ramp is pinned: "RAMP 1,0,0"; "SETP 1,<T now>"; "RAMP 1,1,<rate>";
    "SETP 1,<target>".  A 340 ramps from the CURRENT SETPOINT, not from the
    temperature.  Heater range 5 is set BEFORE the ramp is enabled.  The
    ramp rate is validated to 0.1-100 K/min; the sign of the rate entry is
    still the sweep direction (as in the 350 version), only its magnitude
    is sent to the 340.
  * HTRST? (heater error, 0 = ok, 5 = open load, 6 = load < 10 ohm) is read
    with every sample and logged once whenever it changes: beep + console,
    no dialog.
  * RDGST? <input> is read with every sample (printed 9-41).  A non-zero
    status (invalid, old, under/over range, units zero/overrange) is logged
    with the reading and such a sample never drives the stabilisation test
    or the end-of-ramp / cutoff test.
  * The control input is selectable (A or B on a base Model 340; C and D
    only with the 3462 option card).  The 350 version fixed it to A.
  * End of run, cutoff and runtime errors no longer open a modal dialog:
    a ramp can run unattended, so they log, beep and retitle the plot.
    Only a refused Start opens a dialog.
  * The scanner pre-selects the lab's 340 at IEEE address 19 ("::19::")
    instead of the old '12'/'15' hint; the *IDN? reply must contain MODEL340.

Commands used (all verified against the Model 340 User's Manual, Chapter 9):
  *IDN?, *CLS, CSET 1,<in>,1,1 / CSET? 1, CMODE 1,1 / CMODE? 1, CLIMIT? 1,
  RAMP 1,<on>,<rate> (0.1-100 K/min), SETP 1,<K>, RANGE <0-5> / RANGE?,
  HTR?, HTRST?, KRDG? <in>, RDGST? <in>
"""

# -------------------------------------------------------------------------------
# Name:         V-T Sweep Active GUI for K2400/2182 & LS340
# Purpose:      Provide a professional GUI for performing automated V vs T sweeps
#               with active temperature control (stabilize then ramp).
# Author:       Prathamesh Deshmukh
# Created:      05/10/2025
# Version:      2.2 (JOSS Cleaned)
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa, Keithley2400 = None, None
    PYMEASURE_AVAILABLE = False


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
        # Go up 2 levels: k2400_2182 -> keithley -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")

def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: k2400_2182 -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------
# RDGST? bit weights, Model 340 manual printed 9-41.
RDGST_BITS = (
    (1, "invalid reading"),
    (2, "old reading"),
    (16, "temp underrange"),
    (32, "temp overrange"),
    (64, "units zero"),
    (128, "units overrange"),
)


def describe_reading_status(status):
    """Turn an RDGST? integer into readable text ('' when the reading is good)."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return f"unparseable status '{status}'"
    if status == 0:
        return ""
    names = [name for bit, name in RDGST_BITS if status & bit]
    if not names:
        names = [f"unknown bit(s) {status}"]
    return ", ".join(names)


# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


class VT_Backend:
    """ Manages communication with the K2400, K2182, and Lakeshore 340. """

    MODEL_TOKENS = ("MODEL340", "MODEL 340")
    # HTRST? codes, 340 manual 11.9.
    HEATER_ERRORS = {
        0: "No error",
        1: "Power supply over voltage",
        2: "Power supply under voltage",
        3: "Output DAC error",
        4: "Current limit DAC error",
        5: "OPEN HEATER LOAD",
        6: "Heater load < 10 ohm",
    }
    # CLIMIT <max current> codes, printed 9-28.
    MAX_CURRENT_CODES = {1: "0.25 A", 2: "0.5 A", 3: "1.0 A", 4: "2.0 A",
                         5: "User (CLIMI)"}
    # Heater range names kept from the 350 version, mapped to RANGE <n>.
    RANGE_MAP = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
    RAMP_RANGE = 5  # the module's fixed heater range for the ramp

    def __init__(self):
        self.k2400 = None
        self.k2182 = None
        self.lakeshore = None
        self.lakeshore_idn = ""
        self.control_input = 'A'
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            self.rm = None

    # --- Lake Shore 340 low-level helpers ---

    def _ls_write(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Lakeshore 340 is not connected.")
        self.lakeshore.write(cmd)

    def _ls_query(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Lakeshore 340 is not connected.")
        return self.lakeshore.query(cmd).strip()

    def is_model_340(self):
        idn = self.lakeshore_idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def connect(self, k2400_visa, k2182_visa, ls_visa, control_input='A'):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure is not available.")

        self.k2400 = Keithley2400(k2400_visa)
        print(f"  K2400 Connected: {self.k2400.id}")

        self.k2182 = self.rm.open_resource(k2182_visa)
        print(f"  K2182 Connected: {self.k2182.query('*IDN?').strip()}")

        self.control_input = str(control_input).strip().upper() or 'A'
        self.lakeshore = self.rm.open_resource(ls_visa)
        self.lakeshore.timeout = 10000
        # The 340 answers with <CR><LF> and EOI by default (IEEE command,
        # printed 9-33); '\n' as read terminator works on the lab's 340.
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        self.lakeshore_idn = self._ls_query('*IDN?')
        print(f"  Lakeshore Connected: {self.lakeshore_idn}")
        if not self.is_model_340():
            raise RuntimeError(
                f"'{ls_visa}' answered '{self.lakeshore_idn}', which is not a "
                "Lake Shore Model 340. Refusing to send 340-only commands "
                "(CSET, RANGE n) to it. Pick the right address.")

    def configure_instruments(self, current_ma, compliance_v):
        """Lakeshore: *CLS, Loop 1 enabled and verified, ramp off, CLIMIT?
        read.  Keithleys: unchanged.  Returns the CLIMIT? dict."""
        # No *RST: on a 340 it resets loop, setpoint and ramp (printed 9-24).
        self._ls_write('*CLS')
        time.sleep(0.2)
        limits = self.prepare_loop(self.control_input)

        # Keithley 2400/2182 setup
        self.k2400.reset()
        self.k2400.apply_current()
        self.k2400.source_current_range = abs(current_ma * 1e-3) * 1.05
        self.k2400.compliance_voltage = compliance_v
        self.k2400.source_current = current_ma * 1e-3
        self.k2400.enable_source()
        self.k2182.write("*rst; status:preset; *cls")
        time.sleep(1)
        return limits

    def prepare_loop(self, control_input):
        """CSET 1,<input>,1,1 (verified); CMODE 1,1 (verified); ramp off;
        returns CLIMIT? 1 as a dict."""
        control_input = str(control_input).strip().upper()
        self.control_input = control_input
        self._ls_write(f'CSET 1,{control_input},1,1')
        self._ls_write('CMODE 1,1')
        time.sleep(0.2)
        cset = self.get_control_loop()
        if cset['input'].upper() != control_input or not cset['enabled']:
            raise RuntimeError(
                f"CSET 1,{control_input},1,1 did not stick: CSET? 1 reads "
                f"{cset}. Check the front panel (Remote/Local) and retry.")
        cmode = int(float(self._ls_query('CMODE? 1')))
        if cmode != 1:
            raise RuntimeError(f"CMODE 1,1 did not stick: CMODE? 1 = {cmode}.")
        # Ramp off so the stabilisation setpoint takes effect at once; a
        # leftover ramp would make the 340 crawl from its old setpoint.
        self._ls_write('RAMP 1,0,0')
        return self.get_control_limits()

    def get_control_loop(self):
        parts = [p.strip() for p in self._ls_query('CSET? 1').split(',')]
        if len(parts) < 4:
            raise ValueError(f"unexpected CSET? reply '{','.join(parts)}'")
        return {'input': parts[0], 'units': int(float(parts[1])),
                'enabled': int(float(parts[2])), 'powerup': int(float(parts[3]))}

    def get_control_limits(self):
        parts = [p.strip() for p in self._ls_query('CLIMIT? 1').split(',')]
        if len(parts) < 5:
            raise ValueError(f"unexpected CLIMIT? reply '{','.join(parts)}'")
        return {'sp_limit': float(parts[0]), 'pos_slope': float(parts[1]),
                'neg_slope': float(parts[2]), 'max_current': int(float(parts[3])),
                'max_range': int(float(parts[4]))}

    def get_temperature(self):
        if not self.lakeshore:
            return 0.0
        return float(self._ls_query(f'KRDG? {self.control_input}'))

    def get_reading_status(self):
        """RDGST? <input> -> bit-weighted status (0 = good)."""
        return int(float(self._ls_query(f'RDGST? {self.control_input}')))

    def set_heater_range(self, heater_range):
        """RANGE <0-5>: Loop 1 only, no output number on a 340. Verified.

        Accepts the 350-era names (off/low/medium/high -> 0/2/4/5) or a
        numeric code 0-5.
        """
        if isinstance(heater_range, str) and not heater_range.strip().isdigit():
            range_code = self.RANGE_MAP.get(heater_range.strip().lower())
            if range_code is None:
                raise ValueError("Invalid heater range.")
        else:
            range_code = int(heater_range)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5, got {range_code}")
        self._ls_write(f'RANGE {range_code}')
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != range_code:
            raise RuntimeError(
                f"RANGE {range_code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        """RANGE? -> 0 (off) .. 5.  No output argument on a 340."""
        return int(float(self._ls_query('RANGE?')))

    def set_setpoint(self, temperature_k):
        self._ls_write(f'SETP 1,{temperature_k}')

    def start_ramp(self, end_temp, rate_k_min, current_temperature):
        """Range 5, then setpoint = now (ramp off), then ramp on and target.

        The 340 ramps from the current setpoint; pinning it to the present
        temperature first makes the ramp start from where the sample is.
        Only the magnitude of the rate is sent (0.1-100 K/min).
        """
        rate = abs(float(rate_k_min))
        if not (0.1 <= rate <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, "
                f"got {rate_k_min}")
        self.set_heater_range(self.RAMP_RANGE)  # Heater High for ramp
        self._ls_write('RAMP 1,0,0')
        self._ls_write(f'SETP 1,{current_temperature:.3f}')
        time.sleep(0.2)
        self._ls_write(f'RAMP 1,1,{rate}')
        self._ls_write(f'SETP 1,{end_temp}')

    def stop_ramp(self):
        """RAMP off and heater off (RANGE 0). Loop stays enabled."""
        self._ls_write('RAMP 1,0,0')
        self._ls_write('RANGE 0')

    def get_heater_output(self):
        """HTR? -> Loop 1 heater output in percent.  No argument on a 340."""
        return float(self._ls_query('HTR?'))

    def get_heater_status(self):
        """HTRST? -> (code, text); 0 = no error."""
        code = int(float(self._ls_query('HTRST?')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    def get_measurement(self):
        """-> (temperature K, voltage V, RDGST? code)."""
        # K2182 measurement sequence
        self.k2182.write("status:measurement:enable 512; *sre 1")
        self.k2182.write("sample:count 2")
        self.k2182.write("trigger:source bus")
        self.k2182.write("trigger:delay 0.1")
        self.k2182.write("trace:points 2")
        self.k2182.write("trace:feed sense1; feed:control next")
        self.k2182.write("initiate")
        self.k2182.assert_trigger()
        self.k2182.wait_for_srq(timeout=10)
        voltages = self.k2182.query_ascii_values("trace:data?")
        self.k2182.query("status:measurement?")
        self.k2182.write("trace:clear; feed:control next")
        voltage = sum(voltages) / len(voltages) if voltages else float('nan')

        # Lakeshore temperature reading (+ reading status)
        temperature = float(self._ls_query(f'KRDG? {self.control_input}'))
        rd_status = self.get_reading_status()
        return temperature, voltage, rd_status

    def shutdown(self):
        if self.k2400:
            try:
                self.k2400.shutdown()
            except Exception:
                pass
        if self.k2182:
            try:
                self.k2182.write("*rst")
                self.k2182.close()
            except Exception:
                pass
        if self.lakeshore:
            try:
                self.stop_ramp()
            except Exception:
                pass
            try:
                self.lakeshore.close()
            except Exception:
                pass
            self.lakeshore = None
        print("  Instruments shut down and disconnected.")


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class VT_GUI_Active:
    PROGRAM_VERSION = "2.2"
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"K2400/2182 & L340: R-T Sweep (T-Control) v{self.PROGRAM_VERSION}")
        self.root.geometry("1650x950")
        self.root.minsize(1400, 800)
        self.root.configure(bg=self.CLR_BG_DARK)
        self.experiment_state = 'idle'
        self.last_htr_error = 0
        self.logo_image = None
        self.backend = VT_Backend()
        self.data_storage = {'temperature': [], 'voltage': []}
        # Plot updates are decoupled from data acquisition: data callbacks
        # only set this flag; _refresh_plot redraws on a fixed cadence.
        self._plot_dirty = False
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG_LIGHT,
            insertcolor=self.CLR_FG_LIGHT)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER)
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_BG_DARK), ('hover', self.CLR_BG_DARK)])
        style.configure(
            'Start.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure(
            'Browse.TButton',
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_ACCENT_BLUE)
        style.map(
            'Browse.TButton', background=[
                ('active', '#7C899E'), ('hover', '#7C899E')])
        style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_ACCENT_BLUE)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG_LIGHT,
            arrowcolor=self.CLR_FG_LIGHT,
            selectbackground=self.CLR_ACCENT_BLUE,
            selectforeground=self.CLR_FG_LIGHT)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11,
                             'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        ttk.Label(
            header,
            text="K2400/2182 & L340: R-T Sweep (T-Control)",
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left',
            padx=20,
            pady=10)
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        left_panel = ttk.Frame(canvas, padding=10)
        left_panel.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=left_panel, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = left_panel

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = self._create_right_panel(self.main_pane)
        self.main_pane.add(right_panel, weight=1)
        self._populate_left_panel(left_panel)

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

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, 0)
        self._create_params_panel(panel, 1)
        self._create_control_panel(panel, 2)
        self._create_console_panel(panel, 3)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 110
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir,
                "..",
                "..",
                "assets",
                "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font, background=self.CLR_FRAME_BG).grid(
                      row=0, column=1, padx=10, pady=(15, 0), sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=1,
            column=1,
            padx=10,
            pady=(
                0,
                5),
            sticky='nw')
        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)
        details_text = ("Program Name: R vs. T (T-Control) - [range 5]\n"
                        "Instruments: K2400, K2182, L340\n"
                        "Measurement Range: 10⁻⁶ Ω to 10⁹ Ω\n"
                        "Lakeshore inputs: A, B (C, D with 3462 option card)")
        ttk.Label(
            frame,
            text=details_text,
            justify='left',
            background=self.CLR_FRAME_BG).grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(panel, text='Live R-T Curve')
        container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_yscale('log')
        self.ax_main.set_title("Waiting for experiment...", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Voltage (V)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent)
        container.grid(row=grid_row, column=0, sticky='new', pady=5)
        # Sections stack vertically (like the k6517b/keysight panels) so the
        # left panel stays narrow and the graph keeps the space.
        container.grid_columnconfigure(0, weight=1)
        self.entries = {}
        temp_frame = ttk.LabelFrame(container, text='Temperature')
        temp_frame.grid(row=0, column=0, sticky='nsew')
        temp_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(temp_frame, "Start Temp (K)", "300", 0)
        self._create_entry(temp_frame, "End Temp (K)", "310", 1)
        self._create_entry(temp_frame, "Ramp Rate (K/min)", "2", 2)
        self._create_entry(temp_frame, "Safety Cutoff (K)", "320", 3)
        self.ls_cb = self._create_combobox(temp_frame, "Lakeshore VISA", 4)
        # Lake Shore 340 control input (A/B; C/D with the 3462 card).
        ttk.Label(temp_frame, text="Lakeshore Input:").grid(
            row=5, column=0, sticky='w', padx=10, pady=3)
        self.input_var = tk.StringVar(master=self.root, value='A')
        self.input_cb = ttk.Combobox(
            temp_frame, textvariable=self.input_var, font=self.FONT_BASE,
            values=['A', 'B', 'C', 'D'], state='readonly', width=4)
        self.input_cb.grid(row=5, column=1, sticky='w', padx=10, pady=3)

        iv_frame = ttk.LabelFrame(container, text='Measurement Settings')
        iv_frame.grid(row=1, column=0, sticky='nsew', pady=(5, 0))
        iv_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(iv_frame, "Source Current (mA)", "1", 0)
        self._create_entry(iv_frame, "Compliance (V)", "10", 1)
        self._create_entry(iv_frame, "Logging Delay (s)", "1", 2)
        self.k2400_cb = self._create_combobox(
            iv_frame, "Keithley 2400 VISA", 3)
        self.k2182_cb = self._create_combobox(
            iv_frame, "Keithley 2182 VISA", 4)

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Experiment Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_VT_Active", 0)
        self._create_entry(frame, "Save Location", "", 1, browse=True)
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=4, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(
            button_frame,
            text="Start",
            style='Start.TButton',
            command=self.start_experiment)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            style='Stop.TButton',
            state='disabled',
            command=self.stop_experiment)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            button_frame,
            text="Scan",
            command=self._scan_for_visa).grid(
            row=0,
            column=2,
            sticky='ew',
            padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    def _beep(self):
        try:
            self.root.bell()
        except Exception:
            pass

    def start_experiment(self):
        try:
            self.params = self._validate_and_get_params()
            self.log("Connecting to instruments...")
            self.backend.connect(
                self.params['k2400_visa'],
                self.params['k2182_visa'],
                self.params['ls_visa'],
                self.params['input'])
            self.log(f"Lakeshore: {self.backend.lakeshore_idn}")
            limits = self.backend.configure_instruments(
                self.params['current_ma'], self.params['compliance_v'])
            cur = self.backend.MAX_CURRENT_CODES.get(
                limits['max_current'], f"code {limits['max_current']}")
            self.log(f"Loop 1 enabled on input {self.backend.control_input} "
                     f"(kelvin), Manual PID. CLIMIT: setpoint <= "
                     f"{limits['sp_limit']:g} K, max current {cur}, "
                     f"max range {limits['max_range']}.")
            highest = max(self.params['start_temp'], self.params['end_temp'])
            if highest > limits['sp_limit']:
                raise ValueError(
                    f"Target {highest:g} K is above the 340's setpoint limit "
                    f"of {limits['sp_limit']:g} K (CLIMIT). Raise the limit "
                    "in the Direct Control module first.")
            if self.backend.RAMP_RANGE > limits['max_range']:
                raise ValueError(
                    f"This module ramps on heater range {self.backend.RAMP_RANGE}, "
                    f"but the 340's max range is {limits['max_range']} "
                    "(CLIMIT). Raise the limit first.")
            code, text = self.backend.get_heater_status()
            if code != 0:
                raise RuntimeError(f"Heater error HTRST? {code:02d}: {text}. "
                                   "Fix the heater circuit before ramping.")
            self.last_htr_error = 0
            self.log("All instruments connected and configured.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.params['name']}_{ts}_VT_Active.csv"
            self.data_filepath = os.path.join(
                self.params['save_path'], filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Temperature (K)", "Voltage (V)", "Elapsed Time (s)"])

            self.set_ui_state(running=True)
            self.experiment_state = 'stabilizing'
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {self.params['name']}")
            self.ax_main.set_yscale('log')
            self._plot_dirty = False
            self.canvas.draw_idle()

            self.log(
                f"Starting stabilization at {self.params['start_temp']} K...")
            self.root.after(100, self._experiment_loop)
            self.root.after(250, self._refresh_plot)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Start Failed", f"{e}")
            self.backend.shutdown()

    def stop_experiment(self, reason=""):
        if self.experiment_state == 'idle':
            return
        self.log(
            f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.experiment_state = 'idle'
        self.backend.shutdown()
        self.set_ui_state(running=False)
        # Final flush: state is already 'idle', so this won't reschedule.
        self._refresh_plot()
        self.ax_main.set_title(
            f"Experiment stopped. {reason}" if reason else "Experiment stopped.")
        self.canvas.draw_idle()
        if reason:
            # Unattended-safe: no dialog after a run. Console + title + beep.
            self.log(f"Experiment finished: {reason} Heater is OFF.")
            self._beep()

    def _check_heater_status(self):
        """HTRST? each poll; log once on change, beep on error, no dialog."""
        code, text = self.backend.get_heater_status()
        if code != self.last_htr_error:
            self.last_htr_error = code
            if code != 0:
                self.log(f"HEATER ERROR HTRST? {code:02d}: {text}")
                self._beep()
            else:
                self.log("Heater error cleared (HTRST? 00).")

    def _stabilization_loop(self):
        if self.experiment_state != 'stabilizing':
            return
        try:
            current_temp = self.backend.get_temperature()
            rd_text = describe_reading_status(self.backend.get_reading_status())
            start_temp = self.params['start_temp']
            self._check_heater_status()
            if rd_text:
                # Not a valid sample: do not act on it, poll again.
                self.log(f"RDGST {self.backend.control_input}: {rd_text} "
                         f"(reading {current_temp:.4f} K ignored)")
                self.root.after(2000, self._stabilization_loop)
                return

            if current_temp > start_temp + 0.2:
                self.log(
                    f"Cooling... Current: {current_temp:.4f} K > Target: {start_temp} K")
                self.backend.set_heater_range('off')
            else:
                self.log(
                    f"Heating... Current: {current_temp:.4f} K <= Target: {start_temp} K")
                self.backend.set_heater_range('high')
                self.backend.set_setpoint(start_temp)

            if abs(current_temp - start_temp) < 5:
                self.log(
                    f"Stabilized at {current_temp:.4f} K. Waiting 5s before starting ramp...")
                self.experiment_state = 'ramping_setup'
                # Transition to next state
                self.root.after(5000, self._experiment_loop)
            else:
                # Continue stabilizing
                self.root.after(2000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during stabilization: {e}")
            self.stop_experiment("Stabilization Error")

    def _experiment_loop(self):
        if self.experiment_state == 'idle':
            return
        try:
            if self.experiment_state == 'stabilizing':
                self._stabilization_loop()
                return

            elif self.experiment_state == 'ramping_setup':
                temp_now = self.backend.get_temperature()
                self.backend.start_ramp(
                    self.params['end_temp'], self.params['rate'], temp_now)
                self.log(f"RANGE {self.backend.RAMP_RANGE} set; setpoint pinned "
                         f"to {temp_now:.3f} K; ramp started towards "
                         f"{self.params['end_temp']} K at "
                         f"{abs(self.params['rate'])} K/min.")
                self.experiment_state = 'ramping'
                self.start_time = time.time()
                self.root.after(100, self._experiment_loop)
                return

            elif self.experiment_state == 'ramping':
                temp, voltage, rd_status = self.backend.get_measurement()
                rd_text = describe_reading_status(rd_status)
                elapsed = time.time() - self.start_time
                resistance = voltage / \
                    (self.params['current_ma'] * 1e-3) if self.params['current_ma'] != 0 else float('inf')
                line = f"T: {temp:.3f} K | R: {resistance:.4e} Ω"
                if rd_text:
                    line += f" | RDGST {self.backend.control_input}: {rd_text}"
                self.log(line)
                self._check_heater_status()

                self.data_storage['temperature'].append(temp)
                self.data_storage['voltage'].append(voltage)
                with open(self.data_filepath, 'a', newline='') as f:
                    csv.writer(f).writerow(
                        [f"{temp:.4f}", f"{voltage:.6e}", f"{elapsed:.2f}"])

                self._plot_dirty = True

                # Check end conditions (only on a valid reading)
                if rd_text:
                    self.root.after(
                        int(self.params['delay_s'] * 1000), self._experiment_loop)
                elif temp >= self.params['cutoff']:
                    self.stop_experiment(
                        f"Safety cutoff reached at {temp:.2f} K.")
                elif (self.params['rate'] > 0 and temp >= self.params['end_temp']) or \
                     (self.params['rate'] < 0 and temp <= self.params['end_temp']):
                    self.stop_experiment("End temperature reached.")
                else:
                    self.root.after(
                        int(self.params['delay_s'] * 1000), self._experiment_loop)

        except Exception as e:
            # Unattended-safe: no dialog. Heater off, console, beep.
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            self._beep()
            self.stop_experiment(f"Runtime Error: {e}")

    def _refresh_plot(self):
        """Redraws the plot at a fixed cadence, independent of data rate.

        A normal (non-blitted) draw is used so that the axes — ticks,
        limits, gridlines and scale — always stay in sync with the data.
        """
        if self._plot_dirty:
            self._plot_dirty = False

            temps = self.data_storage['temperature']
            volts = self.data_storage['voltage']

            self.line_main.set_data(temps, volts)

            # Recompute and apply limits.
            self._autoscale_axis(self.ax_main, x=temps, y=volts, log_y=True)

            # Full redraw keeps ticks/labels/gridlines correct and is
            # resize-proof. draw_idle() coalesces redraws efficiently.
            self.canvas.draw_idle()

        if self.experiment_state != 'idle':
            self.root.after(250, self._refresh_plot)

    def _autoscale_axis(self, ax, x, y, log_y=False, margin=0.05):
        """Rescale an axis, ignoring non-finite and (for log) non-positive
        values so the axis never collapses or freezes."""
        import math

        xs = [v for v in x if v is not None and math.isfinite(v)]
        if log_y:
            ys = [v for v in y if v is not None and math.isfinite(v) and v > 0]
        else:
            ys = [v for v in y if v is not None and math.isfinite(v)]

        if not xs or not ys:
            return

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)

        # X padding (linear)
        xpad = (xmax - xmin) * margin or 0.5
        ax.set_xlim(xmin - xpad, xmax + xpad)

        # Y padding
        if log_y:
            # pad multiplicatively in log space
            ax.set_ylim(ymin / (1 + margin), ymax * (1 + margin))
        else:
            ypad = (ymax - ymin) * margin or abs(ymax) * margin or 1e-12
            ax.set_ylim(ymin - ypad, ymax + ypad)

    def _validate_and_get_params(self):
        try:
            params = {
                'name': self.entries["Sample Name"].get(),
                'save_path': self.entries["Save Location"].get(),
                'start_temp': float(self.entries["Start Temp (K)"].get()),
                'end_temp': float(self.entries["End Temp (K)"].get()),
                'rate': float(self.entries["Ramp Rate (K/min)"].get()),
                'cutoff': float(self.entries["Safety Cutoff (K)"].get()),
                'ls_visa': self.ls_cb.get(),
                'input': self.input_var.get(),
                'current_ma': float(self.entries["Source Current (mA)"].get()),
                'compliance_v': float(self.entries["Compliance (V)"].get()),
                'delay_s': float(self.entries["Logging Delay (s)"].get()),
                'k2400_visa': self.k2400_cb.get(),
                'k2182_visa': self.k2182_cb.get()
            }
            if not all([p for k, p in params.items()
                       if k not in ['rate', 'cutoff']]):
                raise ValueError("A required field is empty.")
            if params['rate'] == 0:
                raise ValueError(
                    "Ramp Rate cannot be zero for an active sweep.")
            if not (0.1 <= abs(params['rate']) <= 100):
                raise ValueError(
                    "Ramp rate magnitude must be 0.1-100 K/min on a Model 340 "
                    "(negative = cooling sweep).")
            if params['rate'] > 0 and not (
                    params['start_temp'] < params['end_temp'] < params['cutoff']):
                raise ValueError(
                    "For heating, temperatures must be in order: start < end < cutoff.")
            if params['rate'] < 0 and not (
                    params['start_temp'] > params['end_temp'] > params['cutoff']):
                raise ValueError(
                    "For cooling, temperatures must be in order: start > end > cutoff.")
            return params
        except Exception as e:
            raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values():
            w.config(state=state)
        for cb in [self.ls_cb, self.k2400_cb, self.k2182_cb]:
            cb.config(state=state if state == 'normal' else 'readonly')
        self.input_cb.config(state='disabled' if running else 'readonly')
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}")
            self.ls_cb['values'] = resources
            self.k2400_cb['values'] = resources
            self.k2182_cb['values'] = resources
            for r in resources:
                # Lab 340 sits at IEEE address 19; a hint only, the IDN
                # check at Start decides.
                if LAKESHORE340_ADDRESS_HINT in r:
                    self.ls_cb.set(r)
                if '2400' in r or 'GPIB::4' in r:
                    self.k2400_cb.set(r)
                if '2182' in r or 'GPIB::7' in r:
                    self.k2182_cb.set(r)
        else:
            self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.entries["Save Location"].config(state='normal')
            self.entries["Save Location"].delete(0, 'end')
            self.entries["Save Location"].insert(0, path)
            self.entries["Save Location"].config(state='disabled')

    def _create_entry(
            self,
            parent,
            label_text,
            default_value,
            row,
            browse=False):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=30)
        entry.grid(
            row=row,
            column=1,
            sticky='ew',
            padx=10,
            pady=3,
            columnspan=2)
        entry.insert(0, default_value)
        self.entries[label_text] = entry
        if browse:
            btn = ttk.Button(parent, text="Browse...", style='Browse.TButton',
                             command=self._browse_file_location)
            btn.grid(row=row, column=3, sticky='e', padx=(0, 10))
            entry.config(state='disabled')

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        cb = ttk.Combobox(
            parent,
            font=self.FONT_BASE,
            state='readonly',
            style='TCombobox')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.experiment_state != 'idle' and messagebox.askyesno(
                "Exit", "Experiment is running. Stop and exit?"):
            self.stop_experiment("Application closed by user.")
            self.root.destroy()
        elif self.experiment_state == 'idle':
            self.root.destroy()


if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror(
            "Dependency Error",
            "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk()
        app = VT_GUI_Active(root)
        root.mainloop()