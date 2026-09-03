"""
Module: T_Control_L340_DirectControl_GUI.py
Purpose: Direct command workbench GUI for the Lake Shore Model 340 Temperature
         Controller.  Port of T_Control_L350_DirectControl_GUI.py.
         Non-destructive disconnect: the instrument keeps every setting when
         the programme closes.

Every command is verified against the Lake Shore Model 340 User's Manual,
Chapter 9 (Remote Operation), command reference printed 9-23 to 9-43.

Model 340 vs Model 350: what changed in this port and why
---------------------------------------------------------
  RANGE <0-5>      No output number. Heater range is Loop 1 only.   (9-40)
  RANGE?           No argument.                                      (9-41)
  HTR?             No argument; Loop 1 heater output in percent.     (9-33)
  HTRST?           Heater error code 0-6 (11.9). 05 = open heater load.
  CSET             Control loop setup: input, units, ON/OFF, power-up
                   enable.  A 340 loop is DISABLED from the factory and
                   the heater stays off until CSET turns it on (6.2).
                   Replaces the 350's OUTMODE input field.            (9-31)
  CMODE            1 Manual PID, 2 Zone, 3 Open Loop, 4/5/6 AutoTune.
                   Replaces the 350's OUTMODE mode field.             (9-29)
  CLIMIT           Setpoint limit, slope limits, max current code and max
                   heater range for a loop.  The 340 has no TLIMIT; the
                   setpoint limit is the 340's over-temperature guard: the
                   loop output turns off when the reading reaches it. (9-28)
  MODE             1 local, 2 remote, 3 remote + local lockout.  The 350
                   uses 0/1/2, so "MODE 0" is invalid on a 340.       (9-38)
  INTYPE           <input>,<type> with 340 sensor codes 0-12 (Special,
                   Si diode, GaAlAs, Pt100 250 ohm, Pt100 500 ohm, Pt1000,
                   RhFe, Carbon-Glass, Cernox, RuOx, Ge, Capacitor,
                   Thermocouple).  Supplying units/coefficient/excitation/
                   range turns the input into type 0 (Special), so only the
                   type is ever sent from here.                       (9-34)
  DISPLAY / DISPFLD  DISPLAY <fields>,[contrast],[backlight];
                   DISPFLD <field>,<input>,<source 1-6>.  The 350's DFLD
                   does not exist.                                    (9-32)
  FILTER           <input>,<on>,<points>,<window %>.                  (9-33)
  ZONE             <loop>,<zone 1-10>,<top>,<P>,<I>,<D>,<mout>,<range>.
                   No input or ramp-rate fields (those are 350-only). (9-43)
  PID              P 0-1000, I 0-1000, D 0-1000 (spec 1.7.2).
  RAMP             0.1-100 K/min (spec 1.7.2).
  INNAME, HTRSET, TLIMIT, OUTMODE, DFLD  do not exist on a 340 and are not
                   sent.  Heater resistance is a CDISP field and max
                   current is a CLIMIT field on the 340.
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


def launch_curve_loader():
    """Open the sensor curve loader (340 / 350) in its own process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        loader_path = os.path.join(
            script_dir, "Sensor_Curve_Loader_L340_L350_GUI.py")
        if not os.path.exists(loader_path):
            messagebox.showerror(
                "File Not Found",
                f"Sensor Curve Loader not found at:\n{loader_path}\n\n"
                "Everything else in this window works normally.")
            return
        Process(target=run_script_process, args=(loader_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch the Sensor Curve Loader: {e}")


# ---------------------------------------------------------------------------
# ZONE DEFAULTS (shared wording with T_Control_L340_RangeControl_GUI.py)
# ---------------------------------------------------------------------------

# (upper bound K, P, I, D, heater range).  The first zone runs from 0 K up
# to its bound; a temperature above the last bound uses the last zone.
#
# 100-310 K is the lab's tested setting for the CCR (P 0.5, I 4, D 0,
# range 5).  Below that the heater range is dropped one step per zone and P
# is raised by about 3x per step (one range step is 10x in power, 3.16x in
# current, and the PID output is a percentage of full-scale current), which
# is the rule the lab uses: "reduce the range at low temperature, then
# increase P; same again further down".  Starting points, edit freely.
ZONE_DEFAULTS = (
    (20.0, 5.0, 4.0, 0.0, 3),
    (50.0, 1.5, 4.0, 0.0, 4),
    (100.0, 1.5, 4.0, 0.0, 4),
    (310.0, 0.5, 4.0, 0.0, 5),
)
ZONE_T_MIN = 3.0
ZONE_T_MAX = 310.0


def select_zone(zones, temperature):
    """Index of the zone a temperature falls in (no hysteresis).

    zones: sequence of (upper_bound, P, I, D, range), sorted ascending.
    The first zone whose upper bound is >= temperature wins; anything above
    the last bound uses the last zone.
    """
    if not zones:
        raise ValueError("zone table is empty")
    for idx, zone in enumerate(zones):
        if temperature <= zone[0]:
            return idx
    return len(zones) - 1


def generate_equal_zones(n_segments, t_min=ZONE_T_MIN, t_max=ZONE_T_MAX,
                         template=ZONE_DEFAULTS):
    """Split [t_min, t_max] into n equal segments, PID/range from template.

    Each generated segment takes the P, I, D and range of the template zone
    its upper bound falls in, so the default gains travel with the
    temperature they were chosen for.
    """
    n_segments = int(n_segments)
    if n_segments < 1:
        raise ValueError("at least one segment is required")
    if t_max <= t_min:
        raise ValueError("t_max must be above t_min")
    width = (t_max - t_min) / n_segments
    out = []
    for k in range(1, n_segments + 1):
        ub = t_min + width * k
        if k == n_segments:
            ub = t_max
        tpl = template[select_zone(template, ub)]
        out.append((round(ub, 3), tpl[1], tpl[2], tpl[3], tpl[4]))
    return out


# ---------------------------------------------------------------------------
# BACKEND: Lake Shore 340 Instrument Control
# ---------------------------------------------------------------------------

# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


class Lakeshore340Backend:
    """
    Backend for Lake Shore Model 340 communication.

    Disconnect is non-destructive: it only closes the VISA resource.
    No *RST, no heater off, no PID/range/setpoint changes on disconnect.
    """

    MODEL_TOKENS = ("MODEL340", "MODEL 340")

    # INTYPE <type> codes, manual printed 9-34.
    SENSOR_TYPES = {
        0: "Special",
        1: "Silicon Diode",
        2: "GaAlAs Diode",
        3: "Platinum 100 (250 ohm)",
        4: "Platinum 100 (500 ohm)",
        5: "Platinum 1000",
        6: "Rhodium Iron",
        7: "Carbon-Glass",
        8: "Cernox",
        9: "RuOx",
        10: "Germanium",
        11: "Capacitor",
        12: "Thermocouple",
    }

    # CMODE <mode> codes, printed 9-29.
    CONTROL_MODES = {
        1: "Manual PID",
        2: "Zone",
        3: "Open Loop",
        4: "AutoTune PID",
        5: "AutoTune PI",
        6: "AutoTune P",
    }

    # DISPFLD <source> codes, printed 9-32.
    DISPLAY_SOURCES = {
        1: "Kelvin",
        2: "Celsius",
        3: "Sensor Units",
        4: "Linear Data",
        5: "Minimum",
        6: "Maximum",
    }

    # MODE codes, printed 9-38 (NOT the 350's 0/1/2).
    INTERFACE_MODES = {
        1: "Local",
        2: "Remote",
        3: "Remote + Lockout",
    }

    # HTRST? codes, manual 11.9.
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
    MAX_CURRENT_CODES = {
        1: "0.25 A",
        2: "0.5 A",
        3: "1.0 A",
        4: "2.0 A",
        5: "User (CLIMI)",
    }

    # RDGST? bits, printed 9-41.
    RDGST_BITS = (
        (1, "invalid reading"),
        (2, "old reading"),
        (16, "temp underrange"),
        (32, "temp overrange"),
        (64, "units zero"),
        (128, "units overrange"),
    )

    def __init__(self):
        self.lakeshore = None
        self.rm = None
        self.idn = ""
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
        """Open the VISA session and query *IDN?.  Returns the IDN string."""
        if not self.rm:
            raise ConnectionError(
                "PyVISA ResourceManager not available. "
                "Install pyvisa and a VISA backend (NI-VISA or pyvisa-py).")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        self.idn = self.lakeshore.query('*IDN?').strip()
        return self.idn

    def is_model_340(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def disconnect(self):
        """Non-destructive disconnect: closes the VISA handle only."""
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
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        self.lakeshore.write(command)

    def _query(self, command):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        return self.lakeshore.query(command).strip()

    @staticmethod
    def _split(resp, n):
        parts = [p.strip() for p in resp.split(',')]
        if len(parts) < n:
            raise ValueError(f"expected {n} fields, got '{resp}'")
        return parts

    # -- PID (printed 9-40) --
    # PID <loop>,<P>,<I>,<D>;  P 0-1000, I 0-1000, D 0-1000 (spec 1.7.2)

    def set_pid(self, loop, p, i, d):
        if not (0 <= p <= 1000):
            raise ValueError(f"P must be 0-1000 on a Model 340, got {p}")
        if not (0 <= i <= 1000):
            raise ValueError(f"I must be 0-1000 on a Model 340, got {i}")
        if not (0 <= d <= 1000):
            raise ValueError(f"D must be 0-1000 on a Model 340, got {d}")
        cmd = f"PID {loop},{p},{i},{d}"
        self._write(cmd)
        return cmd

    def get_pid(self, loop):
        parts = self._split(self._query(f"PID? {loop}"), 3)
        return float(parts[0]), float(parts[1]), float(parts[2])

    # -- Setpoint and ramp (printed 9-40, 9-42) --

    def set_setpoint_immediate(self, loop, value):
        """RAMP off, then SETP so the change takes effect at once."""
        self._write(f"RAMP {loop},0,0")
        cmd = f"SETP {loop},{value}"
        self._write(cmd)
        return f"RAMP {loop},0,0; {cmd}"

    def set_setpoint_with_ramp(self, loop, value, rate_k_per_min):
        """RAMP on at <rate>, then SETP.  Rate 0.1-100 K/min on a 340.

        The 340 ramps from the CURRENT setpoint, not from the current
        temperature.  If the two differ by a lot, set the setpoint to the
        present temperature first ("Setpoint = current T" button).
        """
        if not (0.1 <= rate_k_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, "
                f"got {rate_k_per_min}")
        self._write(f"RAMP {loop},1,{rate_k_per_min}")
        cmd = f"SETP {loop},{value}"
        self._write(cmd)
        return f"RAMP {loop},1,{rate_k_per_min}; {cmd}"

    def get_setpoint(self, loop):
        return float(self._query(f"SETP? {loop}"))

    def get_ramp_status(self, loop):
        """RAMP? <loop> -> (on_off, rate)."""
        parts = self._split(self._query(f"RAMP? {loop}"), 2)
        return int(float(parts[0])), float(parts[1])

    def is_ramping(self, loop):
        return int(float(self._query(f"RAMPST? {loop}"))) == 1

    # -- Heater range (printed 9-40): Loop 1 only, no output argument --

    def set_range(self, range_value):
        if not (0 <= int(range_value) <= 5):
            raise ValueError(f"Range must be 0-5, got {range_value}")
        cmd = f"RANGE {int(range_value)}"
        self._write(cmd)
        return cmd

    def get_range(self):
        return int(float(self._query("RANGE?")))

    def get_heater_output(self):
        """HTR? -> Loop 1 heater output in percent (no argument)."""
        return float(self._query("HTR?"))

    def get_heater_status(self):
        """HTRST? -> (code, text)."""
        code = int(float(self._query("HTRST?")))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    # -- Control loop setup (CSET, printed 9-31) --
    # CSET <loop>,[<input>],[<units>],[<off/on>],[<powerup enable>]

    def set_control_loop(self, loop, input_ch, units=1, enabled=1,
                         powerup=None):
        input_ch = str(input_ch).strip().upper()
        if not input_ch:
            raise ValueError("control input is required")
        if units not in (1, 2, 3):
            raise ValueError("units must be 1 (K), 2 (C) or 3 (sensor)")
        cmd = f"CSET {loop},{input_ch},{units},{1 if enabled else 0}"
        if powerup is not None:
            cmd += f",{1 if powerup else 0}"
        self._write(cmd)
        return cmd

    def get_control_loop(self, loop):
        """CSET? <loop> -> dict(input, units, enabled, powerup)."""
        parts = self._split(self._query(f"CSET? {loop}"), 4)
        return {
            'input': parts[0],
            'units': int(float(parts[1])),
            'enabled': int(float(parts[2])),
            'powerup': int(float(parts[3])),
        }

    # -- Control mode (CMODE, printed 9-29) --

    def set_control_mode(self, loop, mode):
        if mode not in self.CONTROL_MODES:
            raise ValueError(f"CMODE mode must be 1-6, got {mode}")
        cmd = f"CMODE {loop},{mode}"
        self._write(cmd)
        return cmd

    def get_control_mode(self, loop):
        return int(float(self._query(f"CMODE? {loop}")))

    # -- Control limits (CLIMIT, printed 9-28) --
    # CLIMIT <loop>,<SP limit>,<pos slope>,<neg slope>,<max current>,<max range>

    def get_control_limits(self, loop):
        parts = self._split(self._query(f"CLIMIT? {loop}"), 5)
        return {
            'sp_limit': float(parts[0]),
            'pos_slope': float(parts[1]),
            'neg_slope': float(parts[2]),
            'max_current': int(float(parts[3])),
            'max_range': int(float(parts[4])),
        }

    def set_control_limits(self, loop, sp_limit=None, max_range=None,
                           pos_slope=None, neg_slope=None, max_current=None):
        """Read-modify-write so an unspecified field keeps its value."""
        cur = self.get_control_limits(loop)
        if sp_limit is not None:
            if sp_limit < 0:
                raise ValueError("setpoint limit cannot be negative")
            cur['sp_limit'] = float(sp_limit)
        if max_range is not None:
            if not (0 <= int(max_range) <= 5):
                raise ValueError("max range must be 0-5")
            cur['max_range'] = int(max_range)
        if pos_slope is not None:
            cur['pos_slope'] = float(pos_slope)
        if neg_slope is not None:
            cur['neg_slope'] = float(neg_slope)
        if max_current is not None:
            if int(max_current) not in self.MAX_CURRENT_CODES:
                raise ValueError("max current code must be 1-5")
            cur['max_current'] = int(max_current)
        cmd = (f"CLIMIT {loop},{cur['sp_limit']},{cur['pos_slope']},"
               f"{cur['neg_slope']},{cur['max_current']},{cur['max_range']}")
        self._write(cmd)
        return cmd

    # -- Readings (printed 9-35, 9-43, 9-41) --

    def get_temperature(self, channel):
        return float(self._query(f"KRDG? {channel}"))

    def get_sensor_reading(self, channel):
        return float(self._query(f"SRDG? {channel}"))

    def get_reading_status(self, channel):
        """RDGST? -> (code, text); text is '' for a good reading."""
        code = int(float(self._query(f"RDGST? {channel}")))
        names = [name for bit, name in self.RDGST_BITS if code & bit]
        return code, ", ".join(names)

    # -- Display (DISPLAY / DISPFLD, printed 9-32) --

    def set_display_fields(self, n_fields):
        if not (1 <= int(n_fields) <= 8):
            raise ValueError("number of display fields must be 1-8")
        cmd = f"DISPLAY {int(n_fields)}"
        self._write(cmd)
        return cmd

    def set_display_field(self, field, input_ch, source=1):
        if not (1 <= int(field) <= 8):
            raise ValueError("display field must be 1-8")
        if source not in self.DISPLAY_SOURCES:
            raise ValueError("display source must be 1-6")
        cmd = f"DISPFLD {int(field)},{input_ch},{source}"
        self._write(cmd)
        return cmd

    def get_display(self):
        """DISPLAY? -> raw '<fields>,<contrast>,<backlight>'."""
        return self._query("DISPLAY?")

    def get_display_field(self, field):
        return self._query(f"DISPFLD? {int(field)}")

    # -- Input configuration (INTYPE / INCRV, printed 9-33, 9-34) --

    def set_input_type(self, channel, sensor_type):
        """INTYPE <input>,<type>.  Only the type is sent: any other field
        turns the input into type 0 (Special)."""
        if sensor_type not in self.SENSOR_TYPES:
            raise ValueError(f"sensor type must be 0-12, got {sensor_type}")
        cmd = f"INTYPE {channel},{sensor_type}"
        self._write(cmd)
        return cmd

    def get_input_type(self, channel):
        """INTYPE? -> raw '<type>,<units>,<coefficient>,<excitation>,<range>'."""
        return self._query(f"INTYPE? {channel}")

    def set_input_curve(self, channel, curve):
        if not (0 <= int(curve) <= 60):
            raise ValueError("curve must be 0 (none), 1-20 standard, "
                             "21-60 user on a Model 340")
        cmd = f"INCRV {channel},{int(curve)}"
        self._write(cmd)
        return cmd

    def get_input_curve(self, channel):
        return int(float(self._query(f"INCRV? {channel}")))

    # -- Manual output (MOUT, printed 9-38) --

    def set_manual_output(self, loop, value):
        if not (0 <= value <= 100):
            raise ValueError(f"Manual output must be 0-100%, got {value}")
        cmd = f"MOUT {loop},{value}"
        self._write(cmd)
        return cmd

    def get_manual_output(self, loop):
        return float(self._query(f"MOUT? {loop}"))

    # -- Input filter (FILTER, printed 9-33) --

    def set_filter(self, channel, on_off, points=10, window=2):
        if not (2 <= int(points) <= 64):
            raise ValueError(f"Filter points must be 2-64, got {points}")
        if not (1 <= int(window) <= 10):
            raise ValueError(f"Filter window must be 1-10 %, got {window}")
        cmd = f"FILTER {channel},{1 if on_off else 0},{int(points)},{int(window)}"
        self._write(cmd)
        return cmd

    def get_filter(self, channel):
        return self._query(f"FILTER? {channel}")

    # -- Remote/Local interface mode (MODE, printed 9-38): 1/2/3 --

    def set_interface_mode(self, mode):
        if mode not in self.INTERFACE_MODES:
            raise ValueError(f"MODE must be 1, 2 or 3 on a Model 340, got {mode}")
        cmd = f"MODE {mode}"
        self._write(cmd)
        return cmd

    def get_interface_mode(self):
        return int(float(self._query("MODE?")))

    # -- Zone table (ZONE, printed 9-43) --
    # ZONE <loop>,<zone>,<top>,<P>,<I>,<D>,<mout>,<range>

    def set_zone(self, loop, zone, top, p, i, d, mout=0, range_val=0):
        if not (1 <= int(zone) <= 10):
            raise ValueError("zone number must be 1-10")
        cmd = (f"ZONE {loop},{int(zone)},{top},{p},{i},{d},"
               f"{mout},{int(range_val)}")
        self._write(cmd)
        return cmd

    def get_zone(self, loop, zone):
        return self._query(f"ZONE? {loop},{int(zone)}")

    # -- Common commands --

    def clear_status(self):
        self._write("*CLS")

    def reset(self):
        """*RST: sets controller parameters to power-up settings."""
        self._write("*RST")

    def get_idn(self):
        return self._query("*IDN?")

    # -- VISA resource discovery --

    def scan_resources(self):
        if not self.rm:
            return []
        return self.rm.list_resources()

    def identify_resources(self, addresses, timeout_ms=2000):
        """Send *IDN? to each address.  Returns {address: reply or 'ERROR: ..'}.

        Explicit, user-triggered: the same thing the GPIB scanner utility
        does.  Each session is closed again immediately.
        """
        out = {}
        if not self.rm:
            return out
        for addr in addresses:
            try:
                inst = self.rm.open_resource(addr)
                try:
                    inst.timeout = timeout_ms
                    inst.read_termination = '\n'
                    inst.write_termination = '\n'
                    out[addr] = inst.query('*IDN?').strip()
                finally:
                    inst.close()
            except Exception as e:
                out[addr] = f"ERROR: {e}"
        return out


def explain_visa_error(exc):
    """Plain-language hint for the VISA errors seen on the lab PCs."""
    text = str(exc)
    if 'VI_ERROR_ALLOC' in text:
        return ("VI_ERROR_ALLOC comes from the VISA driver before any "
                "command is sent: the VISA library cannot open a session on "
                "that GPIB interface. Most often the interface is a STALE entry "
                "cached by Keysight Connection Expert (an adapter that is no "
                "longer plugged in): list_resources() still reports it, and "
                "opening it fails. Remove the dead interface in Connection "
                "Expert and rescan; the live adapter usually is the other "
                "board (GPIB1::..). If Connection Expert itself cannot talk "
                "to the instrument either, PyVISA may be loading NI-VISA for a "
                "Keysight adapter: tick 'Keysight VISA as primary VISA' in "
                "Connection Expert settings, or set "
                "PYVISA_LIBRARY=C:\Windows\System32\ktvisa32.dll and restart "
                "PICA. Then use 'Identify' to see which address answers as "
                "MODEL340.")
    if 'VI_ERROR_TMO' in text:
        return ("Timeout: the address exists but nothing answered *IDN?. "
                "Check the 340 is powered, its IEEE address (Interface "
                "screen) matches, and no other programme holds the session.")
    if 'VI_ERROR_RSRC_NFOUND' in text or 'VI_ERROR_RSRC_BUSY' in text:
        return "The resource is missing or busy: rescan, close other programmes."
    return ""


# ---------------------------------------------------------------------------
# FRONTEND: Direct Control GUI
# ---------------------------------------------------------------------------

class DirectControlGUI:
    """GUI for the Lake Shore 340 Direct Control Utility."""

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Lakeshore 340 Direct Control Utility"

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

    LEFT_PANEL_WIDTH = 540

    PID_PRESETS = {
        'CCR 100-310 K (P=0.5, I=4, D=0, range 5)': (0.5, 4.0, 0),
        'CCR 20-100 K (P=1.5, I=4, D=0, range 4)': (1.5, 4.0, 0),
        'CCR below 20 K (P=5, I=4, D=0, range 3)': (5.0, 4.0, 0),
    }

    INPUT_CHOICES = ['A', 'B', 'C', 'D']

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x950")
        self.root.minsize(1200, 750)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = Lakeshore340Backend()
        self.logo_image = None
        self.is_connected = False
        self.polling_active = False
        self.resource_labels = {}   # combobox label -> VISA address

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # -----------------------------------------------------------------------
    # STYLES
    # -----------------------------------------------------------------------

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Connect.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Connect.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Disconnect.TButton', background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Disconnect.TButton',
                  background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK)
        style.configure('TCombobox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure('TSpinbox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)

    # -----------------------------------------------------------------------
    # WIDGET CREATION
    # -----------------------------------------------------------------------

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(header, text=self.PROGRAM_NAME, style='Header.TLabel',
                  font=font_title_main,
                  foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)
        ttk.Button(header, text="📈", command=launch_plotter_utility,
                   width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side='right', padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scroll_frame
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        canvas.bind('<Enter>',
                    lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        scroll_frame.grid_columnconfigure(0, weight=1)
        scroll_frame.grid_rowconfigure(99, weight=1)

        self._create_info_panel(scroll_frame, 0)
        self._create_connection_panel(scroll_frame, 1)
        self._create_control_loop_panel(scroll_frame, 2)
        self._create_pid_panel(scroll_frame, 3)
        self._create_setpoint_panel(scroll_frame, 4)
        self._create_range_panel(scroll_frame, 5)
        self._create_temp_zone_panel(scroll_frame, 6)
        self._create_climit_panel(scroll_frame, 7)
        self._create_display_panel(scroll_frame, 8)
        self._create_input_config_panel(scroll_frame, 9)
        self._create_manual_output_panel(scroll_frame, 10)
        self._create_advanced_panel(scroll_frame, 11)
        self._create_console_panel(scroll_frame, 99)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 90
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "assets", "LOGO",
                                     "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE / 2, LOGO_SIZE / 2,
                                         image=self.logo_image)
        except Exception:
            pass

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font, background=self.CLR_FRAME_BG).grid(
            row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font,
                  background=self.CLR_FRAME_BG).grid(
            row=1, column=1, padx=10, pady=(0, 5), sticky='nw')
        ttk.Label(frame,
                  text="Lake Shore Model 340 | Loop 1 heater, Loop 2 analog",
                  background=self.CLR_FRAME_BG).grid(
            row=2, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="VISA Address:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.visa_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.visa_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.connect_btn = ttk.Button(btn_frame, text="Connect",
                                      style='Connect.TButton',
                                      command=self._do_connect)
        self.connect_btn.grid(row=0, column=0, sticky='ew', padx=5)
        self.disconnect_btn = ttk.Button(btn_frame, text="Disconnect",
                                         style='Disconnect.TButton',
                                         state='disabled',
                                         command=self._do_disconnect)
        self.disconnect_btn.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Scan", command=self._scan_visa).grid(
            row=0, column=2, sticky='ew', padx=5)
        self.identify_btn = ttk.Button(btn_frame, text="Identify",
                                       command=self._identify_visa)
        self.identify_btn.grid(row=0, column=3, sticky='ew', padx=5)

        ttk.Label(frame,
                  text=("Scan lists addresses. Identify sends *IDN? to each\n"
                        "listed address (2 s timeout) and picks the MODEL340."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(
            row=2, column=0, columnspan=2, sticky='w', padx=10)

        self.status_label = ttk.Label(frame, text="● Not Connected",
                                      font=self.FONT_STATUS,
                                      foreground=self.CLR_STATUS_BAD,
                                      background=self.CLR_FRAME_BG)
        self.status_label.grid(row=3, column=0, columnspan=2, sticky='w',
                               padx=10, pady=(0, 5))

    def _create_control_loop_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Control Loop Setup (CSET / CMODE)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame,
                  text=("A 340 loop is DISABLED from the factory: the heater\n"
                        "stays off until the loop is enabled here, whatever\n"
                        "RANGE says. Control mode 1 = Manual PID."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(
            row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(5, 5))

        self.cset_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(row=1, column=0, sticky='w',
                                            padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.cset_loop_var, values=['1', '2'],
                     state='readonly', width=5).grid(
            row=1, column=1, sticky='w', padx=10, pady=5)

        self.cset_input_var = tk.StringVar(value='A')
        ttk.Label(frame, text="Control Input:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.cset_input_var,
                     values=self.INPUT_CHOICES, state='readonly',
                     width=5).grid(row=2, column=1, sticky='w', padx=10, pady=5)

        self.cset_enable_var = tk.StringVar(value='On')
        ttk.Label(frame, text="Loop Enable:").grid(
            row=3, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.cset_enable_var,
                     values=['On', 'Off'], state='readonly', width=5).grid(
            row=3, column=1, sticky='w', padx=10, pady=5)

        self.cmode_var = tk.StringVar(value='Manual PID')
        ttk.Label(frame, text="Control Mode:").grid(
            row=4, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.cmode_var,
                     values=list(Lakeshore340Backend.CONTROL_MODES.values()),
                     state='readonly').grid(
            row=4, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send Loop Setup",
                   command=self._send_cset).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read Loop Setup",
                   command=self._read_cset).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Send Control Mode",
                   command=self._send_cmode).grid(
            row=1, column=0, sticky='ew', padx=5, pady=(4, 0))
        ttk.Button(btn_frame, text="Read Control Mode",
                   command=self._read_cmode).grid(
            row=1, column=1, sticky='ew', padx=5, pady=(4, 0))

    def _create_pid_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='PID Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.pid_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(row=0, column=0, sticky='w',
                                            padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.pid_loop_var, values=['1', '2'],
                     state='readonly', width=5).grid(
            row=0, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(frame, text="Preset:").grid(row=1, column=0, sticky='w',
                                              padx=10, pady=5)
        self.pid_preset_var = tk.StringVar()
        pid_preset_cb = ttk.Combobox(
            frame, textvariable=self.pid_preset_var,
            values=list(self.PID_PRESETS.keys()) + ['Custom'],
            state='readonly')
        pid_preset_cb.grid(row=1, column=1, sticky='ew', padx=10, pady=5)
        pid_preset_cb.bind('<<ComboboxSelected>>', self._on_pid_preset_change)

        self.pid_p_entry = self._make_entry(frame, "P (0-1000)", "0.5", 2)
        self.pid_i_entry = self._make_entry(frame, "I (0-1000)", "4.0", 3)
        self.pid_d_entry = self._make_entry(frame, "D (0-1000 s)", "0", 4)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send PID", command=self._send_pid).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read PID", command=self._read_pid).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_setpoint_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Setpoint Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.setp_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(row=0, column=0, sticky='w',
                                            padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.setp_loop_var, values=['1', '2'],
                     state='readonly', width=5).grid(
            row=0, column=1, sticky='w', padx=10, pady=5)

        self.setp_entry = self._make_entry(frame, "Setpoint (K)", "300", 1)

        self.ramp_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Enable Ramp",
                        variable=self.ramp_enabled_var,
                        command=self._toggle_ramp_fields).grid(
            row=2, column=0, columnspan=2, sticky='w', padx=10, pady=2)

        self.ramp_rate_entry = self._make_entry(
            frame, "Ramp Rate (0.1-100 K/min)", "2.0", 3)
        self.ramp_rate_entry.config(state='disabled')

        ttk.Label(frame,
                  text=("A 340 ramps from the CURRENT SETPOINT, not from the\n"
                        "temperature. Use 'Setpoint = current T' first if the\n"
                        "old setpoint is far away."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(
            row=4, column=0, columnspan=2, sticky='w', padx=10)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send Setpoint",
                   command=self._send_setpoint).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read Setpoint",
                   command=self._read_setpoint).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Setpoint = current T (no ramp)",
                   command=self._setpoint_to_current).grid(
            row=1, column=0, columnspan=2, sticky='ew', padx=5, pady=(4, 0))

    def _create_range_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Heater Range (Loop 1)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame,
                  text=("RANGE has no loop number on a 340; it is Loop 1 only.\n"
                        "Full-scale power = range x heater ohms x max current\n"
                        "(CLIMIT). Each range step is 10x in power."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(
            row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(5, 5))

        ttk.Label(frame, text="Range:").grid(row=1, column=0, sticky='w',
                                             padx=10, pady=5)
        self.range_var = tk.StringVar(value='3')
        ttk.Combobox(frame, textvariable=self.range_var,
                     values=['0 (Off)', '1', '2', '3', '4', '5'],
                     state='readonly').grid(
            row=1, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send Range", command=self._send_range).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read Range", command=self._read_range).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="HEATER OFF (RANGE 0)",
                   command=self._heater_off).grid(
            row=1, column=0, columnspan=2, sticky='ew', padx=5, pady=(4, 0))

    def _create_temp_zone_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Temperature-Dependent PID Zones')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        ttk.Label(frame,
                  text=("One PID + heater range per temperature segment.\n"
                        "'Upper Bound' is the top of the segment; the first\n"
                        "segment starts at 0 K, the last also covers anything\n"
                        f"above it. Defaults span {ZONE_T_MIN:g}-{ZONE_T_MAX:g} K "
                        "for the CCR."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(
            row=0, column=0, sticky='w', padx=10, pady=(5, 5))

        sel = ttk.Frame(frame)
        sel.grid(row=1, column=0, sticky='ew', padx=10, pady=2)
        ttk.Label(sel, text="Loop:", background=self.CLR_FRAME_BG).pack(side='left')
        self.zone_loop_var = tk.StringVar(value='1')
        ttk.Combobox(sel, textvariable=self.zone_loop_var, values=['1', '2'],
                     state='readonly', width=4).pack(side='left', padx=5)
        ttk.Label(sel, text="Temp. Input:",
                  background=self.CLR_FRAME_BG).pack(side='left', padx=(10, 0))
        self.zone_input_var = tk.StringVar(value='A')
        ttk.Combobox(sel, textvariable=self.zone_input_var,
                     values=self.INPUT_CHOICES, state='readonly',
                     width=4).pack(side='left', padx=5)
        ttk.Label(sel, text="Segments:",
                  background=self.CLR_FRAME_BG).pack(side='left', padx=(10, 0))
        self.zone_segments_var = tk.IntVar(value=len(ZONE_DEFAULTS))
        ttk.Spinbox(sel, from_=1, to=10, width=4,
                    textvariable=self.zone_segments_var).pack(side='left', padx=5)
        ttk.Button(sel, text="Generate equal",
                   command=self._generate_zone_rows).pack(side='left', padx=5)

        self.zone_table = ttk.Frame(frame)
        self.zone_table.grid(row=2, column=0, sticky='ew', padx=10, pady=5)
        for col, h in enumerate(['Upper Bound (K)', 'P', 'I', 'D', 'Range']):
            ttk.Label(self.zone_table, text=h, background=self.CLR_FRAME_BG,
                      font=('Segoe UI', 9, 'bold')).grid(
                row=0, column=col, padx=4, pady=2)

        self.zone_rows = []
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)

        btns = ttk.Frame(frame)
        btns.grid(row=3, column=0, sticky='ew', padx=10, pady=5)
        btns.grid_columnconfigure((0, 1, 2), weight=1)
        ttk.Button(btns, text="+ Add Zone",
                   command=lambda: self._add_zone_row()).grid(
            row=0, column=0, sticky='ew', padx=5, pady=2)
        ttk.Button(btns, text="- Remove Zone",
                   command=self._remove_zone_row).grid(
            row=0, column=1, sticky='ew', padx=5, pady=2)
        ttk.Button(btns, text="Reset Defaults",
                   command=self._reset_zone_rows).grid(
            row=0, column=2, sticky='ew', padx=5, pady=2)
        ttk.Button(btns, text="Auto-Select PID/Range for Current Temp",
                   command=self._auto_select_zone).grid(
            row=1, column=0, columnspan=3, sticky='ew', padx=5, pady=2)
        ttk.Button(btns,
                   text="Write table to instrument (ZONE + CMODE 2, max 10)",
                   command=self._apply_zones).grid(
            row=2, column=0, columnspan=3, sticky='ew', padx=5, pady=2)

    def _add_zone_row(self, upper=ZONE_T_MAX, p=0.5, i=4.0, d=0.0, rng=5):
        r = len(self.zone_rows) + 1
        widgets = []
        entries = []
        for col, val, width in ((0, upper, 10), (1, p, 7), (2, i, 7), (3, d, 7)):
            e = ttk.Entry(self.zone_table, width=width, font=self.FONT_BASE)
            e.insert(0, f"{val:g}")
            e.grid(row=r, column=col, padx=4, pady=2)
            widgets.append(e)
            entries.append(e)
        rng_var = tk.StringVar(value=str(int(rng)))
        rng_cb = ttk.Combobox(self.zone_table, textvariable=rng_var,
                              values=['0', '1', '2', '3', '4', '5'],
                              state='readonly', width=5)
        rng_cb.grid(row=r, column=4, padx=4, pady=2)
        widgets.append(rng_cb)
        self.zone_rows.append({'ub': entries[0], 'p': entries[1],
                               'i': entries[2], 'd': entries[3],
                               'range': rng_var, 'widgets': widgets})

    def _remove_zone_row(self):
        if len(self.zone_rows) <= 1:
            self.log("At least one zone is required.")
            return
        row = self.zone_rows.pop()
        for w in row['widgets']:
            w.destroy()

    def _clear_zone_rows(self):
        while self.zone_rows:
            row = self.zone_rows.pop()
            for w in row['widgets']:
                w.destroy()

    def _reset_zone_rows(self):
        self._clear_zone_rows()
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)
        self.zone_segments_var.set(len(ZONE_DEFAULTS))
        self.log("Zone table reset to the CCR defaults.")

    def _generate_zone_rows(self):
        try:
            n = int(self.zone_segments_var.get())
            zones = generate_equal_zones(n)
        except (ValueError, tk.TclError) as e:
            self.log(f"ERROR: cannot generate segments: {e}")
            return
        self._clear_zone_rows()
        for z in zones:
            self._add_zone_row(*z)
        self.log(f"Generated {n} equal segment(s) from {ZONE_T_MIN:g} K to "
                 f"{ZONE_T_MAX:g} K with default PID/range per segment.")

    def _collect_zones(self):
        """Return zones sorted by upper bound as (ub, P, I, D, range), or None."""
        zones = []
        for r in self.zone_rows:
            try:
                ub = float(r['ub'].get())
                p = float(r['p'].get())
                i = float(r['i'].get())
                d = float(r['d'].get())
                rng = int(r['range'].get())
            except ValueError:
                self.log("ERROR: zone bounds and PID values must be numeric.")
                messagebox.showerror("Invalid Input",
                                     "All zone bounds and PID values must be numeric.")
                return None
            zones.append((ub, p, i, d, rng))
        zones.sort(key=lambda z: z[0])
        return zones

    def _apply_zones(self):
        """Write the zone table to the instrument and switch to Zone mode."""
        if not self._require_connection():
            return
        zones = self._collect_zones()
        if zones is None:
            return
        if len(zones) > 10:
            self.log("ERROR: the 340 zone table holds 10 zones; reduce the rows.")
            messagebox.showerror("Too many zones",
                                 "The Model 340 stores at most 10 zones.")
            return
        loop = int(self.zone_loop_var.get())
        self.log(f">>> Writing {len(zones)} zone(s) to Loop {loop}...")
        for idx, (ub, p, i, d, rng) in enumerate(zones, start=1):
            try:
                cmd = self.backend.set_zone(loop, idx, ub, p, i, d, 0, rng)
                self.log(f"    SENT: {cmd}")
            except Exception as e:
                self.log(f"    ERROR: {e}")
                messagebox.showerror("Command Failed", str(e))
                return
        try:
            cmd = self.backend.set_control_mode(loop, 2)
            self.log(f"    SENT: {cmd}  (Zone mode)")
            self.cmode_var.set('Zone')
        except Exception as e:
            self.log(f"    ERROR enabling zone mode: {e}")
            return
        self.log("    OK: zone table written; loop in Zone mode. The 340 "
                 "picks the zone from the SETPOINT, not the reading.")

    def _auto_select_zone(self):
        """Read the current temperature and send the matching zone's PID + range."""
        if not self._require_connection():
            return
        zones = self._collect_zones()
        if zones is None:
            return
        loop = int(self.zone_loop_var.get())
        input_ch = self.zone_input_var.get()
        try:
            temp = self.backend.get_temperature(input_ch)
        except Exception as e:
            self.log(f"ERROR reading temperature: {e}")
            messagebox.showerror("Read Failed", str(e))
            return
        idx = select_zone(zones, temp)
        ub, p, i, d, rng = zones[idx]
        self.log(f">>> Input {input_ch} = {temp:.3f} K -> zone {idx + 1} "
                 f"(<= {ub:g} K): P={p:g}, I={i:g}, D={d:g}, range {rng}")
        try:
            cmd1 = self.backend.set_pid(loop, p, i, d)
            self.log(f"    SENT: {cmd1}")
            if loop == 1:
                cmd2 = self.backend.set_range(rng)
                self.log(f"    SENT: {cmd2}")
            else:
                self.log("    Range not sent: RANGE is Loop 1 only on a 340.")
            self.log("    OK: PID and range applied for the current temperature.")
        except Exception as e:
            self.log(f"    ERROR: {e}")
            messagebox.showerror("Command Failed", str(e))

    def _create_climit_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(
            parent, text='⚠ Control Limits (CLIMIT): Setpoint Limit / Max Range')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="THIS IS NOT THE CONTROL SETPOINT!",
                  background=self.CLR_FRAME_BG, foreground=self.CLR_ACCENT_RED,
                  font=('Segoe UI', 10, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(5, 0))
        ttk.Label(frame,
                  text=("The 340 has no TLIMIT. Its guard is the loop setpoint\n"
                        "limit: the setpoint cannot be set above it and the\n"
                        "loop output turns OFF when the reading reaches it.\n"
                        "Max Range caps RANGE for Loop 1. Other CLIMIT fields\n"
                        "(slopes, max current) are read and re-sent unchanged."),
                  background=self.CLR_FRAME_BG, foreground=self.CLR_ACCENT_RED,
                  font=('Segoe UI', 9), justify='left').grid(
            row=1, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 5))

        self.climit_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(row=2, column=0, sticky='w',
                                            padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.climit_loop_var,
                     values=['1', '2'], state='readonly', width=5).grid(
            row=2, column=1, sticky='w', padx=10, pady=5)
        self.climit_entry = self._make_entry(frame, "Setpoint Limit (K)", "325", 3)
        ttk.Label(frame, text="Max Heater Range:").grid(
            row=4, column=0, sticky='w', padx=10, pady=5)
        self.climit_range_var = tk.StringVar(value='5')
        ttk.Combobox(frame, textvariable=self.climit_range_var,
                     values=['0', '1', '2', '3', '4', '5'], state='readonly',
                     width=5).grid(row=4, column=1, sticky='w', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send Limits",
                   command=self._send_climit).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read Limits",
                   command=self._read_climit).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_display_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Display Settings (DISPLAY / DISPFLD)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Fields shown:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.display_nfields_var = tk.StringVar(value='2')
        ttk.Combobox(frame, textvariable=self.display_nfields_var,
                     values=[str(n) for n in range(1, 9)], state='readonly',
                     width=5).grid(row=0, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(frame, text="Field number:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        self.display_field_var = tk.StringVar(value='1')
        ttk.Combobox(frame, textvariable=self.display_field_var,
                     values=[str(n) for n in range(1, 9)], state='readonly',
                     width=5).grid(row=1, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(frame, text="Input:").grid(row=2, column=0, sticky='w',
                                             padx=10, pady=5)
        self.display_input_var = tk.StringVar(value='A')
        ttk.Combobox(frame, textvariable=self.display_input_var,
                     values=self.INPUT_CHOICES, state='readonly',
                     width=5).grid(row=2, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(frame, text="Source:").grid(row=3, column=0, sticky='w',
                                              padx=10, pady=5)
        self.display_source_var = tk.StringVar(value='Kelvin')
        ttk.Combobox(frame, textvariable=self.display_source_var,
                     values=list(Lakeshore340Backend.DISPLAY_SOURCES.values()),
                     state='readonly').grid(
            row=3, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send Display",
                   command=self._send_display).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read Display",
                   command=self._read_display).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_input_config_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Input Configuration (INTYPE / INCRV)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.intype_ch_var = tk.StringVar(value='A')
        ttk.Label(frame, text="Input:").grid(row=0, column=0, sticky='w',
                                             padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.intype_ch_var,
                     values=self.INPUT_CHOICES, state='readonly').grid(
            row=0, column=1, sticky='ew', padx=10, pady=5)

        self.intype_sensor_var = tk.StringVar(value='Silicon Diode')
        ttk.Label(frame, text="Sensor Type:").grid(
            row=1, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.intype_sensor_var,
                     values=list(Lakeshore340Backend.SENSOR_TYPES.values()),
                     state='readonly').grid(
            row=1, column=1, sticky='ew', padx=10, pady=5)

        self.incrv_entry = self._make_entry(
            frame, "Curve number (0, 1-20, 21-60)", "1", 2)

        ttk.Label(frame,
                  text=("Only the type is sent (INTYPE <input>,<type>): the\n"
                        "340 derives units, coefficient, excitation and range\n"
                        "from it. Sending them would make the input 'Special'."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(
            row=3, column=0, columnspan=2, sticky='w', padx=10)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send Type",
                   command=self._send_input_config).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read Type",
                   command=self._read_input_config).grid(
            row=0, column=1, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Send Curve",
                   command=self._send_input_curve).grid(
            row=1, column=0, sticky='ew', padx=5, pady=(4, 0))
        ttk.Button(btn_frame, text="Read Curve",
                   command=self._read_input_curve).grid(
            row=1, column=1, sticky='ew', padx=5, pady=(4, 0))
        ttk.Button(frame, text="Sensor Curve Loader…",
                   command=launch_curve_loader).grid(
            row=5, column=0, columnspan=2, sticky='ew', padx=10, pady=(5, 8))

    def _create_manual_output_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Manual Output (MOUT)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.mout_loop_var = tk.StringVar(value='1')
        ttk.Label(frame, text="Loop:").grid(row=0, column=0, sticky='w',
                                            padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.mout_loop_var, values=['1', '2'],
                     state='readonly', width=5).grid(
            row=0, column=1, sticky='w', padx=10, pady=5)
        self.mout_entry = self._make_entry(frame, "Manual Output (%)", "0", 1)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Send MOUT", command=self._send_mout).grid(
            row=0, column=0, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Read MOUT", command=self._read_mout).grid(
            row=0, column=1, sticky='ew', padx=5)

    def _create_advanced_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Advanced Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Interface (MODE 1/2/3):").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.iface_mode_var = tk.StringVar(value='Remote')
        ttk.Combobox(frame, textvariable=self.iface_mode_var,
                     values=list(Lakeshore340Backend.INTERFACE_MODES.values()),
                     state='readonly').grid(
            row=0, column=1, sticky='ew', padx=10, pady=5)
        ttk.Button(frame, text="Send Interface Mode",
                   command=self._send_iface_mode).grid(
            row=1, column=0, columnspan=2, sticky='ew', padx=10, pady=2)

        ttk.Label(frame, text="Filter Input:").grid(
            row=2, column=0, sticky='w', padx=10, pady=5)
        self.filter_ch_var = tk.StringVar(value='A')
        ttk.Combobox(frame, textvariable=self.filter_ch_var,
                     values=self.INPUT_CHOICES, state='readonly',
                     width=5).grid(row=2, column=1, sticky='w', padx=10, pady=5)
        self.filter_on_var = tk.StringVar(value='On')
        ttk.Label(frame, text="Filter:").grid(row=3, column=0, sticky='w',
                                              padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.filter_on_var,
                     values=['On', 'Off'], state='readonly', width=5).grid(
            row=3, column=1, sticky='w', padx=10, pady=5)
        self.filter_points_var = tk.StringVar(value='10')
        ttk.Label(frame, text="Filter Points (2-64):").grid(
            row=4, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.filter_points_var,
                     values=['2', '4', '8', '10', '16', '32', '64'],
                     state='readonly').grid(
            row=4, column=1, sticky='ew', padx=10, pady=5)
        self.filter_window_var = tk.StringVar(value='2')
        ttk.Label(frame, text="Filter Window (1-10 %):").grid(
            row=5, column=0, sticky='w', padx=10, pady=5)
        ttk.Combobox(frame, textvariable=self.filter_window_var,
                     values=[str(n) for n in range(1, 11)],
                     state='readonly').grid(
            row=5, column=1, sticky='ew', padx=10, pady=5)
        ttk.Button(frame, text="Send Filter", command=self._send_filter).grid(
            row=6, column=0, columnspan=2, sticky='ew', padx=10, pady=2)
        ttk.Button(frame, text="Read Filter", command=self._read_filter).grid(
            row=7, column=0, columnspan=2, sticky='ew', padx=10, pady=2)

        ttk.Button(frame, text="⚠ Reset to power-up settings (*RST)",
                   command=self._factory_reset).grid(
            row=8, column=0, columnspan=2, sticky='ew', padx=10, pady=(8, 2))

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word',
            borderwidth=0, height=8)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.log("Console initialized. Scan for instruments, then connect.")

    # -- Right panel (live status monitor) --

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=2)
        panel.grid_columnconfigure(1, weight=1)

        container = ttk.LabelFrame(panel, text='Live Instrument Status')
        container.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(container, bg=self.CLR_FRAME_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient='vertical', command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left', fill='both', expand=True, padx=5, pady=5)
        scrollbar.pack(side='right', fill='y')

        self.status_labels = {}
        row = 0

        def _section(text):
            nonlocal row
            ttk.Label(scroll_frame, text=text, font=self.FONT_TITLE,
                      background=self.CLR_FRAME_BG).grid(
                row=row, column=0, columnspan=2, sticky='w', pady=(12, 5))
            row += 1

        def _item(text, key, font=None):
            nonlocal row
            ttk.Label(scroll_frame, text=text,
                      background=self.CLR_FRAME_BG).grid(
                row=row, column=0, sticky='w', padx=20, pady=2)
            lbl = ttk.Label(scroll_frame, text="---",
                            font=font or self.FONT_BASE,
                            background=self.CLR_FRAME_BG)
            lbl.grid(row=row, column=1, sticky='w', padx=20, pady=2)
            self.status_labels[key] = lbl
            row += 1

        _section("── Temperatures ──")
        for ch in ['A', 'B']:
            _item(f"Input {ch} (K):", f'temp_{ch}', self.FONT_STATUS)
        self.poll_cd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(scroll_frame,
                        text="Also poll C and D (3462 option card only)",
                        variable=self.poll_cd_var).grid(
            row=row, column=0, columnspan=2, sticky='w', padx=20, pady=2)
        row += 1
        for ch in ['C', 'D']:
            _item(f"Input {ch} (K):", f'temp_{ch}', self.FONT_STATUS)

        _section("── Heater (Loop 1) ──")
        _item("Heater Output (%):", 'heater_1', self.FONT_STATUS)
        _item("Heater Range:", 'range_1')
        _item("Heater Status (HTRST?):", 'htrst')

        _section("── Control Parameters ──")
        for text, key in [
            ("Setpoint 1 (K):", 'setpoint_1'),
            ("Setpoint 2 (K):", 'setpoint_2'),
            ("PID 1 (P, I, D):", 'pid_1'),
            ("PID 2 (P, I, D):", 'pid_2'),
            ("Ramp 1 (on/off, rate):", 'ramp_1'),
            ("Ramp 2 (on/off, rate):", 'ramp_2'),
            ("Loop 1 (input, units, enabled):", 'cset_1'),
            ("Control Mode 1:", 'cmode_1'),
            ("Limits 1 (SP limit, max I, max range):", 'climit_1'),
            ("Interface Mode:", 'iface_mode'),
        ]:
            _item(text, key)

        row += 1
        ttk.Separator(scroll_frame).grid(row=row, column=0, columnspan=2,
                                         sticky='ew', padx=20, pady=10)
        row += 1
        self.poll_btn = ttk.Button(scroll_frame, text="Start Polling",
                                   command=self._toggle_polling)
        self.poll_btn.grid(row=row, column=0, columnspan=2, sticky='ew',
                           padx=20, pady=5)

        # --- Notes Panel ---
        notes_frame = ttk.LabelFrame(panel, text="Notes & Guides")
        notes_frame.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        notes_frame.grid_columnconfigure(0, weight=1)
        notes_frame.grid_rowconfigure(0, weight=1)
        notes = scrolledtext.ScrolledText(
            notes_frame, wrap='word', bg=self.CLR_FRAME_BG,
            fg=self.CLR_TEXT_DARK, font=('Consolas', 10), borderwidth=0,
            relief='flat')
        notes.pack(fill='both', expand=True, padx=5, pady=5)

        def _build_table(headers, rows, widths):
            def fmt(cells):
                return "│ " + " │ ".join(
                    c.ljust(w) for c, w in zip(cells, widths)) + " │"
            seg = ["─" * (w + 2) for w in widths]
            lines = ["┌" + "┬".join(seg) + "┐", fmt(headers),
                     "├" + "┼".join(seg) + "┤"]
            lines += [fmt(r) for r in rows]
            lines.append("└" + "┴".join(seg) + "┘")
            return "\n".join(lines)

        zone_rows = [(f"{'0' if k == 0 else f'{ZONE_DEFAULTS[k - 1][0]:g}'}-{ub:g} K",
                      f"{p:g}", f"{i:g}", f"{d:g}", str(rng))
                     for k, (ub, p, i, d, rng) in enumerate(ZONE_DEFAULTS)]
        zone_table = _build_table(("Segment", "P", "I", "D", "Range"),
                                  zone_rows, (12, 5, 4, 3, 5))
        power_rows = [
            ("5", "100 W", "25 W", "6.25 W", "1.56 W"),
            ("4", "10 W", "2.5 W", "625 mW", "156 mW"),
            ("3", "1 W", "250 mW", "62.5 mW", "15.6 mW"),
            ("2", "100 mW", "25 mW", "6.25 mW", "1.56 mW"),
            ("1", "10 mW", "2.5 mW", "625 uW", "156 uW"),
        ]
        power_table = _build_table(("Range", "2 A", "1 A", "0.5 A", "0.25 A"),
                                   power_rows, (5, 7, 7, 7, 7))

        notes.insert('1.0', (
            "Model 340 facts used by this window (User's Manual):\n"
            "  • Inputs A and B; C and D only with the 3462 card\n"
            "  • Loop 1 drives the heater; Loop 2 drives Analog Out 2\n"
            "  • Loops are DISABLED at the factory: CSET <loop>,<in>,1,1\n"
            "    enables one. Until then RANGE changes nothing.\n"
            "  • RANGE 0-5 and HTR? take no loop number (Loop 1 only)\n"
            "  • PID: P 0-1000, I 0-1000, D 0-1000 s\n"
            "  • Setpoint ramp 0.1-100 K/min, from the current SETPOINT\n"
            "  • MODE 1 local, 2 remote, 3 remote+lockout (not 0/1/2)\n"
            "  • No TLIMIT: CLIMIT setpoint limit turns the loop off\n"
            "    when the reading reaches it\n"
            "  • HTRST? 05 = open heater load, 06 = load under 10 ohm\n"
            "  • *RST = power-up settings. Curves need CRVSAV to stick\n\n"
            "Loop 1 full-scale power, 25 ohm heater (Table 1-6):\n\n"
            + power_table +
            "\n\nMax current is the CLIMIT 4th field (1=0.25 A .. 4=2 A).\n"
            "Read Limits shows which one this 340 is set to.\n\n"
            "Default PID zones for the CCR (edit in the zone panel):\n\n"
            + zone_table +
            "\n\n100-310 K is the tested setting. Below that the range is\n"
            "dropped one step per zone and P raised ~3x per step, which\n"
            "keeps the loop's power response similar (one range step is\n"
            "10x in power, 3.16x in current; the PID output is a % of\n"
            "full-scale current). Starting points, not measurements.\n\n"
            "'Auto-Select' sends the PID + range for the reading now.\n"
            "'Write table' stores up to 10 zones in the instrument and\n"
            "switches the loop to Zone mode, which the 340 then applies\n"
            "by SETPOINT. The Ramp Control (L340) module does the\n"
            "software version live, by measured temperature.\n"
        ))
        notes.config(state='disabled')

    # -----------------------------------------------------------------------
    # HELPERS
    # -----------------------------------------------------------------------

    def _make_entry(self, parent, label_text, default, row):
        ttk.Label(parent, text=f"{label_text}:").grid(
            row=row, column=0, sticky='w', padx=10, pady=5)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=5)
        entry.insert(0, default)
        return entry

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _require_connection(self):
        if not self.is_connected or not self.backend.is_connected:
            self.log("ERROR: Not connected to instrument.")
            messagebox.showerror("Not Connected",
                                 "Please connect to the instrument first.")
            return False
        return True

    def _safe_command(self, description, func, *args, **kwargs):
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
            self.log(f"    ERROR: {traceback.format_exc()}")
            messagebox.showerror("Command Failed", str(e))
        return None

    def _selected_address(self):
        label = self.visa_cb.get()
        return self.resource_labels.get(label, label)

    # -----------------------------------------------------------------------
    # CONNECTION HANDLERS
    # -----------------------------------------------------------------------

    def _scan_visa(self):
        if not self.backend.rm:
            self.log("ERROR: PyVISA library not available.")
            messagebox.showerror("PyVISA Missing",
                                 "PyVISA is not installed. "
                                 "Run: pip install pyvisa")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = list(self.backend.scan_resources())
        except Exception as e:
            self.log(f"Scan error: {e}")
            return
        if resources:
            self.log(f"Found {len(resources)} resource(s):")
            for r in resources:
                self.log(f"  {r}")
            self.resource_labels = {r: r for r in resources}
            self.visa_cb['values'] = resources
            # The lab's 340 is set to address 19 (3 Sep 2026); 12 is
            # shared by the 350, the Cryocon and the 6221. ::19:: is
            # pre-selected when present; Identify confirms by *IDN?.
            preferred = [r for r in resources if LAKESHORE340_ADDRESS_HINT in r]
            if preferred:
                self.visa_cb.set(preferred[0])
                self.log(f"Pre-selected {preferred[0]} (lab address 19). "
                         "Press Identify to confirm it answers as MODEL340.")
            elif len(resources) == 1:
                self.visa_cb.set(resources[0])
            else:
                self.visa_cb.set('')
                self.log("Several addresses. Press Identify to find the "
                         "MODEL340, or pick it yourself.")
        else:
            self.log("No VISA instruments found.")

    def _identify_visa(self):
        """Send *IDN? to every listed address, label them, select the 340."""
        if not self.backend.rm:
            self.log("ERROR: PyVISA library not available.")
            return
        if self.is_connected:
            self.log("Identify is disabled while connected.")
            return
        addresses = list(self.resource_labels.values())
        if not addresses:
            self.log("Nothing to identify: press Scan first.")
            return
        self.log(f"Identifying {len(addresses)} address(es) with *IDN? "
                 "(2 s timeout each)...")
        self.root.update_idletasks()
        replies = self.backend.identify_resources(addresses)
        labels = {}
        chosen = None
        for addr in addresses:
            reply = replies.get(addr, "no reply")
            if reply.startswith("ERROR:"):
                hint = explain_visa_error(reply)
                short = reply.split('):')[0] + ')' if '):' in reply else reply
                self.log(f"  {addr}: {short}")
                if hint:
                    self.log(f"      {hint}")
                labels[f"{addr}  (no answer)"] = addr
            else:
                self.log(f"  {addr}: {reply}")
                labels[f"{addr}  ({reply[:32]})"] = addr
                up = reply.upper().replace(' ', '')
                if 'MODEL340' in up and chosen is None:
                    chosen = f"{addr}  ({reply[:32]})"
        self.resource_labels = labels
        self.visa_cb['values'] = list(labels.keys())
        if chosen:
            self.visa_cb.set(chosen)
            self.log(f"Selected the Model 340 at {labels[chosen]}.")
        else:
            self.visa_cb.set('')
            self.log("No address answered as a Model 340.")

    def _do_connect(self):
        visa_addr = self._selected_address()
        if not visa_addr:
            self.log("ERROR: No VISA address selected.")
            messagebox.showerror("No Address",
                                 "Please scan and select a VISA address.")
            return
        try:
            self.log(f"Connecting to {visa_addr}...")
            idn = self.backend.connect(visa_addr)
            self.log(f"Connected: {idn}")
            if not self.backend.is_model_340():
                self.backend.disconnect()
                raise RuntimeError(
                    f"'{visa_addr}' answered '{idn}', which is not a Lake "
                    "Shore Model 340. Refusing: this window sends 340-only "
                    "commands (RANGE n, CSET, CMODE, MODE 1/2/3).")
            self.is_connected = True
            self.status_label.config(text="● Connected",
                                     foreground=self.CLR_STATUS_OK)
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
            self.identify_btn.config(state='disabled')
            self.visa_cb.config(state='disabled')
            self._start_polling()
        except Exception as e:
            self.log(f"CONNECT ERROR: {traceback.format_exc()}")
            hint = explain_visa_error(e)
            if hint:
                self.log(f"HINT: {hint}")
            messagebox.showerror(
                "Connection Failed",
                f"Could not connect to {visa_addr}:\n{e}"
                + (f"\n\n{hint}" if hint else ""))

    def _do_disconnect(self):
        self._stop_polling()
        self.log("Disconnecting (non-destructive)...")
        self.backend.disconnect()
        self.is_connected = False
        self.log("Disconnected. Instrument continues operating with its "
                 "current settings.")
        self.status_label.config(text="● Not Connected",
                                 foreground=self.CLR_STATUS_BAD)
        self.connect_btn.config(state='normal')
        self.disconnect_btn.config(state='disabled')
        self.identify_btn.config(state='normal')
        self.visa_cb.config(state='readonly')
        for key in self.status_labels:
            self.status_labels[key].config(text="---")

    # -----------------------------------------------------------------------
    # CONTROL LOOP HANDLERS
    # -----------------------------------------------------------------------

    def _send_cset(self):
        loop = int(self.cset_loop_var.get())
        input_ch = self.cset_input_var.get()
        enabled = 1 if self.cset_enable_var.get() == 'On' else 0
        self._safe_command(
            f"Set Loop {loop}: input {input_ch}, kelvin, "
            f"{'enabled' if enabled else 'DISABLED'}",
            self.backend.set_control_loop, loop, input_ch, 1, enabled)

    def _read_cset(self):
        if not self._require_connection():
            return
        try:
            loop = int(self.cset_loop_var.get())
            info = self.backend.get_control_loop(loop)
            units = {1: 'K', 2: 'C', 3: 'sensor'}.get(info['units'], '?')
            self.log(f"Read Loop {loop}: input {info['input']}, units {units}, "
                     f"{'enabled' if info['enabled'] else 'DISABLED'}, "
                     f"power-up {'on' if info['powerup'] else 'off'}")
            if info['input'] in self.INPUT_CHOICES:
                self.cset_input_var.set(info['input'])
            self.cset_enable_var.set('On' if info['enabled'] else 'Off')
        except Exception as e:
            self.log(f"ERROR reading loop setup: {e}")
            messagebox.showerror("Read Failed", str(e))

    def _send_cmode(self):
        loop = int(self.cset_loop_var.get())
        name = self.cmode_var.get()
        code = next((c for c, n in Lakeshore340Backend.CONTROL_MODES.items()
                     if n == name), None)
        if code is None:
            self.log("ERROR: unknown control mode.")
            return
        self._safe_command(f"Set Control Mode (Loop {loop}): {name}",
                           self.backend.set_control_mode, loop, code)

    def _read_cmode(self):
        if not self._require_connection():
            return
        try:
            loop = int(self.cset_loop_var.get())
            code = self.backend.get_control_mode(loop)
            name = Lakeshore340Backend.CONTROL_MODES.get(code, f"Unknown({code})")
            self.log(f"Read Control Mode (Loop {loop}): {code} = {name}")
            if name in Lakeshore340Backend.CONTROL_MODES.values():
                self.cmode_var.set(name)
        except Exception as e:
            self.log(f"ERROR reading control mode: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # PID HANDLERS
    # -----------------------------------------------------------------------

    def _on_pid_preset_change(self, event=None):
        preset = self.pid_preset_var.get()
        if preset in self.PID_PRESETS:
            p, i, d = self.PID_PRESETS[preset]
            for entry, val in ((self.pid_p_entry, p), (self.pid_i_entry, i),
                               (self.pid_d_entry, d)):
                entry.delete(0, 'end')
                entry.insert(0, str(val))

    def _send_pid(self):
        try:
            loop = int(self.pid_loop_var.get())
            p = float(self.pid_p_entry.get())
            i = float(self.pid_i_entry.get())
            d = float(self.pid_d_entry.get())
        except ValueError:
            self.log("ERROR: Invalid PID values.")
            messagebox.showerror("Invalid Input", "P, I, D must be numeric values.")
            return
        self._safe_command(f"Set PID (Loop {loop}): P={p}, I={i}, D={d}",
                           self.backend.set_pid, loop, p, i, d)

    def _read_pid(self):
        if not self._require_connection():
            return
        try:
            loop = int(self.pid_loop_var.get())
            p, i, d = self.backend.get_pid(loop)
            self.log(f"Read PID (Loop {loop}): P={p}, I={i}, D={d}")
            for entry, val in ((self.pid_p_entry, p), (self.pid_i_entry, i),
                               (self.pid_d_entry, d)):
                entry.delete(0, 'end')
                entry.insert(0, str(val))
            self.pid_preset_var.set('Custom')
        except Exception as e:
            self.log(f"ERROR reading PID: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # SETPOINT HANDLERS
    # -----------------------------------------------------------------------

    def _toggle_ramp_fields(self):
        self.ramp_rate_entry.config(
            state='normal' if self.ramp_enabled_var.get() else 'disabled')

    def _send_setpoint(self):
        try:
            loop = int(self.setp_loop_var.get())
            setpoint = float(self.setp_entry.get())
        except ValueError:
            self.log("ERROR: Invalid setpoint value.")
            messagebox.showerror("Invalid Input", "Setpoint must be a number.")
            return
        if self.ramp_enabled_var.get():
            try:
                rate = float(self.ramp_rate_entry.get())
            except ValueError:
                self.log("ERROR: Invalid ramp rate.")
                messagebox.showerror("Invalid Input", "Ramp rate must be a number.")
                return
            self._safe_command(
                f"Set Setpoint (Loop {loop}): {setpoint} K with ramp {rate} K/min",
                self.backend.set_setpoint_with_ramp, loop, setpoint, rate)
        else:
            self._safe_command(
                f"Set Setpoint (Loop {loop}): {setpoint} K (immediate, no ramp)",
                self.backend.set_setpoint_immediate, loop, setpoint)

    def _setpoint_to_current(self):
        """Ramp off and setpoint = reading of the loop's control input."""
        if not self._require_connection():
            return
        loop = int(self.setp_loop_var.get())
        try:
            info = self.backend.get_control_loop(loop)
            temp = self.backend.get_temperature(info['input'])
        except Exception as e:
            self.log(f"ERROR reading loop input: {e}")
            messagebox.showerror("Read Failed", str(e))
            return
        self._safe_command(
            f"Setpoint (Loop {loop}) = current input {info['input']} "
            f"reading {temp:.3f} K, ramp off",
            self.backend.set_setpoint_immediate, loop, f"{temp:.3f}")
        self.setp_entry.delete(0, 'end')
        self.setp_entry.insert(0, f"{temp:.3f}")

    def _read_setpoint(self):
        if not self._require_connection():
            return
        try:
            loop = int(self.setp_loop_var.get())
            sp = self.backend.get_setpoint(loop)
            on_off, rate = self.backend.get_ramp_status(loop)
            self.log(f"Read Setpoint (Loop {loop}): {sp} K; ramp "
                     f"{'ON' if on_off else 'OFF'}, rate={rate} K/min")
            self.setp_entry.delete(0, 'end')
            self.setp_entry.insert(0, str(sp))
            self.ramp_enabled_var.set(bool(on_off))
            self.ramp_rate_entry.config(state='normal' if on_off else 'disabled')
            if on_off:
                self.ramp_rate_entry.delete(0, 'end')
                self.ramp_rate_entry.insert(0, str(rate))
        except Exception as e:
            self.log(f"ERROR reading setpoint: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # RANGE HANDLERS
    # -----------------------------------------------------------------------

    def _send_range(self):
        try:
            range_val = int(self.range_var.get().split()[0])
        except ValueError:
            self.log("ERROR: Invalid range value.")
            return
        self._safe_command(f"Set Heater Range (Loop 1): {range_val}",
                           self.backend.set_range, range_val)

    def _heater_off(self):
        self._safe_command("HEATER OFF (RANGE 0)", self.backend.set_range, 0)
        self.range_var.set('0 (Off)')

    def _read_range(self):
        if not self._require_connection():
            return
        try:
            r = self.backend.get_range()
            self.log(f"Read Heater Range (Loop 1): {r}")
            self.range_var.set('0 (Off)' if r == 0 else str(r))
        except Exception as e:
            self.log(f"ERROR reading range: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # CLIMIT HANDLERS
    # -----------------------------------------------------------------------

    def _send_climit(self):
        loop = int(self.climit_loop_var.get())
        try:
            limit = float(self.climit_entry.get())
            max_range = int(self.climit_range_var.get())
        except ValueError:
            self.log("ERROR: Invalid limit value.")
            messagebox.showerror("Invalid Input", "Setpoint limit must be a number.")
            return
        if not messagebox.askyesno(
                "⚠ Control Limits",
                f"Set Loop {loop} setpoint limit to {limit} K and max heater "
                f"range to {max_range}?\n\nThis is NOT the control setpoint. "
                f"The setpoint can no longer go above {limit} K and the loop "
                f"output turns OFF when the reading reaches {limit} K.\n\n"
                "Continue?"):
            self.log("Control limit change cancelled by user.")
            return
        self._safe_command(
            f"Set Limits (Loop {loop}): SP limit {limit} K, max range {max_range}",
            self.backend.set_control_limits, loop, limit, max_range)

    def _read_climit(self):
        if not self._require_connection():
            return
        try:
            loop = int(self.climit_loop_var.get())
            lim = self.backend.get_control_limits(loop)
            cur = Lakeshore340Backend.MAX_CURRENT_CODES.get(
                lim['max_current'], f"code {lim['max_current']}")
            self.log(f"Read Limits (Loop {loop}): SP limit {lim['sp_limit']} K, "
                     f"slopes +{lim['pos_slope']}/-{lim['neg_slope']} %, "
                     f"max current {cur}, max range {lim['max_range']}")
            self.climit_entry.delete(0, 'end')
            self.climit_entry.insert(0, str(lim['sp_limit']))
            self.climit_range_var.set(str(lim['max_range']))
        except Exception as e:
            self.log(f"ERROR reading limits: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # DISPLAY HANDLERS
    # -----------------------------------------------------------------------

    def _send_display(self):
        n_fields = int(self.display_nfields_var.get())
        field = int(self.display_field_var.get())
        input_ch = self.display_input_var.get()
        source = next((c for c, n in Lakeshore340Backend.DISPLAY_SOURCES.items()
                       if n == self.display_source_var.get()), 1)
        if field > n_fields:
            self.log(f"Field {field} is beyond the {n_fields} field(s) shown; "
                     "it is stored but not visible.")

        def _both():
            c1 = self.backend.set_display_fields(n_fields)
            c2 = self.backend.set_display_field(field, input_ch, source)
            return f"{c1}; {c2}"

        self._safe_command(
            f"Set Display: {n_fields} field(s); field {field} = input "
            f"{input_ch}, {self.display_source_var.get()}", _both)

    def _read_display(self):
        if not self._require_connection():
            return
        try:
            resp = self.backend.get_display()
            self.log(f"Read Display (fields, contrast, backlight): {resp}")
            field = int(self.display_field_var.get())
            fresp = self.backend.get_display_field(field)
            self.log(f"Read Display Field {field} (input, source): {fresp}")
        except Exception as e:
            self.log(f"ERROR reading display: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # INPUT CONFIG HANDLERS
    # -----------------------------------------------------------------------

    def _send_input_config(self):
        channel = self.intype_ch_var.get()
        name = self.intype_sensor_var.get()
        code = next((c for c, n in Lakeshore340Backend.SENSOR_TYPES.items()
                     if n == name), None)
        if code is None:
            self.log("ERROR: Unknown sensor type.")
            return
        if not messagebox.askyesno(
                "Change Input Type",
                f"Set Input {channel} sensor type to {name} (INTYPE {channel},"
                f"{code})?\n\nThis changes excitation and range for that input. "
                "The curve (INCRV) may reset to 0 if it does not match the "
                "new type.\n\nContinue?"):
            self.log("Input type change cancelled by user.")
            return
        self._safe_command(f"Set Input Type ({channel}): {name}",
                           self.backend.set_input_type, channel, code)

    def _read_input_config(self):
        if not self._require_connection():
            return
        try:
            channel = self.intype_ch_var.get()
            resp = self.backend.get_input_type(channel)
            parts = resp.split(',')
            code = int(float(parts[0]))
            name = Lakeshore340Backend.SENSOR_TYPES.get(code, f"Unknown({code})")
            self.log(f"Read Input Type ({channel}): {resp} -> {name}")
            if name in Lakeshore340Backend.SENSOR_TYPES.values():
                self.intype_sensor_var.set(name)
        except Exception as e:
            self.log(f"ERROR reading input type: {e}")
            messagebox.showerror("Read Failed", str(e))

    def _send_input_curve(self):
        channel = self.intype_ch_var.get()
        try:
            curve = int(self.incrv_entry.get())
        except ValueError:
            self.log("ERROR: curve number must be an integer.")
            return
        self._safe_command(f"Set Input Curve ({channel}): {curve}",
                           self.backend.set_input_curve, channel, curve)
        if self.is_connected:
            try:
                back = self.backend.get_input_curve(channel)
                if back != curve:
                    self.log(f"    WARNING: INCRV? reads back {back}, not {curve}. "
                             "The 340 resets a curve that does not match the "
                             "input type to 0.")
            except Exception:
                pass

    def _read_input_curve(self):
        if not self._require_connection():
            return
        try:
            channel = self.intype_ch_var.get()
            curve = self.backend.get_input_curve(channel)
            self.log(f"Read Input Curve ({channel}): {curve}")
            self.incrv_entry.delete(0, 'end')
            self.incrv_entry.insert(0, str(curve))
        except Exception as e:
            self.log(f"ERROR reading curve: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # MANUAL OUTPUT HANDLERS
    # -----------------------------------------------------------------------

    def _send_mout(self):
        try:
            loop = int(self.mout_loop_var.get())
            value = float(self.mout_entry.get())
        except ValueError:
            self.log("ERROR: Invalid manual output value.")
            messagebox.showerror("Invalid Input", "Manual output must be 0-100.")
            return
        if value > 50 and not messagebox.askyesno(
                "Confirm High Output",
                f"Manual output of {value}% is above 50%. This may cause "
                "rapid heating.\n\nContinue?"):
            self.log("Manual output cancelled by user.")
            return
        self._safe_command(f"Set Manual Output (Loop {loop}): {value}%",
                           self.backend.set_manual_output, loop, value)

    def _read_mout(self):
        if not self._require_connection():
            return
        try:
            loop = int(self.mout_loop_var.get())
            val = self.backend.get_manual_output(loop)
            self.log(f"Read Manual Output (Loop {loop}): {val}%")
            self.mout_entry.delete(0, 'end')
            self.mout_entry.insert(0, str(val))
        except Exception as e:
            self.log(f"ERROR reading manual output: {e}")
            messagebox.showerror("Read Failed", str(e))

    # -----------------------------------------------------------------------
    # ADVANCED HANDLERS
    # -----------------------------------------------------------------------

    def _send_iface_mode(self):
        name = self.iface_mode_var.get()
        mode = next((c for c, n in Lakeshore340Backend.INTERFACE_MODES.items()
                     if n == name), 2)
        self._safe_command(f"Set Interface Mode: {name} (MODE {mode})",
                           self.backend.set_interface_mode, mode)

    def _send_filter(self):
        channel = self.filter_ch_var.get()
        on_off = 1 if self.filter_on_var.get() == 'On' else 0
        points = int(self.filter_points_var.get())
        window = int(self.filter_window_var.get())
        self._safe_command(
            f"Set Filter ({channel}): {'On' if on_off else 'Off'}, "
            f"{points} points, {window}% window",
            self.backend.set_filter, channel, on_off, points, window)

    def _read_filter(self):
        if not self._require_connection():
            return
        try:
            channel = self.filter_ch_var.get()
            self.log(f"Read Filter ({channel}) (on, points, window): "
                     f"{self.backend.get_filter(channel)}")
        except Exception as e:
            self.log(f"ERROR reading filter: {e}")
            messagebox.showerror("Read Failed", str(e))

    def _factory_reset(self):
        if not self._require_connection():
            return
        if not messagebox.askyesno(
                "⚠ DANGER: Reset",
                "*RST sets the 340's controller parameters to power-up "
                "settings: loops off, setpoints, ramps, PID and ranges to "
                "their power-up values.\n\nAre you sure?"):
            self.log("Reset cancelled.")
            return
        if not messagebox.askyesno("Final Confirmation",
                                   "Last chance. Reset the instrument?"):
            self.log("Reset cancelled.")
            return
        self._safe_command("RESET (*RST)", self.backend.reset)

    # -----------------------------------------------------------------------
    # POLLING / LIVE STATUS
    # -----------------------------------------------------------------------

    def _toggle_polling(self):
        if self.polling_active:
            self._stop_polling()
        else:
            self._start_polling()

    def _start_polling(self):
        if not self._require_connection():
            return
        self.polling_active = True
        self.poll_btn.config(text="Stop Polling")
        self.log("Live status polling started (1 s interval).")
        self._poll_loop()

    def _stop_polling(self):
        self.polling_active = False
        self.poll_btn.config(text="Start Polling")
        if self.is_connected:
            self.log("Live status polling stopped.")

    def _poll_loop(self):
        if not self.polling_active or not self.is_connected:
            return
        b = self.backend
        try:
            channels = ['A', 'B'] + (['C', 'D'] if self.poll_cd_var.get() else [])
            for ch in channels:
                try:
                    temp = b.get_temperature(ch)
                    code, text = b.get_reading_status(ch)
                    self.status_labels[f'temp_{ch}'].config(
                        text=f"{temp:.3f} K" + (f"  ({text})" if text else ""))
                except Exception:
                    self.status_labels[f'temp_{ch}'].config(text="Error")
            try:
                self.status_labels['heater_1'].config(
                    text=f"{b.get_heater_output():.1f} %")
            except Exception:
                self.status_labels['heater_1'].config(text="Error")
            try:
                r = b.get_range()
                self.status_labels['range_1'].config(text="Off" if r == 0 else str(r))
            except Exception:
                pass
            try:
                code, text = b.get_heater_status()
                self.status_labels['htrst'].config(text=f"{code:02d} {text}")
            except Exception:
                pass
            for loop in (1, 2):
                try:
                    self.status_labels[f'setpoint_{loop}'].config(
                        text=f"{b.get_setpoint(loop):.3f} K")
                except Exception:
                    pass
                try:
                    p, i, d = b.get_pid(loop)
                    self.status_labels[f'pid_{loop}'].config(
                        text=f"P={p}, I={i}, D={d}")
                except Exception:
                    pass
                try:
                    on_off, rate = b.get_ramp_status(loop)
                    self.status_labels[f'ramp_{loop}'].config(
                        text=f"{'ON' if on_off else 'OFF'}, {rate} K/min")
                except Exception:
                    pass
            try:
                info = b.get_control_loop(1)
                units = {1: 'K', 2: 'C', 3: 'sensor'}.get(info['units'], '?')
                self.status_labels['cset_1'].config(
                    text=f"{info['input']}, {units}, "
                         f"{'enabled' if info['enabled'] else 'DISABLED'}")
            except Exception:
                pass
            try:
                code = b.get_control_mode(1)
                self.status_labels['cmode_1'].config(
                    text=Lakeshore340Backend.CONTROL_MODES.get(code, f"Unknown({code})"))
            except Exception:
                pass
            try:
                lim = b.get_control_limits(1)
                cur = Lakeshore340Backend.MAX_CURRENT_CODES.get(
                    lim['max_current'], f"code {lim['max_current']}")
                self.status_labels['climit_1'].config(
                    text=f"{lim['sp_limit']:g} K, {cur}, range {lim['max_range']}")
            except Exception:
                pass
            try:
                code = b.get_interface_mode()
                self.status_labels['iface_mode'].config(
                    text=Lakeshore340Backend.INTERFACE_MODES.get(code, f"Unknown({code})"))
            except Exception:
                pass
        except Exception as e:
            self.log(f"Polling error: {e}")

        if self.polling_active:
            self.root.after(1000, self._poll_loop)

    # -----------------------------------------------------------------------
    # WINDOW CLOSE
    # -----------------------------------------------------------------------

    def _on_closing(self):
        if self.is_connected:
            if messagebox.askyesno(
                    "Exit",
                    "You are still connected to the instrument.\n\n"
                    "Disconnecting is non-destructive: the instrument keeps "
                    "all settings and continues operating.\n\n"
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
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\nPlease run:\n  pip install pyvisa\n\n"
            "and install a VISA runtime (NI-VISA or Keysight IO Libraries).")
        root.destroy()
    else:
        root = tk.Tk()
        app = DirectControlGUI(root)
        root.mainloop()
