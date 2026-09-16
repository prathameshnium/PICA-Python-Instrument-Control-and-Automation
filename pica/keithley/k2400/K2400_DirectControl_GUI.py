"""
Module: K2400_DirectControl_GUI.py
Purpose: Direct command workbench GUI for the Keithley Series 2400 SourceMeter.
         Sets source function and level, compliance, source/measure ranges,
         integration time, source delay, sensing mode, terminals, averaging
         filter, auto zero and the output-off state - each sent on its own,
         as an independent command.

         This is a bench tool, not a measurement programme: it holds a single
         operating point and shows a live reading. It writes no data file and
         performs no sweeps. For sweeps use IV_K2400_GUI.py.

SCPI commands and every limit in this file were verified against:
  - Keithley Series 2400 SourceMeter User's Manual, 2400S-900-01 Rev. K
    (local copy: Untracked_Stuff/Keithley_2400.pdf)

Instrument facts confirmed from that manual:
  - Source ranges (Model 2400), Specifications table:
        V source: 200mV / 2V / 20V / 200V   -> max +/-210mV .. +/-210V
        I source: 1uA .. 1A                 -> max +/-1.05uA .. +/-1.05A
        "Max Power = 22W"
  - Compliance limits (Section 4): current limit 1nA to 1.05A; voltage limit
        200uV to 210V -- but only 21V on the 2400-LV and the 2401.
        The model is therefore read from *IDN? and the envelope chosen to match,
        rather than assuming the 210V part.
  - Compliance detection (Section 18):
        :SENSe:CURRent:PROTection:TRIPped?   1 = in current compliance
        :SENSe:VOLTage:PROTection:TRIPped?   1 = in voltage compliance
  - Beeper (Section 18): :SYSTem:BEEPer[:IMMediate] <freq>,<time>
  - Output-off states (Section 13): HIGH IMPEDANCE opens the output relay, and
        the manual warns against using it for tests that switch the output off
        and on frequently, because of relay wear. This module switches the
        output by hand, often, so NORMAL is the default here.
  - Safety (manual front matter, quoting ANSI): a shock hazard exists above
        30V RMS, 42.4V peak or 60VDC. The default soft ceiling sits below that.

Non-destructive with one deliberate exception: on disconnect and on window
close the output is switched OFF first, then the VISA session is closed. Every
other setting is left exactly as the user left it. No *RST is ever sent.
"""

import os
import time
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# --- Optional packages ---------------------------------------------------
# PIL is used only for the header logo; the module runs fine without it.
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# pyvisa is the only transport. Absent, the GUI still builds so the layout can
# be inspected and tested off-instrument; every command path raises instead.
try:
    import pyvisa
except ImportError:
    pyvisa = None

# runpy + multiprocessing only exist to launch the two sibling utilities from
# the header buttons, exactly as the other PICA modules do.
import runpy
from multiprocessing import Process


# ---------------------------------------------------------------------------
# UTILITY LAUNCHERS (identical to the other PICA modules)
# ---------------------------------------------------------------------------

def run_script_process(script_path):
    """Wrapper to execute a script in its own directory via runpy."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up 2 levels: k2400 -> keithley -> pica
    plotter_path = os.path.join(
        script_dir, "..", "..", "utils", "PlotterUtil_GUI.py")
    if not os.path.exists(plotter_path):
        messagebox.showerror("File Not Found",
                             f"Plotter Utility not found at:\n{plotter_path}")
        return
    Process(target=run_script_process, args=(plotter_path,)).start()


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    scanner_path = os.path.join(
        script_dir, "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
    if not os.path.exists(scanner_path):
        messagebox.showerror("File Not Found",
                             f"GPIB Scanner not found at:\n{scanner_path}")
        return
    Process(target=run_script_process, args=(scanner_path,)).start()


# ---------------------------------------------------------------------------
# SAFETY ENVELOPE
# ---------------------------------------------------------------------------
#
# The 2400 envelope is NOT a rectangle. The specification gives
#   +/-21V at +/-1.05A   or   +/-210V at +/-105mA,
# which is the same thing as saying: stay inside the absolute maxima AND stay
# under 22W. pymeasure's driver validates voltage and current independently
# against +/-210V and +/-1.05A, so it will happily accept 200V at 1A - a point
# the instrument cannot deliver. Everything below enforces the joint limit.

class SafetyError(ValueError):
    """Raised when a requested operating point is outside the allowed envelope.

    Carries a plain-language explanation; the GUI shows `str(exc)` directly.
    """


class SourceMeterLimits:
    """Absolute limits for one model, plus the soft ceiling in force."""

    # Manual, Specifications: source range tables for the Model 2400.
    V_SOURCE_RANGES = (0.2, 2.0, 20.0, 200.0)
    I_SOURCE_RANGES = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0)

    # Each range sources up to 105% of its nominal value.
    RANGE_HEADROOM = 1.05

    # Manual, Section 4: compliance floor.
    V_COMPLIANCE_MIN = 200e-6   # 200 uV
    I_COMPLIANCE_MIN = 1e-9     # 1 nA

    # Manual, Specifications: "Max Power = 22W".
    #
    # That 22 W is a rounded figure. The two corners the specification itself
    # quotes are 21 V at 1.05 A and 210 V at 105 mA, and both of those come to
    # 22.05 W. Enforcing a flat 22.0 W would therefore refuse the instrument's
    # own documented operating points, so the check is made against the true
    # corner product and 22 W is what gets quoted in the error message.
    P_MAX = 22.0
    P_MAX_ENFORCED = 22.05

    # Manual front matter, quoting ANSI: a shock hazard exists above 30V RMS,
    # 42.4V peak or 60VDC. The default ceiling sits below all three, and
    # coincides with a real instrument range boundary (the 20V range tops out
    # at 21V), so the default costs no usable headroom on that range.
    SOFT_V_DEFAULT = 21.0
    SOFT_I_DEFAULT = 0.105

    # Model -> absolute maximum source voltage. Manual, Section 4:
    # "the voltage limit can be set from 200uV to 210V (21V for 2400-LV and
    # 2401)". Current is 1.05A on all of them.
    MODEL_V_MAX = {
        '2400': 210.0,
        '2400-C': 210.0,
        '2400-LV': 21.0,
        '2401': 21.0,
    }
    I_MAX = 1.05

    # Used when *IDN? cannot be matched to a known model. The smallest
    # envelope of the family, because guessing high is the dangerous direction.
    UNKNOWN_V_MAX = 21.0

    def __init__(self, model=None):
        self.model = model
        self.model_recognised = model in self.MODEL_V_MAX
        self.v_max = self.MODEL_V_MAX.get(model, self.UNKNOWN_V_MAX)
        self.i_max = self.I_MAX
        # Soft ceiling, never above the hard one.
        self.soft_v = min(self.SOFT_V_DEFAULT, self.v_max)
        self.soft_i = min(self.SOFT_I_DEFAULT, self.i_max)
        self.unlocked = False

    # -- ceiling in force ---------------------------------------------------

    @property
    def effective_v(self):
        """Highest voltage magnitude the module will currently permit."""
        return self.v_max if self.unlocked else self.soft_v

    @property
    def effective_i(self):
        """Highest current magnitude the module will currently permit."""
        return self.i_max if self.unlocked else self.soft_i

    def set_soft_limits(self, volts, amps):
        """Tighten (or loosen, within the hard maxima) the soft ceiling.

        A negative limit is refused rather than quietly read as its magnitude.
        Someone who types -5 has made a mistake, and silently reinterpreting a
        safety limit is exactly the wrong response to a mistake.
        """
        volts = float(volts)
        amps = float(amps)
        if volts <= 0 or amps <= 0:
            raise SafetyError(
                "Both limits must be positive numbers greater than zero.\n\n"
                f"Got {volts:g} V and {amps:g} A.")
        if volts > self.v_max:
            raise SafetyError(
                f"{volts:g} V is above what this instrument can source "
                f"({self.v_max:g} V maximum for the {self.model or 'unknown'}).")
        if amps > self.i_max:
            raise SafetyError(
                f"{amps:g} A is above what this instrument can source "
                f"({self.i_max:g} A maximum).")
        self.soft_v = volts
        self.soft_i = amps

    # -- the actual check ---------------------------------------------------

    def check_operating_point(self, source_function, level, compliance):
        """Validate one source level against its compliance limit.

        `source_function` is 'current' or 'voltage'. `level` is the source
        value in A or V; `compliance` is the limit in the other unit. Raises
        SafetyError with a readable explanation, or returns None.

        The worst case for power is the full source level against the full
        compliance limit, because that is what the instrument delivers into a
        load that actually reaches compliance.
        """
        level = float(level)
        compliance = float(compliance)

        if source_function == 'current':
            amps, volts = abs(level), abs(compliance)
            level_name, comp_name = "source current", "compliance voltage"
        elif source_function == 'voltage':
            volts, amps = abs(level), abs(compliance)
            level_name, comp_name = "source voltage", "compliance current"
        else:
            raise SafetyError(
                f"Unknown source function {source_function!r}.")

        ceiling_v = self.effective_v
        ceiling_i = self.effective_i
        ceiling_name = ("instrument maximum" if self.unlocked
                        else "safe bench ceiling")

        if volts > ceiling_v:
            which = level_name if source_function == 'voltage' else comp_name
            raise SafetyError(
                f"{volts:g} V exceeds the {ceiling_v:g} V limit currently in "
                f"force ({ceiling_name}).\n\n"
                f"This is the {which}.")
        if amps > ceiling_i:
            which = level_name if source_function == 'current' else comp_name
            raise SafetyError(
                f"{amps:g} A exceeds the {ceiling_i:g} A limit currently in "
                f"force ({ceiling_name}).\n\n"
                f"This is the {which}.")

        power = volts * amps
        if power > self.P_MAX_ENFORCED:
            raise SafetyError(
                f"{volts:g} V into {amps:g} A is {power:.1f} W, and the 2400 "
                f"can deliver at most {self.P_MAX:g} W.\n\n"
                f"The instrument sources up to 21 V at 1.05 A, or up to 210 V "
                f"at 105 mA, but never both at once. Lower the "
                f"{comp_name} or the {level_name}.")

        # Compliance floors, from the manual.
        if source_function == 'current' and volts < self.V_COMPLIANCE_MIN:
            raise SafetyError(
                f"The lowest voltage compliance the 2400 accepts is "
                f"{self.V_COMPLIANCE_MIN * 1e6:g} uV.")
        if source_function == 'voltage' and amps < self.I_COMPLIANCE_MIN:
            raise SafetyError(
                f"The lowest current compliance the 2400 accepts is "
                f"{self.I_COMPLIANCE_MIN * 1e9:g} nA.")

    # -- range helpers ------------------------------------------------------

    @classmethod
    def smallest_range_for(cls, ranges, value):
        """Smallest range from `ranges` that can hold `value`, else the largest."""
        magnitude = abs(float(value))
        for rng in ranges:
            if magnitude <= rng * cls.RANGE_HEADROOM:
                return rng
        return ranges[-1]


# ---------------------------------------------------------------------------
# BACKEND
# ---------------------------------------------------------------------------

class Keithley2400Backend:
    """VISA backend for the Series 2400 SourceMeter.

    Every command below appears in the Series 2400 User's Manual; the manual
    section is given at each method. No *RST is ever sent: a reset would drop
    the user's whole front-panel configuration, and there is no reason for a
    bench tool to do that.

    Disconnect switches the output OFF and then closes the session. That is
    the one destructive act this class performs, and it is deliberate: leaving
    a source energised on a sample after the window has gone is a worse
    failure than losing the output state.
    """

    # Output-off states, :OUTPut[1]:SMODe <name> (Manual Section 13).
    OUTPUT_OFF_MODES = {
        'NORMAL': "Normal - V-source at 0 V, low compliance (default)",
        'HIMPedance': "High impedance - output relay opens (wears the relay)",
        'ZERO': "Zero - 0 V, compliance unchanged, still measures",
        'GUARD': "Guard - I-source at 0 A, for 6-wire guarded ohms",
    }

    # :SENSe:AVERage:TCONtrol <type>
    FILTER_TYPES = {'REP': "Repeating", 'MOV': "Moving"}

    SOURCE_FUNCTIONS = {'current': 'CURR', 'voltage': 'VOLT'}

    def __init__(self):
        self.instrument = None
        self.rm = None
        self.idn = None
        self.limits = SourceMeterLimits()
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            print("PyVISA not available.")

    # -- connection ---------------------------------------------------------

    def scan_resources(self):
        """Return the VISA resource strings currently visible on the bus."""
        if not self.rm:
            return []
        try:
            return list(self.rm.list_resources())
        except Exception as e:
            print(f"VISA scan failed: {e}")
            return []

    @staticmethod
    def parse_model(idn):
        """Pull the model designation out of an *IDN? string.

        A 2400 answers something like
            KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234567,C32   Oct  4 2010
        Returns e.g. '2400', '2400-LV', '2401', or None when unrecognised.
        """
        if not idn:
            return None
        upper = idn.upper()
        # Longest first, so '2400-LV' is not shadowed by '2400'.
        for candidate in ('2400-LV', '2400-C', '2401', '2400'):
            if candidate in upper:
                return candidate
        return None

    @staticmethod
    def describe_idn(idn):
        """Break an *IDN? answer into its four documented fields.

        The 488.2 reply is
            <manufacturer>,<model>,<serial>,<firmware>
        Returns a list of (caption, value) pairs for the diagnostics log.
        Anything that does not split into four parts is reported as-is
        rather than guessed at.
        """
        if not idn:
            return [("Raw *IDN?", "(no reply)")]
        parts = [p.strip() for p in idn.split(',')]
        captions = ("Manufacturer", "Model field", "Serial number",
                    "Firmware")
        rows = [("Raw *IDN?", idn)]
        if len(parts) >= 4:
            rows.extend(zip(captions, parts[:4]))
        else:
            rows.append(("Fields", f"{len(parts)} (expected 4) - {parts}"))
        return rows

    def connect(self, visa_address):
        """Open the VISA session, identify the instrument, set the envelope.

        Refuses anything that is not a Series 2400. Sending 2400 source
        commands to, say, a Lakeshore would be a genuinely bad outcome, and
        the check costs one query.
        """
        if not self.rm:
            raise ConnectionError(
                "PyVISA ResourceManager not available. Install pyvisa and a "
                "VISA backend (NI-VISA or pyvisa-py).")
        inst = self.rm.open_resource(visa_address)
        inst.timeout = 10000
        inst.read_termination = '\n'
        inst.write_termination = '\n'
        try:
            idn = inst.query('*IDN?').strip()
        except Exception:
            inst.close()
            raise

        model = self.parse_model(idn)
        if model is None or 'KEITHLEY' not in idn.upper():
            inst.close()
            raise ConnectionError(
                "That instrument does not identify as a Keithley Series 2400.\n\n"
                f"It answered: {idn}\n\n"
                "Refusing to send source commands to it.")

        self.instrument = inst
        self.idn = idn
        self.limits = SourceMeterLimits(model)
        return idn

    def disconnect(self):
        """Switch the output off, then close the session.

        The output-off is attempted first and its failure never prevents the
        close, so a dead link cannot leave a dangling VISA handle.
        """
        if not self.instrument:
            return
        try:
            self.instrument.write(':OUTPut OFF')
        except Exception as e:
            print(f"  Warning: could not switch output off on disconnect: {e}")
        try:
            self.instrument.close()
        except Exception as e:
            print(f"  Warning during disconnect: {e}")
        finally:
            self.instrument = None
            self.idn = None

    @property
    def is_connected(self):
        return self.instrument is not None

    # -- low level ----------------------------------------------------------

    def _write(self, command):
        if not self.instrument:
            raise ConnectionError("Not connected to instrument.")
        self.instrument.write(command)

    def _query(self, command):
        if not self.instrument:
            raise ConnectionError("Not connected to instrument.")
        return self.instrument.query(command).strip()

    # -- source configuration ----------------------------------------------

    def set_source_function(self, function):
        """:SOURce[1]:FUNCtion[:MODE] <name>  (Manual Section 18)."""
        code = self.SOURCE_FUNCTIONS[function]
        self._write(f':SOURce:FUNCtion:MODE {code}')

    def get_source_function(self):
        answer = self._query(':SOURce:FUNCtion:MODE?').upper()
        return 'current' if 'CURR' in answer else 'voltage'

    def set_source_level(self, function, level):
        """:SOURce:CURRent:LEVel / :SOURce:VOLTage:LEVel."""
        code = self.SOURCE_FUNCTIONS[function]
        self._write(f':SOURce:{code}:LEVel {level:g}')

    def get_source_level(self, function):
        code = self.SOURCE_FUNCTIONS[function]
        return float(self._query(f':SOURce:{code}:LEVel?'))

    def set_source_range(self, function, value, auto=False):
        """Source range, or auto-range. Manual Section 18."""
        code = self.SOURCE_FUNCTIONS[function]
        if auto:
            self._write(f':SOURce:{code}:RANGe:AUTO ON')
        else:
            self._write(f':SOURce:{code}:RANGe:AUTO OFF')
            self._write(f':SOURce:{code}:RANGe {value:g}')

    def set_source_delay(self, seconds, auto=False):
        """:SOURce:DELay <n> / :SOURce:DELay:AUTO. Range 0 to 999.9999 s."""
        if auto:
            self._write(':SOURce:DELay:AUTO ON')
            return
        seconds = float(seconds)
        if not 0.0 <= seconds <= 999.9999:
            raise SafetyError(
                "Source delay must be between 0 and 999.9999 seconds.")
        self._write(':SOURce:DELay:AUTO OFF')
        self._write(f':SOURce:DELay {seconds:g}')

    # -- compliance ---------------------------------------------------------

    def set_compliance(self, function, value):
        """Set the compliance limit for the *measured* quantity.

        Sourcing current, the limit is a voltage (:SENSe:VOLTage:PROTection);
        sourcing voltage, it is a current (:SENSe:CURRent:PROTection).
        Manual Section 18.
        """
        if function == 'current':
            self._write(f':SENSe:VOLTage:PROTection {value:g}')
        else:
            self._write(f':SENSe:CURRent:PROTection {value:g}')

    def get_compliance(self, function):
        if function == 'current':
            return float(self._query(':SENSe:VOLTage:PROTection?'))
        return float(self._query(':SENSe:CURRent:PROTection?'))

    def in_compliance(self):
        """Is the instrument sitting in compliance right now?

        :SENSe:CURRent:PROTection:TRIPped? and the voltage equivalent both
        answer 1 when that limit is being hit (Manual Section 18). Returns
        (tripped, which) where `which` is 'current', 'voltage' or None.
        """
        try:
            if self._query(':SENSe:VOLTage:PROTection:TRIPped?').strip() == '1':
                return True, 'voltage'
        except Exception:
            pass
        try:
            if self._query(':SENSe:CURRent:PROTection:TRIPped?').strip() == '1':
                return True, 'current'
        except Exception:
            pass
        return False, None

    # -- measurement configuration -----------------------------------------

    def set_measure_range(self, quantity, value, auto=False):
        """quantity is 'voltage', 'current' or 'resistance'."""
        code = {'voltage': 'VOLTage', 'current': 'CURRent',
                'resistance': 'RESistance'}[quantity]
        if auto:
            self._write(f':SENSe:{code}:RANGe:AUTO ON')
        else:
            self._write(f':SENSe:{code}:RANGe:AUTO OFF')
            self._write(f':SENSe:{code}:RANGe {value:g}')

    def set_nplc(self, quantity, nplc):
        """Integration time in power line cycles. Manual: valid 0.01 to 10."""
        nplc = float(nplc)
        if not 0.01 <= nplc <= 10.0:
            raise SafetyError(
                "Integration time must be between 0.01 and 10 power line cycles.")
        code = {'voltage': 'VOLTage', 'current': 'CURRent',
                'resistance': 'RESistance'}[quantity]
        self._write(f':SENSe:{code}:NPLCycles {nplc:g}')

    def set_sense_mode(self, four_wire):
        """:SYSTem:RSENse <b> - remote (4-wire) sensing on or off."""
        self._write(f':SYSTem:RSENse {1 if four_wire else 0}')

    def get_sense_mode(self):
        return self._query(':SYSTem:RSENse?').strip() == '1'

    def set_terminals(self, rear):
        """:ROUTe:TERMinals FRONt|REAR."""
        self._write(f':ROUTe:TERMinals {"REAR" if rear else "FRONt"}')

    def get_terminals(self):
        return 'REAR' in self._query(':ROUTe:TERMinals?').upper()

    def set_filter(self, enabled, count=10, mode='REP'):
        """Averaging filter: :SENSe:AVERage subsystem. Count 1 to 100."""
        count = int(count)
        if not 1 <= count <= 100:
            raise SafetyError("Filter count must be between 1 and 100.")
        if mode not in self.FILTER_TYPES:
            raise SafetyError(f"Unknown filter mode {mode!r}.")
        self._write(f':SENSe:AVERage:TCONtrol {mode}')
        self._write(f':SENSe:AVERage:COUNt {count}')
        self._write(f':SENSe:AVERage:STATe {1 if enabled else 0}')

    def set_auto_zero(self, enabled):
        """:SYSTem:AZERo:STATe <b>."""
        self._write(f':SYSTem:AZERo:STATe {1 if enabled else 0}')

    def set_output_off_mode(self, mode):
        """:OUTPut[1]:SMODe <name>. Manual Section 13."""
        if mode not in self.OUTPUT_OFF_MODES:
            raise SafetyError(f"Unknown output-off mode {mode!r}.")
        self._write(f':OUTPut:SMODe {mode}')

    def set_measure_function(self, quantity):
        """Choose what :READ? returns. 'voltage', 'current' or 'resistance'."""
        code = {'voltage': 'VOLT', 'current': 'CURR',
                'resistance': 'RES'}[quantity]
        self._write(f':SENSe:FUNCtion "{code}"')

    def set_resistance_mode(self, auto):
        """[:SENSe[1]]:RESistance:MODE MANual|AUTO. Manual Section 4 and 18.

        Auto ohms makes the instrument a conventional constant-current
        ohmmeter: it picks the test current from the ohms range, and the
        manual is explicit that the source current CANNOT then be changed -
        "If you attempt to change the source current in auto ohms, the
        SourceMeter will display an error message." The GUI greys the source
        controls out to match.

        Manual ohms leaves the user to configure source V or I, and the
        instrument computes V/I.
        """
        self._write(f':SENSe:RESistance:MODE {"AUTO" if auto else "MANual"}')

    def get_resistance_mode(self):
        """True when the instrument is in auto ohms."""
        return 'AUTO' in self._query(':SENSe:RESistance:MODE?').upper()

    # -- output and reading -------------------------------------------------

    def output_on(self):
        self._write(':OUTPut ON')

    def output_off(self):
        self._write(':OUTPut OFF')

    def get_output_state(self):
        return self._query(':OUTPut?').strip() == '1'

    def read(self):
        """One :READ? cycle -> (voltage, current, resistance).

        :FORMat:ELEMents is set to all three so a single reading serves every
        display field regardless of which source mode is in force.
        """
        raw = self._query(':READ?')
        parts = [float(p) for p in raw.split(',')]
        # Default :READ? element order is VOLT,CURR,RES,TIME,STATUS.
        volts = parts[0] if len(parts) > 0 else float('nan')
        amps = parts[1] if len(parts) > 1 else float('nan')
        ohms = parts[2] if len(parts) > 2 else float('nan')
        return volts, amps, ohms

    def set_read_elements(self):
        """Ask for voltage, current and resistance on every reading."""
        self._write(':FORMat:ELEMents VOLTage,CURRent,RESistance')

    def beep(self, frequency=1000, duration=0.3):
        """:SYSTem:BEEPer:IMMediate <freq>,<time>. Manual Section 18.

        The beeper is in the instrument, so this sounds at the rack rather
        than at the PC, which is where someone watching a sample will be.
        """
        self._write(f':SYSTem:BEEPer:IMMediate {frequency:g},{duration:g}')

    # -- gentle level changes ----------------------------------------------

    def ramp_source_level(self, function, target, steps=20, pause=0.02,
                          start=None, abort_check=None):
        """Walk the source level to `target` instead of jumping to it.

        A step change into a fragile contact is exactly the event that blows
        a sample, so this is the default path for every level change.
        `abort_check` is polled between steps and stops the walk when it
        returns True. Returns the level actually reached.
        """
        if start is None:
            start = self.get_source_level(function)
        start = float(start)
        target = float(target)
        steps = max(1, int(steps))

        if start == target:
            return target

        level = start
        for n in range(1, steps + 1):
            if abort_check is not None and abort_check():
                return level
            level = start + (target - start) * (n / steps)
            self.set_source_level(function, level)
            if pause:
                time.sleep(pause)
        # Land exactly on the requested value rather than on a rounding of it.
        self.set_source_level(function, target)
        return target


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class K2400DirectControlGUI:
    """Bench workbench for the Keithley 2400.

    Each panel sends its own commands independently; nothing is batched behind
    a single Apply. The output cannot be switched on until the compliance
    value has been confirmed once, and every source level change is checked
    against the joint voltage/current/power envelope before a byte is sent.
    """

    PROGRAM_VERSION = "1.0"
    PROGRAM_NAME = "Keithley 2400 Direct Control Utility"

    # Colour scheme, matched to the other PICA direct-control modules.
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
    CLR_LIVE_ON = '#C0392B'
    CLR_LIVE_DIM = '#E5DCD3'

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 12, 'bold')
    FONT_READING = ('Consolas', 18, 'bold')

    LEFT_PANEL_WIDTH = 560
    # Upper bound on how much of the window the control column may take,
    # so the three live-reading values always have room to sit side by side.
    MAX_LEFT_FRACTION = 0.42

    POLL_SECONDS_DEFAULT = 1.0
    BLINK_MS = 500

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self.root.geometry("1500x950")
        self.root.minsize(1150, 750)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = Keithley2400Backend()
        self.logo_image = None
        self.is_connected = False

        # Polling and blinking are both tk.after chains on the main thread.
        # Their ids are stored so they can be cancelled exactly once; a chain
        # left running after the widgets go away is how these GUIs hang.
        self._poll_after_id = None
        self._blink_after_id = None
        self._blink_on = False

        self.output_live = False
        self.compliance_confirmed = False
        self._last_compliance_state = False
        self._ramping = False

        self.setup_styles()
        self.create_widgets()
        self._set_controls_enabled(False)
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
        style.configure('Danger.TButton', background='#C0392B',
                        foreground='#FFFFFF', font=('Segoe UI', 12, 'bold'))
        style.map('Danger.TButton',
                  background=[('active', '#E74C3C'), ('hover', '#E74C3C')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK)
        style.configure('TCombobox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('TRadiobutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)

    # -----------------------------------------------------------------------
    # WIDGETS
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

            # Never let the control column starve the reading column. The
            # live reading is three side-by-side values, and if the sash is
            # placed purely by the left panel's requested width the third one
            # (resistance) lands off the right edge of the window.
            window_w = self.root.winfo_width()
            if window_w > 1:
                target = min(target, int(window_w * self.MAX_LEFT_FRACTION))
            target = max(target, 320)

            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical',
                                  command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=scroll_frame,
                                         anchor='nw')
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
        self._create_safety_panel(scroll_frame, 2)
        self._create_source_panel(scroll_frame, 3)
        self._create_range_panel(scroll_frame, 4)
        self._create_measure_panel(scroll_frame, 5)
        self._create_advanced_panel(scroll_frame, 6)

    def _populate_right_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        self._create_output_panel(panel, 0)
        self._create_reading_panel(panel, 1)
        self._create_console_panel(panel, 2)

    # -- individual panels --------------------------------------------------

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 90
        logo_canvas = tk.Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                                bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            # k2400 -> keithley -> pica -> assets
            logo_path = os.path.join(script_dir, "..", "..", "assets", "LOGO",
                                     "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE / 2, LOGO_SIZE / 2,
                                         image=self.logo_image)
        except Exception:
            pass  # Logo is optional

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font,
                  background=self.CLR_FRAME_BG).grid(
            row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font,
                  background=self.CLR_FRAME_BG).grid(
            row=1, column=1, padx=10, pady=(0, 5), sticky='nw')

        self.model_label = ttk.Label(frame, text="Keithley 2400 | not connected",
                                     background=self.CLR_FRAME_BG)
        self.model_label.grid(row=2, column=0, columnspan=2, padx=10,
                              pady=(0, 10), sticky='w')

    def _create_connection_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="VISA Address:").grid(
            row=0, column=0, sticky='w', padx=10, pady=5)
        self.visa_cb = ttk.Combobox(frame, font=self.FONT_BASE,
                                    state='readonly')
        self.visa_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky='ew', pady=5)
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.connect_btn = ttk.Button(btn_frame, text="Connect",
                                      style='Connect.TButton',
                                      command=self._do_connect)
        self.connect_btn.grid(row=0, column=0, sticky='ew', padx=5)
        self.disconnect_btn = ttk.Button(btn_frame, text="Disconnect",
                                         style='Disconnect.TButton',
                                         state='disabled',
                                         command=self._do_disconnect)
        self.disconnect_btn.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(btn_frame, text="Scan",
                   command=self._scan_visa).grid(row=0, column=2, sticky='ew',
                                                 padx=5)

        self.status_label = ttk.Label(frame, text="● Not Connected",
                                      font=self.FONT_STATUS,
                                      foreground=self.CLR_STATUS_BAD,
                                      background=self.CLR_FRAME_BG)
        self.status_label.grid(row=2, column=0, columnspan=2, sticky='w',
                               padx=10, pady=(0, 5))

    def _create_safety_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Safety limits')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=("The module refuses any point outside these, and always "
                  "refuses more than 22 W."),
            wraplength=480, justify='left').grid(
            row=0, column=0, columnspan=3, sticky='w', padx=10, pady=(5, 2))

        ttk.Label(frame, text="Never exceed (V):").grid(
            row=1, column=0, sticky='w', padx=10, pady=4)
        self.soft_v_entry = ttk.Entry(frame, font=self.FONT_BASE, width=12)
        self.soft_v_entry.insert(0, f"{SourceMeterLimits.SOFT_V_DEFAULT:g}")
        self.soft_v_entry.grid(row=1, column=1, sticky='w', padx=10, pady=4)

        ttk.Label(frame, text="Never exceed (A):").grid(
            row=2, column=0, sticky='w', padx=10, pady=4)
        self.soft_i_entry = ttk.Entry(frame, font=self.FONT_BASE, width=12)
        self.soft_i_entry.insert(0, f"{SourceMeterLimits.SOFT_I_DEFAULT:g}")
        self.soft_i_entry.grid(row=2, column=1, sticky='w', padx=10, pady=4)

        ttk.Button(frame, text="Apply limits",
                   command=self._apply_soft_limits).grid(
            row=1, column=2, rowspan=2, sticky='ew', padx=10, pady=4)

        self.unlock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Allow the full instrument range (up to 210 V)",
            variable=self.unlock_var,
            command=self._toggle_unlock).grid(
            row=3, column=0, columnspan=3, sticky='w', padx=10, pady=(4, 8))

    def _create_source_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Source')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Source:").grid(row=0, column=0, sticky='w',
                                              padx=10, pady=5)
        self.source_func_var = tk.StringVar(value='current')
        func_frame = ttk.Frame(frame)
        func_frame.grid(row=0, column=1, columnspan=2, sticky='w', padx=10)
        ttk.Radiobutton(func_frame, text="Current, measure voltage",
                        variable=self.source_func_var, value='current',
                        command=self._on_source_function_changed).pack(
            anchor='w')
        ttk.Radiobutton(func_frame, text="Voltage, measure current",
                        variable=self.source_func_var, value='voltage',
                        command=self._on_source_function_changed).pack(
            anchor='w')
        ttk.Radiobutton(func_frame, text="Resistance (built-in ohms)",
                        variable=self.source_func_var, value='resistance',
                        command=self._on_source_function_changed).pack(
            anchor='w')

        self.ohms_mode_cb = ttk.Combobox(
            func_frame, font=self.FONT_BASE, state='readonly', width=30,
            values=["Auto ohms - instrument picks the test current",
                    "Manual ohms - I set the source myself"])
        self.ohms_mode_cb.current(0)
        self.ohms_mode_cb.bind('<<ComboboxSelected>>',
                               lambda _e: self._apply_ohms_mode())
        self.ohms_mode_cb.pack(anchor='w', pady=(4, 0))

        self.level_label = ttk.Label(frame, text="Source current (A):")
        self.level_label.grid(row=1, column=0, sticky='w', padx=10, pady=5)
        self.level_entry = ttk.Entry(frame, font=self.FONT_BASE, width=14)
        self.level_entry.insert(0, "0")
        self.level_entry.grid(row=1, column=1, sticky='w', padx=10, pady=5)
        ttk.Button(frame, text="Send level",
                   command=self._apply_source_level).grid(
            row=1, column=2, sticky='ew', padx=10, pady=5)

        self.compliance_label = ttk.Label(frame, text="Compliance (V):")
        self.compliance_label.grid(row=2, column=0, sticky='w', padx=10,
                                   pady=5)
        self.compliance_entry = ttk.Entry(frame, font=self.FONT_BASE, width=14)
        self.compliance_entry.insert(0, "1")
        self.compliance_entry.grid(row=2, column=1, sticky='w', padx=10,
                                   pady=5)
        ttk.Button(frame, text="Send compliance",
                   command=self._apply_compliance).grid(
            row=2, column=2, sticky='ew', padx=10, pady=5)

        self.ramp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame,
                        text="Step gently to a new level instead of jumping",
                        variable=self.ramp_var).grid(
            row=3, column=0, columnspan=3, sticky='w', padx=10, pady=(2, 8))

    def _create_range_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Ranges')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.source_autorange_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Auto-range the source",
                        variable=self.source_autorange_var).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=10, pady=4)
        ttk.Label(frame, text="Source range:").grid(row=1, column=0,
                                                    sticky='w', padx=10,
                                                    pady=4)
        self.source_range_entry = ttk.Entry(frame, font=self.FONT_BASE,
                                            width=14)
        self.source_range_entry.grid(row=1, column=1, sticky='w', padx=10,
                                     pady=4)
        ttk.Button(frame, text="Send",
                   command=self._apply_source_range).grid(
            row=1, column=2, sticky='ew', padx=10, pady=4)

        self.measure_autorange_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Auto-range the measurement",
                        variable=self.measure_autorange_var).grid(
            row=2, column=0, columnspan=2, sticky='w', padx=10, pady=4)
        ttk.Label(frame, text="Measure range:").grid(row=3, column=0,
                                                     sticky='w', padx=10,
                                                     pady=4)
        self.measure_range_entry = ttk.Entry(frame, font=self.FONT_BASE,
                                             width=14)
        self.measure_range_entry.grid(row=3, column=1, sticky='w', padx=10,
                                      pady=4)
        ttk.Button(frame, text="Send",
                   command=self._apply_measure_range).grid(
            row=3, column=2, sticky='ew', padx=10, pady=(4, 8))

    def _create_measure_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Measurement')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Integration (PLC):").grid(
            row=0, column=0, sticky='w', padx=10, pady=4)
        self.nplc_entry = ttk.Entry(frame, font=self.FONT_BASE, width=14)
        self.nplc_entry.insert(0, "1")
        self.nplc_entry.grid(row=0, column=1, sticky='w', padx=10, pady=4)
        ttk.Button(frame, text="Send", command=self._apply_nplc).grid(
            row=0, column=2, sticky='ew', padx=10, pady=4)

        ttk.Label(frame, text="Source delay (s):").grid(
            row=1, column=0, sticky='w', padx=10, pady=4)
        self.delay_entry = ttk.Entry(frame, font=self.FONT_BASE, width=14)
        self.delay_entry.insert(0, "0")
        self.delay_entry.grid(row=1, column=1, sticky='w', padx=10, pady=4)
        ttk.Button(frame, text="Send", command=self._apply_delay).grid(
            row=1, column=2, sticky='ew', padx=10, pady=4)

        self.four_wire_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Four-wire sensing",
                        variable=self.four_wire_var,
                        command=self._apply_sense_mode).grid(
            row=2, column=0, columnspan=3, sticky='w', padx=10, pady=4)

        self.rear_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Use rear terminals",
                        variable=self.rear_var,
                        command=self._apply_terminals).grid(
            row=3, column=0, columnspan=3, sticky='w', padx=10, pady=(4, 8))

    def _create_advanced_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Filter, auto zero, output-off')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)

        self.filter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Averaging filter",
                        variable=self.filter_var).grid(
            row=0, column=0, sticky='w', padx=10, pady=4)
        ttk.Label(frame, text="Count:").grid(row=0, column=1, sticky='e',
                                             padx=5)
        self.filter_count_entry = ttk.Entry(frame, font=self.FONT_BASE,
                                            width=6)
        self.filter_count_entry.insert(0, "10")
        self.filter_count_entry.grid(row=0, column=2, sticky='w', padx=(0, 10))
        self.filter_mode_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly', width=12,
            values=["Repeating", "Moving"])
        self.filter_mode_cb.current(0)
        self.filter_mode_cb.grid(row=1, column=1, columnspan=2, sticky='w',
                                 padx=5, pady=4)
        ttk.Button(frame, text="Send filter",
                   command=self._apply_filter).grid(
            row=1, column=0, sticky='ew', padx=10, pady=4)

        self.azero_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Auto zero", variable=self.azero_var,
                        command=self._apply_auto_zero).grid(
            row=2, column=0, columnspan=3, sticky='w', padx=10, pady=4)

        ttk.Label(frame, text="When output is off:").grid(
            row=3, column=0, sticky='w', padx=10, pady=4)
        self.off_mode_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=list(Keithley2400Backend.OUTPUT_OFF_MODES.values()))
        self.off_mode_cb.current(0)
        self.off_mode_cb.grid(row=3, column=1, columnspan=2, sticky='ew',
                              padx=10, pady=4)
        ttk.Button(frame, text="Send output-off state",
                   command=self._apply_output_off_mode).grid(
            row=4, column=0, columnspan=3, sticky='ew', padx=10, pady=(4, 8))

    def _create_output_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Output')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(2, weight=1)

        self.output_btn = ttk.Button(frame, text="Turn output ON",
                                     style='Danger.TButton',
                                     command=self._toggle_output)
        self.output_btn.grid(row=0, column=0, sticky='ew', padx=10, pady=10)

        # Blinking indicator rather than recolouring the whole panel.
        self.live_indicator = tk.Label(frame, text="  ● OUTPUT LIVE  ",
                                       font=self.FONT_STATUS,
                                       bg=self.CLR_FRAME_BG,
                                       fg=self.CLR_FRAME_BG)
        self.live_indicator.grid(row=0, column=1, sticky='w', padx=10)

        self.compliance_banner = ttk.Label(
            frame, text="", font=self.FONT_STATUS,
            foreground=self.CLR_STATUS_BAD, background=self.CLR_FRAME_BG)
        self.compliance_banner.grid(row=0, column=2, sticky='w', padx=10)

        ttk.Button(frame, text="Measure once",
                   command=self._measure_once).grid(
            row=1, column=0, sticky='ew', padx=10, pady=(0, 10))
        self.poll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Keep reading while the output is on",
                        variable=self.poll_var,
                        command=self._on_poll_toggle).grid(
            row=1, column=1, columnspan=2, sticky='w', padx=10, pady=(0, 10))

    def _create_reading_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Live reading')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.reading_labels = {}
        for col, (key, caption) in enumerate(
                (('voltage', "Voltage"), ('current', "Current"),
                 ('resistance', "Resistance"))):
            ttk.Label(frame, text=caption, font=self.FONT_BASE).grid(
                row=0, column=col, pady=(8, 0))
            lbl = ttk.Label(frame, text="---", font=self.FONT_READING)
            lbl.grid(row=1, column=col, pady=(0, 10))
            self.reading_labels[key] = lbl

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            frame, height=10, bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_TEXT_DARK, font=self.FONT_CONSOLE, wrap='word',
            relief='flat')
        self.console.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)

    # -----------------------------------------------------------------------
    # LOGGING AND SMALL HELPERS
    # -----------------------------------------------------------------------

    def log(self, message):
        stamp = datetime.now().strftime('%H:%M:%S')
        try:
            self.console.insert('end', f"[{stamp}] {message}\n")
            self.console.see('end')
        except tk.TclError:
            pass  # widget already destroyed during shutdown

    def _warn(self, title, message):
        """Show a modal warning, but stop polling first.

        A tk.after chain that fires while a modal dialog owns the event loop
        is how these GUIs deadlock, so the chain is torn down before the
        dialog goes up and restarted afterwards if the output is still live.
        """
        was_polling = self._poll_after_id is not None
        self._stop_polling()
        messagebox.showwarning(title, message)
        if was_polling and self.output_live and self.poll_var.get():
            self._start_polling()

    def _error(self, title, message):
        was_polling = self._poll_after_id is not None
        self._stop_polling()
        messagebox.showerror(title, message)
        if was_polling and self.output_live and self.poll_var.get():
            self._start_polling()

    @staticmethod
    def _format_reading(value, unit):
        """Engineering-notation reading, or --- for a missing one."""
        if value is None or value != value:  # NaN
            return "---"
        magnitude = abs(value)
        for factor, prefix in ((1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
                               (1.0, ''), (1e-3, 'm'), (1e-6, 'µ'),
                               (1e-9, 'n'), (1e-12, 'p')):
            if magnitude >= factor:
                return f"{value / factor:.4f} {prefix}{unit}"
        return f"{value:.4e} {unit}"

    def _read_float(self, entry, what):
        text = entry.get().strip()
        try:
            return float(text)
        except ValueError:
            raise SafetyError(
                f"{what} needs to be a number. It currently reads {text!r}.")

    def _set_controls_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for widget in (getattr(self, name, None) for name in (
                'level_entry', 'compliance_entry', 'source_range_entry',
                'measure_range_entry', 'nplc_entry', 'delay_entry',
                'filter_count_entry', 'output_btn')):
            if widget is not None:
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass
        # Auto ohms overrides the above: the manual forbids editing the source
        # there, so those two boxes stay disabled even once connected.
        self._sync_source_entry_state()

    # -----------------------------------------------------------------------
    # CONNECTION
    # -----------------------------------------------------------------------

    def _scan_visa(self):
        resources = self.backend.scan_resources()
        self.visa_cb['values'] = resources
        if resources:
            self.visa_cb.current(0)
            self.log(f"Found {len(resources)} instrument(s) on the bus.")
        else:
            self.log("No instruments found. Is the GPIB adaptor connected?")

    def _do_connect(self):
        address = self.visa_cb.get().strip()
        if not address:
            self._warn("No address",
                       "Pick a VISA address first, or press Scan to look for one.")
            return
        try:
            idn = self.backend.connect(address)
        except Exception as e:
            self.log(f"Connection failed: {e}")
            self._error("Could not connect", str(e))
            return

        self.is_connected = True
        limits = self.backend.limits
        self.status_label.configure(text="● Connected",
                                    foreground=self.CLR_STATUS_OK)
        self.connect_btn.configure(state='disabled')
        self.disconnect_btn.configure(state='normal')
        self._set_controls_enabled(True)

        # --- Identification diagnostics -----------------------------------
        # The model decides the voltage envelope (210 V on a plain 2400, but
        # only 21 V on a 2400-LV or 2401), so exactly what the instrument
        # said and what was made of it are both written out in full.
        self.log("--- Instrument identification ---")
        for caption, value in Keithley2400Backend.describe_idn(idn):
            self.log(f"  {caption:<16}{value}")

        if limits.model_recognised:
            self.model_label.configure(
                text=(f"Keithley {limits.model} | {limits.v_max:g} V, "
                      f"{limits.i_max:g} A, {limits.P_MAX:g} W max"))
            self.log(f"  {'Matched model':<16}{limits.model}")
            self.log(f"  {'Source limits':<16}{limits.v_max:g} V max, "
                     f"{limits.i_max:g} A max, {limits.P_MAX:g} W max")
        else:
            self.model_label.configure(
                text=f"Keithley 2400 family | model not recognised, assuming {limits.v_max:g} V max")
            self.log(f"  {'Matched model':<16}NONE - not a known 2400 variant")
            self.log(f"  {'Source limits':<16}assuming the smallest envelope "
                     f"in the family ({limits.v_max:g} V max, "
                     f"{limits.i_max:g} A max), rather than guessing high")
            self.log("  If this really is a 210 V part, the ceiling can be "
                     "raised by hand in the Safety limits panel.")

        self.log(f"  {'Safe ceiling':<16}{limits.effective_v:g} V / "
                 f"{limits.effective_i:g} A in force now")
        self.log("---------------------------------")

        try:
            self.backend.set_read_elements()
        except Exception as e:
            self.log(f"Could not set reading format: {e}")

        # The output may already be on from a previous session.
        try:
            if self.backend.get_output_state():
                self.output_live = True
                self.compliance_confirmed = True
                self._start_blinking()
                self.output_btn.configure(text="Turn output OFF")
                self.log("NOTE: the output was already ON when this "
                         "programme connected.")
                if self.poll_var.get():
                    self._start_polling()
        except Exception as e:
            self.log(f"Could not read the output state: {e}")

    def _do_disconnect(self):
        self._stop_polling()
        self._stop_blinking()
        try:
            self.backend.disconnect()
            self.log("Output switched off, then disconnected.")
        except Exception as e:
            self.log(f"Disconnect problem: {e}")
        self.is_connected = False
        self.output_live = False
        self.compliance_confirmed = False
        self.status_label.configure(text="● Not Connected",
                                    foreground=self.CLR_STATUS_BAD)
        self.model_label.configure(text="Keithley 2400 | not connected")
        self.connect_btn.configure(state='normal')
        self.disconnect_btn.configure(state='disabled')
        self.output_btn.configure(text="Turn output ON")
        self._set_controls_enabled(False)

    # -----------------------------------------------------------------------
    # SAFETY PANEL
    # -----------------------------------------------------------------------

    def _apply_soft_limits(self):
        try:
            volts = self._read_float(self.soft_v_entry, "The voltage limit")
            amps = self._read_float(self.soft_i_entry, "The current limit")
            self.backend.limits.set_soft_limits(volts, amps)
        except SafetyError as e:
            self._warn("Limit not accepted", str(e))
            return
        self.log(f"Safe ceiling now {volts:g} V and {amps:g} A.")

    def _toggle_unlock(self):
        limits = self.backend.limits
        if not self.unlock_var.get():
            limits.unlocked = False
            self.log("Back to the safe bench ceiling.")
            return
        # Stop polling before the dialog, same reason as _warn.
        was_polling = self._poll_after_id is not None
        self._stop_polling()
        agreed = messagebox.askyesno(
            "Allow the full instrument range?",
            f"This lets the module go up to {limits.v_max:g} V and "
            f"{limits.i_max:g} A.\n\n"
            "Above 42 V the output is a genuine shock hazard, and at these "
            "levels a wiring mistake will destroy a sample rather than just "
            "spoil a measurement.\n\n"
            "The 22 W power limit still applies.\n\n"
            "Are you sure?")
        if agreed:
            limits.unlocked = True
            self.log(f"FULL RANGE UNLOCKED: up to {limits.v_max:g} V / "
                     f"{limits.i_max:g} A.")
        else:
            self.unlock_var.set(False)
            limits.unlocked = False
            self.log("Full range declined; safe ceiling still in force.")
        if was_polling and self.output_live and self.poll_var.get():
            self._start_polling()

    # -----------------------------------------------------------------------
    # SOURCE PANEL
    # -----------------------------------------------------------------------

    @property
    def auto_ohms(self):
        """True when resistance mode is selected AND set to auto ohms.

        Guarded with getattr because _set_controls_enabled runs during
        construction, before the source panel's widgets necessarily exist.
        """
        func_var = getattr(self, 'source_func_var', None)
        mode_cb = getattr(self, 'ohms_mode_cb', None)
        if func_var is None or mode_cb is None:
            return False
        try:
            return func_var.get() == 'resistance' and mode_cb.current() == 0
        except tk.TclError:
            return False

    def _on_source_function_changed(self):
        function = self.source_func_var.get()
        if function == 'current':
            self.level_label.configure(text="Source current (A):")
            self.compliance_label.configure(text="Compliance (V):")
        elif function == 'voltage':
            self.level_label.configure(text="Source voltage (V):")
            self.compliance_label.configure(text="Compliance (A):")
        else:
            # Manual ohms still sources something; auto ohms does not let the
            # user touch it at all, which _sync_source_entry_state reflects.
            self.level_label.configure(text="Source current (A):")
            self.compliance_label.configure(text="Compliance (V):")

        # Changing what is sourced invalidates the confirmed compliance.
        self.compliance_confirmed = False
        self._sync_source_entry_state()
        if not self.is_connected:
            return
        try:
            if function == 'resistance':
                self.backend.set_measure_function('resistance')
                self._apply_ohms_mode()
            else:
                self.backend.set_source_function(function)
                self.backend.set_measure_function(
                    'voltage' if function == 'current' else 'current')
                self.log(f"Now sourcing {function}.")
        except Exception as e:
            self.log(f"Could not change source function: {e}")
            self._error("Command failed", str(e))

    def _apply_ohms_mode(self):
        """Send the ohms mode, and reflect what it allows in the form."""
        if self.source_func_var.get() != 'resistance':
            return
        auto = self.ohms_mode_cb.current() == 0
        self._sync_source_entry_state()
        if not self.is_connected:
            return
        try:
            self.backend.set_resistance_mode(auto)
        except Exception as e:
            self.log(f"Could not set the ohms mode: {e}")
            self._error("Command failed", str(e))
            return
        if auto:
            self.log("Auto ohms: the instrument picks the test current from "
                     "the ohms range. The manual is explicit that the source "
                     "current cannot be changed in this mode, so the source "
                     "boxes are disabled.")
        else:
            self.log("Manual ohms: set the source yourself; the instrument "
                     "computes V/I.")

    def _sync_source_entry_state(self):
        """Grey the source boxes out when auto ohms forbids editing them."""
        # The ohms-mode choice only means anything in resistance mode.
        mode_cb = getattr(self, 'ohms_mode_cb', None)
        func_var = getattr(self, 'source_func_var', None)
        if mode_cb is not None and func_var is not None:
            try:
                mode_cb.configure(
                    state='readonly' if func_var.get() == 'resistance'
                    else 'disabled')
            except tk.TclError:
                pass

        state = 'disabled' if self.auto_ohms else (
            'normal' if self.is_connected else 'disabled')
        for name in ('level_entry', 'compliance_entry'):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _effective_source_function(self):
        """What the instrument is actually sourcing.

        Resistance is a measurement function, not a source function: in manual
        ohms the 2400 still sources current (or voltage) and computes V/I, so
        the envelope check needs a real source quantity to work with.
        """
        function = self.source_func_var.get()
        return 'current' if function == 'resistance' else function

    def _measured_quantity(self):
        """Which SENSe subsystem the range and NPLC controls should address."""
        function = self.source_func_var.get()
        if function == 'resistance':
            return 'resistance'
        return 'voltage' if function == 'current' else 'current'

    def _current_operating_point(self):
        """(function, level, compliance) from the form, validated.

        Returns (None, None, None) in auto ohms, where the instrument chooses
        the test current itself and the form's source boxes do not apply.
        """
        if self.auto_ohms:
            return None, None, None
        function = self._effective_source_function()
        level = self._read_float(self.level_entry, "The source level")
        compliance = self._read_float(self.compliance_entry,
                                      "The compliance value")
        self.backend.limits.check_operating_point(function, level, compliance)
        return function, level, compliance

    def _apply_compliance(self):
        if not self.is_connected:
            return
        try:
            function, _level, compliance = self._current_operating_point()
            if function is None:
                self.log("Auto ohms sets its own test current; there is no "
                         "compliance value to send here.")
                return
            self.backend.set_compliance(function, compliance)
        except SafetyError as e:
            self._warn("Outside the safe envelope", str(e))
            return
        except Exception as e:
            self.log(f"Could not set compliance: {e}")
            self._error("Command failed", str(e))
            return
        self.compliance_confirmed = True
        unit = 'V' if function == 'current' else 'A'
        self.log(f"Compliance set to {compliance:g} {unit}.")

    def _apply_source_level(self):
        if not self.is_connected:
            return
        if self._ramping:
            self.log("Still stepping to the last level; ignoring.")
            return
        try:
            function, level, _compliance = self._current_operating_point()
            if function is None:
                self.log("Auto ohms picks the test current itself; the "
                         "manual warns the instrument errors if you try to "
                         "set it.")
                return
        except SafetyError as e:
            self._warn("Outside the safe envelope", str(e))
            return

        unit = 'A' if function == 'current' else 'V'
        try:
            if self.ramp_var.get() and self.output_live:
                self._ramping = True
                try:
                    self.backend.ramp_source_level(function, level)
                finally:
                    self._ramping = False
                self.log(f"Stepped gently to {level:g} {unit}.")
            else:
                self.backend.set_source_level(function, level)
                self.log(f"Source level set to {level:g} {unit}.")
        except Exception as e:
            self._ramping = False
            self.log(f"Could not set the source level: {e}")
            self._error("Command failed", str(e))

    # -----------------------------------------------------------------------
    # OTHER PANELS
    # -----------------------------------------------------------------------

    def _apply_source_range(self):
        if not self.is_connected:
            return
        function = self._effective_source_function()
        auto = self.source_autorange_var.get()
        try:
            value = 0.0 if auto else self._read_float(
                self.source_range_entry, "The source range")
            self.backend.set_source_range(function, value, auto=auto)
        except SafetyError as e:
            self._warn("Range not accepted", str(e))
            return
        except Exception as e:
            self.log(f"Could not set the source range: {e}")
            self._error("Command failed", str(e))
            return
        self.log("Source auto-range on." if auto
                 else f"Source range set to {value:g}.")

    def _apply_measure_range(self):
        if not self.is_connected:
            return
        quantity = self._measured_quantity()
        auto = self.measure_autorange_var.get()
        try:
            value = 0.0 if auto else self._read_float(
                self.measure_range_entry, "The measure range")
            self.backend.set_measure_range(quantity, value, auto=auto)
        except SafetyError as e:
            self._warn("Range not accepted", str(e))
            return
        except Exception as e:
            self.log(f"Could not set the measure range: {e}")
            self._error("Command failed", str(e))
            return
        self.log("Measurement auto-range on." if auto
                 else f"Measure range set to {value:g}.")

    def _apply_nplc(self):
        if not self.is_connected:
            return
        quantity = self._measured_quantity()
        try:
            nplc = self._read_float(self.nplc_entry, "The integration time")
            self.backend.set_nplc(quantity, nplc)
        except SafetyError as e:
            self._warn("Value not accepted", str(e))
            return
        except Exception as e:
            self.log(f"Could not set integration time: {e}")
            self._error("Command failed", str(e))
            return
        self.log(f"Integration time set to {nplc:g} power line cycles.")

    def _apply_delay(self):
        if not self.is_connected:
            return
        try:
            delay = self._read_float(self.delay_entry, "The source delay")
            self.backend.set_source_delay(delay)
        except SafetyError as e:
            self._warn("Value not accepted", str(e))
            return
        except Exception as e:
            self.log(f"Could not set source delay: {e}")
            self._error("Command failed", str(e))
            return
        self.log(f"Source delay set to {delay:g} s.")

    def _apply_sense_mode(self):
        if not self.is_connected:
            return
        four = self.four_wire_var.get()
        try:
            self.backend.set_sense_mode(four)
        except Exception as e:
            self.log(f"Could not change sensing mode: {e}")
            self._error("Command failed", str(e))
            return
        self.log("Four-wire sensing on." if four else "Two-wire sensing on.")

    def _apply_terminals(self):
        if not self.is_connected:
            return
        rear = self.rear_var.get()
        if self.output_live:
            self._warn(
                "Output is live",
                "Switching terminals while the output is on is not a good "
                "idea. Turn the output off first.")
            self.rear_var.set(not rear)
            return
        try:
            self.backend.set_terminals(rear)
        except Exception as e:
            self.log(f"Could not change terminals: {e}")
            self._error("Command failed", str(e))
            return
        self.log(f"Using the {'rear' if rear else 'front'} terminals.")

    def _apply_filter(self):
        if not self.is_connected:
            return
        mode = 'REP' if self.filter_mode_cb.get() == "Repeating" else 'MOV'
        try:
            count = int(self._read_float(self.filter_count_entry,
                                         "The filter count"))
            self.backend.set_filter(self.filter_var.get(), count, mode)
        except SafetyError as e:
            self._warn("Value not accepted", str(e))
            return
        except Exception as e:
            self.log(f"Could not set the filter: {e}")
            self._error("Command failed", str(e))
            return
        self.log(f"Filter {'on' if self.filter_var.get() else 'off'}, "
                 f"{count} readings, {self.filter_mode_cb.get().lower()}.")

    def _apply_auto_zero(self):
        if not self.is_connected:
            return
        enabled = self.azero_var.get()
        try:
            self.backend.set_auto_zero(enabled)
        except Exception as e:
            self.log(f"Could not change auto zero: {e}")
            self._error("Command failed", str(e))
            return
        self.log(f"Auto zero {'on' if enabled else 'off'}.")

    def _apply_output_off_mode(self):
        if not self.is_connected:
            return
        description = self.off_mode_cb.get()
        mode = next(k for k, v in Keithley2400Backend.OUTPUT_OFF_MODES.items()
                    if v == description)
        try:
            self.backend.set_output_off_mode(mode)
        except Exception as e:
            self.log(f"Could not set the output-off state: {e}")
            self._error("Command failed", str(e))
            return
        self.log(f"Output-off state set to {mode}.")
        if mode == 'HIMPedance':
            self.log("NOTE: the manual warns that high impedance wears the "
                     "output relay if the output is switched often.")

    # -----------------------------------------------------------------------
    # OUTPUT CONTROL
    # -----------------------------------------------------------------------

    def _toggle_output(self):
        if not self.is_connected:
            return
        if self.output_live:
            self._output_off()
        else:
            self._output_on()

    def _output_on(self):
        # Gate 1: the operating point has to be inside the envelope.
        try:
            function, level, compliance = self._current_operating_point()
        except SafetyError as e:
            self._warn("Outside the safe envelope", str(e))
            return

        # Auto ohms: the instrument chooses its own test current from the ohms
        # range, so there is no operating point of ours to confirm. The ohms
        # ranges top out well inside the envelope, so this stays a safe path.
        if function is None:
            try:
                self.backend.output_on()
            except Exception as e:
                self.log(f"Could not switch the output on: {e}")
                self._error("Command failed", str(e))
                return
            self.output_live = True
            self.output_btn.configure(text="Turn output OFF")
            self._start_blinking()
            self.log("OUTPUT ON in auto ohms; the instrument sets the test "
                     "current from the ohms range.")
            if self.poll_var.get():
                self._start_polling()
            return

        # Gate 2: compliance has to have been sent and confirmed at least once
        # for the current source function.
        if not self.compliance_confirmed:
            was_polling = self._poll_after_id is not None
            self._stop_polling()
            unit = 'V' if function == 'current' else 'A'
            agreed = messagebox.askyesno(
                "Confirm the compliance limit",
                f"About to source {level:g} "
                f"{'A' if function == 'current' else 'V'} with the compliance "
                f"set to {compliance:g} {unit}.\n\n"
                f"Worst case that is {abs(level * compliance):.3g} W into the "
                f"sample.\n\n"
                "Is that compliance value right?")
            if was_polling:
                self._start_polling()
            if not agreed:
                self.log("Output not switched on: compliance not confirmed.")
                return
            try:
                self.backend.set_compliance(function, compliance)
            except Exception as e:
                self._error("Command failed", str(e))
                return
            self.compliance_confirmed = True

        try:
            # Always start from zero and walk up, so switching the output on
            # never lands a finite level on the sample as a step.
            if self.ramp_var.get() and level != 0:
                self.backend.set_source_level(function, 0.0)
                self.backend.output_on()
                self._ramping = True
                try:
                    self.backend.ramp_source_level(function, level, start=0.0)
                finally:
                    self._ramping = False
            else:
                self.backend.output_on()
        except Exception as e:
            self._ramping = False
            self.log(f"Could not switch the output on: {e}")
            self._error("Command failed", str(e))
            return

        self.output_live = True
        self.output_btn.configure(text="Turn output OFF")
        self._start_blinking()
        self.log(f"OUTPUT ON at {level:g} "
                 f"{'A' if function == 'current' else 'V'}.")
        if self.poll_var.get():
            self._start_polling()

    def _output_off(self):
        self._stop_polling()
        try:
            self.backend.output_off()
        except Exception as e:
            self.log(f"Could not switch the output off: {e}")
            self._error("Command failed", str(e))
            return
        self.output_live = False
        self._stop_blinking()
        self.output_btn.configure(text="Turn output ON")
        self.compliance_banner.configure(text="")
        self.log("Output off.")

    # -----------------------------------------------------------------------
    # READING, POLLING, BLINKING
    # -----------------------------------------------------------------------

    def _measure_once(self):
        if not self.is_connected:
            return
        try:
            volts, amps, ohms = self.backend.read()
        except Exception as e:
            self.log(f"Reading failed: {e}")
            return
        self._show_reading(volts, amps, ohms)
        self.log(f"Reading: {self._format_reading(volts, 'V')}, "
                 f"{self._format_reading(amps, 'A')}, "
                 f"{self._format_reading(ohms, 'Ω')}")

    def _show_reading(self, volts, amps, ohms):
        self.reading_labels['voltage'].configure(
            text=self._format_reading(volts, 'V'))
        self.reading_labels['current'].configure(
            text=self._format_reading(amps, 'A'))
        self.reading_labels['resistance'].configure(
            text=self._format_reading(ohms, 'Ω'))

    def _on_poll_toggle(self):
        if self.poll_var.get():
            if self.output_live:
                self._start_polling()
        else:
            self._stop_polling()

    def _start_polling(self):
        """Idempotent: never leaves two after-chains running at once."""
        if self._poll_after_id is not None:
            return
        if not self.is_connected:
            return
        self._poll_tick()

    def _stop_polling(self):
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None

    def _poll_tick(self):
        self._poll_after_id = None
        if not (self.is_connected and self.output_live and self.poll_var.get()):
            return
        try:
            volts, amps, ohms = self.backend.read()
            self._show_reading(volts, amps, ohms)
            self._check_compliance()
        except Exception as e:
            self.log(f"Polling stopped: {e}")
            return
        interval = int(self.POLL_SECONDS_DEFAULT * 1000)
        self._poll_after_id = self.root.after(interval, self._poll_tick)

    def _check_compliance(self):
        """Warn once per excursion rather than on every single reading."""
        try:
            tripped, which = self.backend.in_compliance()
        except Exception:
            return
        if tripped and not self._last_compliance_state:
            self.compliance_banner.configure(
                text=f"⚠ IN {which.upper()} COMPLIANCE")
            self.log(f"WARNING: the instrument is in {which} compliance. "
                     f"The reading is limited by the compliance setting, not "
                     f"by the sample.")
            try:
                self.backend.beep(2000, 0.3)
            except Exception:
                pass
        elif not tripped and self._last_compliance_state:
            self.compliance_banner.configure(text="")
            self.log("Out of compliance again.")
        self._last_compliance_state = tripped

    def _start_blinking(self):
        if self._blink_after_id is not None:
            return
        self._blink_tick()

    def _stop_blinking(self):
        if self._blink_after_id is not None:
            try:
                self.root.after_cancel(self._blink_after_id)
            except tk.TclError:
                pass
            self._blink_after_id = None
        self._blink_on = False
        try:
            self.live_indicator.configure(fg=self.CLR_FRAME_BG)
        except tk.TclError:
            pass

    def _blink_tick(self):
        self._blink_after_id = None
        if not self.output_live:
            try:
                self.live_indicator.configure(fg=self.CLR_FRAME_BG)
            except tk.TclError:
                pass
            return
        self._blink_on = not self._blink_on
        try:
            self.live_indicator.configure(
                fg=self.CLR_LIVE_ON if self._blink_on else self.CLR_LIVE_DIM)
        except tk.TclError:
            return
        self._blink_after_id = self.root.after(self.BLINK_MS,
                                               self._blink_tick)

    # -----------------------------------------------------------------------
    # SHUTDOWN
    # -----------------------------------------------------------------------

    def _on_closing(self):
        self._stop_polling()
        self._stop_blinking()
        if self.is_connected:
            try:
                self.backend.disconnect()
            except Exception as e:
                print(f"Problem during shutdown: {e}")
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    root = tk.Tk()
    K2400DirectControlGUI(root)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
