"""
Module:             Temprature_Scan_L340_E4980A_GUI.py
Purpose:             GUI module for Temperature-Dependent Dielectric
                     Measurement (Keysight E4980A + Lakeshore 340).
                     Port of Temprature_Scan_E4980A_GUI.py (Lakeshore 350)
                     to the Model 340 command set.
Original Authors:    Prathamesh Deshmukh (template programs)
Integrated by:       AI-assisted merge per design specification
Version:             V: 1.1

What is different from the Model 350 version, and why
-----------------------------------------------------
  * No *RST at start.  On a 340 "*RST sets controller parameters to
    power-up settings" (340 manual, printed 9-24): it resets the control
    loop, setpoint and ramp.  Only *CLS is sent.  Because nothing resets
    the ramp any more, the ramp is explicitly switched off (RAMP 1,0,0)
    before the stabilization phase sends its direct setpoint.
  * HTRSET does not exist on a 340.  Instead the control loop is
    enabled at start with CSET 1,<input>,1,1 (printed 9-31; a 340 loop is
    DISABLED from the factory and the heater stays off until CSET turns
    it on), put in Manual PID mode with CMODE 1,1 (9-29), and both are
    read back and verified.  CLIMIT? 1 (9-28) is read and logged: a
    Start or End temperature above the 340's setpoint limit, or the
    heater range 5 above its max-range field, is refused before the
    heater is ever turned on (the 340 has no TLIMIT).
  * RANGE takes no output number on a 340: "RANGE <0-5>" / "RANGE?"
    (printed 9-40/9-41).  Every range write is read back with RANGE? and
    an unexpected readback raises.  The 'off/low/medium/high' names of
    the 350 version are kept and map to 0/2/4/5 exactly as before.
  * HTR? takes no argument on a 340 and reports Loop 1 in percent
    (printed 9-33).
  * The 340 ramps from the CURRENT SETPOINT, not from the temperature
    (RAMP, printed 9-40).  The ramp phase therefore pins the setpoint to
    the present temperature with the ramp off, then enables the ramp at
    the requested rate (0.1-100 K/min on a 340) and sends the end
    temperature.  Heater range is set BEFORE the ramp, as the brief asks.
  * RDGST? <input> (printed 9-41) is read with every temperature sample.
    A non-zero status (invalid, old, under/over range, units zero/over)
    is logged with the sample; such a sample never satisfies the
    stabilization test and is reported per frequency point.
  * HTRST? (heater error, 0 ok ... 5 open load, 6 load < 10 ohm) is read
    every poll and logged once on every change; a non-zero code logs,
    beeps and marks the console, no dialog.
  * The control/sensor input is selectable (A or B on a base Model 340;
    C and D only with the 3462 option card); the 350 version fixed 'A'.
  * The identity-aware scan accepts an address only if *IDN? contains
    MODEL340 (case-insensitive, spaces ignored); Start refuses anything
    else.  The lab's 340 answers at IEEE address 19 (::19::).
  * Stop / close = RAMP 1,0,0 + RANGE 0 exactly where the 350 version
    turned the heater off (close_instruments and the safety kill).
  MODE (9-38), INTYPE (9-34) and ZONE (9-43) are not sent by this module;
  the 350 version did not send their counterparts either.

Model 340 commands used (User's Manual, Chapter 9):
  *IDN?, *CLS, CSET 1,<in>,1,1 / CSET? 1, CMODE 1,1 / CMODE? 1, CLIMIT? 1,
  RAMP 1,<on>,<rate> (0.1-100 K/min), SETP 1,<K>, RANGE <0-5> / RANGE?,
  HTR?, HTRST?, KRDG? <in>, RDGST? <in>
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
from collections import deque
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
            script_dir, "..", "utils", "PlotterUtil_GUI.py")
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
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
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
# BACKEND: LAKESHORE 340  (Model 340 command set, see header docstring)
# ===============================================================================

# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


def is_model_340_idn(idn):
    """True when an *IDN? reply names a Model 340 (spaces/case ignored)."""
    return "MODEL340" in str(idn).upper().replace(' ', '')


class Lakeshore340_Backend:
    """A class to control the Lakeshore Model 340 Temperature Controller.

    Loop 1 only.  RANGE / HTR? / HTRST? take no output number on a 340.
    No *RST is ever sent (it would reset loop, setpoint and ramp).
    """

    MODEL_TOKENS = ("MODEL340", "MODEL 340")
    RANGE_NAMES = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
    HEATER_ERRORS = {
        0: "No error",
        1: "Power supply over voltage",
        2: "Power supply under voltage",
        3: "Output DAC error",
        4: "Current limit DAC error",
        5: "OPEN HEATER LOAD",
        6: "Heater load < 10 ohm",
    }
    MAX_CURRENT_CODES = {1: "0.25 A", 2: "0.5 A", 3: "1.0 A", 4: "2.0 A",
                         5: "User (CLIMI)"}
    # RDGST? bit weights, Model 340 manual printed 9-41.
    RDGST_BITS = (
        (1, "invalid reading"), (2, "old reading"), (16, "temp underrange"),
        (32, "temp overrange"), (64, "units zero"), (128, "units overrange"),
    )

    def __init__(self, visa_address):
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        # The 340 answers with <CR><LF> and EOI by default; '\n' as read
        # terminator is what the lab's 340 modules use and it works.
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.idn = self.instrument.query('*IDN?').strip()
        print(f"Lakeshore Connected: {self.idn}")

    def is_model_340(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def _query(self, cmd):
        return self.instrument.query(cmd).strip()

    def clear_status(self):
        """*CLS only.  No *RST on a 340 (printed 9-24)."""
        self.instrument.write('*CLS')
        time.sleep(0.2)

    # -- loop setup (CSET 9-31, CMODE 9-29, CLIMIT 9-28) --

    def prepare_loop(self, control_input):
        """*CLS; enable Loop 1 on <input> in kelvin; Manual PID; ramp off.

        Returns (CSET? dict, CMODE? code, CLIMIT? dict) for logging.
        RAMP 1,0,0 replaces the ramp-off side effect the 350 version got
        from *RST, so the stabilization setpoint is applied at once.
        """
        self.clear_status()
        self.instrument.write(f'CSET 1,{control_input},1,1')
        self.instrument.write('CMODE 1,1')
        time.sleep(0.2)
        cset = self.get_control_loop(1)
        if (cset['input'].upper() != str(control_input).upper()
                or not cset['enabled']):
            raise RuntimeError(
                f"CSET 1,{control_input},1,1 did not stick: CSET? 1 reads "
                f"{cset}. Check the front panel (Remote/Local) and retry.")
        cmode = int(float(self._query('CMODE? 1')))
        if cmode != 1:
            raise RuntimeError(f"CMODE 1,1 did not stick: CMODE? 1 = {cmode}.")
        self.instrument.write('RAMP 1,0,0')
        time.sleep(0.2)
        return cset, cmode, self.get_control_limits(1)

    def get_control_loop(self, loop=1):
        parts = [p.strip() for p in self._query(f'CSET? {loop}').split(',')]
        if len(parts) < 4:
            raise ValueError(f"unexpected CSET? reply '{','.join(parts)}'")
        return {'input': parts[0], 'units': int(float(parts[1])),
                'enabled': int(float(parts[2])), 'powerup': int(float(parts[3]))}

    def get_control_limits(self, loop=1):
        parts = [p.strip() for p in self._query(f'CLIMIT? {loop}').split(',')]
        if len(parts) < 5:
            raise ValueError(f"unexpected CLIMIT? reply '{','.join(parts)}'")
        return {'sp_limit': float(parts[0]), 'pos_slope': float(parts[1]),
                'neg_slope': float(parts[2]), 'max_current': int(float(parts[3])),
                'max_range': int(float(parts[4]))}

    # -- ramp / setpoint (RAMP, SETP printed 9-40, 9-42) --

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        """Configures the instrument's internal ramp generator (0.1-100 K/min)."""
        if ramp_on and not (0.1 <= rate_k_per_min <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate_k_per_min}")
        self.instrument.write(
            f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)

    def set_setpoint(self, output, temperature_k):
        self.instrument.write(f'SETP {output},{temperature_k}')

    def start_ramp(self, target, rate, current_temperature):
        """Setpoint = now (ramp off), then ramp on and target.

        The 340 ramps from the current setpoint; pinning it to the present
        temperature first makes the ramp start from where the sample is.
        """
        if not (0.1 <= rate <= 100):
            raise ValueError(
                f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self.instrument.write('RAMP 1,0,0')
        self.instrument.write(f'SETP 1,{current_temperature:.3f}')
        time.sleep(0.2)
        self.instrument.write(f'RAMP 1,1,{rate}')
        self.instrument.write(f'SETP 1,{target}')

    # -- heater range (RANGE printed 9-40/9-41): no output number --

    @classmethod
    def range_code(cls, heater_range):
        """'off'/'low'/'medium'/'high' (as the 350 version) or 0-5."""
        try:
            code = int(heater_range)
        except (TypeError, ValueError):
            code = cls.RANGE_NAMES.get(str(heater_range).lower())
        if code is None or not (0 <= code <= 5):
            raise ValueError(f"Invalid heater range: {heater_range!r}")
        return code

    def set_heater_range(self, heater_range):
        """RANGE <0-5>, verified with RANGE?."""
        code = self.range_code(heater_range)
        self.instrument.write(f'RANGE {code}')
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != code:
            raise RuntimeError(
                f"RANGE {code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        return int(float(self._query('RANGE?')))

    def heater_off(self):
        """RAMP off + RANGE 0: the 340 equivalent of the 350 'heater off'."""
        self.instrument.write('RAMP 1,0,0')
        self.set_heater_range('off')

    # -- readings --

    def get_temperature(self, sensor):
        return float(self._query(f'KRDG? {sensor}'))

    def get_reading_status(self, sensor):
        """RDGST? <input> -> (code, text); text is '' for a good reading."""
        code = int(float(self._query(f'RDGST? {sensor}')))
        names = [n for bit, n in self.RDGST_BITS if code & bit]
        if code and not names:
            names = [f"unknown bit(s) {code}"]
        return code, ", ".join(names)

    def get_heater_output(self):
        """HTR? -> Loop 1 heater output in percent (no argument on a 340)."""
        return float(self._query('HTR?'))

    def get_heater_status(self):
        """HTRST? -> (code, text)."""
        code = int(float(self._query('HTRST?')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    def close(self):
        if self.instrument:
            try:
                self.heater_off()
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
        self.instrument.query("*OPC?")  # added for robustness on LAN
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
    """Manages the Lakeshore 340 and the Keysight E4980A together."""

    def __init__(self):
        self.lakeshore = None
        self.lcr = LCR_Backend()
        self.params = {}
        self.SAFETY_KILL_TEMP_K = 350.0  # Hard safety ceiling
        self.limits = {}                 # CLIMIT? 1 readback at start
        self.startup_notes = []          # log lines for the GUI console

    def initialize_instruments(self, parameters):
        self.params = parameters
        self.startup_notes = []
        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore340_Backend(parameters['lakeshore_visa'])
        if not self.lakeshore.is_model_340():
            idn = self.lakeshore.idn
            self.lakeshore.instrument.close()
            self.lakeshore = None
            raise RuntimeError(
                f"'{parameters['lakeshore_visa']}' answered '{idn}', which is "
                "not a Lake Shore Model 340. Refusing to send 340-only "
                "commands (CSET, RANGE n) to it. Pick the right address.")

        # No *RST (would reset loop/setpoint/ramp on a 340). CSET enables
        # Loop 1 on the chosen input, CMODE 1,1 = Manual PID, CLIMIT? is
        # the 340's over-temperature guard (no TLIMIT on a 340).
        sensor = parameters.get('sensor', 'A')
        cset, cmode, limits = self.lakeshore.prepare_loop(sensor)
        self.limits = limits
        cur = self.lakeshore.MAX_CURRENT_CODES.get(
            limits['max_current'], f"code {limits['max_current']}")
        self.startup_notes.append(
            f"Lakeshore 340: {self.lakeshore.idn}")
        self.startup_notes.append(
            f"Loop 1 enabled on input {cset['input']} (kelvin), Manual PID "
            f"(CMODE? 1 = {cmode}), ramp off. CLIMIT? 1: setpoint <= "
            f"{limits['sp_limit']:g} K, max current {cur}, "
            f"max range {limits['max_range']}.")
        too_hot = [k for k in ('start_temp', 'end_temp')
                   if parameters[k] > limits['sp_limit']]
        if too_hot:
            raise ValueError(
                f"{', '.join(too_hot)} above the 340's setpoint limit of "
                f"{limits['sp_limit']:g} K (CLIMIT? 1). Lower the target or "
                "raise the limit in the L340 Direct Control module first.")
        high_code = self.lakeshore.range_code('high')
        if high_code > limits['max_range']:
            raise ValueError(
                f"This program ramps with heater range {high_code} ('high'), "
                f"but the 340's CLIMIT max range is {limits['max_range']}. "
                "Raise it in the L340 Direct Control module first.")
        code, text = self.lakeshore.get_heater_status()
        if code != 0:
            raise RuntimeError(
                f"Heater error HTRST? {code:02d}: {text}. Fix the heater "
                "circuit before starting.")
        # LCR_Backend.initialize_instrument expects keys:
        #   lcr_visa, ac_bias, dc_bias, aper, alc_enabled,
        #   corr_enabled, cable_len
        self.lcr.initialize_instrument(parameters)

    def check_safety_kill(self, temperature_k):
        if temperature_k >= self.SAFETY_KILL_TEMP_K:
            for attempt in range(3):
                try:
                    self.lakeshore.heater_off()   # RAMP 1,0,0 + RANGE 0
                    break
                except Exception as e:
                    print(f"Kill attempt {attempt+1} failed: {e}")
                    time.sleep(0.5)
            print(f"!!! SAFETY KILL: T={temperature_k:.3f} K. Heater OFF.")
            return True
        return False

    def measure_frequency_sweep(self, frequencies, delay, stop_event=None):
        """
        Appendix A.1 — CRITICAL:
        Reads Lakeshore temperature INSIDE the frequency loop so every
        single data point is bound to the exact temperature at which it
        was measured.  No cycle-average temperature is computed.
        """
        sensor = self.params.get('sensor', 'A')
        htr = self.lakeshore.get_heater_output()        # HTR? (no argument)
        htr_status = self.lakeshore.get_heater_status()  # HTRST? once per cycle
        points = []
        tstatus = []          # (RDGST? code, text) parallel to points
        last_valid_temp = None
        killed = False
        for f in frequencies:
            if stop_event is not None and stop_event.is_set():
                break                     # abort mid-sweep, cleanly
            temp = self.lakeshore.get_temperature(sensor)  # T for THIS point
            rdg_code, rdg_text = self.lakeshore.get_reading_status(sensor)

            # Issue #2: per-point safety kill check
            if temp >= self.SAFETY_KILL_TEMP_K:
                killed = self.check_safety_kill(temp)
                points.append((temp, f, float('nan'), float('nan'), -1))
                tstatus.append((rdg_code, rdg_text))
                break

            R, X, status = self.lcr.perform_measurement(f, delay)
            points.append((temp, f, R, X, status))
            tstatus.append((rdg_code, rdg_text))
            if rdg_code == 0:
                last_valid_temp = temp
        return {'heater': htr, 'heater_status': htr_status,
                'points': points, 'tstatus': tstatus,
                'last_valid_temp': last_valid_temp, 'killed': killed}

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
    Combines Lakeshore 340 temperature control with E4980A multi-frequency
    LCR measurement.
    """

    PROGRAM_VERSION = "1.1"
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

    # --- Robust logo path finding (fixes missing logo issue) ---
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        # Try going up 3 levels first (as in R-T template)
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "..", "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
        if not os.path.exists(LOGO_FILE_PATH):
            # Fallback to 1 level up (as in Freq-scan template)
            LOGO_FILE_PATH = os.path.join(
                SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

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

    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    # ------------------------------------------------------------------
    def __init__(self, root):
        self.root = root
        self.root.title(
            "E4980A & L340: Dielectric vs. Temperature (T-Control)")
        self.root.geometry("1600x980")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 880)

        # --- State flags ---
        self.is_running = False
        self.is_stabilizing = False
        self._stopping = False          # re-entrancy guard for stop
        self._close_after_stop = False  # destroy window once worker exits
        self.start_time = None
        self._last_draw_time = 0.0
        self._redraw_interval = 0.25   # seconds; redraw at most ~4×/sec

        # --- Stop Event (Issue #1 Fix) ---
        self.stop_event = threading.Event()

        # --- Backend ---
        self.backend = Combined_Backend()
        atexit.register(self.backend.close_instruments)

        # --- File / frequency state ---
        self.file_location_path = ""
        self.frequencies = []
        self.freq_filepaths = {}
        self.plot_freq = None

        # --- Data storage (Appendix A.4: keyed per frequency, bounded) ---
        self.data_storage = {
            'time': deque(maxlen=10000),
            'temperature': deque(maxlen=10000),
            'cp': {},   # {freq: {'T': deque, 'v': deque}}
            'g':  {},   # {freq: {'T': deque, 'v': deque}}
        }

        # --- UI variables ---
        # Y-scale mode: 'auto' uses decade-snapped log when the data
        # spans >= 1 decade and linear otherwise; 'log'/'linear' force it.
        self.y_scale_var = tk.StringVar(value="auto")
        self.logo_image = None

        # Decade log autoscale state (LabVIEW-style): current snapped
        # y-limits per axis key ("cp" / "g")
        self._decade_ylims = {}

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

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # --- Left panel (scrollable) ---
        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK,
                        highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container,
                                  orient="vertical",
                                  command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame,
                             anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame).pack(
            fill='x', padx=10, pady=5)
        self.create_input_frame(scrollable_frame).pack(
            fill='x', padx=10, pady=5)
        self.create_console_frame(scrollable_frame).pack(
            fill='both', expand=True, padx=10, pady=5)

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

    # ------------------------------------------------------------------
    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        Label(header_frame,
              text="E4980A & L340: Dielectric vs. Temperature (T-Control)",
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
            "Instruments: Lakeshore 340, Keysight E4980A\n"
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
        self.aper_combobox.set("LONG")
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

        # --- Lakeshore control/sensor input (A, B; C, D need the 3462 card) ---
        Label(frame, text="Lakeshore 340 Input (CSET / KRDG?):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.sensor_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=["A", "B", "C", "D"])
        self.sensor_combobox.set("A")
        self.sensor_combobox.grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
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

        # --- Y-scale selector ---
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        scale_bar = tk.Frame(top_bar, bg=self.CLR_GRAPH_BG)
        scale_bar.pack(side='right', padx=5)
        tk.Label(scale_bar, text="Y scale:",
                 bg=self.CLR_GRAPH_BG).pack(side='left')
        for text, val in (("Auto", "auto"), ("Log", "log"),
                          ("Linear", "linear")):
            ttk.Radiobutton(scale_bar, text=text, value=val,
                            variable=self.y_scale_var,
                            command=self._on_y_scale_change).pack(
                side='left', padx=(8, 0))

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
        Cs_double_prime = D * Cs  # Fix Issue #7: Physically correct series loss

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
            self.stop_event.clear()  # Issue #1: Clear stop event
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.scan_button.config(state='disabled')

            self.data_storage['time'] = deque(maxlen=10000)
            self.data_storage['temperature'] = deque(maxlen=10000)
            self.data_storage['cp'] = {
                f: {'T': deque(maxlen=10000), 'v': deque(maxlen=10000)} for f in self.frequencies}
            self.data_storage['g'] = {
                f: {'T': deque(maxlen=10000), 'v': deque(maxlen=10000)} for f in self.frequencies}
            self._decade_ylims.clear()  # fresh dataset re-snaps decades

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
        lowest frequency (index 0) — the sweep measures lowest-first, so
        the live plot shows data as soon as the measurement starts.
        """
        vals = [f"{int(f)} Hz" for f in self.frequencies]
        self.plot_freq_cb.config(state='readonly')
        self.plot_freq_cb['values'] = vals
        default_index = 0
        self.plot_freq_cb.current(default_index)
        self.plot_freq = self.frequencies[default_index]
        self.log(
            f"Default live-plot frequency: {int(self.plot_freq)} Hz "
            f"(lowest of {len(self.frequencies)}).")

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
        self._decade_ylims.clear()  # new frequency re-snaps decades
        self.ax_main.set_title(
            f"Cp vs. T @ {int(self.plot_freq)} Hz", fontweight='bold')
        self._update_live_plots(force=True)

    # ------------------------------------------------------------------
    def stop_measurement(self, from_user=True):
        # Issue #1 Fix: Wait for worker to finish before touching VISA.
        # The wait is a non-blocking root.after() poll so the GUI never
        # freezes (was a join(15) that stalled the window on Stop).
        if self._stopping or not (self.is_running or self.is_stabilizing):
            return
        self._stopping = True
        self.is_running, self.is_stabilizing = False, False
        self.stop_event.set()
        self.stop_button.config(state='disabled')
        if from_user:
            self.log("Stop requested; waiting for worker to finish...")

        self._stop_deadline = time.time() + 15.0
        self._poll_worker_stopped(from_user)

    def _poll_worker_stopped(self, from_user):
        t = self.measurement_thread
        if (
            t is not None
            and t.is_alive()
            and time.time() < self._stop_deadline
        ):
            self.root.after(
                200, lambda: self._poll_worker_stopped(from_user))
            return
        if t is not None and t.is_alive():
            self.log("WARNING: worker did not exit in 15 s; "
                     "closing sessions anyway.")
        self._finalize_stop(from_user)

    def _finalize_stop(self, from_user):
        self.start_button.config(state='normal')
        self.scan_button.config(state='normal')
        try:
            self.backend.close_instruments()
        except Exception as e:
            self.log(f"WARNING: error closing instruments: {e}")
        self._stopping = False

        if self._close_after_stop:
            self.root.destroy()
            return

        if from_user:
            self.log("Measurement stopped and instruments disconnected.")
            messagebox.showinfo(
                "Info", "Measurement stopped and instruments disconnected.")

    # ==================================================================
    # WORKER THREAD  (Section 7, amended by Appendix A.1)
    # ==================================================================
    def _measurement_worker(self):
        params = self.backend.params
        try:
            # --- Stabilization Phase (verbatim from R-T template) ---
            while self.is_stabilizing and not self.stop_event.is_set():
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
                    self.stop_event.wait(5)
                    self.is_stabilizing = False
                    self.is_running = True
                    break
                self.stop_event.wait(2)

            # --- Ramp Phase ---
            if self.is_running and not self.stop_event.is_set():
                # Enable ramp FIRST: a SETP sent while ramping is off is
                # applied instantly and would bypass the ramp rate entirely.
                self.backend.lakeshore.setup_ramp(1, params['rate'])
                self.backend.lakeshore.set_setpoint(1, params['end_temp'])
                self.backend.lakeshore.set_heater_range(1, 'high')
                self.data_queue.put(
                    f"LOG:Hardware ramp started towards "
                    f"{params['end_temp']} K at {params['rate']} K/min.")
                self.start_time = time.time()

            # --- Measurement Loop ---
            # Appendix A.1: measure_frequency_sweep reads T inside the
            # frequency loop so every data point is bound to its own T.
            while self.is_running and not self.stop_event.is_set():
                cycle = self.backend.measure_frequency_sweep(
                    self.frequencies, params['delay'], self.stop_event)
                
                if not cycle['points']:
                    break
                
                elapsed = time.time() - self.start_time
                self.data_queue.put(('CYCLE', cycle, elapsed))

                # Check if the safety kill was triggered
                if cycle.get('killed', False):
                    self.data_queue.put("CUTOFF")
                    break

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
        # Issue #4 Fix: Fully drain the queue before acting on terminal events
        terminal = None
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()

                if isinstance(data, str) and data.startswith("LOG:"):
                    self._handle_log_message(data[4:])
                elif isinstance(data, str) and data == "CUTOFF":
                    terminal = "CUTOFF"          # defer; keep draining
                elif isinstance(data, str) and data == "COMPLETE":
                    terminal = "COMPLETE"        # defer; keep draining
                elif isinstance(data, Exception):
                    terminal = data
                elif isinstance(data, tuple) and data[0] == 'CYCLE':
                    _, cycle, elapsed = data
                    self._process_cycle(cycle, elapsed)
        except queue.Empty:
            pass

        if terminal == "CUTOFF":
            self._handle_cutoff_event()
            return
        if terminal == "COMPLETE":
            self._handle_complete_event()
            return
        if isinstance(terminal, Exception):
            self._handle_runtime_error(terminal)
            return
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
    def _apply_y_scale(self, ax, values, key):
        """Adaptive Y-scale driven by self.y_scale_var ('auto'|'log'|'linear').

        log:    LabVIEW-style decade autoscale — snap y-limits to
                [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))],
                expand only (cached in self._decade_ylims[key]) so the
                scale never jitters per point.
        linear: plain relim/autoscale_view.
        auto:   log when the positive data spans >= 1 decade, else
                linear, so narrow-span data is not squashed onto a
                single decade band. Data only accumulates during a
                sweep, so the span is monotone and auto cannot flicker
                back from log to linear mid-sweep.
        Falls back to linear whenever there is no positive finite data.
        Returns True when a log scale was applied."""
        mode = self.y_scale_var.get()
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        use_log = bool(pos) and mode != 'linear'
        if use_log and mode == 'auto':
            span = math.log10(max(pos)) - math.log10(min(pos))
            use_log = span >= 1.0  # at least one full decade of data
        if not use_log:
            ax.set_yscale('linear')
            ax.relim()
            ax.set_autoscaley_on(True)  # set_ylim in log mode disables it
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

    # ------------------------------------------------------------------
    def _on_y_scale_change(self):
        self._decade_ylims.clear()  # re-snap from scratch on mode change
        self._update_live_plots(force=True)
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
        self.ax_sub1.relim()
        self.ax_sub1.autoscale_view(scalex=True, scaley=False)
        self._apply_y_scale(
            self.ax_sub1, self.data_storage['g'][fq]['v'], "g")
        self.ax_sub2.relim()
        self.ax_sub2.autoscale_view()

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

        self._apply_y_scale(self.ax_main, vals, "cp")

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
        to auto-select both the Lakeshore 340 and the E4980A).
        Queries *IDN? on every discovered VISA resource, then auto-selects
        the device reporting 'E4980' → LCR combobox and the device
        reporting 'MODEL340' → Lakeshore combobox.
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

            # Auto-select the Lakeshore 340 by model, never by the LSCI
            # maker token alone: a 350 on the same bus also answers LSCI.
            if (lakeshore_label is None and
                    "MODEL340" in idn.upper().replace(" ", "")):
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
                # Destroy is deferred to _finalize_stop so the worker
                # can exit cleanly without freezing the GUI.
                self._close_after_stop = True
                self.stop_measurement(from_user=False)
        elif self._stopping:
            # Stop already in progress — just close once it finishes.
            self._close_after_stop = True
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