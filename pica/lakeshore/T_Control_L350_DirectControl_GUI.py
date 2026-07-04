"""
Module: T_Control_L350_DirectControl_GUI.py
Purpose: Direct command workbench GUI for Lake Shore Model 350 Temperature Controller.
         Sends individual SCPI commands for PID, setpoint, heater range,
         temperature limit, display, input configuration, manual output,
         and more.
         Non-destructive disconnect: instrument retains all settings after programme close.

SCPI commands verified against:
  - Lake Shore Model 350 User's Manual (Section 6: Remote Interface)
    https://www.lakeshore.com/docs/default-source/product-downloads/lstc_350_l.pdf
  - Lake Shore Forums (official Lake Shore support responses)
    https://forums.lakeshore.com/thread/319/model-setting-querying-control-loops
  - Lake Shore Model 336/335 family (shared command set with Model 350)
    https://www.lakeshore.com/docs/default-source/product-downloads/lstc_335_l.pdf

Instrument specs confirmed from manual:
  - 4 sensor inputs (A, B, C, D)
  - 4 outputs: 1 & 2 = heater (75W max on Output 1), 3 & 4 = analog
  - 5 heater ranges (0=Off, 1-5 increasing power)
  - PID: P=0-9999, I=0-1000, D=0-200
  - Setpoint ramping: 0.001-100 K/min
  - Display: 1 to 8 reading displays
  - Interfaces: Ethernet, USB, IEEE-488
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas
import os
import time
import traceback
from datetime import datetime

# --- Optional Packages ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

import runpy
from multiprocessing import Process


# ---------------------------------------------------------------------------
# UTILITY LAUNCHERS (identical to reference programme)
# ---------------------------------------------------------------------------

def run_script_process(script_path):
    """Wrapper to execute a script in its own directory via runpy."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in "
              f"{os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_plotter_utility():
    """Launch the plotter utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(
            script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Launch the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}")


# ---------------------------------------------------------------------------
# BACKEND: Lake Shore 350 Instrument Control
# ---------------------------------------------------------------------------

class Lakeshore350Backend:
    """
    Backend for Lake Shore Model 350 communication.

    All SCPI commands are verified against the Model 350 User's Manual
    (Section 6: Remote Interface) and Lake Shore official forum responses.

    Disconnect is non-destructive: it only closes the VISA resource.
    No *RST, no heater off, no PID/range/setpoint changes on disconnect.
    The instrument continues operating autonomously with its current settings.
    """

    # --- Sensor type codes for INTYPE command (Manual Section 6.6.2) ---
    SENSOR_TYPES = {
        0: "Disabled",
        1: "Silicon Diode",
        2: "Platinum RTD",
        3: "NTC RTD (Cernox/Rox)",
        4: "Thermocouple",
        5: "Capacitance",
    }

    # --- Display units codes for DFLD command ---
    DISPLAY_UNITS = {
        1: "Kelvin",
        2: "Celsius",
        3: "Sensor Units",
        4: "Minimum",
        5: "Maximum",
    }

    # --- Output mode codes for OUTMODE command (Manual Section 6.6.4) ---
    OUTPUT_MODES = {
        0: "Off",
        1: "Closed Loop PID",
        2: "Zone",
        3: "Open Loop",
        4: "Monitor Out",
        5: "Warmup",
    }

    def __init__(self):
        self.lakeshore = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            print("PyVISA not available.")

    # -- Connection management --

    def connect(self, visa_address):
        """
        Open VISA connection and query *IDN?.
        Returns the identification string.
        """
        if not self.rm:
            raise ConnectionError(
                "PyVISA ResourceManager not available. "
                "Install pyvisa and a VISA backend (NI-VISA or pyvisa-py).")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        idn = self.lakeshore.query('*IDN?').strip()
        return idn

    def disconnect(self):
        """
        Non-destructive disconnect.
        Only closes the VISA resource handle. Does NOT:
          - send *RST
          - turn off the heater
          - change PID, range, setpoint, or any setting
        The instrument retains all current settings and continues
        operating autonomously.
        """
        if self.lakeshore:
            try:
                self.lakeshore.close()
            except Exception as e:
                print(f"  Warning during disconnect: {e}")
            finally:
                self.lakeshore = None

    @property
    def is_connected(self):
        return self.lakeshore is not None

    # -- Low-level command helpers --

    def _write(self, command):
        """Send a SCPI write command. Raises if not connected."""
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        self.lakeshore.write(command)

    def _query(self, command):
        """Send a SCPI query and return stripped response."""
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        return self.lakeshore.query(command).strip()

    # -- PID control (Manual: PID command) --
    # PID <output>,<P>,<I>,<D>
    # P: 0 to 9999 (0.1 resolution)
    # I: 1 to 1000 (0.1 resolution)
    # D: 1 to 200 (1% resolution)

    def set_pid(self, output, p, i, d):
        """Set PID values for the given output (1 or 2)."""
        if not (0 <= p <= 9999):
            raise ValueError(
                f"P must be 0-9999, got {p}")
        if not (0 <= i <= 1000):
            raise ValueError(
                f"I must be 0-1000, got {i}")
        if not (0 <= d <= 200):
            raise ValueError(
                f"D must be 0-200, got {d}")
        cmd = f"PID {output},{p},{i},{d}"
        self._write(cmd)
        return cmd

    def get_pid(self, output):
        """Query current PID values. Returns (P, I, D) as floats."""
        resp = self._query(f"PID? {output}")
        parts = resp.split(',')
        return float(parts[0]), float(parts[1]), float(parts[2])

    # -- Setpoint (Manual: SETP command) --
    # SETP <output>,<value>

    def set_setpoint_immediate(self, output, value):
        """
        Set setpoint immediately (no ramp).
        First disables any active ramp via RAMP <output>,0,0,
        then sends SETP <output>,<value>.
        """
        # Disable ramp so setpoint takes effect immediately
        self._write(f"RAMP {output},0,0")
        cmd = f"SETP {output},{value}"
        self._write(cmd)
        return f"RAMP {output},0,0; {cmd}"

    def set_setpoint_with_ramp(self, output, value, rate_k_per_min):
        """
        Set setpoint with controlled ramp.
        Sends RAMP <output>,1,<rate> then SETP <output>,<value>.
        Rate: 0.001 to 100 K/min.
        """
        if not (0.001 <= rate_k_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be 0.001-100 K/min, got {rate_k_per_min}")
        self._write(f"RAMP {output},1,{rate_k_per_min}")
        cmd = f"SETP {output},{value}"
        self._write(cmd)
        return f"RAMP {output},1,{rate_k_per_min}; {cmd}"

    def get_setpoint(self, output):
        """Query current setpoint for the given output."""
        return float(self._query(f"SETP? {output}"))

    def get_ramp_status(self, output):
        """
        Query ramp status. Returns (on_off, rate).
        on_off: 0=off, 1=on
        rate: float K/min
        """
        resp = self._query(f"RAMP? {output}")
        parts = resp.split(',')
        return int(parts[0]), float(parts[1])

    # -- Heater range (Manual: RANGE command) --
    # RANGE <output>,<range>
    # For outputs 1 & 2: 0=Off, 1-5 (increasing power)
    # For outputs 3 & 4: different meaning (analog)

    def set_range(self, output, range_value):
        """Set heater range (0=Off, 1-5 for outputs 1&2)."""
        if not (0 <= range_value <= 5):
            raise ValueError(
                f"Range must be 0-5, got {range_value}")
        cmd = f"RANGE {output},{range_value}"
        self._write(cmd)
        return cmd

    def get_range(self, output):
        """Query current heater range."""
        return int(self._query(f"RANGE? {output}"))

    # -- Temperature limit (Manual: TLIMIT command) --
    # TLIMIT <input>,<limit>
    # input: A, B, C, or D
    # limit: 0 to 2999 K; 0 = feature disabled (default)
    # Safety: if the input reading exceeds the limit, ALL control
    # outputs are shut down.

    def set_temp_limit(self, channel, limit):
        """Set over-temperature limit in K for input A-D (0 disables)."""
        if not (0 <= limit <= 2999):
            raise ValueError(
                f"Temperature limit must be 0-2999 K, got {limit}")
        cmd = f"TLIMIT {channel},{limit}"
        self._write(cmd)
        return cmd

    def get_temp_limit(self, channel):
        """Query over-temperature limit for input A-D. Returns float K."""
        return float(self._query(f"TLIMIT? {channel}"))

    # -- Temperature readings (Manual: KRDG? / SRDG? commands) --
    # KRDG? <channel>  -> temperature in Kelvin
    # SRDG? <channel>  -> raw sensor reading

    def get_temperature(self, channel):
        """Query temperature in Kelvin for channel A, B, C, or D."""
        return float(self._query(f"KRDG? {channel}"))

    def get_sensor_reading(self, channel):
        """Query raw sensor reading for channel A, B, C, or D."""
        return float(self._query(f"SRDG? {channel}"))

    # -- Heater output (Manual: HTR? command) --
    # HTR? <output>  -> heater output percentage

    def get_heater_output(self, output):
        """Query heater output percentage for output 1 or 2."""
        return float(self._query(f"HTR? {output}"))

    # -- Display configuration (Manual: DISPLAY / DFLD commands) --
    # DISPLAY <mode>,<numfields>,<relays>
    #   mode: 0=all inputs, 1=custom
    #   numfields: 1-8
    #   relays: 0=off, 1=on
    # DFLD <field>,<input>,<units>
    #   field: 1-8
    #   input: A-D
    #   units: 1=K, 2=C, 3=sensor, 4=min, 5=max

    def set_display_single(self, channel, units=1):
        """
        Configure display to show a single input.
        channel: 'A', 'B', 'C', or 'D'
        units: 1=K, 2=C, 3=sensor units
        """
        self._write(f"DISPLAY 1,1,0")
        self._write(f"DFLD 1,{channel},{units}")
        return f"DISPLAY 1,1,0; DFLD 1,{channel},{units}"

    def set_display_dual(self, ch1, ch2, units1=1, units2=1):
        """
        Configure display to show two inputs side by side.
        ch1, ch2: 'A'-'D'
        units1, units2: 1=K, 2=C, 3=sensor units
        """
        self._write(f"DISPLAY 1,2,0")
        self._write(f"DFLD 1,{ch1},{units1}")
        self._write(f"DFLD 2,{ch2},{units2}")
        return (f"DISPLAY 1,2,0; "
                f"DFLD 1,{ch1},{units1}; "
                f"DFLD 2,{ch2},{units2}")

    def get_display(self):
        """Query current display configuration. Returns raw string."""
        return self._query("DISPLAY?")

    # -- Input configuration (Manual: INTYPE command) --
    # INTYPE <input>,<sensor type>,<autorange>,<range>,
    #         <compensation>,<units>,<excitation>
    # sensor type: 0=disabled, 1=Si diode, 2=PT RTD,
    #              3=NTC RTD, 4=thermocouple, 5=capacitance
    # autorange: 0=off, 1=on
    # compensation: 0=off, 1=on (current reversal)
    # units: 1=K, 2=C, 3=sensor units
    # excitation/range: sensor-dependent

    def set_input_type(self, channel, sensor_type, autorange=0,
                       range_val=0, compensation=0, units=1,
                       excitation=0):
        """
        Configure input sensor type and parameters.
        channel: 'A'-'D'
        sensor_type: 0=Disabled, 1=Si Diode, 2=PT RTD,
                     3=NTC RTD, 4=Thermocouple, 5=Capacitance
        """
        cmd = (f"INTYPE {channel},{sensor_type},{autorange},"
               f"{range_val},{compensation},{units},{excitation}")
        self._write(cmd)
        return cmd

    def get_input_type(self, channel):
        """Query input configuration. Returns raw response string."""
        return self._query(f"INTYPE? {channel}")

    # -- Output mode (Manual: OUTMODE command) --
    # OUTMODE <output>,<mode>,<input>,<powerup>,<polarity>,
    #         <filter>,<delay>
    # mode: 0=Off, 1=Closed Loop PID, 2=Zone, 3=Open Loop,
    #       4=Monitor Out, 5=Warmup

    def set_output_mode(self, output, mode, input_channel='A',
                        powerup=0, polarity=0, filter_on=0, delay=0):
        """Configure output control mode."""
        cmd = (f"OUTMODE {output},{mode},{input_channel},"
               f"{powerup},{polarity},{filter_on},{delay}")
        self._write(cmd)
        return cmd

    def get_output_mode(self, output):
        """Query output mode configuration."""
        return self._query(f"OUTMODE? {output}")

    # -- Zone tuning table (Manual: ZONE command) --
    # ZONE <output>,<zone>,<upper bound>,<P>,<I>,<D>,
    #      <mout>,<range>,<input>,<rate>
    # zone: 1-10, upper bound in setpoint units (K),
    # input: 0=default,1=A,2=B,3=C,4=D, rate in K/min (0=off)

    def set_zone(self, output, zone, upper_bound, p, i, d,
                 mout=0, range_val=0, input_num=0, rate=0):
        """Define one entry in the instrument zone table."""
        cmd = (f"ZONE {output},{zone},{upper_bound},{p},{i},{d},"
               f"{mout},{range_val},{input_num},{rate}")
        self._write(cmd)
        return cmd

    def get_zone(self, output, zone):
        """Query a single zone table entry."""
        return self._query(f"ZONE? {output},{zone}")

    # -- Manual output (Manual: MOUT command) --
    # MOUT <output>,<value>
    # value: 0 to 100 (% output)

    def set_manual_output(self, output, value):
        """Set manual output percentage (0-100%)."""
        if not (0 <= value <= 100):
            raise ValueError(
                f"Manual output must be 0-100%, got {value}")
        cmd = f"MOUT {output},{value}"
        self._write(cmd)
        return cmd

    def get_manual_output(self, output):
        """Query current manual output percentage."""
        return float(self._query(f"MOUT? {output}"))

    # -- Input filter (Manual: FILTER command) --
    # FILTER <input>,<on/off>,<points>,<threshold>
    # points: 2-64
    # threshold: 1-10 (% of reading)

    def set_filter(self, channel, on_off, points=4, threshold=2):
        """Configure input digital filter."""
        if not (2 <= points <= 64):
            raise ValueError(
                f"Filter points must be 2-64, got {points}")
        if not (1 <= threshold <= 10):
            raise ValueError(
                f"Filter threshold must be 1-10%, got {threshold}")
        cmd = f"FILTER {channel},{on_off},{points},{threshold}"
        self._write(cmd)
        return cmd

    def get_filter(self, channel):
        """Query input filter configuration."""
        return self._query(f"FILTER? {channel}")

    # -- Remote/Local interface mode (Manual: MODE command) --
    # MODE <mode>
    # 0=local, 1=remote, 2=remote+local lockout

    def set_interface_mode(self, mode):
        """Set interface mode: 0=local, 1=remote, 2=remote+lockout."""
        if mode not in (0, 1, 2):
            raise ValueError(f"Mode must be 0, 1, or 2, got {mode}")
        cmd = f"MODE {mode}"
        self._write(cmd)
        return cmd

    def get_interface_mode(self):
        """Query current interface mode."""
        return self._query("MODE?")

    # -- Input curve (Manual: INCRV command) --
    # INCRV <input>,<curve number>
    # curve: 0=none, 1-20=standard, 21-59=user

    def set_input_curve(self, channel, curve):
        """Set calibration curve for an input."""
        cmd = f"INCRV {channel},{curve}"
        self._write(cmd)
        return cmd

    def get_input_curve(self, channel):
        """Query calibration curve number for an input."""
        return int(self._query(f"INCRV? {channel}"))

    # -- Input name (Manual: INNAME command) --
    # INNAME <input>,<name>

    def set_input_name(self, channel, name):
        """Set a custom name for an input channel."""
        cmd = f"INNAME {channel},\"{name}\""
        self._write(cmd)
        return cmd

    def get_input_name(self, channel):
        """Query the name of an input channel."""
        return self._query(f"INNAME? {channel}")

    # -- Heater setup (Manual: HTRSET command) --
    # HTRSET <output>,<heater type>,<resistance>,<max current>,
    #        <max user current>,<display>
    # heater type: 0=current, 1=voltage
    # resistance: 1-10000 ohms
    # display: 1=current, 2=power

    def get_heater_setup(self, output):
        """Query heater setup configuration."""
        return self._query(f"HTRSET? {output}")

    # -- Common SCPI commands --

    def clear_status(self):
        """Send *CLS to clear status registers."""
        self._write("*CLS")

    def reset(self):
        """Send *RST for factory reset. DANGEROUS - use with caution."""
        self._write("*RST")

    def get_idn(self):
        """Query *IDN? for instrument identification."""
        return self._query("*IDN?")

    # -- Scan for VISA resources --

    def scan_resources(self):
        """List available VISA resources."""
        if not self.rm:
            return []
        return self.rm.list_resources()


# ---------------------------------------------------------------------------
# FRONTEND: Direct Control GUI
# ---------------------------------------------------------------------------

class DirectControlGUI:
    """
    GUI for the Lake Shore 350 Direct Control Utility.

    Each control panel sends SCPI commands independently.
    Disconnect is non-destructive - instrument settings persist.
    """

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Lakeshore 350 Direct Control Utility"

    # Color scheme (identical to reference programme)
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_STATUS_OK = '#6B8E4E'
    CLR_STATUS_BAD = '#BA6B5E'

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 12, 'bold')

    LEFT_PANEL_WIDTH = 520  # default sash position so the left panel starts fully visible

    # PID presets
    PID_PRESETS = {
        'Slow (P=0.5, I=4, D=0)': (0.5, 4.0, 0),
        'Medium (P=20, I=15, D=0)': (20.0, 15.0, 0),
        'Fast (P=50, I=20, D=0)': (50.0, 20.0, 0),
    }

    # Simple-name -> (P, I, D) map used by the temperature-zone panel.
    # Kept in sync with PID_PRESETS.
    ZONE_PID_MODES = {
        'Slow': (0.5, 4.0, 0),
        'Medium': (20.0, 15.0, 0),
        'Fast': (50.0, 20.0, 0),
    }

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x950")
        self.root.minsize(1200, 750)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = Lakeshore350Backend()
        self.logo_image = None
        self.is_connected = False
        self.polling_active = False

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # -----------------------------------------------------------------------
    # STYLES
    # -----------------------------------------------------------------------

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
            'TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[('active', self.CLR_TEXT_DARK),
                        ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Connect.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Connect.TButton',
            background=[('active', '#8AB845'),
                        ('hover', '#8AB845')])
        style.configure(
            'Disconnect.TButton',
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Disconnect.TButton',
            background=[('active', '#D63C2A'),
                        ('hover', '#D63C2A')])
        style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor='#BA6B5E')
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            insertcolor=self.CLR_TEXT_DARK)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK)

    # -----------------------------------------------------------------------
    # WIDGET CREATION
    # -----------------------------------------------------------------------

    def create_widgets(self):
        # --- Header ---
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = (
            'Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(
            header,
            text=self.PROGRAM_NAME,
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)

        plotter_button = ttk.Button(
            header,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)
        gpib_button = ttk.Button(
            header,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        # --- Main paned window ---
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # FIX (2b): pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)

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

    # -- Left panel (controls) --

    def _populate_left_panel(self, panel):
        """Create a scrollable left panel containing all control sections."""
        # Scrollable canvas setup
        canvas = tk.Canvas(
            panel,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            panel, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(
                scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            '<Configure>',
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scroll_frame

        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(
                int(-1 * (event.delta / 120)), 'units')

        canvas.bind_all('<MouseWheel>', _on_mousewheel)
        canvas.bind(
            '<Enter>',
            lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind(
            '<Leave>',
            lambda e: canvas.unbind_all('<MouseWheel>'))

        # Populate the scrollable frame
        scroll_frame.grid_columnconfigure(0, weight=1)
        scroll_frame.grid_rowconfigure(99, weight=1)

        self._create_info_panel(scroll_frame, 0)
        self._create_connection_panel(scroll_frame, 1)
        self._create_pid_panel(scroll_frame, 2)
        self._create_setpoint_panel(scroll_frame, 3)
        self._create_range_panel(scroll_frame, 4)
        self._create_display_panel(scroll_frame, 5)
        self._create_input_config_panel(scroll_frame, 6)
        self._create_manual_output_panel(scroll_frame, 7)
        self._create_advanced_panel(scroll_frame, 8)
        self._create_temp_zone_panel(scroll_frame, 9)
        self._create_tlimit_panel(scroll_frame, 10)
        self._create_console_panel(scroll_frame, 99)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 90
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2,
                         padx=10, pady=10)

        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir, "..", "assets", "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE),
                    Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2,
                    image=self.logo_image)
        except Exception as e:
            pass  # Logo is optional

        institute_font = (
            'Segoe UI', self.FONT_BASE[1] + 1, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=1, column=1, padx=10, pady=(0, 5), sticky='nw')
        ttk.Label(
            frame,
            text="Lake Shore Model 350 | Output 1: 75W Heater",
            background=self.CLR_FRAME_BG).grid(
            row=2, column=0, columnspan=2,
            padx=10, pady=(0, 10), sticky='w')

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="VISA Address:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.visa_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.visa_cb.grid(row=0, column=1, sticky='ew',
                          padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=1, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.connect_btn = ttk.Button(
            btn_frame,
            text="Connect",
            style='Connect.TButton',
            command=self._do_connect)
        self.connect_btn.grid(row=0, column=0, sticky='ew', padx=5)

        self.disconnect_btn = ttk.Button(
            btn_frame,
            text="Disconnect",
            style='Disconnect.TButton',
            state='disabled',
            command=self._do_disconnect)
        self.disconnect_btn.grid(
            row=0, column=1, sticky='ew', padx=5)

        ttk.Button(
            btn_frame,
            text="Scan",
            command=self._scan_visa).grid(
            row=0, column=2, sticky='ew', padx=5)

        # Connection status label
        self.status_label = ttk.Label(
            frame,
            text="● Not Connected",
            font=self.FONT_STATUS,
            foreground=self.CLR_STATUS_BAD,
            background=self.CLR_FRAME_BG)
        self.status_label.grid(
            row=2, column=0, columnspan=2,
            sticky='w', padx=10, pady=(0, 5))

    def _create_pid_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='PID Control')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        # Output selector
        self.pid_output_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Output:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.pid_output_var,
            values=['1', '2'], state='readonly',
            width=5).grid(row=0, column=1, sticky='w',
                          padx=10, pady=5)

        # Preset selector
        ttk.Label(frame, text="Preset:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.pid_preset_var = tk.StringVar()
        pid_preset_cb = ttk.Combobox(
            frame, textvariable=self.pid_preset_var,
            values=list(self.PID_PRESETS.keys()) + ['Custom'],
            state='readonly')
        pid_preset_cb.grid(row=1, column=1, sticky='ew',
                           padx=10, pady=5)
        pid_preset_cb.bind(
            '<<ComboboxSelected>>',
            self._on_pid_preset_change)

        # P, I, D entries
        self.pid_p_entry = self._make_entry(
            frame, "P (Proportional)", "0.5", 2)
        self.pid_i_entry = self._make_entry(
            frame, "I (Integral)", "4.0", 3)
        self.pid_d_entry = self._make_entry(
            frame, "D (Derivative)", "0", 4)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send PID",
            command=self._send_pid).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read PID",
            command=self._read_pid).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_temp_zone_panel(self, parent, grid_row):
        """Configurable temperature-dependent PID zone table."""
        frame = ttk.LabelFrame(
            parent, text='Temperature-Dependent PID Zones')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=("Bind a PID mode + heater range to each zone.\n"
                  "'Upper Bound (K)' is the top of the zone; the\n"
                  "lowest zone starts at 0 K. Use 9999 for the top.\n"
                  "Defaults: <120 K Slow/4, 120-200 K Medium/4,\n"
                  ">200 K Slow/5."),
            background=self.CLR_FRAME_BG,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=0, column=0, sticky='w', padx=10, pady=(5, 5))

        # Output + control input
        sel = ttk.Frame(frame)
        sel.grid(row=1, column=0, sticky='ew', padx=10, pady=2)
        ttk.Label(sel, text="Output:",
                  background=self.CLR_FRAME_BG).pack(side='left')
        self.zone_output_var = tk.StringVar(value='1')
        ttk.Combobox(sel, textvariable=self.zone_output_var,
                     values=['1', '2'], state='readonly',
                     width=4).pack(side='left', padx=5)
        ttk.Label(sel, text="Control Input:",
                  background=self.CLR_FRAME_BG).pack(
            side='left', padx=(10, 0))
        self.zone_input_var = tk.StringVar(value='A')
        ttk.Combobox(sel, textvariable=self.zone_input_var,
                     values=['A', 'B', 'C', 'D'], state='readonly',
                     width=4).pack(side='left', padx=5)

        # Zone table (header + dynamic rows)
        self.zone_table = ttk.Frame(frame)
        self.zone_table.grid(row=2, column=0, sticky='ew',
                             padx=10, pady=5)
        for col, h in enumerate(
                ['Upper Bound (K)', 'PID Mode', 'Heater Range']):
            ttk.Label(self.zone_table, text=h,
                      background=self.CLR_FRAME_BG,
                      font=('Segoe UI', 9, 'bold')).grid(
                row=0, column=col, padx=5, pady=2)

        self.zone_rows = []
        for ub, mode, rng in [('120', 'Slow', '4'),
                              ('200', 'Medium', '4'),
                              ('9999', 'Slow', '5')]:
            self._add_zone_row(ub, mode, rng)

        # Buttons
        btns = ttk.Frame(frame)
        btns.grid(row=3, column=0, sticky='ew', padx=10, pady=5)
        btns.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btns, text="+ Add Zone",
                   command=lambda: self._add_zone_row()).grid(
            row=0, column=0, sticky='ew', padx=5, pady=2)
        ttk.Button(btns, text="- Remove Zone",
                   command=self._remove_zone_row).grid(
            row=0, column=1, sticky='ew', padx=5, pady=2)
        ttk.Button(btns, text="Apply Zone Table (instrument Zone mode)",
                   command=self._apply_zones).grid(
            row=1, column=0, columnspan=2, sticky='ew',
            padx=5, pady=2)
        ttk.Button(btns, text="Auto-Select PID/Range for Current Temp",
                   command=self._auto_select_zone).grid(
            row=2, column=0, columnspan=2, sticky='ew',
            padx=5, pady=2)

    def _add_zone_row(self, upper='9999', mode='Slow', rng='5'):
        """Append an editable zone row to the table."""
        r = len(self.zone_rows) + 1  # +1 for header row
        ub = ttk.Entry(self.zone_table, width=12, font=self.FONT_BASE)
        ub.insert(0, upper)
        ub.grid(row=r, column=0, padx=5, pady=2)

        mode_var = tk.StringVar(value=mode)
        mode_cb = ttk.Combobox(
            self.zone_table, textvariable=mode_var,
            values=list(self.ZONE_PID_MODES.keys()),
            state='readonly', width=10)
        mode_cb.grid(row=r, column=1, padx=5, pady=2)

        rng_var = tk.StringVar(value=rng)
        rng_cb = ttk.Combobox(
            self.zone_table, textvariable=rng_var,
            values=['0', '1', '2', '3', '4', '5'],
            state='readonly', width=6)
        rng_cb.grid(row=r, column=2, padx=5, pady=2)

        self.zone_rows.append({
            'ub': ub, 'mode': mode_var, 'range': rng_var,
            'widgets': [ub, mode_cb, rng_cb]})

    def _remove_zone_row(self):
        """Remove the last zone row (at least one must remain)."""
        if len(self.zone_rows) <= 1:
            self.log("At least one zone is required.")
            return
        row = self.zone_rows.pop()
        for w in row['widgets']:
            w.destroy()

    def _collect_zones(self):
        """Return zones sorted by upper bound, or None on bad input."""
        zones = []
        for r in self.zone_rows:
            try:
                ub = float(r['ub'].get())
            except ValueError:
                self.log("ERROR: Zone upper bounds must be numeric.")
                messagebox.showerror(
                    "Invalid Input",
                    "All zone upper bounds must be numeric.")
                return None
            zones.append((ub, r['mode'].get(), r['range'].get()))
        zones.sort(key=lambda z: z[0])
        return zones

    def _apply_zones(self):
        """Write the zone table to the instrument and enable Zone mode."""
        if not self._require_connection():
            return
        zones = self._collect_zones()
        if zones is None:
            return
        output = int(self.zone_output_var.get())
        input_letter = self.zone_input_var.get()
        input_num = {'A': 1, 'B': 2, 'C': 3, 'D': 4}.get(
            input_letter, 1)

        self.log(f">>> Applying {len(zones)} PID zone(s) to "
                 f"Output {output}...")
        for idx, (ub, mode, rng) in enumerate(zones, start=1):
            p, i, d = self.ZONE_PID_MODES.get(mode, (0.5, 4.0, 0))
            try:
                cmd = self.backend.set_zone(
                    output, idx, ub, p, i, d, 0,
                    int(rng), input_num, 0)
                self.log(f"    SENT: {cmd}")
            except Exception as e:
                self.log(f"    ERROR: {e}")
                messagebox.showerror("Command Failed", str(e))
                return
        try:
            cmd = self.backend.set_output_mode(output, 2, input_letter)
            self.log(f"    SENT: {cmd}  (Zone mode enabled)")
        except Exception as e:
            self.log(f"    ERROR enabling zone mode: {e}")
            return
        self.log("    OK: Zone table applied; output in Zone mode.")

    def _auto_select_zone(self):
        """Read current temp and apply the matching zone's PID + range."""
        if not self._require_connection():
            return
        zones = self._collect_zones()
        if zones is None:
            return
        output = int(self.zone_output_var.get())
        input_letter = self.zone_input_var.get()
        try:
            temp = self.backend.get_temperature(input_letter)
        except Exception as e:
            self.log(f"ERROR reading temperature: {e}")
            messagebox.showerror("Read Failed", str(e))
            return

        selected = next(((ub, m, r) for ub, m, r in zones
                         if temp <= ub), zones[-1])
        ub, mode, rng = selected
        p, i, d = self.ZONE_PID_MODES.get(mode, (0.5, 4.0, 0))
        self.log(f">>> Current temp {temp:.3f} K -> zone <= {ub} K: "
                 f"{mode} PID, range {rng}")
        try:
            self.backend.set_pid(output, p, i, d)
            self.backend.set_range(output, int(rng))
            self.log(f"    SENT: PID {output},{p},{i},{d}; "
                     f"RANGE {output},{rng}")
            self.log("    OK: PID and range applied for current temp.")
        except Exception as e:
            self.log(f"    ERROR: {e}")
            messagebox.showerror("Command Failed", str(e))

    def _create_setpoint_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Setpoint Control')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        # Output selector
        self.setp_output_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Output:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.setp_output_var,
            values=['1', '2'], state='readonly',
            width=5).grid(row=0, column=1, sticky='w',
                          padx=10, pady=5)

        self.setp_entry = self._make_entry(
            frame, "Setpoint (K)", "300", 1)

        # Ramp checkbox
        self.ramp_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable Ramp",
            variable=self.ramp_enabled_var,
            command=self._toggle_ramp_fields).grid(
            row=2, column=0, columnspan=2, sticky='w',
            padx=10, pady=2)

        self.ramp_rate_entry = self._make_entry(
            frame, "Ramp Rate (K/min)", "2.0", 3)
        self.ramp_rate_entry.config(state='disabled')

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Setpoint",
            command=self._send_setpoint).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Setpoint",
            command=self._read_setpoint).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_range_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Heater Range')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        # Output selector
        self.range_output_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Output:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.range_output_var,
            values=['1', '2'], state='readonly',
            width=5).grid(row=0, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Label(frame, text="Range:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.range_var = tk.StringVar(value='3')
        ttk.Combobox(
            frame, textvariable=self.range_var,
            values=['0 (Off)', '1', '2', '3', '4', '5'],
            state='readonly').grid(
            row=1, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Range",
            command=self._send_range).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Range",
            command=self._read_range).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_tlimit_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent,
            text='⚠ DANGER: Safety Temperature Limit (TLIMIT)')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="THIS IS NOT THE CONTROL SETPOINT!",
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_ACCENT_RED,
            font=('Segoe UI', 10, 'bold')).grid(
            row=0, column=0, columnspan=2,
            sticky='w', padx=10, pady=(5, 0))
        ttk.Label(
            frame,
            text=("Over-temperature safety shutdown: if this\n"
                  "input exceeds the limit, ALL control outputs\n"
                  "turn off. 0 K disables the protection.\n"
                  "To set a target temperature, use the\n"
                  "'Setpoint Control' panel above instead."),
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_ACCENT_RED,
            font=('Segoe UI', 9),
            justify='left').grid(
            row=1, column=0, columnspan=2,
            sticky='w', padx=10, pady=(0, 5))

        # Input selector
        self.tlimit_ch_var = tk.StringVar(value='A')
        ttk.Label(frame, text="Input:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.tlimit_ch_var,
            values=['A', 'B', 'C', 'D'], state='readonly',
            width=5).grid(row=2, column=1, sticky='w',
                          padx=10, pady=5)

        self.tlimit_entry = self._make_entry(
            frame, "Safety Limit (K)", "325", 3)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send T-Limit",
            command=self._send_tlimit).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read T-Limit",
            command=self._read_tlimit).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_display_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Display Settings')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Mode:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.display_mode_var = tk.StringVar(
            value='Single Input')
        display_mode_cb = ttk.Combobox(
            frame, textvariable=self.display_mode_var,
            values=['Single Input', 'Dual Input'],
            state='readonly')
        display_mode_cb.grid(row=0, column=1, sticky='ew',
                             padx=10, pady=5)
        display_mode_cb.bind(
            '<<ComboboxSelected>>',
            self._toggle_display_fields)

        # Single input channel
        self.display_ch1_var = tk.StringVar(value='A')
        ttk.Label(frame, text="Input 1:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.display_ch1_var,
            values=['A', 'B', 'C', 'D'],
            state='readonly').grid(
            row=1, column=1, sticky='ew', padx=10, pady=5)

        # Dual input second channel
        self.display_ch2_var = tk.StringVar(value='B')
        self.display_ch2_label = ttk.Label(frame, text="Input 2:")
        self.display_ch2_label.grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.display_ch2_cb = ttk.Combobox(
            frame, textvariable=self.display_ch2_var,
            values=['A', 'B', 'C', 'D'],
            state='readonly')
        self.display_ch2_cb.grid(
            row=2, column=1, sticky='ew', padx=10, pady=5)

        # Hide dual fields initially
        self.display_ch2_label.grid_remove()
        self.display_ch2_cb.grid_remove()

        # Units selector
        self.display_units_var = tk.StringVar(value='Kelvin')
        ttk.Label(frame, text="Units:").grid(
            row=3, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.display_units_var,
            values=['Kelvin', 'Celsius', 'Sensor Units'],
            state='readonly').grid(
            row=3, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Display",
            command=self._send_display).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Display",
            command=self._read_display).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_input_config_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Input Configuration')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        # Channel selector
        self.intype_ch_var = tk.StringVar(value='A')
        ttk.Label(frame, text="Channel:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.intype_ch_var,
            values=['A', 'B', 'C', 'D'],
            state='readonly').grid(
            row=0, column=1, sticky='ew', padx=10, pady=5)

        # Sensor type
        self.intype_sensor_var = tk.StringVar(
            value='Silicon Diode')
        ttk.Label(frame, text="Sensor Type:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.intype_sensor_var,
            values=list(
                Lakeshore350Backend.SENSOR_TYPES.values()),
            state='readonly').grid(
            row=1, column=1, sticky='ew', padx=10, pady=5)

        # Compensation
        self.intype_comp_var = tk.StringVar(value='Off')
        ttk.Label(frame, text="Compensation:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.intype_comp_var,
            values=['Off', 'On'],
            state='readonly').grid(
            row=2, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send Config",
            command=self._send_input_config).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read Config",
            command=self._read_input_config).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_manual_output_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Manual Output')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.mout_output_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Output:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.mout_output_var,
            values=['1', '2'], state='readonly',
            width=5).grid(row=0, column=1, sticky='w',
                          padx=10, pady=5)

        self.mout_entry = self._make_entry(
            frame, "Manual Output (%)", "0", 1)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2,
                       sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            btn_frame,
            text="Send MOUT",
            command=self._send_mout).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(
            btn_frame,
            text="Read MOUT",
            command=self._read_mout).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_advanced_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Advanced Control')
        frame.grid(row=grid_row, column=0, sticky='new',
                   pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        # Interface mode (Local/Remote/Lockout)
        ttk.Label(frame, text="Interface:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.iface_mode_var = tk.StringVar(value='Remote')
        ttk.Combobox(
            frame, textvariable=self.iface_mode_var,
            values=['Local', 'Remote', 'Remote + Lockout'],
            state='readonly').grid(
            row=0, column=1, sticky='ew', padx=10, pady=5)

        ttk.Button(
            frame,
            text="Send Interface Mode",
            command=self._send_iface_mode).grid(
            row=1, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        # Output mode
        ttk.Label(frame, text="Output Mode:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.outmode_var = tk.StringVar(
            value='Closed Loop PID')
        ttk.Combobox(
            frame, textvariable=self.outmode_var,
            values=list(
                Lakeshore350Backend.OUTPUT_MODES.values()),
            state='readonly').grid(
            row=2, column=1, sticky='ew', padx=10, pady=5)

        self.outmode_output_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Output:").grid(
            row=3, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.outmode_output_var,
            values=['1', '2', '3', '4'],
            state='readonly',
            width=5).grid(row=3, column=1, sticky='w',
                          padx=10, pady=5)

        self.outmode_input_var = tk.StringVar(value='A')
        ttk.Label(frame, text="Control Input:").grid(
            row=4, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.outmode_input_var,
            values=['A', 'B', 'C', 'D'],
            state='readonly',
            width=5).grid(row=4, column=1, sticky='w',
                          padx=10, pady=5)

        ttk.Button(
            frame,
            text="Send Output Mode",
            command=self._send_outmode).grid(
            row=5, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        # Input filter
        ttk.Label(frame, text="Filter Input:").grid(
            row=6, column=0, sticky='w', padx=10, pady=5)
        self.filter_ch_var = tk.StringVar(value='A')
        ttk.Combobox(
            frame, textvariable=self.filter_ch_var,
            values=['A', 'B', 'C', 'D'],
            state='readonly',
            width=5).grid(row=6, column=1, sticky='w',
                          padx=10, pady=5)

        self.filter_on_var = tk.StringVar(value='On')
        ttk.Label(frame, text="Filter:").grid(
            row=7, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.filter_on_var,
            values=['On', 'Off'],
            state='readonly',
            width=5).grid(row=7, column=1, sticky='w',
                          padx=10, pady=5)

        self.filter_points_var = tk.StringVar(value='4')
        ttk.Label(frame, text="Filter Points:").grid(
            row=8, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(
            frame, textvariable=self.filter_points_var,
            values=['2', '4', '8', '16', '32', '64'],
            state='readonly').grid(
            row=8, column=1, sticky='ew', padx=10, pady=5)

        ttk.Button(
            frame,
            text="Send Filter",
            command=self._send_filter).grid(
            row=9, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

        # Factory reset (dangerous)
        ttk.Button(
            frame,
            text="⚠ Factory Reset (*RST)",
            command=self._factory_reset).grid(
            row=10, column=0, columnspan=2,
            sticky='ew', padx=10, pady=2)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew',
                   pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.console = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            borderwidth=0,
            height=8)
        self.console.grid(row=0, column=0, sticky='nsew',
                          padx=5, pady=5)
        self.log("Console initialized. Scan for instruments, "
                 "then connect.")

    # -- Right panel (live status monitor) --

    def _populate_right_panel(self, panel):
        # One row, two columns. Give more space to the status panel.
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=2)
        panel.grid_columnconfigure(1, weight=1)

        container = ttk.LabelFrame(
            panel, text='Live Instrument Status')
        container.grid(row=0, column=0,
                       sticky='nsew',
                       padx=5, pady=5)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # Scrollable status frame
        canvas = tk.Canvas(
            container,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            container, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(
                scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame,
                             anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side='left', fill='both', expand=True,
                    padx=5, pady=5)
        scrollbar.pack(side='right', fill='y')

        # --- Status display widgets ---
        self.status_labels = {}
        row = 0

        # Temperatures
        ttk.Label(
            scroll_frame,
            text="── Temperatures ──",
            font=self.FONT_TITLE,
            background=self.CLR_FRAME_BG).grid(
            row=row, column=0, columnspan=2,
            sticky='w', pady=(10, 5))
        row += 1

        for ch in ['A', 'B', 'C', 'D']:
            ttk.Label(
                scroll_frame,
                text=f"Input {ch} (K):",
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w', padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                font=self.FONT_STATUS,
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w', padx=20, pady=2)
            self.status_labels[f'temp_{ch}'] = lbl
            row += 1

        # Heater output
        ttk.Label(
            scroll_frame,
            text="── Heater ──",
            font=self.FONT_TITLE,
            background=self.CLR_FRAME_BG).grid(
            row=row, column=0, columnspan=2,
            sticky='w', pady=(15, 5))
        row += 1

        for out in ['1', '2']:
            ttk.Label(
                scroll_frame,
                text=f"Output {out} Heater (%):",
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w',
                padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                font=self.FONT_STATUS,
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w',
                     padx=20, pady=2)
            self.status_labels[f'heater_{out}'] = lbl
            row += 1

        # Control parameters
        ttk.Label(
            scroll_frame,
            text="── Control Parameters ──",
            font=self.FONT_TITLE,
            background=self.CLR_FRAME_BG).grid(
            row=row, column=0, columnspan=2,
            sticky='w', pady=(15, 5))
        row += 1

        for label_text, key in [
            ("Setpoint 1 (K):", 'setpoint_1'),
            ("Setpoint 2 (K):", 'setpoint_2'),
            ("PID 1 (P, I, D):", 'pid_1'),
            ("PID 2 (P, I, D):", 'pid_2'),
            ("Range 1:", 'range_1'),
            ("Range 2:", 'range_2'),
            ("Ramp 1 (on/off, rate):", 'ramp_1'),
            ("Ramp 2 (on/off, rate):", 'ramp_2'),
            ("Output Mode 1:", 'outmode_1'),
            ("Display Mode:", 'display_mode'),
            ("Interface Mode:", 'iface_mode'),
        ]:
            ttk.Label(
                scroll_frame,
                text=label_text,
                background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w',
                padx=20, pady=2)
            lbl = ttk.Label(
                scroll_frame,
                text="---",
                background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w',
                     padx=20, pady=2)
            self.status_labels[key] = lbl
            row += 1

        # Polling control
        row += 1
        ttk.Separator(scroll_frame).grid(
            row=row, column=0, columnspan=2,
            sticky='ew', padx=20, pady=10)
        row += 1

        self.poll_btn = ttk.Button(
            scroll_frame,
            text="Start Polling",
            command=self._toggle_polling)
        self.poll_btn.grid(row=row, column=0, columnspan=2,
                           sticky='ew', padx=20, pady=5)

        # --- Notes Panel ---
        notes_frame = ttk.LabelFrame(panel, text="Notes & Guides")
        notes_frame.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        notes_frame.grid_columnconfigure(0, weight=1)
        notes_frame.grid_rowconfigure(0, weight=1)

        notes_text_widget = scrolledtext.ScrolledText(
            notes_frame,
            wrap='word',
            bg=self.CLR_FRAME_BG,
            fg=self.CLR_TEXT_DARK,
            font=('Consolas', 10),
            borderwidth=0,
            relief='flat'
        )
        notes_text_widget.pack(fill='both', expand=True, padx=5, pady=5)

        # --- Build the PID/Range guide table with fixed-width columns ---
        def _build_table(headers, rows, widths):
            def fmt(cells):
                return (
                    "│ "
                    + " │ ".join(
                        c.ljust(w) for c, w in zip(cells, widths)
                    )
                    + " │"
                )

            seg = ["─" * (w + 2) for w in widths]
            top = "┌" + "┬".join(seg) + "┐"
            mid = "├" + "┼".join(seg) + "┤"
            bot = "└" + "┴".join(seg) + "┘"

            lines = [top, fmt(headers), mid]
            lines += [fmt(r) for r in rows]
            lines.append(bot)
            return "\n".join(lines)

        guide_headers = (
            "Use Case",
            "PID Mode",
            "Range",
            "Notes",
        )
        guide_rows = [
            (
                "Temperature-dependent ramp",
                "Slow",
                "5",
                "Best for controlled temp ramps",
            ),
            (
                "Setpoint stabilization (Tstep)",
                "Medium",
                "5",
                "Good stability at each Tstep",
            ),
            (
                "Fast temperature ramp",
                "Fast",
                "5",
                "Best for rapid temperature changes",
            ),
            (
                "Temperature ramp below 120 K",
                "Slow",
                "4",
                "Recommended for slow ramps <120 K",
            ),
            (
                "Stabilization below 200 K",
                "Fast",
                "4",
                "Good for stable hold below 200 K",
            ),
            (
                "Stabilization near 200 K",
                "Medium",
                "5",
                "Good stability around 200 K",
            ),
        ]
        # Column widths (chars); cells are padded/aligned to these.
        guide_widths = (32, 8, 5, 36)
        guide_table = _build_table(
            guide_headers, guide_rows, guide_widths
        )

        notes_content = (
            "Instrument specs confirmed from manual:\n"
            "  \u2022 4 sensor inputs (A, B, C, D)\n"
            "  \u2022 4 outputs: 1 & 2 = heater (75W max on"
            " Output 1), 3 & 4 = analog\n"
            "  \u2022 5 heater ranges (0=Off, 1-5 increasing"
            " power)\n"
            "  \u2022 PID: P=0-9999, I=0-1000, D=0-200\n"
            "  \u2022 Setpoint ramping: 0.001-100 K/min\n"
            "  \u2022 TLIMIT safety: per-input over-temp limit"
            " shuts down all outputs; 0 K = disabled\n"
            "  \u2022 Display: 1 to 8 reading displays\n"
            "  \u2022 Interfaces: Ethernet, USB, IEEE-488\n\n"
            "PID / Heater-Range selection guide (operator notes):\n\n"
            + guide_table
            + "\n\n"
            "The \"Temperature-Dependent PID Zone\" panel lets you"
            " bind\n"
            "a PID mode + heater range to user-defined temperature\n"
            "thresholds, automating these selections."
        )

        notes_text_widget.insert('1.0', notes_content)
        notes_text_widget.config(state='disabled')

    # -----------------------------------------------------------------------
    # HELPER METHODS
    # -----------------------------------------------------------------------

    def _make_entry(self, parent, label_text, default, row):
        """Create a labeled entry widget. Returns the entry."""
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row, column=0, sticky='w', padx=10, pady=5)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=5)
        entry.insert(0, default)
        return entry

    def log(self, message):
        """Append a timestamped message to the console."""
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    def _require_connection(self):
        """Check if connected. Returns True/False, shows error if not."""
        if not self.is_connected or not self.backend.is_connected:
            self.log("ERROR: Not connected to instrument.")
            messagebox.showerror(
                "Not Connected",
                "Please connect to the instrument first.")
            return False
        return True

    def _safe_command(self, description, func, *args, **kwargs):
        """
        Execute a backend command with full error handling.
        Logs the command, response, and any errors.
        """
        if not self._require_connection():
            return None
        try:
            self.log(f">>> {description}")
            result = func(*args, **kwargs)
            if result:
                self.log(f"    SENT: {result}")
            self.log(f"    OK: {description} completed.")
            return result
        except ValueError as e:
            self.log(f"    VALIDATION ERROR: {e}")
            messagebox.showerror("Invalid Input", str(e))
        except Exception as e:
            err_detail = traceback.format_exc()
            self.log(f"    ERROR: {err_detail}")
            messagebox.showerror("Command Failed", str(e))
        return None

    # -----------------------------------------------------------------------
    # CONNECTION HANDLERS
    # -----------------------------------------------------------------------

    def _scan_visa(self):
        """Scan for VISA resources and populate the combobox."""
        if not self.backend.rm:
            self.log("ERROR: PyVISA library not available.")
            messagebox.showerror(
                "PyVISA Missing",
                "PyVISA is not installed. "
                "Run: pip install pyvisa pyvisa-py")
            return

        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.scan_resources()
        except Exception as e:
            self.log(f"Scan error: {e}")
            return

        if resources:
            self.log(f"Found {len(resources)} resource(s):")
            for r in resources:
                self.log(f"  {r}")
            self.visa_cb['values'] = list(resources)
            # Auto-select common GPIB addresses for Lakeshore
            for r in resources:
                if 'GPIB' in r and ('12' in r or '15' in r):
                    self.visa_cb.set(r)
                    break
            if not self.visa_cb.get():
                self.visa_cb.set(resources[0])
        else:
            self.log("No VISA instruments found.")

    def _do_connect(self):
        """Connect to the selected VISA address."""
        visa_addr = self.visa_cb.get()
        if not visa_addr:
            self.log("ERROR: No VISA address selected.")
            messagebox.showerror(
                "No Address",
                "Please scan and select a VISA address.")
            return

        try:
            self.log(f"Connecting to {visa_addr}...")
            idn = self.backend.connect(visa_addr)
            self.is_connected = True
            self.log(f"Connected: {idn}")
            self.status_label.config(
                text="● Connected",
                foreground=self.CLR_STATUS_OK)
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
            self.visa_cb.config(state='disabled')
            # Auto-start polling
            self._start_polling()
        except Exception as e:
            err_detail = traceback.format_exc()
            self.log(f"CONNECT ERROR: {err_detail}")
            messagebox.showerror(
                "Connection Failed",
                f"Could not connect to {visa_addr}:\n{e}")

    def _do_disconnect(self):
        """
        Non-destructive disconnect.
        Only closes the VISA resource. Instrument retains all settings.
        """
        self._stop_polling()
        self.log("Disconnecting (non-destructive)...")
        self.log("  Instrument settings will be retained.")
        self.backend.disconnect()
        self.is_connected = False
        self.log("Disconnected. Instrument continues operating "
                 "with current settings.")
        self.status_label.config(
            text="● Not Connected",
            foreground=self.CLR_STATUS_BAD)
        self.connect_btn.config(state='normal')
        self.disconnect_btn.config(state='disabled')
        self.visa_cb.config(state='readonly')
        # Reset status labels
        for key in self.status_labels:
            self.status_labels[key].config(text="---")

    # -----------------------------------------------------------------------
    # PID HANDLERS
    # -----------------------------------------------------------------------

    def _on_pid_preset_change(self, event=None):
        """Update P/I/D entries when a preset is selected."""
        preset = self.pid_preset_var.get()
        if preset in self.PID_PRESETS:
            p, i, d = self.PID_PRESETS[preset]
            self.pid_p_entry.delete(0, 'end')
            self.pid_p_entry.insert(0, str(p))
            self.pid_i_entry.delete(0, 'end')
            self.pid_i_entry.insert(0, str(i))
            self.pid_d_entry.delete(0, 'end')
            self.pid_d_entry.insert(0, str(d))

    def _send_pid(self):
        """Send PID values to the instrument."""
        try:
            output = int(self.pid_output_var.get())
            p = float(self.pid_p_entry.get())
            i = float(self.pid_i_entry.get())
            d = float(self.pid_d_entry.get())
        except ValueError:
            self.log("ERROR: Invalid PID values.")
            messagebox.showerror(
                "Invalid Input",
                "P, I, D must be numeric values.")
            return

        self._safe_command(
            f"Set PID (Output {output}): P={p}, I={i}, D={d}",
            self.backend.set_pid, output, p, i, d)

    def _read_pid(self):
        """Read current PID values from the instrument."""
        if not self._require_connection():
            return
        try:
            output = int(self.pid_output_var.get())
            p, i, d = self.backend.get_pid(output)
            self.log(f"Read PID (Output {output}): "
                     f"P={p}, I={i}, D={d}")
            self.pid_p_entry.delete(0, 'end')
            self.pid_p_entry.insert(0, str(p))
            self.pid_i_entry.delete(0, 'end')
            self.pid_i_entry.insert(0, str(i))
            self.pid_d_entry.delete(0, 'end')
            self.pid_d_entry.insert(0, str(d))
        except Exception as e:
            self.log(f"ERROR reading PID: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # SETPOINT HANDLERS
    # -----------------------------------------------------------------------

    def _toggle_ramp_fields(self):
        """Enable/disable ramp rate entry based on checkbox."""
        if self.ramp_enabled_var.get():
            self.ramp_rate_entry.config(state='normal')
        else:
            self.ramp_rate_entry.config(state='disabled')

    def _send_setpoint(self):
        """Send setpoint to the instrument."""
        try:
            output = int(self.setp_output_var.get())
            setpoint = float(self.setp_entry.get())
        except ValueError:
            self.log("ERROR: Invalid setpoint value.")
            messagebox.showerror(
                "Invalid Input", "Setpoint must be a number.")
            return

        if self.ramp_enabled_var.get():
            try:
                rate = float(self.ramp_rate_entry.get())
            except ValueError:
                self.log("ERROR: Invalid ramp rate.")
                messagebox.showerror(
                    "Invalid Input",
                    "Ramp rate must be a number.")
                return
            self._safe_command(
                f"Set Setpoint (Output {output}): "
                f"{setpoint} K with ramp {rate} K/min",
                self.backend.set_setpoint_with_ramp,
                output, setpoint, rate)
        else:
            self._safe_command(
                f"Set Setpoint (Output {output}): "
                f"{setpoint} K (immediate, no ramp)",
                self.backend.set_setpoint_immediate,
                output, setpoint)

    def _read_setpoint(self):
        """Read current setpoint from the instrument."""
        if not self._require_connection():
            return
        try:
            output = int(self.setp_output_var.get())
            sp = self.backend.get_setpoint(output)
            on_off, rate = self.backend.get_ramp_status(output)
            self.log(f"Read Setpoint (Output {output}): {sp} K")
            self.log(f"  Ramp: {'ON' if on_off else 'OFF'}, "
                     f"rate={rate} K/min")
            self.setp_entry.delete(0, 'end')
            self.setp_entry.insert(0, str(sp))
            if on_off:
                self.ramp_enabled_var.set(True)
                self.ramp_rate_entry.config(state='normal')
                self.ramp_rate_entry.delete(0, 'end')
                self.ramp_rate_entry.insert(0, str(rate))
            else:
                self.ramp_enabled_var.set(False)
                self.ramp_rate_entry.config(state='disabled')
        except Exception as e:
            self.log(f"ERROR reading setpoint: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # RANGE HANDLERS
    # -----------------------------------------------------------------------

    def _send_range(self):
        """Send heater range to the instrument."""
        try:
            output = int(self.range_output_var.get())
            range_str = self.range_var.get()
            # Parse "0 (Off)" -> 0, "1" -> 1, etc.
            range_val = int(range_str.split()[0])
        except ValueError:
            self.log("ERROR: Invalid range value.")
            return

        self._safe_command(
            f"Set Range (Output {output}): {range_val}",
            self.backend.set_range, output, range_val)

    def _read_range(self):
        """Read current heater range from the instrument."""
        if not self._require_connection():
            return
        try:
            output = int(self.range_output_var.get())
            r = self.backend.get_range(output)
            self.log(f"Read Range (Output {output}): {r}")
            if r == 0:
                self.range_var.set('0 (Off)')
            else:
                self.range_var.set(str(r))
        except Exception as e:
            self.log(f"ERROR reading range: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # TEMPERATURE LIMIT HANDLERS
    # -----------------------------------------------------------------------

    def _send_tlimit(self):
        """Send over-temperature limit (TLIMIT) to the instrument."""
        channel = self.tlimit_ch_var.get()
        try:
            limit = float(self.tlimit_entry.get())
        except ValueError:
            self.log("ERROR: Invalid temperature limit value.")
            messagebox.showerror(
                "Invalid Input",
                "Temperature limit must be a number 0-2999 K.")
            return

        # Always confirm: this is a safety limit, easily confused
        # with the control setpoint.
        if limit == 0:
            confirm_msg = (
                f"A limit of 0 K turns the over-temperature "
                f"safety shutdown OFF for Input {channel}.\n\n"
                f"The instrument will no longer shut down "
                f"outputs if this input overheats.\n\n"
                f"Continue?")
        else:
            confirm_msg = (
                f"You are about to set the SAFETY temperature "
                f"limit for Input {channel} to {limit} K.\n\n"
                f"This is NOT the control setpoint. If Input "
                f"{channel} reads above {limit} K, the instrument "
                f"will shut down ALL control outputs.\n\n"
                f"To set a target temperature instead, cancel "
                f"and use the Setpoint Control panel.\n\n"
                f"Set the safety limit?")
        if not messagebox.askyesno(
                "⚠ DANGER: Safety Temperature Limit", confirm_msg):
            self.log("Temperature limit change cancelled by user.")
            return

        self._safe_command(
            f"Set Temp Limit (Input {channel}): {limit} K",
            self.backend.set_temp_limit, channel, limit)

    def _read_tlimit(self):
        """Read current over-temperature limit from the instrument."""
        if not self._require_connection():
            return
        try:
            channel = self.tlimit_ch_var.get()
            limit = self.backend.get_temp_limit(channel)
            state = "disabled" if limit == 0 else "active"
            self.log(f"Read Temp Limit (Input {channel}): "
                     f"{limit} K ({state})")
            self.tlimit_entry.delete(0, 'end')
            self.tlimit_entry.insert(0, str(limit))
        except Exception as e:
            self.log(f"ERROR reading temperature limit: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # DISPLAY HANDLERS
    # -----------------------------------------------------------------------

    def _toggle_display_fields(self, event=None):
        """Show/hide dual-input fields based on mode."""
        if self.display_mode_var.get() == 'Dual Input':
            self.display_ch2_label.grid()
            self.display_ch2_cb.grid()
        else:
            self.display_ch2_label.grid_remove()
            self.display_ch2_cb.grid_remove()

    def _send_display(self):
        """Send display configuration to the instrument."""
        units_map = {
            'Kelvin': 1, 'Celsius': 2, 'Sensor Units': 3}
        units = units_map.get(
            self.display_units_var.get(), 1)

        if self.display_mode_var.get() == 'Dual Input':
            ch1 = self.display_ch1_var.get()
            ch2 = self.display_ch2_var.get()
            self._safe_command(
                f"Set Display: Dual ({ch1}, {ch2})",
                self.backend.set_display_dual,
                ch1, ch2, units, units)
        else:
            ch = self.display_ch1_var.get()
            self._safe_command(
                f"Set Display: Single ({ch})",
                self.backend.set_display_single,
                ch, units)

    def _read_display(self):
        """Read current display configuration."""
        if not self._require_connection():
            return
        try:
            resp = self.backend.get_display()
            self.log(f"Read Display: {resp}")
        except Exception as e:
            self.log(f"ERROR reading display: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # INPUT CONFIG HANDLERS
    # -----------------------------------------------------------------------

    def _send_input_config(self):
        """Send input configuration to the instrument."""
        channel = self.intype_ch_var.get()
        sensor_name = self.intype_sensor_var.get()

        # Find sensor type code
        sensor_code = None
        for code, name in (
                Lakeshore350Backend.SENSOR_TYPES.items()):
            if name == sensor_name:
                sensor_code = code
                break
        if sensor_code is None:
            self.log("ERROR: Unknown sensor type.")
            return

        comp = 1 if self.intype_comp_var.get() == 'On' else 0

        self._safe_command(
            f"Set Input Config (Ch {channel}): "
            f"{sensor_name}, comp={'On' if comp else 'Off'}",
            self.backend.set_input_type,
            channel, sensor_code, 0, 0, comp, 1, 0)

    def _read_input_config(self):
        """Read current input configuration."""
        if not self._require_connection():
            return
        try:
            channel = self.intype_ch_var.get()
            resp = self.backend.get_input_type(channel)
            self.log(f"Read Input Config (Ch {channel}): {resp}")
            parts = resp.split(',')
            if len(parts) >= 1:
                code = int(parts[0])
                name = Lakeshore350Backend.SENSOR_TYPES.get(
                    code, f"Unknown({code})")
                self.intype_sensor_var.set(name)
            if len(parts) >= 5:
                comp_val = int(parts[4])
                self.intype_comp_var.set(
                    'On' if comp_val else 'Off')
        except Exception as e:
            self.log(f"ERROR reading input config: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # MANUAL OUTPUT HANDLERS
    # -----------------------------------------------------------------------

    def _send_mout(self):
        """Send manual output percentage."""
        try:
            output = int(self.mout_output_var.get())
            value = float(self.mout_entry.get())
        except ValueError:
            self.log("ERROR: Invalid manual output value.")
            messagebox.showerror(
                "Invalid Input",
                "Manual output must be a number 0-100.")
            return

        # Safety: confirm if above 50%
        if value > 50:
            if not messagebox.askyesno(
                    "Confirm High Output",
                    f"Manual output of {value}% is above 50%. "
                    f"This may cause rapid heating.\n\n"
                    f"Continue?"):
                self.log("Manual output cancelled by user.")
                return

        self._safe_command(
            f"Set Manual Output (Output {output}): {value}%",
            self.backend.set_manual_output, output, value)

    def _read_mout(self):
        """Read current manual output."""
        if not self._require_connection():
            return
        try:
            output = int(self.mout_output_var.get())
            val = self.backend.get_manual_output(output)
            self.log(f"Read Manual Output (Output {output}): "
                     f"{val}%")
            self.mout_entry.delete(0, 'end')
            self.mout_entry.insert(0, str(val))
        except Exception as e:
            self.log(f"ERROR reading manual output: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # ADVANCED HANDLERS
    # -----------------------------------------------------------------------

    def _send_iface_mode(self):
        """Send interface mode (Local/Remote/Lockout)."""
        mode_map = {
            'Local': 0, 'Remote': 1, 'Remote + Lockout': 2}
        mode = mode_map.get(self.iface_mode_var.get(), 1)
        self._safe_command(
            f"Set Interface Mode: "
            f"{self.iface_mode_var.get()}",
            self.backend.set_interface_mode, mode)

    def _send_outmode(self):
        """Send output mode configuration."""
        mode_name = self.outmode_var.get()
        mode_code = None
        for code, name in (
                Lakeshore350Backend.OUTPUT_MODES.items()):
            if name == mode_name:
                mode_code = code
                break
        if mode_code is None:
            self.log("ERROR: Unknown output mode.")
            return

        output = int(self.outmode_output_var.get())
        input_ch = self.outmode_input_var.get()

        self._safe_command(
            f"Set Output Mode (Output {output}): "
            f"{mode_name}, Input {input_ch}",
            self.backend.set_output_mode,
            output, mode_code, input_ch)

    def _send_filter(self):
        """Send input filter configuration."""
        channel = self.filter_ch_var.get()
        on_off = 1 if self.filter_on_var.get() == 'On' else 0
        points = int(self.filter_points_var.get())

        self._safe_command(
            f"Set Filter (Ch {channel}): "
            f"{'On' if on_off else 'Off'}, "
            f"{points} points",
            self.backend.set_filter,
            channel, on_off, points, 2)

    def _factory_reset(self):
        """Send *RST with confirmation dialog."""
        if not self._require_connection():
            return
        confirm = messagebox.askyesno(
            "⚠ DANGER: Factory Reset",
            "*RST will reset ALL instrument settings to "
            "factory defaults.\n\n"
            "This includes:\n"
            "  - PID values\n"
            "  - Setpoints\n"
            "  - Heater ranges (will turn OFF)\n"
            "  - Ramp settings\n"
            "  - Display configuration\n"
            "  - Input configurations\n"
            "  - All other settings\n\n"
            "This action CANNOT be undone.\n\n"
            "Are you absolutely sure?")
        if not confirm:
            self.log("Factory reset cancelled.")
            return

        confirm2 = messagebox.askyesno(
            "Final Confirmation",
            "Last chance. Reset the instrument to "
            "factory defaults?")
        if not confirm2:
            self.log("Factory reset cancelled.")
            return

        self._safe_command(
            "FACTORY RESET (*RST)",
            self.backend.reset)
        self.log("Instrument has been reset to factory defaults.")

    # -----------------------------------------------------------------------
    # POLLING / LIVE STATUS
    # -----------------------------------------------------------------------

    def _toggle_polling(self):
        """Start or stop the live status polling."""
        if self.polling_active:
            self._stop_polling()
        else:
            self._start_polling()

    def _start_polling(self):
        """Start the 1-second polling loop."""
        if not self._require_connection():
            return
        self.polling_active = True
        self.poll_btn.config(text="Stop Polling")
        self.log("Live status polling started (1s interval).")
        self._poll_loop()

    def _stop_polling(self):
        """Stop the polling loop."""
        self.polling_active = False
        self.poll_btn.config(text="Start Polling")
        if self.is_connected:
            self.log("Live status polling stopped.")

    def _poll_loop(self):
        """Polling loop that queries instrument status every 1 second."""
        if not self.polling_active or not self.is_connected:
            return
        try:
            # Temperatures (all channels)
            for ch in ['A', 'B', 'C', 'D']:
                try:
                    temp = self.backend.get_temperature(ch)
                    self.status_labels[f'temp_{ch}'].config(
                        text=f"{temp:.3f} K")
                except Exception:
                    self.status_labels[f'temp_{ch}'].config(
                        text="Error")

            # Heater outputs
            for out in ['1', '2']:
                try:
                    htr = self.backend.get_heater_output(
                        int(out))
                    self.status_labels[f'heater_{out}'].config(
                        text=f"{htr:.1f} %")
                except Exception:
                    self.status_labels[f'heater_{out}'].config(
                        text="Error")

            # Setpoints
            for out in ['1', '2']:
                try:
                    sp = self.backend.get_setpoint(int(out))
                    self.status_labels[
                        f'setpoint_{out}'].config(
                        text=f"{sp:.3f} K")
                except Exception:
                    pass

            # PID
            for out in ['1', '2']:
                try:
                    p, i, d = self.backend.get_pid(int(out))
                    self.status_labels[f'pid_{out}'].config(
                        text=f"P={p}, I={i}, D={d}")
                except Exception:
                    pass

            # Range
            for out in ['1', '2']:
                try:
                    r = self.backend.get_range(int(out))
                    range_text = "Off" if r == 0 else str(r)
                    self.status_labels[
                        f'range_{out}'].config(text=range_text)
                except Exception:
                    pass

            # Ramp status
            for out in ['1', '2']:
                try:
                    on_off, rate = self.backend.get_ramp_status(
                        int(out))
                    self.status_labels[f'ramp_{out}'].config(
                        text=f"{'ON' if on_off else 'OFF'}, "
                             f"{rate} K/min")
                except Exception:
                    pass

            # Output mode (output 1 only)
            try:
                om = self.backend.get_output_mode(1)
                parts = om.split(',')
                if parts:
                    mode_code = int(parts[0])
                    mode_name = (
                        Lakeshore350Backend.OUTPUT_MODES.get(
                            mode_code, f"Unknown({mode_code})"))
                    self.status_labels[
                        'outmode_1'].config(text=mode_name)
            except Exception:
                pass

            # Display mode
            try:
                disp = self.backend.get_display()
                self.status_labels[
                    'display_mode'].config(text=disp)
            except Exception:
                pass

            # Interface mode
            try:
                iface = self.backend.get_interface_mode()
                self.status_labels[
                    'iface_mode'].config(text=iface)
            except Exception:
                pass

        except Exception as e:
            self.log(f"Polling error: {e}")

        # Schedule next poll
        if self.polling_active:
            self.root.after(1000, self._poll_loop)

    # -----------------------------------------------------------------------
    # WINDOW CLOSE
    # -----------------------------------------------------------------------

    def _on_closing(self):
        """Handle window close event."""
        if self.is_connected:
            if messagebox.askyesno(
                    "Exit",
                    "You are still connected to the instrument.\n\n"
                    "Disconnecting is non-destructive - the "
                    "instrument will retain all settings and "
                    "continue operating.\n\n"
                    "Disconnect and exit?"):
                self._stop_polling()
                self.backend.disconnect()
                self.root.destroy()
        else:
            self.root.destroy()


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if not pyvisa:
        # Show error in a simple dialog
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\n"
            "Please run:\n"
            "  pip install pyvisa pyvisa-py\n\n"
            "For NI-VISA backend, install NI-VISA runtime\n"
            "from national instruments.")
        root.destroy()
    else:
        root = tk.Tk()
        app = DirectControlGUI(root)
        root.mainloop()