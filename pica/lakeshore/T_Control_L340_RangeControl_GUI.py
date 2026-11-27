"""
Module: T_Control_L340_RangeControl_GUI.py
Purpose: Single temperature ramp on a Lake Shore Model 340 (Loop 1 heater)
         with heater ranges shown in watts, CCR PID presets, a software PID
         zone table, and optional heater / sensor / display setup.  v2.0.

Port of T_Control_L350_RangeControl_GUI.py.  Since 27 Nov 2026 the two are
laid out and handled the same way; only the commands differ (the 340 has one
heater loop, no OUTMODE/HTRSET, and takes INTYPE as a type code alone).

How a run works
---------------
  1. Connect (or press Start, which connects first).  The window reads the
     present temperature, setpoint, PID, manual output, heater range, the
     loop limits and heater setup (CLIMIT?, CDISP?), and the input type.
     Connect/Disconnect never change a setting.
  2. Start enables Loop 1 on the chosen input in kelvin (CSET 1,<in>,1,1),
     puts it in Manual PID (CMODE 1,1) so nothing in the instrument
     overrides the PID this window sends, and reads CLIMIT? so a target
     above the setpoint limit, or a range above the max range, is refused.
  3. PID, manual output (MOUT) and heater range are sent, then the setpoint
     is pinned to the present temperature with the ramp off and the ramp is
     started.  A 340 ramps from the CURRENT SETPOINT, not from the
     temperature; without this a leftover 300 K setpoint would make a ramp
     to 3 K start with the heater at full power.
  4. Every "Log every" seconds: read, write a CSV row (fsync), plot, and in
     zone mode send the PID / MOUT / range of the zone the MEASURED
     temperature is in when the zone changes (no hysteresis, as asked).
  5. Done when the reading stays inside the tolerance for the dwell time:
     heater off (default, as the v1 window did) or hold at target.
  6. Stop = RAMP 1,0,0 + RANGE 0.  Closing the window while idle leaves the
     instrument exactly as it is.

No modal dialog appears during or after a run (a ramp can run for hours
unattended): completion and errors go to the console, the status banner and
a beep.  Dialogs only appear for actions you start yourself.

Heater power (manual 2.4.1, Table 1-6)
--------------------------------------
  Loop 1 is a current source with 50 V compliance: full-scale power is the
  smaller of I^2 R and (50 V)^2 / R, with I the max current setting
  (0.25 / 0.5 / 1 / 2 A, or a user value 0.1-2.0 A set by CLIMI) and R the
  heater resistance entered in CDISP.  25 ohm at 2 A gives 100 W.  Each
  range is one decade lower in power: range 5 = full power, 4 = 1/10, ...

Manual output (MOUT, manual 6.12)
---------------------------------
  In Manual PID the manual output is ADDED to the PID output: the control
  equation works about that heater percentage.  A value near what holds the
  temperature can settle the loop with less integral wind-up; too much
  heats the sample even when PID asks for zero.  Default setting is 0; this
  window sends the value you set (0 by default) at Start.

Model 340 commands used (User's Manual, chapter 9):
  CSET 1,<in>,1,1      enable Loop 1 on input, kelvin        (printed 9-31)
  CMODE 1,1            Manual PID                            (9-29)
  CLIMIT? 1 / CLIMIT 1,<SP lim>,<+slope>,<-slope>,<max I>,<max range>  (9-28)
  CLIMI <A> / CLIMI?   user max current 0.1-2.0 A            (9-28)
  CDISP? 1 / CDISP 1,<loops>,<ohm>,<I/P>,<large>            (9-27)
  RAMP 1,<on>,<rate>   0.1-100 K/min                         (9-40)
  SETP 1,<K>                                                 (9-42)
  RANGE <0-5>          Loop 1 heater range, no loop number   (9-40)
  PID 1,<P>,<I>,<D>    P 0-1000, I 0-1000, D 0-1000          (9-40)
  MOUT 1,<%>           0-100 % here (negative not used)      (9-38)
  INTYPE <in>,<type>   type code only (more fields = Special) (9-34)
  INCRV <in>,<curve>, DISPLAY <fields>, DISPFLD <f>,<in>,<source>
  KRDG? <in>, RDGST? <in>, HTR?, HTRST?, SETP? 1, RANGE?, PID? 1, MOUT? 1
  (*RST is never sent: it would reset the loop, setpoint and ramp.)
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas
import os
import csv
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

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


def _is_gpib_address(resource, hint):
    """True if resource is on the GPIB primary address named in hint, such
    as "::19::" or "::23::INSTR", on any board. Secondary-address copies
    (board::primary::secondary::INSTR), which a Lakeshore answers on, are
    not a match."""
    want = [part for part in hint.split("::") if part.isdigit()][:1]
    parts = resource.split("::")
    return (len(parts) == 3 and parts[0].upper().startswith("GPIB")
            and parts[1:2] == want)


def run_script_process(script_path):
    """Execute a script using runpy in its own directory (child process)."""
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
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


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
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# -------------------------------------------------------------------------------
# --- HEATER POWER (manual 2.4.1, Table 1-6) ---
# -------------------------------------------------------------------------------

# Same names as the Model 350 window: Low = 2, Medium = 4, High = 5.
RANGE_NAMES = {0: "Off", 1: "Very low", 2: "Low", 3: "Medium-low",
               4: "Medium", 5: "High"}
# CLIMIT <max current> codes; 5 = User (CLIMI, main firmware 01.03.08+).
MAX_CURRENT_A = {1: 0.25, 2: 0.5, 3: 1.0, 4: 2.0}
USER_CURRENT_RANGE = (0.1, 2.0)
COMPLIANCE_V = 50.0
NOMINAL_RESISTANCE = 25.0       # the CCR heater is 25 ohm


def heater_max_power(current_a, resistance_ohm, compliance_v=COMPLIANCE_V):
    """Full-scale power of a current-source heater: the smaller of I^2 R and
    V^2 / R (manual 2.4.1)."""
    if current_a <= 0 or resistance_ohm <= 0:
        return 0.0
    return min(current_a ** 2 * resistance_ohm, compliance_v ** 2 / resistance_ohm)


def loop1_full_power(max_current_code, resistance_ohm, user_current=0.0):
    """Loop 1 power on range 5 for a CLIMIT max-current code and heater R."""
    code = int(max_current_code)
    i = float(user_current) if code == 5 else MAX_CURRENT_A[code]
    return heater_max_power(i, float(resistance_ohm))


def range_power(range_code, full_power_w):
    """Power available on a range: each range is one decade below the next."""
    code = int(range_code)
    if code <= 0:
        return 0.0
    return full_power_w / (10 ** (5 - code))


def format_power(watts):
    """'100 W', '7.5 mW', '750 uW' (ASCII, safe in data files)."""
    if watts <= 0:
        return "0 W"
    if watts >= 1:
        return f"{watts:.3g} W"
    if watts >= 1e-3:
        return f"{watts * 1e3:.3g} mW"
    return f"{watts * 1e6:.3g} uW"


def range_label(code, full_power_w):
    """'5 - High (100 W)'; '0 - Off'."""
    code = int(code)
    if code == 0:
        return "0 - Off"
    return f"{code} - {RANGE_NAMES[code]} ({format_power(range_power(code, full_power_w))})"


def parse_range(label):
    """Range code from a range label or a bare digit."""
    return int(str(label).strip().split()[0])


def heater_watts(percent, range_power_w, display_is_power):
    """Rough heater power from HTR? percent: percent of full-scale power, or
    of full-scale current (power goes with the square)."""
    frac = max(0.0, float(percent)) / 100.0
    return range_power_w * (frac if display_is_power else frac * frac)


# -------------------------------------------------------------------------------
# --- PID PRESETS AND ZONES (CCR) ---
# -------------------------------------------------------------------------------

# The CCR presets of the Direct Control window, one per default zone.
PID_PRESETS = {
    "CCR 100-310 K (P=0.5, I=4, D=0) - use range 5": (0.5, 4.0, 0.0),
    "CCR 20-100 K (P=1.5, I=4, D=0) - use range 4": (1.5, 4.0, 0.0),
    "CCR below 20 K (P=5, I=4, D=0) - use range 3": (5.0, 4.0, 0.0),
}
PID_CUSTOM = "Custom (type P, I, D)"
PID_DEFAULT_PRESET = "CCR 100-310 K (P=0.5, I=4, D=0) - use range 5"

# (upper bound K, P, I, D, heater range).  The first zone runs from 0 K up
# to its bound; a temperature above the last bound uses the last zone.  The
# zone table adds a MOUT column (default 0); collected rows carry it as a
# sixth item.
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
SAFETY_MAX_DEFAULT_K = 320.0


def select_zone(zones, temperature):
    """Index of the zone a temperature falls in (no hysteresis).

    zones: sequence of (upper_bound, P, I, D, range[, MOUT]), sorted
    ascending.  The first zone whose upper bound is >= temperature wins;
    anything above the last bound uses the last zone.
    """
    if not zones:
        raise ValueError("zone table is empty")
    for idx, zone in enumerate(zones):
        if temperature <= zone[0]:
            return idx
    return len(zones) - 1


def generate_equal_zones(n_segments, t_min=ZONE_T_MIN, t_max=ZONE_T_MAX,
                         template=ZONE_DEFAULTS):
    """Split [t_min, t_max] into n equal segments, PID/range from template."""
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
        out.append((round(ub, 3),) + tuple(tpl[1:]))
    return out


def validate_pid(p, i, d):
    """Model 340 PID limits: 0-1000 each."""
    if not (0 <= p <= 1000 and 0 <= i <= 1000 and 0 <= d <= 1000):
        raise ValueError("PID values must be 0-1000 on a Model 340.")


def validate_mout(value):
    if not 0 <= value <= 100:
        raise ValueError(f"Manual output must be 0-100 %, got {value:g}")


def ascii_label(text):
    """Data-file headers stay ASCII (lab PCs open files as cp1252)."""
    return (str(text).replace("→", "->").replace("µ", "u")
            .replace("Ω", "Ohm").replace("±", "+/-")
            .encode("ascii", "replace").decode("ascii"))


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
                "PYVISA_LIBRARY=C:\\Windows\\System32\\ktvisa32.dll and restart "
                "PICA. Then use 'Identify' to see which address answers as "
                "MODEL340.")
    if 'VI_ERROR_TMO' in text:
        return ("Timeout: the address exists but nothing answered *IDN?. "
                "Check the 340 is powered, its IEEE address matches, and no "
                "other programme holds the session.")
    return ""


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


class Lakeshore340_Backend:
    MODEL_TOKENS = ("MODEL340", "MODEL 340")
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
    RDGST_BITS = (
        (1, "invalid reading"), (2, "old reading"), (16, "temp underrange"),
        (32, "temp overrange"), (64, "units zero"), (128, "units overrange"),
    )
    # INTYPE <type> codes, printed 9-34.
    SENSOR_TYPES = {
        0: "Special", 1: "Silicon Diode", 2: "GaAlAs Diode",
        3: "Platinum 100 (250 ohm)", 4: "Platinum 100 (500 ohm)",
        5: "Platinum 1000", 6: "Rhodium Iron", 7: "Carbon-Glass", 8: "Cernox",
        9: "RuOx", 10: "Germanium", 11: "Capacitor", 12: "Thermocouple",
    }
    # DISPFLD <source> codes, printed 9-32.
    DISPLAY_SOURCES = {1: "Kelvin", 2: "Celsius", 3: "Sensor units",
                       4: "Linear data", 5: "Minimum", 6: "Maximum"}

    def __init__(self):
        self.lakeshore = None
        self.idn = ""
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            self.rm = None

    # -- session --

    @property
    def is_connected(self):
        return self.lakeshore is not None

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        self.idn = self.lakeshore.query('*IDN?').strip()
        print(f"  Lakeshore Connected: {self.idn}")
        return self.idn

    def is_model_340(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def _write(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        self.lakeshore.write(cmd)

    def _query(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        return self.lakeshore.query(cmd).strip()

    def _fields(self, cmd, n):
        parts = [p.strip() for p in self._query(cmd).split(',')]
        if len(parts) < n:
            raise ValueError(f"unexpected reply to {cmd}: '{','.join(parts)}'")
        return parts

    def clear_status(self):
        self._write('*CLS')

    def identify_resources(self, addresses, timeout_ms=2000):
        """Send *IDN? to each address (user-triggered). {addr: reply|'ERROR: ..'}"""
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

    # -- loop setup --

    def prepare_loop(self, control_input):
        """*CLS; enable Loop 1 on <input> in kelvin; Manual PID mode.

        Returns (CSET? dict, CMODE? code, CLIMIT? dict) for logging.
        """
        self._write('*CLS')
        time.sleep(0.2)
        self._write(f'CSET 1,{control_input},1,1')
        self._write('CMODE 1,1')
        time.sleep(0.2)
        cset = self.get_control_loop(1)
        if (cset['input'].upper() != str(control_input).upper() or not cset['enabled']
                or cset['units'] != 1):
            raise RuntimeError(
                f"CSET 1,{control_input},1,1 did not stick: CSET? 1 reads "
                f"{cset}. Check the front panel (Remote/Local) and retry.")
        cmode = int(float(self._query('CMODE? 1')))
        if cmode != 1:
            raise RuntimeError(f"CMODE 1,1 did not stick: CMODE? 1 = {cmode}.")
        return cset, cmode, self.get_control_limits(1)

    def get_control_loop(self, loop=1):
        parts = self._fields(f'CSET? {loop}', 4)
        return {'input': parts[0], 'units': int(float(parts[1])),
                'enabled': int(float(parts[2])), 'powerup': int(float(parts[3]))}

    def get_control_mode(self, loop=1):
        return int(float(self._query(f'CMODE? {loop}')))

    def get_control_limits(self, loop=1):
        parts = self._fields(f'CLIMIT? {loop}', 5)
        return {'sp_limit': float(parts[0]), 'pos_slope': float(parts[1]),
                'neg_slope': float(parts[2]), 'max_current': int(float(parts[3])),
                'max_range': int(float(parts[4]))}

    # -- ramp --

    def pin_setpoint(self, current_temperature):
        """Ramp off and setpoint = present temperature, so a loop that is
        switched on does not chase a leftover setpoint."""
        self._write('RAMP 1,0,0')
        self._write(f'SETP 1,{current_temperature:.3f}')

    def start_ramp(self, target, rate, current_temperature):
        """Setpoint = now (ramp off), then ramp on and target.

        The 340 ramps from the current setpoint; pinning it to the present
        temperature first makes the ramp start from where the sample is.
        """
        if not (0.1 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self.pin_setpoint(current_temperature)
        time.sleep(0.2)
        self._write(f'RAMP 1,1,{rate}')
        self._write(f'SETP 1,{target}')

    def set_ramp_rate(self, rate):
        if not (0.1 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self._write(f'RAMP 1,1,{rate}')

    # -- PID, manual output, range --

    def set_heater_range(self, range_code):
        """RANGE <0-5>: Loop 1 only, no loop number on a 340. Verified."""
        range_code = int(range_code)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5, got {range_code}")
        self._write(f'RANGE {range_code}')
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != range_code:
            raise RuntimeError(
                f"RANGE {range_code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        return int(float(self._query('RANGE?')))

    def set_pid(self, p, i, d):
        validate_pid(p, i, d)
        self._write(f'PID 1,{p},{i},{d}')

    def get_pid(self):
        parts = self._fields('PID? 1', 3)
        return float(parts[0]), float(parts[1]), float(parts[2])

    def set_mout(self, value):
        validate_mout(value)
        self._write(f'MOUT 1,{value}')

    def get_mout(self):
        return float(self._query('MOUT? 1'))

    # -- heater setup (CLIMIT max current, CLIMI, CDISP resistance) --

    def get_heater_setup(self):
        """{'max_current', 'user_current', 'resistance', 'display', ...}.

        CLIMI? is asked only when the max current is User: older firmware
        does not know it and would time out.
        """
        lim = self.get_control_limits(1)
        disp = self._fields('CDISP? 1', 4)
        user = 0.0
        if lim['max_current'] == 5:
            user = float(self._query('CLIMI?'))
        return {'max_current': lim['max_current'], 'user_current': user,
                'resistance': float(disp[1]), 'display': int(float(disp[2])),
                'max_range': lim['max_range'], 'sp_limit': lim['sp_limit']}

    def set_heater_setup(self, max_current, user_current, resistance, display):
        """CLIMIT (read-modify-write: only the max current changes), CLIMI for
        a user current, CDISP (read-modify-write: resistance and I/P)."""
        max_current, display = int(max_current), int(display)
        if max_current not in self.MAX_CURRENT_CODES:
            raise ValueError("max current code must be 1-5")
        if not 1 <= float(resistance) <= 1000:
            raise ValueError("heater resistance must be 1-1000 ohm")
        if display not in (1, 2):
            raise ValueError("display must be 1 (current) or 2 (power)")
        if max_current == 5:
            lo, hi = USER_CURRENT_RANGE
            if not lo <= float(user_current) <= hi:
                raise ValueError(f"user max current must be {lo:g}-{hi:g} A")
        sent = []
        lim = self.get_control_limits(1)
        cmd = (f"CLIMIT 1,{lim['sp_limit']},{lim['pos_slope']},{lim['neg_slope']},"
               f"{max_current},{lim['max_range']}")
        self._write(cmd)
        sent.append(cmd)
        if max_current == 5:
            cmd = f"CLIMI {float(user_current):.3f}"
            self._write(cmd)
            sent.append(cmd)
        disp = self._fields('CDISP? 1', 4)
        cmd = (f"CDISP 1,{int(float(disp[0]))},{int(round(float(resistance)))},"
               f"{display},{int(float(disp[3]))}")
        self._write(cmd)
        sent.append(cmd)
        return "; ".join(sent)

    # -- sensor input, curve, display --

    def get_input_type(self, control_input):
        """INTYPE? -> type code (first field of the reply)."""
        return int(float(self._fields(f'INTYPE? {control_input}', 1)[0]))

    def set_input_type(self, control_input, sensor_type):
        """INTYPE <input>,<type>.  Only the type is sent: any other field
        turns the input into type 0 (Special)."""
        if int(sensor_type) not in self.SENSOR_TYPES or int(sensor_type) == 0:
            raise ValueError("sensor type must be 1-12 (Special is set from the front panel)")
        cmd = f'INTYPE {control_input},{int(sensor_type)}'
        self._write(cmd)
        return cmd

    def get_input_curve(self, control_input):
        return int(float(self._query(f'INCRV? {control_input}')))

    def set_input_curve(self, control_input, curve):
        if not 0 <= int(curve) <= 60:
            raise ValueError("curve must be 0 (none), 1-20 standard or 21-60 user")
        cmd = f'INCRV {control_input},{int(curve)}'
        self._write(cmd)
        return cmd

    def get_display_fields(self):
        """DISPLAY? -> number of fields (contrast / backlight are left alone)."""
        return int(float(self._fields('DISPLAY?', 1)[0]))

    def set_display_fields(self, n_fields):
        if not 1 <= int(n_fields) <= 8:
            raise ValueError("number of display fields must be 1-8")
        cmd = f'DISPLAY {int(n_fields)}'
        self._write(cmd)
        return cmd

    def get_display_field(self, field):
        parts = self._fields(f'DISPFLD? {int(field)}', 2)
        return parts[0], int(float(parts[1]))

    def set_display_field(self, field, control_input, source):
        if not 1 <= int(field) <= 8:
            raise ValueError("display field must be 1-8")
        if int(source) not in self.DISPLAY_SOURCES:
            raise ValueError("display source must be 1-6")
        cmd = f'DISPFLD {int(field)},{control_input},{int(source)}'
        self._write(cmd)
        return cmd

    # -- readings --

    def get_status(self, control_input):
        """-> (temperature K, heater %, setpoint K, reading-status text)."""
        temp = float(self._query(f'KRDG? {control_input}'))
        htr = float(self._query('HTR?'))
        setp = float(self._query('SETP? 1'))
        return temp, htr, setp, self.get_reading_status(control_input)

    def get_reading_status(self, control_input):
        code = int(float(self._query(f'RDGST? {control_input}')))
        return ", ".join(n for bit, n in self.RDGST_BITS if code & bit)

    def get_temperature(self, control_input):
        return float(self._query(f'KRDG? {control_input}'))

    def get_setpoint(self):
        return float(self._query('SETP? 1'))

    def get_heater_output(self):
        return float(self._query('HTR?'))

    def get_heater_status(self):
        code = int(float(self._query('HTRST?')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    # -- stop --

    def stop_ramp(self):
        """Heater off (RANGE 0, sent first and on its own), then RAMP off.
        The loop stays enabled (harmless at range 0).  Returns a list of
        problems; empty means RANGE 0 was sent."""
        if not self.lakeshore:
            return ["not connected: RANGE 0 not sent"]
        problems = []
        try:
            self._write('RANGE 0')
        except Exception as e:
            problems.append(f"RANGE 0 failed: {e}")
        try:
            self._write('RAMP 1,0,0')
        except Exception as e:
            print(f"  Warning: RAMP 1,0,0 failed. {e}")
        print("  Lakeshore ramp stopped and heater turned off." if not problems
              else f"  WARNING: {problems}")
        return problems

    def close(self):
        """Close the session only; the instrument keeps its state."""
        if self.lakeshore:
            try:
                self.lakeshore.close()
            except Exception as e:
                print(f"  Warning: Error closing Lakeshore session. {e}")
            finally:
                self.lakeshore = None

    def shutdown(self):
        if self.lakeshore:
            problems = self.stop_ramp()
            self.close()
            return problems
        return []


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class TempControlGUI:
    PROGRAM_VERSION = "2.0"
    LEFT_PANEL_WIDTH = 640
    IDLE_POLL_MS = 2000
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_MUTED = '#6E625A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_GOLD = '#B68B6E', '#BA6B5E', '#BA6B5E'
    CLR_STABLE_WAIT = '#D4A373'
    CLR_OK_GREEN = '#8AB845'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 11)
    FONT_SMALL = ('Segoe UI', 9)
    FONT_TABLE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Lakeshore 340 Temperature Control v{self.PROGRAM_VERSION}")
        self.root.geometry("1450x880")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.logo_image = None
        self.backend = Lakeshore340_Backend()
        self.data_storage = {'time': [], 'temperature': [], 'setpoint': [],
                             'heater': []}
        self.resource_labels = {}
        self.params = {}
        self.data_file = None
        self.csv_writer = None
        self.data_filepath = None
        self.active_zone = None       # index into self.zones, or None
        self.zones = []
        self.loop_started = False     # heater commands sent in this Start
        self.limits = None            # CLIMIT? 1 read at Start
        # Send buttons for sensor / display stay greyed until the matching
        # Read has succeeded, so defaults are never sent by accident.
        self.sensor_read_input = None
        self.display_read_ok = False
        self.stable_since = None
        self.reached = False
        self.last_htr_error = 0
        self.poll_job = None
        self.start_time = time.time()
        # Heater setup known from the instrument (nominal until read).
        self.heater_cfg = {'max_current': 4, 'user_current': 0.0,
                           'resistance': NOMINAL_RESISTANCE, 'display': 1,
                           'known': False}
        self._ui_ready = False

        self.setup_styles()
        self.create_widgets()           # sets _ui_ready when every widget exists
        self._refresh_range_labels()
        self._refresh_states()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ------------------------------------------------------------------ styles

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('Card.TFrame', background=self.CLR_FRAME_BG)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Muted.TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_MUTED, font=self.FONT_SMALL)
        style.configure('Bold.TLabel', background=self.CLR_FRAME_BG,
                        font=('Segoe UI', 10, 'bold'))
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('TRadiobutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.map('TCheckbutton', foreground=[('disabled', self.CLR_MUTED)])
        style.map('TRadiobutton', foreground=[('disabled', self.CLR_MUTED)])
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TButton', font=self.FONT_BASE, padding=(8, 6),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('disabled', '#A89C92'),
                              ('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton',
                  background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK)
        style.map('TEntry', fieldbackground=[('disabled', '#DDD3CA'),
                                             ('readonly', '#E9E1D9')],
                  foreground=[('disabled', '#8F837A')])
        style.configure('TCombobox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.map('TCombobox', fieldbackground=[('disabled', '#DDD3CA'),
                                                ('readonly', self.CLR_GRAPH_BG)],
                  foreground=[('disabled', '#8F837A')])
        style.configure('TSpinbox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11,
                             'axes.titlesize': 14, 'axes.labelsize': 12})

    # ----------------------------------------------------------------- layout

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(header, text="Lakeshore 340 Temperature Ramp Utility",
                  style='Header.TLabel', font=font_title_main,
                  foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)
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
        self._ui_ready = True
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            target = max(target, self.LEFT_PANEL_WIDTH)
            # Leave the plot at least ~45 % of the window.
            window_w = self.root.winfo_width()
            if window_w > 1:
                target = min(target, int(window_w * 0.55))
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        frame = ttk.Frame(canvas)
        frame.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = frame
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        frame.grid_columnconfigure(0, weight=1)
        self._create_info_panel(frame, 0)
        self._create_connect_panel(frame, 1)
        self._create_ramp_panel(frame, 2)
        self._create_pid_panel(frame, 3)
        self._create_heater_setup_panel(frame, 4)
        self._create_sensor_panel(frame, 5)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 90
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=8)
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
        except Exception as e:
            print(f"Warning: Could not load logo. {e}")
        institute_font = ('Segoe UI', self.FONT_BASE[1] + 2, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font).grid(row=0, column=1, padx=10, pady=(14, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font).grid(
            row=1, column=1, padx=10, sticky='nw')
        ttk.Label(frame, text="Lake Shore Model 340 | Loop 1 heater | CCR 3-310 K",
                  style='Muted.TLabel').grid(row=2, column=1, padx=10, pady=(0, 8), sticky='w')

    def _section(self, parent, grid_row, title):
        frame = ttk.LabelFrame(parent, text=title)
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        # Spare width goes to the last column, so labels and their boxes stay
        # side by side on the left.
        frame.grid_columnconfigure(3, weight=1)
        return frame

    def _note(self, parent, text, row, column=0, columnspan=4, pady=(0, 4)):
        lbl = ttk.Label(parent, text=text, style='Muted.TLabel', justify='left')
        lbl.grid(row=row, column=column, columnspan=columnspan, sticky='w', padx=10, pady=pady)
        return lbl

    def _inline_entry(self, parent, default, width):
        e = ttk.Entry(parent, width=width, font=self.FONT_BASE, justify='center')
        e.insert(0, default)
        e.pack(side='left', padx=4)
        return e

    # -- 1. instrument --

    def _create_connect_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '1. Instrument')
        ttk.Label(frame, text="VISA address:").grid(row=0, column=0, sticky='w', padx=10, pady=4)
        self.ls_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.ls_cb.grid(row=0, column=1, columnspan=3, sticky='ew', padx=10, pady=4)
        btns = ttk.Frame(frame, style='Card.TFrame')
        btns.grid(row=1, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 4))
        btns.grid_columnconfigure((0, 1, 2), weight=1)
        self.scan_btn = ttk.Button(btns, text="Scan", command=self._scan_for_visa)
        self.scan_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.identify_btn = ttk.Button(btns, text="Identify", command=self._identify_visa)
        self.identify_btn.grid(row=0, column=1, sticky='ew', padx=4)
        self.connect_btn = ttk.Button(btns, text="Connect", command=self._toggle_connect)
        self.connect_btn.grid(row=0, column=2, sticky='ew', padx=4)
        self.lbl_conn = ttk.Label(frame, text="Not connected. Connect only reads; it changes nothing.",
                                  style='Muted.TLabel')
        self.lbl_conn.grid(row=2, column=0, columnspan=4, sticky='w', padx=10, pady=(0, 6))

    # -- 2. ramp --

    def _create_ramp_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '2. Ramp')
        self.entries = {}
        row1 = ttk.Frame(frame, style='Card.TFrame')
        row1.grid(row=0, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row1, text="Ramp to").pack(side='left')
        self.entries['target'] = self._inline_entry(row1, "310", 7)
        ttk.Label(row1, text="K at").pack(side='left')
        self.entries['rate'] = self._inline_entry(row1, "2", 6)
        ttk.Label(row1, text="K/min, controlling on input").pack(side='left')
        self.input_var = tk.StringVar(value='A')
        self.input_cb = ttk.Combobox(row1, textvariable=self.input_var,
                                     values=['A', 'B', 'C', 'D'], state='readonly', width=3)
        self.input_cb.pack(side='left', padx=4)

        row2 = ttk.Frame(frame, style='Card.TFrame')
        row2.grid(row=1, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row2, text="Done when within ±").pack(side='left')
        self.entries['tol'] = self._inline_entry(row2, "0.5", 5)
        ttk.Label(row2, text="K for").pack(side='left')
        self.entries['dwell'] = self._inline_entry(row2, "60", 5)
        ttk.Label(row2, text="s.  Log every").pack(side='left')
        self.entries['delay'] = self._inline_entry(row2, "1", 4)
        ttk.Label(row2, text="s.").pack(side='left')

        row3 = ttk.Frame(frame, style='Card.TFrame')
        row3.grid(row=2, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row3, text="When done:").pack(side='left')
        self.hold_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(row3, text="turn the heater off", variable=self.hold_var,
                        value=False).pack(side='left', padx=6)
        ttk.Radiobutton(row3, text="hold at the target", variable=self.hold_var,
                        value=True).pack(side='left', padx=6)

        row4 = ttk.Frame(frame, style='Card.TFrame')
        row4.grid(row=3, column=0, columnspan=4, sticky='w', padx=10, pady=4)
        ttk.Label(row4, text="Safety: heater off and stop if T goes above").pack(side='left')
        self.entries['max_temp'] = self._inline_entry(row4, f"{SAFETY_MAX_DEFAULT_K:g}", 6)
        ttk.Label(row4, text="K").pack(side='left')
        self._note(frame, "The 340's own setpoint limit (CLIMIT) is checked at Start too.", 4)

        btns = ttk.Frame(frame, style='Card.TFrame')
        btns.grid(row=5, column=0, columnspan=4, sticky='ew', padx=6, pady=(4, 8))
        btns.grid_columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(btns, text="Start Ramp", style='Start.TButton',
                                       command=self.start_ramp)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=4)
        self.stop_button = ttk.Button(btns, text="Stop (heater off)", style='Stop.TButton',
                                      command=self.stop_ramp)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=4)

    # -- 3. heater and PID --

    def _create_pid_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '3. Heater power and PID (Loop 1)')
        self.pid_mode_var = tk.StringVar(value='zones')
        ttk.Radiobutton(frame, text="One setting for the whole ramp", value='fixed',
                        variable=self.pid_mode_var, command=self._on_mode_changed).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=10, pady=(4, 0))
        ttk.Radiobutton(frame, text="Zone table: change with the measured temperature",
                        value='zones', variable=self.pid_mode_var,
                        command=self._on_mode_changed).grid(
            row=0, column=2, columnspan=2, sticky='w', padx=10, pady=(4, 0))

        # ---- one setting ----
        fixed = ttk.LabelFrame(frame, text='One setting')
        fixed.grid(row=1, column=0, columnspan=4, sticky='ew', padx=8, pady=6)
        self.pid_w = {}
        w = self.pid_w
        ttk.Label(fixed, text="PID:").grid(row=0, column=0, sticky='w', padx=8, pady=3)
        w['preset_var'] = tk.StringVar(value=PID_DEFAULT_PRESET)
        w['preset'] = ttk.Combobox(fixed, textvariable=w['preset_var'], state='readonly',
                                   values=list(PID_PRESETS.keys()) + [PID_CUSTOM], width=44)
        w['preset'].grid(row=0, column=1, sticky='w', padx=4, pady=3)
        w['preset'].bind('<<ComboboxSelected>>', lambda e: self._on_preset())
        line = ttk.Frame(fixed, style='Card.TFrame')
        line.grid(row=1, column=0, columnspan=2, sticky='w', padx=4, pady=3)
        p, i, d = PID_PRESETS[PID_DEFAULT_PRESET]
        for key, label, val, width in (('p', 'P', p, 6), ('i', 'I', i, 6), ('d', 'D', d, 5),
                                       ('mout', 'MOUT %', 0.0, 5)):
            ttk.Label(line, text=label).pack(side='left', padx=(6, 2))
            e = ttk.Entry(line, width=width, font=self.FONT_TABLE, justify='center')
            e.insert(0, f"{val:g}")
            e.pack(side='left')
            w[key] = e
        ttk.Label(line, text="Range").pack(side='left', padx=(8, 2))
        w['range_var'] = tk.StringVar(value="5")
        w['range'] = ttk.Combobox(line, textvariable=w['range_var'], state='readonly', width=22)
        w['range'].pack(side='left')
        fb = ttk.Frame(fixed, style='Card.TFrame')
        fb.grid(row=2, column=0, columnspan=2, sticky='ew', padx=4, pady=(2, 6))
        fb.grid_columnconfigure((0, 1), weight=1)
        self.load_pid_btn = ttk.Button(fb, text="Copy present PID from instrument",
                                       command=self._load_present_pid)
        self.load_pid_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.apply_fixed_btn = ttk.Button(fb, text="Apply now (during a run)",
                                          command=self._apply_fixed_live)
        self.apply_fixed_btn.grid(row=0, column=1, sticky='ew', padx=4)
        self._note(fixed, "Manual output (MOUT) is a fixed heater % added on top of PID. "
                          "Leave 0 unless you know\nthe % that holds this temperature; "
                          "too much keeps heating when PID asks for zero.",
                   row=3, columnspan=2, pady=(0, 6))

        # ---- zone table ----
        zf = ttk.LabelFrame(frame, text='Zone table')
        zf.grid(row=2, column=0, columnspan=4, sticky='ew', padx=8, pady=6)
        zf.grid_columnconfigure(0, weight=1)
        self._note(zf, "Each row runs up to its 'Up to' temperature (the first from 0 K; the last "
                       "also covers anything\nabove). The switch happens when the MEASURED "
                       "temperature crosses a bound, no hysteresis.\nRule of thumb: at lower "
                       "temperature use a lower range and a higher P.", row=0, pady=(4, 2))
        gen = ttk.Frame(zf, style='Card.TFrame')
        gen.grid(row=1, column=0, sticky='w', padx=10, pady=2)
        ttk.Label(gen, text="Make").pack(side='left')
        self.zone_segments_var = tk.StringVar(value=str(len(ZONE_DEFAULTS)))
        self.zone_seg_spin = ttk.Spinbox(gen, from_=1, to=20, width=3,
                                         textvariable=self.zone_segments_var)
        self.zone_seg_spin.pack(side='left', padx=4)
        ttk.Label(gen, text="equal zones from").pack(side='left')
        self.zone_tmin = self._inline_entry(gen, f"{ZONE_T_MIN:g}", 5)
        ttk.Label(gen, text="to").pack(side='left')
        self.zone_tmax = self._inline_entry(gen, f"{ZONE_T_MAX:g}", 5)
        ttk.Label(gen, text="K").pack(side='left')
        self.zone_gen_btn = ttk.Button(gen, text="Make", command=self._generate_zone_rows)
        self.zone_gen_btn.pack(side='left', padx=4)
        self.zone_reset_btn = ttk.Button(gen, text="Defaults", command=self._reset_zone_rows)
        self.zone_reset_btn.pack(side='left', padx=2)

        self.zone_table = ttk.Frame(zf, style='Card.TFrame')
        self.zone_table.grid(row=2, column=0, sticky='w', padx=8, pady=4)
        for col, h in enumerate(["Up to K", "P", "I", "D", "MOUT%", "Range"]):
            ttk.Label(self.zone_table, text=h, style='Bold.TLabel').grid(
                row=0, column=col, padx=4, pady=1)
        self.zone_rows = []
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)

        zb = ttk.Frame(zf, style='Card.TFrame')
        zb.grid(row=3, column=0, sticky='ew', padx=4, pady=(2, 4))
        zb.grid_columnconfigure((0, 1, 2), weight=1)
        self.zone_add_btn = ttk.Button(zb, text="+ Add zone", command=lambda: self._add_zone_row())
        self.zone_add_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.zone_del_btn = ttk.Button(zb, text="- Remove last", command=self._remove_zone_row)
        self.zone_del_btn.grid(row=0, column=1, sticky='ew', padx=4)
        self.zone_apply_btn = ttk.Button(zb, text="Apply edits (during a run)",
                                         command=self._apply_zone_table)
        self.zone_apply_btn.grid(row=0, column=2, sticky='ew', padx=4)
        self.lbl_power_legend = self._note(zf, "", row=4, pady=(0, 2))
        self.lbl_zone_status = ttk.Label(zf, text="Active zone: --", style='Bold.TLabel',
                                         foreground=self.CLR_ACCENT_GOLD)
        self.lbl_zone_status.grid(row=5, column=0, sticky='w', padx=10, pady=(0, 6))

    def _create_heater_setup_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '4. Heater setup (optional)')
        self.htr_edit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Let me change the heater setup",
                        variable=self.htr_edit_var, command=self._refresh_states).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(4, 2))
        ttk.Label(frame, text="Heater resistance:").grid(row=1, column=0, sticky='w', padx=10, pady=3)
        rf = ttk.Frame(frame, style='Card.TFrame')
        rf.grid(row=1, column=1, columnspan=3, sticky='w', padx=10)
        self.htr_res_entry = ttk.Entry(rf, width=7, font=self.FONT_TABLE, justify='center')
        self.htr_res_entry.insert(0, f"{NOMINAL_RESISTANCE:g}")
        self.htr_res_entry.pack(side='left')
        ttk.Label(rf, text="ohm (the heater really wired; CDISP)", style='Muted.TLabel').pack(
            side='left', padx=4)
        ttk.Label(frame, text="Max current:").grid(row=2, column=0, sticky='w', padx=10, pady=3)
        self.htr_cur_var = tk.StringVar(value="2.0 A")
        self.htr_cur_cb = ttk.Combobox(frame, textvariable=self.htr_cur_var, state='readonly',
                                       values=["0.25 A", "0.5 A", "1.0 A", "2.0 A", "User"],
                                       width=10)
        self.htr_cur_cb.grid(row=2, column=1, sticky='w', padx=10, pady=3)
        ucf = ttk.Frame(frame, style='Card.TFrame')
        ucf.grid(row=2, column=2, columnspan=2, sticky='w')
        ttk.Label(ucf, text="user value").pack(side='left')
        self.htr_user_entry = ttk.Entry(ucf, width=6, font=self.FONT_TABLE, justify='center')
        self.htr_user_entry.insert(0, "1.0")
        self.htr_user_entry.pack(side='left', padx=4)
        ttk.Label(ucf, text="A").pack(side='left')
        ttk.Label(frame, text="Heater % means % of:").grid(row=3, column=0, sticky='w', padx=10, pady=3)
        self.htr_disp_var = tk.StringVar(value="Current")
        self.htr_disp_cb = ttk.Combobox(frame, textvariable=self.htr_disp_var, state='readonly',
                                        values=["Current", "Power"], width=10)
        self.htr_disp_cb.grid(row=3, column=1, sticky='w', padx=10, pady=3)
        self.lbl_htr_preview = ttk.Label(frame, text="", style='Bold.TLabel')
        self.lbl_htr_preview.grid(row=4, column=0, columnspan=4, sticky='w', padx=10, pady=(4, 0))
        self._note(frame, "Full power = the smaller of I² R and (50 V)² / R (manual 2.4.1); "
                          "25 ohm at 2 A = 100 W.\nUser value 0.1-2.0 A needs main firmware "
                          "01.03.08 or later (CLIMI).", row=5)
        self.htr_cur_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        self.htr_disp_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        for wdg in (self.htr_user_entry, self.htr_res_entry):
            wdg.bind('<KeyRelease>', lambda e: self._refresh_states())
        hb = ttk.Frame(frame, style='Card.TFrame')
        hb.grid(row=6, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 8))
        hb.grid_columnconfigure((0, 1), weight=1)
        self.htr_read_btn = ttk.Button(hb, text="Read from instrument", command=self._read_heater_setup)
        self.htr_read_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.htr_send_btn = ttk.Button(hb, text="Send to instrument", command=self._send_heater_setup)
        self.htr_send_btn.grid(row=0, column=1, sticky='ew', padx=4)

    def _create_sensor_panel(self, parent, grid_row):
        frame = self._section(parent, grid_row, '5. Sensor input and front display (optional)')
        B = Lakeshore340_Backend
        self.sensor_edit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Let me change the sensor input and display settings",
                        variable=self.sensor_edit_var, command=self._refresh_states).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=10, pady=(4, 2))
        ttk.Label(frame, text="Input:").grid(row=1, column=0, sticky='w', padx=10, pady=3)
        self.sens_input_var = tk.StringVar(value='A')
        self.sens_input_cb = ttk.Combobox(frame, textvariable=self.sens_input_var, state='readonly',
                                          values=['A', 'B', 'C', 'D'], width=4)
        self.sens_input_cb.grid(row=1, column=1, sticky='w', padx=10, pady=3)
        self.sens_input_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_states())
        ttk.Label(frame, text="press Read input before Send",
                  style='Muted.TLabel').grid(row=1, column=2, columnspan=2, sticky='w')
        ttk.Label(frame, text="Sensor type:").grid(row=2, column=0, sticky='w', padx=10, pady=3)
        self.sens_type_var = tk.StringVar(value=B.SENSOR_TYPES[8])
        self.sens_type_cb = ttk.Combobox(frame, textvariable=self.sens_type_var, state='readonly',
                                         values=[v for k, v in B.SENSOR_TYPES.items() if k],
                                         width=24)
        self.sens_type_cb.grid(row=2, column=1, columnspan=3, sticky='w', padx=10, pady=3)
        ttk.Label(frame, text="Curve number:").grid(row=3, column=0, sticky='w', padx=10, pady=3)
        self.sens_curve_entry = ttk.Entry(frame, width=6, font=self.FONT_TABLE, justify='center')
        self.sens_curve_entry.grid(row=3, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(frame, text="blank = leave the curve as it is",
                  style='Muted.TLabel').grid(row=3, column=2, columnspan=2, sticky='w')
        self._note(frame, "The 340 sets excitation and range from the type itself. Special "
                          "inputs are set up on the front panel.", row=4)
        sb = ttk.Frame(frame, style='Card.TFrame')
        sb.grid(row=5, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 6))
        sb.grid_columnconfigure((0, 1), weight=1)
        self.sens_read_btn = ttk.Button(sb, text="Read input", command=self._read_sensor)
        self.sens_read_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.sens_send_btn = ttk.Button(sb, text="Send input", command=self._send_sensor)
        self.sens_send_btn.grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Separator(frame).grid(row=6, column=0, columnspan=4, sticky='ew', padx=10, pady=4)
        df = ttk.Frame(frame, style='Card.TFrame')
        df.grid(row=7, column=0, columnspan=4, sticky='w', padx=10, pady=3)
        ttk.Label(df, text="Display shows").pack(side='left')
        self.disp_n_var = tk.StringVar(value="2")
        self.disp_n_cb = ttk.Combobox(df, textvariable=self.disp_n_var, state='readonly',
                                      values=[str(k) for k in range(1, 9)], width=3)
        self.disp_n_cb.pack(side='left', padx=4)
        ttk.Label(df, text="fields.").pack(side='left')
        ff = ttk.Frame(frame, style='Card.TFrame')
        ff.grid(row=8, column=0, columnspan=4, sticky='w', padx=10, pady=3)
        ttk.Label(ff, text="Field").pack(side='left')
        self.disp_field_var = tk.StringVar(value="1")
        self.disp_field_cb = ttk.Combobox(ff, textvariable=self.disp_field_var, state='readonly',
                                          values=[str(k) for k in range(1, 9)], width=3)
        self.disp_field_cb.pack(side='left', padx=4)
        # Another field must be read before it can be sent.
        self.disp_field_cb.bind('<<ComboboxSelected>>', lambda e: self._display_field_changed())
        ttk.Label(ff, text="shows input").pack(side='left')
        self.disp_fin_var = tk.StringVar(value="A")
        self.disp_fin_cb = ttk.Combobox(ff, textvariable=self.disp_fin_var, state='readonly',
                                        values=['A', 'B', 'C', 'D'], width=3)
        self.disp_fin_cb.pack(side='left', padx=4)
        ttk.Label(ff, text="as").pack(side='left')
        self.disp_src_var = tk.StringVar(value=B.DISPLAY_SOURCES[1])
        self.disp_src_cb = ttk.Combobox(ff, textvariable=self.disp_src_var, state='readonly',
                                        values=list(B.DISPLAY_SOURCES.values()), width=12)
        self.disp_src_cb.pack(side='left', padx=4)
        db = ttk.Frame(frame, style='Card.TFrame')
        db.grid(row=9, column=0, columnspan=4, sticky='ew', padx=6, pady=(2, 8))
        db.grid_columnconfigure((0, 1), weight=1)
        self.disp_read_btn = ttk.Button(db, text="Read display", command=self._read_display)
        self.disp_read_btn.grid(row=0, column=0, sticky='ew', padx=4)
        self.disp_send_btn = ttk.Button(db, text="Send display", command=self._send_display)
        self.disp_send_btn.grid(row=0, column=1, sticky='ew', padx=4)

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(2, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        status_frame = ttk.Frame(panel)
        status_frame.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        status_frame.grid_columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(status_frame, text="READY: CONNECT OR START",
                                   font=('Segoe UI', 15, 'bold'), bg=self.CLR_FRAME_BG,
                                   fg=self.CLR_TEXT_DARK, pady=10)
        self.lbl_status.grid(row=0, column=0, sticky='ew')
        self.lbl_current_temp = tk.Label(status_frame, text="--- K", font=('Segoe UI', 26, 'bold'),
                                         bg=self.CLR_FRAME_BG, fg=self.CLR_ACCENT_RED, padx=20)
        self.lbl_current_temp.grid(row=0, column=1, sticky='e', padx=(10, 0))

        strip = tk.Frame(panel, bg=self.CLR_FRAME_BG)
        strip.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self.present = {}
        for col, key in enumerate(("Setpoint", "Heater", "PID", "Zone")):
            strip.grid_columnconfigure(col, weight=1)
            tk.Label(strip, text=key, bg=self.CLR_FRAME_BG, fg=self.CLR_MUTED,
                     font=self.FONT_SMALL).grid(row=0, column=col, sticky='w', padx=8, pady=(4, 0))
            lbl = tk.Label(strip, text="--", bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK,
                           font=('Segoe UI', 10, 'bold'), justify='left', anchor='w')
            lbl.grid(row=1, column=col, sticky='w', padx=8, pady=(0, 4))
            self.present[key] = lbl

        container = ttk.LabelFrame(panel, text='Live Temperature')
        container.grid(row=2, column=0, sticky='nsew')
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)
        self.line_setp = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN, linestyle='--',
                                           label='Setpoint')[0]
        self.line_temp = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker='o',
                                           markersize=3, linestyle='-', label='Temperature')[0]
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle='--', alpha=0.6)
        self.ax_temp.legend(loc='best', frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis='x', which='both', bottom=False, top=False,
                                 labelbottom=False)
        self.line_heater = self.ax_heater.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.',
                                               markersize=3, linestyle='-')[0]
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        cframe = ttk.LabelFrame(panel, text='Console')
        cframe.grid(row=3, column=0, sticky='nsew', pady=(6, 0))
        cframe.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            cframe, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', borderwidth=0, height=9)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.log("Console initialized. Connect to read the instrument, or set the ramp and Start.")

    # ------------------------------------------------------- enable / disable

    @staticmethod
    def _set_state(widget, enabled, readonly_when_on=False):
        try:
            if enabled:
                widget.config(state='readonly' if readonly_when_on else 'normal')
            else:
                widget.config(state='disabled')
        except tk.TclError:
            pass

    def _refresh_states(self):
        """The one place that greys widgets out: a widget is live only when
        what it changes can take effect."""
        if not self._ui_ready:
            return
        connected = self.backend.is_connected
        running = self.is_running
        idle = not running
        fixed = self.pid_mode_var.get() == 'fixed'

        self._set_state(self.ls_cb, idle and not connected, readonly_when_on=True)
        for b in (self.scan_btn, self.identify_btn):
            self._set_state(b, idle and not connected)
        self._set_state(self.connect_btn, idle)
        self.connect_btn.config(text="Disconnect" if connected else "Connect")

        for e in self.entries.values():
            self._set_state(e, idle)
        self._set_state(self.input_cb, idle, readonly_when_on=True)
        self._set_state(self.start_button, idle)
        self._set_state(self.stop_button, running)

        w = self.pid_w
        custom = w['preset_var'].get() == PID_CUSTOM
        self._set_state(w['preset'], fixed, readonly_when_on=True)
        for key in ('p', 'i', 'd'):
            self._set_state(w[key], fixed and custom)
        self._set_state(w['mout'], fixed)
        self._set_state(w['range'], fixed, readonly_when_on=True)
        self._set_state(self.load_pid_btn, fixed and connected)
        self._set_state(self.apply_fixed_btn, fixed and running)
        for b in (self.zone_seg_spin, self.zone_tmin, self.zone_tmax, self.zone_gen_btn,
                  self.zone_reset_btn, self.zone_add_btn, self.zone_del_btn):
            self._set_state(b, not fixed and idle)
        self._set_state(self.zone_apply_btn, not fixed and running)
        for row in self.zone_rows:
            for key in ('ub', 'p', 'i', 'd', 'mout'):
                self._set_state(row[key], not fixed)
            self._set_state(row['range_cb'], not fixed, readonly_when_on=True)

        edit_htr = self.htr_edit_var.get() and idle
        self._set_state(self.htr_res_entry, edit_htr)
        self._set_state(self.htr_cur_cb, edit_htr, readonly_when_on=True)
        self._set_state(self.htr_user_entry, edit_htr and self.htr_cur_var.get() == "User")
        self._set_state(self.htr_disp_cb, edit_htr, readonly_when_on=True)
        self._set_state(self.htr_read_btn, connected and idle)
        self._set_state(self.htr_send_btn, connected and edit_htr and self.heater_cfg['known'])
        self._refresh_heater_preview()

        edit_s = self.sensor_edit_var.get() and idle
        for wdg in (self.sens_input_cb, self.sens_type_cb, self.disp_n_cb, self.disp_field_cb,
                    self.disp_fin_cb, self.disp_src_cb):
            self._set_state(wdg, edit_s, readonly_when_on=True)
        self._set_state(self.sens_curve_entry, edit_s)
        for b in (self.sens_read_btn, self.disp_read_btn):
            self._set_state(b, connected and idle)
        self._set_state(self.sens_send_btn, connected and edit_s
                        and self.sensor_read_input == self.sens_input_var.get())
        self._set_state(self.disp_send_btn, connected and edit_s and self.display_read_ok)

    def _on_mode_changed(self):
        if self.is_running:
            if self.pid_mode_var.get() == 'zones':
                if self._apply_zone_table():
                    self.log("Zones on: PID/range follow the reading from the next sample.")
                else:
                    self.pid_mode_var.set('fixed')
                    self.log("Staying with one setting.")
            else:
                self.log("Zones off: PID/range stay as they are until you press Apply now.")
        self._refresh_states()

    def _check_max_range(self, ranges):
        """Ranges above the CLIMIT max range read at Start would make RANGE
        'not stick' mid-run and abort it, so they are refused up front."""
        if self.limits is None:
            return
        too_high = sorted(r for r in ranges if r > self.limits['max_range'])
        if too_high:
            raise ValueError(f"heater range(s) {too_high} exceed the 340's max range "
                             f"{self.limits['max_range']} (CLIMIT).")

    # ----------------------------------------------------- watt labels

    def _full_power(self):
        c = self.heater_cfg
        return loop1_full_power(c['max_current'], c['resistance'], c['user_current'])

    @staticmethod
    def _safe_range(label, default):
        try:
            return parse_range(label)
        except (ValueError, IndexError):
            return default

    def _refresh_range_labels(self):
        if not self._ui_ready:
            return
        full = self._full_power()
        w = self.pid_w
        code = self._safe_range(w['range_var'].get(), 5)
        w['range']['values'] = [range_label(k, full) for k in (5, 4, 3, 2, 1)]
        w['range_var'].set(range_label(code, full))
        src = ("as read from the instrument" if self.heater_cfg['known'] else
               f"nominal ({NOMINAL_RESISTANCE:g} ohm, 2 A) until read from the instrument")
        self.lbl_power_legend.config(
            text=f"Range power, {src}:\n" + ", ".join(
                f"{k} = {format_power(range_power(k, full))}" for k in (5, 4, 3, 2, 1)))

    def _heater_form(self):
        """(max current code, user current, resistance, display code) from the form."""
        cur_map = {"0.25 A": 1, "0.5 A": 2, "1.0 A": 3, "2.0 A": 4, "User": 5}
        cur = cur_map.get(self.htr_cur_var.get(), 4)
        user = float(self.htr_user_entry.get()) if cur == 5 else 0.0
        res = float(self.htr_res_entry.get())
        disp = 2 if self.htr_disp_var.get() == "Power" else 1
        return cur, user, res, disp

    def _refresh_heater_preview(self):
        try:
            cur, user, res, _disp = self._heater_form()
            text = (f"This setting gives up to "
                    f"{format_power(loop1_full_power(cur, res, user))} on range 5.")
        except (ValueError, KeyError):
            text = "Type numbers for the resistance and the user current."
        self.lbl_htr_preview.config(text=text)

    # --- ZONE TABLE HELPERS ---

    def _add_zone_row(self, upper=ZONE_T_MAX, p=0.5, i=4.0, d=0.0, rng=5, mout=0.0):
        r = len(self.zone_rows) + 1
        widgets, cells = [], {}
        for col, key, val, width in ((0, 'ub', upper, 7), (1, 'p', p, 6), (2, 'i', i, 6),
                                     (3, 'd', d, 5), (4, 'mout', mout, 6)):
            e = ttk.Entry(self.zone_table, width=width, font=self.FONT_TABLE, justify='center')
            e.insert(0, f"{val:g}")
            e.grid(row=r, column=col, padx=3, pady=1)
            widgets.append(e)
            cells[key] = e
        rng_var = tk.StringVar(value=str(int(rng)))
        rng_cb = ttk.Combobox(self.zone_table, textvariable=rng_var,
                              values=['5', '4', '3', '2', '1', '0'], state='readonly', width=4)
        rng_cb.grid(row=r, column=5, padx=3, pady=1)
        widgets.append(rng_cb)
        cells.update({'range': rng_var, 'range_cb': rng_cb, 'widgets': widgets})
        self.zone_rows.append(cells)
        self._refresh_states()

    def _remove_zone_row(self):
        if len(self.zone_rows) <= 1:
            self.log("At least one zone is required.")
            return
        for w in self.zone_rows.pop()['widgets']:
            w.destroy()

    def _clear_zone_rows(self):
        while self.zone_rows:
            for w in self.zone_rows.pop()['widgets']:
                w.destroy()

    def _reset_zone_rows(self):
        self._clear_zone_rows()
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)
        self.zone_segments_var.set(str(len(ZONE_DEFAULTS)))
        self.log("Zone table reset to the CCR defaults.")

    def _generate_zone_rows(self):
        try:
            n = int(self.zone_segments_var.get())
            t_min, t_max = float(self.zone_tmin.get()), float(self.zone_tmax.get())
            zones = generate_equal_zones(n, t_min, t_max)
        except (ValueError, tk.TclError) as e:
            self.log(f"ERROR: cannot generate segments: {e}")
            return
        self._clear_zone_rows()
        for z in zones:
            self._add_zone_row(*z)
        self.log(f"Generated {n} equal segment(s) from {t_min:g} K to "
                 f"{t_max:g} K with default PID/range per segment.")

    def _collect_zones(self):
        """(ub, P, I, D, range, MOUT) rows sorted by bound; raises ValueError."""
        zones = []
        for r in self.zone_rows:
            ub = float(r['ub'].get())
            p = float(r['p'].get())
            i = float(r['i'].get())
            d = float(r['d'].get())
            mout = float(r['mout'].get())
            rng = int(r['range'].get())
            try:
                validate_pid(p, i, d)
                validate_mout(mout)
            except ValueError as e:
                raise ValueError(f"zone <= {ub:g} K: {e}")
            zones.append((ub, p, i, d, rng, mout))
        if not zones:
            raise ValueError("the zone table is empty")
        zones.sort(key=lambda z: z[0])
        bounds = [z[0] for z in zones]
        if len(set(bounds)) != len(bounds):
            raise ValueError("two zones share the same upper bound")
        return zones

    def _apply_zone_table(self):
        """Take the edited table into use.  Returns False (and keeps the old
        table) if it is invalid."""
        try:
            zones = self._collect_zones()
            if self.is_running:
                self._check_max_range({z[4] for z in zones})
        except ValueError as e:
            self.log(f"ERROR in zone table: {e}")
            return False
        self.zones = zones
        self.active_zone = None   # re-evaluate on the next poll
        self.log("Zone table applied: " + "; ".join(
            f"<={ub:g} K: P{p:g}/I{i:g}/D{d:g}/MOUT{m:g}/R{r}"
            for ub, p, i, d, r, m in self.zones))
        return True

    def _send_loop_settings(self, p, i, d, mout, rng):
        self.backend.set_pid(p, i, d)
        self.backend.set_mout(mout)
        self.backend.set_heater_range(rng)

    def _apply_zone_for_temperature(self, temp, force=False):
        """Send the PID + MOUT + range of the zone <temp> is in if it changed."""
        if self.pid_mode_var.get() != 'zones' or not self.zones:
            return
        idx = select_zone(self.zones, temp)
        if idx == self.active_zone and not force:
            return
        ub, p, i, d, rng, mout = self.zones[idx]
        lo = self.zones[idx - 1][0] if idx > 0 else 0.0
        self._send_loop_settings(p, i, d, mout, rng)
        self.active_zone = idx
        self.lbl_zone_status.config(
            text=f"Active zone {idx + 1}: {lo:g}-{ub:g} K  P={p:g} I={i:g} D={d:g} "
                 f"MOUT={mout:g}% range {rng}")
        self.log(f"ZONE {idx + 1} ({lo:g}-{ub:g} K) at T={temp:.3f} K: "
                 f"PID 1,{p:g},{i:g},{d:g}; MOUT 1,{mout:g}; RANGE {rng}")
        self._csv_note(f"zone {idx + 1}: PID {p:g}/{i:g}/{d:g} MOUT {mout:g} range {rng}")

    # --- FIXED PID ---

    def _on_preset(self):
        w = self.pid_w
        name = w['preset_var'].get()
        if name in PID_PRESETS:
            for key, val in zip(('p', 'i', 'd'), PID_PRESETS[name]):
                w[key].config(state='normal')
                w[key].delete(0, 'end')
                w[key].insert(0, f"{val:g}")
        self._refresh_states()

    def _fixed_settings(self):
        w = self.pid_w
        try:
            vals = (float(w['p'].get()), float(w['i'].get()), float(w['d'].get()),
                    float(w['mout'].get()), parse_range(w['range_var'].get()))
        except (ValueError, IndexError):
            raise ValueError("P, I, D and MOUT must be numbers.")
        validate_pid(*vals[:3])
        validate_mout(vals[3])
        if vals[4] == 0:
            raise ValueError("Heater range 0 (Off) cannot ramp. Pick 1-5 or use zones.")
        return vals

    def _load_present_pid(self):
        if not self.backend.is_connected:
            self.log("Connect first to read the present PID.")
            return
        try:
            p, i, d = self.backend.get_pid()
            mout = self.backend.get_mout()
            rng = self.backend.get_heater_range()
        except Exception as e:
            self.log(f"ERROR reading the present PID: {e}")
            return
        w = self.pid_w
        w['preset_var'].set(PID_CUSTOM)
        for key, val in (('p', p), ('i', i), ('d', d), ('mout', mout)):
            w[key].config(state='normal')
            w[key].delete(0, 'end')
            w[key].insert(0, f"{val:g}")
        if rng > 0:
            w['range_var'].set(range_label(rng, self._full_power()))
        self.log(f"Present: PID {p:g}/{i:g}/{d:g}, MOUT {mout:g} %, range {rng}. Copied into "
                 "the form as Custom; edit and Start or Apply.")
        self._refresh_states()

    def _apply_fixed_live(self):
        if not self.is_running:
            return
        try:
            p, i, d, mout, rng = self._fixed_settings()
            self._check_max_range({rng})
            self._send_loop_settings(p, i, d, mout, rng)
            self.log(f"Applied: PID 1,{p:g},{i:g},{d:g}; MOUT 1,{mout:g}; RANGE {rng}")
            self._csv_note(f"manual PID {p:g}/{i:g}/{d:g} MOUT {mout:g} range {rng}")
        except Exception as e:
            self.log(f"ERROR applying: {e}")

    # --- HEATER SETUP ---

    def _apply_heater_cfg(self, cfg):
        self.heater_cfg = dict(cfg, known=True)
        names = {1: "0.25 A", 2: "0.5 A", 3: "1.0 A", 4: "2.0 A", 5: "User"}
        self.htr_cur_var.set(names.get(cfg['max_current'], "2.0 A"))
        for entry, val in ((self.htr_user_entry, cfg['user_current']),
                           (self.htr_res_entry, cfg['resistance'])):
            entry.config(state='normal')
            entry.delete(0, 'end')
            entry.insert(0, f"{val:g}")
        self.htr_disp_var.set("Power" if cfg['display'] == 2 else "Current")
        self._refresh_range_labels()
        self._refresh_states()

    def _read_heater_setup(self, quiet=False):
        try:
            cfg = self.backend.get_heater_setup()
            self._apply_heater_cfg(cfg)
            if not quiet:
                self.log(f"Heater setup: max current {self.htr_cur_var.get()}, "
                         f"{cfg['resistance']:g} ohm, max range {cfg['max_range']}, setpoint "
                         f"limit {cfg['sp_limit']:g} K -> up to "
                         f"{format_power(self._full_power())} on range 5.")
        except Exception as e:
            self.log(f"ERROR reading the heater setup: {e}")

    def _send_heater_setup(self):
        try:
            cur, user, res, disp = self._heater_form()
            p = format_power(loop1_full_power(cur, res, user))
        except (ValueError, KeyError) as e:
            self.log(f"ERROR: heater setup: {e}")
            return
        if not self._ask("Send heater setup",
                         f"Set max current {self.htr_cur_var.get()}"
                         f"{f' ({user:g} A)' if cur == 5 else ''} and heater resistance "
                         f"{res:g} ohm?\n\nRange 5 will then give up to {p}. Use the resistance "
                         "of the heater that is really wired, or the heater can be overdriven."):
            return
        try:
            self.log(f"SENT: {self.backend.set_heater_setup(cur, user, res, disp)}")
            time.sleep(0.2)
            self._read_heater_setup()
        except Exception as e:
            self.log(f"ERROR sending the heater setup: {e}")

    # --- SENSOR / DISPLAY ---

    def _read_sensor(self):
        inp = self.sens_input_var.get()
        try:
            code = self.backend.get_input_type(inp)
            curve = self.backend.get_input_curve(inp)
        except Exception as e:
            self.log(f"ERROR reading input {inp}: {e}")
            return
        name = Lakeshore340_Backend.SENSOR_TYPES.get(code, f"code {code}")
        if code != 0:
            self.sens_type_var.set(name)
        self.sensor_read_input = inp
        self.sens_curve_entry.config(state='normal')
        self.sens_curve_entry.delete(0, 'end')
        self.sens_curve_entry.insert(0, str(curve))
        self.log(f"Input {inp}: {name}, curve {curve}."
                 + (" (Special: set up on the front panel; not changed here.)" if code == 0 else ""))
        self._refresh_states()

    def _send_sensor(self):
        inp = self.sens_input_var.get()
        code = next(k for k, v in Lakeshore340_Backend.SENSOR_TYPES.items()
                    if v == self.sens_type_var.get())
        curve_txt = self.sens_curve_entry.get().strip()
        try:
            curve = int(curve_txt) if curve_txt else None
            if curve is not None and not 0 <= curve <= 60:
                raise ValueError("curve must be 0-60")
        except ValueError as e:
            self.log(f"ERROR: curve number: {e}")
            return
        if not self._ask("Send sensor input",
                         f"Change input {inp} to {self.sens_type_var.get()}"
                         f"{'' if curve is None else f', curve {curve}'}?\n\nThis changes how the "
                         "input reads temperature. A wrong type or curve gives wrong readings "
                         "and wrong control."):
            return
        try:
            self.log(f"SENT: {self.backend.set_input_type(inp, code)}")
            if curve is not None:
                self.log(f"SENT: {self.backend.set_input_curve(inp, curve)}")
            time.sleep(0.3)
            self._read_sensor()
        except Exception as e:
            self.log(f"ERROR sending input settings: {e}")

    def _display_field_changed(self):
        self.display_read_ok = False
        self._refresh_states()

    def _read_display(self):
        try:
            n = self.backend.get_display_fields()
            inp, src = self.backend.get_display_field(int(self.disp_field_var.get()))
            self.disp_n_var.set(str(n))
            self.disp_fin_var.set(inp if inp in ('A', 'B', 'C', 'D') else 'A')
            self.disp_src_var.set(Lakeshore340_Backend.DISPLAY_SOURCES.get(src, "Kelvin"))
            self.display_read_ok = True
            self.log(f"Display: {n} field(s); field {self.disp_field_var.get()} = input {inp}, "
                     f"{self.disp_src_var.get()}.")
        except Exception as e:
            self.log(f"ERROR reading the display: {e}")
        self._refresh_states()

    def _send_display(self):
        try:
            src = next(k for k, v in Lakeshore340_Backend.DISPLAY_SOURCES.items()
                       if v == self.disp_src_var.get())
            self.log(f"SENT: {self.backend.set_display_fields(int(self.disp_n_var.get()))}")
            self.log("SENT: " + self.backend.set_display_field(
                int(self.disp_field_var.get()), self.disp_fin_var.get(), src))
        except Exception as e:
            self.log(f"ERROR sending the display: {e}")

    # --- LOGGING ---

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _beep(self):
        try:
            self.root.bell()
        except Exception:
            pass

    def _set_status(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _open_data_file(self):
        os.makedirs("data", exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_filepath = os.path.join("data", f"TRamp_L340_{stamp}.csv")
        self.data_file = open(self.data_filepath, 'w', newline='', encoding='utf-8')
        self.csv_writer = csv.writer(self.data_file)
        p = self.params
        self.csv_writer.writerow([ascii_label(f"# {self.backend.idn}")])
        self.csv_writer.writerow([ascii_label(
            f"# target {p['setpoint']} K, rate {p['rate']} K/min, input {p['input']}, "
            f"tolerance {p['tol']} K, dwell {p['dwell']} s, mode {self.pid_mode_var.get()}, "
            f"max {p['max_temp']} K")])
        self.csv_writer.writerow(["Timestamp", "Elapsed_s", "Setpoint_K",
                                  "Temperature_K", "Heater_pct", "Range",
                                  "Zone", "Note"])
        self.data_file.flush()
        self.log(f"Logging data to: {self.data_filepath}")

    def _csv_row(self, elapsed, setp, temp, htr, rng, note=""):
        if not self.csv_writer:
            return
        zone = "" if self.active_zone is None else str(self.active_zone + 1)
        try:
            self.csv_writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}",
                f"{setp:.4f}", f"{temp:.4f}", f"{htr:.2f}", rng, zone, ascii_label(note)])
            self.data_file.flush()
            os.fsync(self.data_file.fileno())
        except Exception as e:
            self.log(f"WARN: data write failed: {e}")

    def _csv_note(self, note):
        """A row that carries only a note (events between samples)."""
        if self.csv_writer:
            try:
                self.csv_writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    f"{time.time() - self.start_time:.2f}",
                    "", "", "", "", "", ascii_label(note)])
                self.data_file.flush()
            except Exception:
                pass

    def _close_data_file(self):
        if self.data_file:
            try:
                self.data_file.flush()
                self.data_file.close()
                self.log(f"Data file closed: {self.data_filepath}")
            except Exception:
                pass
            finally:
                self.data_file = None
                self.csv_writer = None

    # --- POLLING (one chain) ---

    def _schedule_poll(self, delay_ms):
        """Only ever one pending tick: cancel before scheduling."""
        self._cancel_poll()
        self.poll_job = self.root.after(int(delay_ms), self._poll_tick)

    def _cancel_poll(self):
        if self.poll_job is not None:
            try:
                self.root.after_cancel(self.poll_job)
            except Exception:
                pass
            self.poll_job = None

    def _poll_tick(self):
        self.poll_job = None
        if not self.backend.is_connected:
            return
        if self.is_running:
            self._monitoring_tick()
        else:
            self._idle_tick()

    def _heater_text(self, pct, rng):
        if rng == 0:
            return f"off ({pct:.1f} %)"
        full = range_power(rng, self._full_power())
        watts = heater_watts(pct, full, self.heater_cfg['display'] == 2)
        return f"{pct:.1f} % on R{rng}\n~{format_power(watts)} of {format_power(full)}"

    def _idle_tick(self):
        try:
            temp, htr, setp, _st = self.backend.get_status(self.input_var.get())
            rng = self.backend.get_heater_range()
            self.lbl_current_temp.config(text=f"{temp:.3f} K")
            self.present["Setpoint"].config(text=f"{setp:.3f} K")
            self.present["Heater"].config(text=self._heater_text(htr, rng))
        except Exception as e:
            self.log(f"Reading stopped: {e}. Press Connect again.")
            self.backend.close()
            self.sensor_read_input = None
            self.display_read_ok = False
            self.lbl_conn.config(text="Disconnected after a read error.")
            self._refresh_states()
            return
        self._schedule_poll(self.IDLE_POLL_MS)

    def _ask(self, title, text):
        """A user-initiated yes/no. Polling stops while the dialog is open."""
        self._cancel_poll()
        try:
            return messagebox.askyesno(title, text)
        finally:
            if self.backend.is_connected:
                self._schedule_poll(300)

    # --- CONNECT / VISA DISCOVERY ---

    def _selected_address(self):
        label = self.ls_cb.get()
        return self.resource_labels.get(label, label)

    def _toggle_connect(self):
        if self.backend.is_connected:
            self._cancel_poll()
            self.backend.close()
            self.sensor_read_input = None
            self.display_read_ok = False
            self.lbl_conn.config(text="Disconnected. The instrument keeps its settings.")
            self.log("Disconnected (session closed, nothing changed on the instrument).")
            self._refresh_states()
            return
        try:
            self._connect()
        except Exception as e:
            hint = explain_visa_error(e)
            self.log(f"ERROR connecting: {e}" + (f"\nHINT: {hint}" if hint else ""))
            self.backend.close()
            self._refresh_states()

    def _connect(self):
        addr = self._selected_address()
        if not addr:
            raise ValueError("Please scan and select the Lakeshore VISA address.")
        self.log(f"Connecting to {addr}...")
        idn = self.backend.connect(addr)
        if not self.backend.is_model_340():
            self.backend.close()
            raise RuntimeError(f"'{idn}' is not a Lake Shore Model 340. Refusing to send "
                               "340-only commands (CSET, RANGE n) to it.")
        self.backend.clear_status()
        self.lbl_conn.config(text=f"Connected: {idn}")
        self.log(f"Connected: {idn}")
        self._read_present_state()
        self._refresh_states()
        self._schedule_poll(200)

    def _read_present_state(self):
        """Read-only snapshot of what the instrument is doing now."""
        b = self.backend
        inp = self.input_var.get()
        try:
            self._read_heater_setup(quiet=True)
            cfg = self.heater_cfg
            cset = b.get_control_loop(1)
            cmode = b.get_control_mode(1)
            p, i, d = b.get_pid()
            mout = b.get_mout()
            rng = b.get_heater_range()
            setp = b.get_setpoint()
            modes = {1: "Manual PID", 2: "Zone", 3: "Open Loop", 4: "AutoTune PID",
                     5: "AutoTune PI", 6: "AutoTune P"}
            self.log(f"Loop 1: input {cset['input']}, {'enabled' if cset['enabled'] else 'DISABLED'}, "
                     f"{modes.get(cmode, cmode)}, setpoint {setp:.3f} K, PID {p:g}/{i:g}/{d:g}, "
                     f"MOUT {mout:g} %, range {rng} "
                     f"({format_power(range_power(rng, self._full_power()))})")
            self.log(f"Limits: setpoint <= {cfg['sp_limit']:g} K, max range {cfg['max_range']}, "
                     f"max current {self.htr_cur_var.get()}, heater {cfg['resistance']:g} ohm "
                     f"-> up to {format_power(self._full_power())} on range 5.")
            if mout > 0:
                self.log(f"  NOTE: MOUT is {mout:g} % (adds to PID). Start sends the MOUT "
                         "you set in this window.")
            self.present["PID"].config(text=f"{p:g}/{i:g}/{d:g}")
            code = b.get_input_type(inp)
            self.log(f"Input {inp}: {Lakeshore340_Backend.SENSOR_TYPES.get(code, code)}, "
                     f"curve {b.get_input_curve(inp)}")
        except Exception as e:
            self.log(f"Could not read everything: {e}")

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = list(self.backend.rm.list_resources())
        except Exception as e:
            self.log(f"Scan error: {e}")
            return
        if resources:
            self.log(f"Found: {resources}")
            self.resource_labels = {r: r for r in resources}
            self.ls_cb['values'] = resources
            # The lab's 340 is set to address 19 (3 Sep 2026); pre-select
            # it when present. Identify confirms by *IDN?.
            preferred = [r for r in resources if _is_gpib_address(r, LAKESHORE340_ADDRESS_HINT)]
            if preferred:
                self.ls_cb.set(preferred[0])
                self.log(f"Pre-selected {preferred[0]} (lab address 19). "
                         "Press Identify to confirm it answers as MODEL340.")
            elif len(resources) == 1:
                self.ls_cb.set(resources[0])
            else:
                self.ls_cb.set('')
                self.log("Several addresses. Press Identify to find the "
                         "MODEL340, or pick it yourself.")
        else:
            self.log("No VISA instruments found.")

    def _identify_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        if self.is_running:
            return
        addresses = list(self.resource_labels.values())
        if not addresses:
            self.log("Nothing to identify: press Scan first.")
            return
        self.log(f"Identifying {len(addresses)} address(es) with *IDN? "
                 "(2 s timeout each)...")
        self.root.update_idletasks()
        replies = self.backend.identify_resources(addresses)
        labels, chosen = {}, None
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
                label = f"{addr}  ({reply[:32]})"
                labels[label] = addr
                if 'MODEL340' in reply.upper().replace(' ', '') and chosen is None:
                    chosen = label
        self.resource_labels = labels
        self.ls_cb['values'] = list(labels.keys())
        if chosen:
            self.ls_cb.set(chosen)
            self.log(f"Selected the Model 340 at {labels[chosen]}.")
        else:
            self.ls_cb.set('')
            self.log("No address answered as a Model 340.")

    # --- RUN CONTROL ---

    def _validate_and_get_params(self):
        try:
            params = {
                'setpoint': float(self.entries['target'].get()),
                'rate': float(self.entries['rate'].get()),
                'delay_s': float(self.entries['delay'].get()),
                'tol': float(self.entries['tol'].get()),
                'dwell': float(self.entries['dwell'].get()),
                'max_temp': float(self.entries['max_temp'].get()),
                'input': self.input_var.get(),
            }
        except ValueError:
            raise ValueError("Every ramp field must be a number.")
        if not (0.1 <= params['rate'] <= 100):
            raise ValueError("Ramp rate must be 0.1-100 K/min on a Model 340.")
        if params['delay_s'] <= 0:
            raise ValueError("'Log every' must be positive.")
        if params['tol'] <= 0:
            raise ValueError("Tolerance must be positive.")
        if params['dwell'] < 0:
            raise ValueError("Dwell cannot be negative.")
        if params['setpoint'] <= 0:
            raise ValueError("Target temperature must be positive.")
        if params['setpoint'] >= params['max_temp']:
            raise ValueError(f"The target {params['setpoint']:g} K is not below the safety "
                             f"maximum {params['max_temp']:g} K.")
        return params

    def start_ramp(self):
        if self.is_running:
            return
        self.loop_started = False
        try:
            self.params = self._validate_and_get_params()
            if self.pid_mode_var.get() == 'zones':
                self.zones = self._collect_zones()
                fixed = None
            else:
                self.zones = []
                fixed = self._fixed_settings()
            if not self.backend.is_connected:
                self._connect()
            self._cancel_poll()
            b = self.backend
            inp = self.params['input']

            # ---- read-only checks: nothing is written before they pass ----
            limits = b.get_control_limits(1)
            if self.params['setpoint'] > limits['sp_limit']:
                raise ValueError(
                    f"Target {self.params['setpoint']} K is above the 340's setpoint limit of "
                    f"{limits['sp_limit']:g} K (CLIMIT). Raise the limit in the Direct Control "
                    "module first.")
            self.limits = limits
            self._check_max_range({z[4] for z in self.zones} if fixed is None else {fixed[4]})
            temp_now = b.get_temperature(inp)
            status = b.get_reading_status(inp)
            if status:
                raise RuntimeError(f"Input {inp} reading is not valid ({status}).")
            if temp_now >= self.params['max_temp']:
                raise RuntimeError(f"Present temperature {temp_now:.2f} K is already above the "
                                   f"safety maximum {self.params['max_temp']:g} K.")
            code, text = b.get_heater_status()
            if code != 0:
                raise RuntimeError(f"Heater error HTRST? {code:02d}: {text}. Reading the error "
                                   "clears it on the instrument, so check the heater circuit "
                                   "before pressing Start again.")
            self.direction = 'up' if self.params['setpoint'] >= temp_now else 'down'
            self.start_time = time.time()
            self._open_data_file()          # a disk problem stops us here, before any write
            self.log(f"Present temperature {temp_now:.3f} K; ramping {self.direction} to "
                     f"{self.params['setpoint']} K at {self.params['rate']} K/min.")

            # ---- writes.  Heater off, loop on the control input in kelvin,
            # setpoint = now, then PID / MOUT / range, then the ramp: the
            # loop never chases a leftover setpoint and never runs on stale
            # gains.
            self.loop_started = True
            b.set_heater_range(0)
            cset, cmode, limits = b.prepare_loop(inp)
            b.pin_setpoint(temp_now)
            cur = b.MAX_CURRENT_CODES.get(limits['max_current'], f"code {limits['max_current']}")
            self.log(f"Loop 1 enabled on input {cset['input']} (kelvin), Manual PID. Limits: "
                     f"setpoint <= {limits['sp_limit']:g} K, max current {cur}, max range "
                     f"{limits['max_range']}.")
            self.active_zone = None
            if fixed is None:
                self._apply_zone_for_temperature(temp_now, force=True)
            else:
                p, i, d, mout, rng = fixed
                self._send_loop_settings(p, i, d, mout, rng)
                self.log(f"One setting: PID 1,{p:g},{i:g},{d:g}; MOUT 1,{mout:g}; RANGE {rng} "
                         f"({format_power(range_power(rng, self._full_power()))})")
                self.lbl_zone_status.config(text="Active zone: -- (one setting)")
            # Re-pinned to a fresh reading right before the ramp starts.
            b.start_ramp(self.params['setpoint'], self.params['rate'], b.get_temperature(inp))
            self.log("Ramp started (setpoint pinned to present temperature first).")

            self.is_running = True
            self.reached = False
            self.stable_since = None
            self.last_htr_error = 0
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in (self.line_temp, self.line_setp, self.line_heater):
                line.set_data([], [])
            self.ax_temp.set_title(f"Ramping to {self.params['setpoint']} K")
            self._set_status(f"RAMPING TO {self.params['setpoint']} K", self.CLR_ACCENT_RED)
            self._refresh_states()
            self._schedule_poll(100)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            hint = explain_visa_error(e)
            if hint:
                self.log(f"HINT: {hint}")
            self.is_running = False
            self._cancel_poll()
            if self.loop_started:
                problems = self.backend.stop_ramp()      # half-started run: heater off
                if problems:
                    self.log("Start failed part-way and the heater could NOT be confirmed "
                             f"off: {problems}. CHECK THE INSTRUMENT.")
                    self._set_status("HEATER OFF NOT CONFIRMED: CHECK THE INSTRUMENT",
                                     self.CLR_ACCENT_RED)
                else:
                    self.log("Start failed part-way: RANGE 0 and RAMP 1,0,0 sent.")
            self.loop_started = False
            self._close_data_file()
            # Start is attended, so a dialog is acceptable here.
            messagebox.showerror("Start Failed", f"{e}" + (f"\n\n{hint}" if hint else ""))
            if self.backend.is_connected:
                self._schedule_poll(500)
            self._refresh_states()

    def stop_ramp(self, reason="Stopping ramp by user request.", heater_off=True,
                  banner=None, banner_color=None):
        """End the run.  Returns False when turning the heater off could not be
        confirmed; the banner then says so, whatever `banner` asked for."""
        if not self.is_running:
            return True
        self.log(reason)
        self.is_running = False
        self._cancel_poll()
        ok = True
        if heater_off:
            problems = self.backend.stop_ramp()
            if problems:
                ok = False
                self.log(f"HEATER OFF NOT CONFIRMED: {problems}. Check the instrument.")
            else:
                self.log("RANGE 0 and RAMP 1,0,0 sent: heater is OFF.")
        else:
            self.log("Heater left ON, loop still controlling at the setpoint.")
        self._close_data_file()
        self.loop_started = False
        self._refresh_states()
        self.canvas.draw_idle()
        if not ok:
            self._beep()
            self._set_status("HEATER OFF NOT CONFIRMED: CHECK THE INSTRUMENT",
                             self.CLR_ACCENT_RED)
        elif banner:
            self._set_status(banner, banner_color or self.CLR_FRAME_BG)
        if self.backend.is_connected:
            self._schedule_poll(self.IDLE_POLL_MS)
        return ok

    def _monitoring_tick(self):
        try:
            b = self.backend
            inp = self.params['input']
            temp, htr, setp, rd_status = b.get_status(inp)
            rng = b.get_heater_range()
            elapsed = time.time() - self.start_time

            self.lbl_current_temp.config(text=f"{temp:.3f} K")
            self.present["Setpoint"].config(text=f"{setp:.3f} K")
            self.present["Heater"].config(text=self._heater_text(htr, rng))
            self.present["Zone"].config(
                text="--" if self.active_zone is None else str(self.active_zone + 1))
            line = f"T: {temp:.3f} K | SP: {setp:.3f} K | Heater: {htr:.1f}% | R{rng}"
            if rd_status:
                line += f" | RDGST: {rd_status}"
            self.log(line)

            code, text = b.get_heater_status()
            if code != self.last_htr_error:
                self.last_htr_error = code
                if code != 0:
                    self.log(f"HEATER ERROR HTRST? {code:02d}: {text}")
                    self._beep()
                    self._csv_note(f"heater error {code:02d} {text}")
                else:
                    self.log("Heater error cleared (HTRST? 00).")

            if not rd_status and temp >= self.params['max_temp']:
                self._csv_row(elapsed, setp, temp, htr, rng, "safety maximum exceeded")
                self._beep()
                self.stop_ramp(reason=f"T {temp:.2f} K is above the safety maximum "
                                      f"{self.params['max_temp']:g} K.", heater_off=True,
                               banner="SAFETY STOP: ABOVE MAX TEMPERATURE, HEATER OFF",
                               banner_color=self.CLR_ACCENT_RED)
                return

            if rd_status:
                # An invalid/over-range reading must not drive the zone
                # table or the arrival test.
                self._csv_row(elapsed, setp, temp, htr, rng, f"reading: {rd_status}")
            else:
                self._apply_zone_for_temperature(temp)
                self._csv_row(elapsed, setp, temp, htr, rng)

            ds = self.data_storage
            ds['time'].append(elapsed)
            ds['temperature'].append(temp)
            ds['setpoint'].append(setp)
            ds['heater'].append(htr)
            self.line_temp.set_data(ds['time'], ds['temperature'])
            self.line_setp.set_data(ds['time'], ds['setpoint'])
            self.line_heater.set_data(ds['time'], ds['heater'])
            for ax in (self.ax_temp, self.ax_heater):
                ax.relim()
                ax.autoscale_view()
            self.canvas.draw_idle()

            if not self.reached and not rd_status and self._arrival_check(temp):
                return
            self._schedule_poll(self.params['delay_s'] * 1000)

        except Exception:
            # Unattended-safe: no dialog. Heater off, banner, beep, console.
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            self._beep()
            self.stop_ramp(reason="Ramp stopped after a runtime error.", heater_off=True,
                           banner="ERROR: RAMP STOPPED, HEATER OFF (see console)",
                           banner_color=self.CLR_ACCENT_RED)

    def _arrival_check(self, temp):
        """Tolerance + dwell test, both directions. True if the run ended."""
        sp = self.params['setpoint']
        if abs(temp - sp) > self.params['tol']:
            if self.stable_since is not None:
                self.log("Left the tolerance band; dwell restarted.")
                self._set_status(f"RAMPING TO {sp} K", self.CLR_ACCENT_RED)
            self.stable_since = None
            return False
        if self.stable_since is None:
            self.stable_since = time.time()
            self._set_status(f"IN BAND AT {sp} K, DWELLING...", self.CLR_STABLE_WAIT)
            self.log(f"Entered ±{self.params['tol']} K band; dwell {self.params['dwell']} s started.")
            return False
        if time.time() - self.stable_since < self.params['dwell']:
            return False
        self.reached = True
        self._beep()
        if self.hold_var.get():
            self._set_status(f"AT {sp} K | HOLDING (heater on)", self.CLR_OK_GREEN)
            self.ax_temp.set_title(f"Holding at {sp} K")
            self.log("Target reached and held for the dwell. Holding: loop keeps "
                     "controlling, logging continues until Stop.")
            self._csv_note("target reached, holding")
            return False
        self.ax_temp.set_title(f"Reached {sp} K, heater off")
        self._csv_note("target reached, heater off")
        self.stop_ramp(reason="Target reached and held for the dwell. Ramp complete.",
                       heater_off=True, banner=f"RAMP COMPLETE | HEATER OFF ({sp} K)",
                       banner_color=self.CLR_OK_GREEN)
        return True

    def _on_closing(self):
        if self.is_running:
            self._cancel_poll()
            if messagebox.askyesno("Exit", "A ramp is active. Stop (heater off) and exit?"):
                self.stop_ramp()
                self.backend.close()
                self.root.destroy()
            else:
                self._schedule_poll(300)
        else:
            self._cancel_poll()
            self.backend.close()
            self.root.destroy()


if __name__ == '__main__':
    if not pyvisa:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed. Please run 'pip install pyvisa'.")
        root.destroy()
    else:
        root = tk.Tk()
        app = TempControlGUI(root)
        root.mainloop()
