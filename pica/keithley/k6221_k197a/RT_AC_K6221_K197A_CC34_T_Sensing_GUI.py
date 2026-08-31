"""
===============================================================================
 PROGRAM:      AC R-T (T Sensing, CC34) - Keithley 6221 + 197A + Cryocon 34
 PURPOSE:      AC resistance against temperature, logged passively while
               something else drives the temperature. Four-probe R(T) at
               one fixed frequency and current amplitude, indexed by the
               Cryo-con 34 reading.

               HOW THE RESISTANCE IS OBTAINED
               ------------------------------
               The 6221 defines the current exactly as it does in the lock-in
               modules: SOUR:WAVE:AMPL is a PEAK amplitude (6221 manual 7-7),
               the meter reads RMS volts, and so

                   I_rms = I_peak / sqrt(2)
                   R     = V_rms / I_rms

               WHAT A DMM CANNOT DO THAT A LOCK-IN CAN
               ---------------------------------------
               The 197A on AC volts is a broadband true-RMS converter with no
               reference input. There is no X, no Y and no phase: R is a
               MAGNITUDE. Nothing outside the drive frequency is rejected, and
               everything inside the meter's passband -- mains hum, thermal
               EMF at the wrong phase, amplifier noise, the harmonics of a
               non-ohmic contact -- adds in quadrature and always upward:

                   V_measured = sqrt(V_signal^2 + V_noise^2) >= V_signal

               so the resistance reported here is an UPPER BOUND, and it stops
               being a good one as soon as the signal stops being large
               compared with the pickup. The four-probe microvolts an SR830
               reads without difficulty are not measurable this way at all.
               Use this pairing when the voltage is comfortably above the
               meter's noise floor and no lock-in is free; use the SR830
               pairing for anything small.

               The spread across the averaged readings is logged next to the
               mean, because with no reference it is the only noise estimate
               there is. The 197A answers at most 3 readings per second and
               its AC accuracy is only specified over a band well inside the
               6221's range, so a drive outside that band is flagged on every
               point rather than silently accepted.

               !! The 197-dialect command letters and the reply format used
               here are UNVERIFIED against the printed Model 1973/1972
               interface manual, exactly as in Monitor_K197A_GUI.py. Confirm a
               reading by hand before a run is trusted.

               TEMPERATURE
               -----------
               The Cryo-con Model 34 equivalent of the Lakeshore passive
               module: same measurement, different thermometer. The Cryocon is
               treated as READ ONLY -- no *RST, no CONTROL/STOP, no setpoint.

               Three Cryocon-specific things are handled here and are worth
               knowing about. INPUT? answers in the CHANNEL'S OWN display
               units, so the channel is checked for Kelvin at connect time and
               the run refuses to start otherwise. A reply can be a status
               string ('-------' for a sensor fault, '.......' for a reading
               off the calibration curve) or carry a trailing unit character
               ('77.350K'), so replies are parsed rather than handed to
               float(). And the first '*IDN?' of a session has been seen to
               time out on hardware that was working, so it is retried.
 REFERENCES:   Model 1973/1972 IEEE-488 Interface Instruction Manual (printed;
                 the command letters here are UNVERIFIED against it)
               Model 6220/6221 Reference Manual, Section 7 "Wave Functions"
               Cryo-con Model 34 User's Guide, "Remote Operation"
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.0
===============================================================================
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import math
import os
import re
import time
import threading
import queue
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

import runpy
from multiprocessing import Process


def run_script_process(script_path):
    """Execute a script with runpy in its own directory, in a new process."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def _utility_path(name):
    """Absolute path to one of the shared utility GUIs beside this package."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Two levels up from this module's folder is the pica package root.
    return os.path.join(script_dir, "..", "..", "utils", name)


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        plotter_path = _utility_path("PlotterUtil_GUI.py")
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
        scanner_path = _utility_path("GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}")


def diagnose_connection_failure(what, address, error):
    """A clean diagnosis, not a traceback."""
    return [
        "Could not talk to the %s at '%s'." % (what, address),
        "Details: %s" % error,
        "Check that:",
        "  - the instrument is powered on and the GPIB cable is seated,",
        "  - the address matches the address set on the instrument itself,",
        "  - NI-488.2 / NI-VISA or the Keysight IO Libraries are installed,",
        "  - no other program is currently holding the GPIB board.",
    ]

PROGRAM_VERSION = "1.0"
MODULE_NAME = "RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py"
PROGRAM_TITLE = "AC R-T (T Sensing, CC34) - Keithley 6221 + 197A + Cryocon 34"
FILE_SUFFIX = "AC_RT_Sensing_CC34_197A"

# One line for the file header, saying what the run actually was.
MEASUREMENT_DESCRIPTION = (
    "AC R(T), passive: the temperature is driven elsewhere")

# The hint above the drive boxes, and the axis labels the detector
# decides.
DRIVE_HINT = (
    "One frequency and one current amplitude, held for the whole run. "
    "Keep the frequency inside the 197A AC volts band.")
VOLTAGE_AXIS_LABEL = "V rms"
SUB_AXIS_KEY = "voltage"
SUB_AXIS_LABEL = "V rms (V)"

INFO_TEXT = (
    "Program: AC R-T, passive temperature logging\n"
    "Instruments: Keithley 6221 (AC current), Keithley 197A (AC\n"
    "  volts), Cryo-con 34 (read only)\n"
    "R = V_rms / I_rms, with I_rms = I_peak / sqrt(2)\n"
    "\n"
    "WIRING\n"
    "  6221 OUTPUT  -> sample current leads (I+, I-)\n"
    "  Voltage probes -> 197A INPUT HI and LO\n"
    "No reference cable and no phase: R is a magnitude, and an\n"
    "upper bound. The 197A needs a Model 1973A / 1972A IEEE-488\n"
    "card and must be in remote.\n"
    "\n"
    "The Cryocon channel must be displaying Kelvin: this module\n"
    "checks it at connect time and never changes it.")

CONSOLE_REMINDERS = [
    "Reminder: the 197A needs a Model 1973A / 1972A IEEE-488 card and "
    "must be in remote.",
    "Its command set here is UNVERIFIED against the 1973/1972 "
    "interface manual. Confirm a reading by hand before trusting a "
    "run."
]


# -----------------------------------------------------------------------------
# --- KEITHLEY 197A: A TRUE-RMS DMM STANDING IN FOR A LOCK-IN ---
#
# WHAT THIS DETECTOR CAN AND CANNOT DO
# ------------------------------------
# The 197A on AC volts is a broadband true-RMS converter. It answers with one
# number: the RMS of everything inside its passband. It has no reference
# input, so there is no phase-sensitive detection and therefore
#
#   - no X and Y, and no theta. Only a magnitude.
#   - no rejection of anything outside the drive frequency. Mains hum,
#     thermal EMF at the wrong phase, amplifier noise and the harmonics of a
#     non-ohmic contact all add into the same number, always upward:
#
#         V_measured = sqrt(V_signal^2 + V_noise^2)  >=  V_signal
#
#     so the resistance this module reports is an UPPER BOUND, and it stops
#     being a good one as soon as the signal stops being large compared with
#     the pickup. A four-probe voltage of a few microvolts, which an SR830
#     reads without difficulty, is not measurable this way at all.
#   - no 4-wire function: the 197A has no sense terminals. It is used here as
#     a voltmeter across the sample's voltage probes; the 6221 supplies and
#     defines the current, which is what makes the measurement four-probe.
#
# The current is still known rather than measured, exactly as in the lock-in
# pairing: the 6221 is a current source, SOUR:WAVE:AMPL is a PEAK amplitude
# (6221 manual 7-7), the meter reports RMS volts, and so
#
#     I_rms = I_peak / sqrt(2)          R = V_rms / I_rms
#
# Use this pairing when the sample resistance is large enough that the voltage
# is comfortably above the meter's noise floor and no lock-in is free. Use the
# SR830 pairing for anything small. The 6221 phase marker is not switched on
# here: nothing is listening to it.
#
# !! UNVERIFIED !!  The command letters and the reply format below have NOT
# been confirmed against hardware or a machine-readable manual. The
# authoritative source is the printed Model 1973/1972 IEEE-488 Interface
# Instruction Manual, and this block is copied unchanged from
# Monitor_K197A_Instrument_Control.py so that the two agree. Correct it from
# that manual before trusting a reading, and change nothing else.
# -----------------------------------------------------------------------------

# Every command is terminated with 'X' to execute. Several may be concatenated
# into one string, e.g. "F1R0X".
K197A_FUNCTION_ACV = "F1"          # F<n>X: AC volts
K197A_RANGES = {                   # R<n>X ; R0 is autorange on this family
    "auto": "R0", "1": "R1", "2": "R2", "3": "R3",
    "4": "R4", "5": "R5", "6": "R6", "7": "R7",
}
K197A_RANGE_NAMES = ["auto", "1", "2", "3", "4", "5", "6", "7"]
K197A_EXECUTE = "X"

# The 197A specifications (Rev. B) give a maximum of 3 readings per second.
K197A_MIN_READ_INTERVAL = 0.34

# UNVERIFIED: the AC volts passband. The 197A is a bench meter, not a wideband
# voltmeter, and its AC accuracy is only specified over a band well inside the
# 6221's 1 mHz to 100 kHz. Outside the window below the reading is not wrong
# in a way the meter reports -- it simply rolls off -- so a frequency outside
# it is WARNED about on every point and written into the Flags column, never
# silently accepted as good data. Narrow these two numbers once the 197A
# specification sheet has been read.
K197A_ACV_BAND_MIN, K197A_ACV_BAND_MAX = 20.0, 20000.0

# The 6221 can generate to 100 kHz, and the meter will return a number there.
# It will just be a number about a signal the meter has attenuated. Refusing
# it outright would stop a deliberate roll-off check, so the ceiling stays at
# the 6221's and the band check does the talking.
DETECTOR_FREQ_MAX = 100000.0
DETECTOR_FREQ_LIMIT_NAME = "Keithley 6221 wave generator"

# Nothing is locked to the 6221 here, so the phase marker stays off.
USE_PHASE_MARKER = False

# UNVERIFIED: the reply format of the 197A. This regex pulls the first
# floating point number out of whatever comes back.
K197A_NUMBER_RE = re.compile(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?')

K197A_NO_RESPONSE_MESSAGE = (
    "No response from GPIB address {addr}. The 197A has no built-in IEEE-488 "
    "interface; check that a Model 1973A or 1972A card is fitted, that the "
    "address switches on the card match, and that the meter is in remote. "
    "Run the GPIB Scanner utility to list what is actually on the bus.")

K197A_WRONG_INSTRUMENT_MESSAGE = (
    "The instrument at {addr} answered {reply!r}, which is not a 197A "
    "reading. That address is holding something else -- the reference table "
    "has a Keithley 2182 on GPIB0::7 as well. Run the GPIB Scanner utility "
    "and use the 197A's actual address. Nothing was sent to the instrument.")


def parse_k197a_reading(raw):
    """Pull the first floating point number out of a 197A reply string.

    Returns (value, raw), with value None if nothing numeric was found.
    """
    if raw is None:
        return None, ""
    match = K197A_NUMBER_RE.search(raw)
    if not match:
        return None, raw
    try:
        return float(match.group(0)), raw
    except ValueError:
        return None, raw


def looks_like_k197a_reading(raw):
    """True if a reply is shaped like a 197A reading, not an identity string.

    A 197A answers with a number, optionally behind a 4-character function
    prefix ("NACV+1.23456E+0"). A modern SCPI instrument left with a pending
    *IDN? also answers something containing digits -- so the comma count, not
    the presence of a number, is what separates them.
    """
    value, _ = parse_k197a_reading(raw)
    if value is None:
        return False
    return str(raw).count(',') < 2


def meter_health(volts, spread, frequency_hz, readings):
    """Everything that makes a 197A point untrustworthy, as a list of strings.

    An empty list means the point is good. There is no status byte to read on
    this meter, so every check here is made out of the numbers themselves.
    """
    problems = []
    if not K197A_ACV_BAND_MIN <= frequency_hz <= K197A_ACV_BAND_MAX:
        problems.append(
            "Drive is at %.4g Hz, outside the 197A AC volts band %.4g to "
            "%.4g Hz: the meter is rolling the signal off and the resistance "
            "is low by an unknown factor."
            % (frequency_hz, K197A_ACV_BAND_MIN, K197A_ACV_BAND_MAX))
    if volts <= 0:
        problems.append(
            "The meter returned %.4g V. An AC volts reading cannot be zero or "
            "negative: check that the meter is on ACV and the leads are on "
            "the voltage probes." % volts)
    elif len(readings) > 1 and spread > 0.1 * abs(volts):
        # The spread across the averaged readings is the only noise estimate
        # available without a reference, so it is the one that gets logged.
        problems.append(
            "The %d averaged readings spread over %.3g V, more than 10%% of "
            "the %.4g V mean: the signal is at or below what this meter can "
            "resolve here." % (len(readings), spread, volts))
    return problems


class NoResponseError(Exception):
    """The address answered nothing at all, so the caller can diagnose it."""


class WrongInstrumentError(Exception):
    """The address answered, but not like a 197A.

    The reference address table has the 197A sharing GPIB0::7 with a Keithley
    2182, and any address can be changed on the rack. Without this check a
    mis-set address would have the meter's 197-dialect commands ("F1R0X")
    written into whatever else is sitting there, and its replies logged as
    voltages.
    """


class Keithley197AMeter:
    """The 197A side: AC volts across the sample's voltage probes.

    Pre-SCPI. There is no identify query, no reset and no colon-prefixed
    command anywhere in this class, so `idn` is a description this module
    writes, not something the meter said.
    """

    def __init__(self, visa_address):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed.")
        self.address = visa_address
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 10000
        self.range_name = "auto"
        self.idn = "Keithley 197A (no identify query on this interface)"
        self.probe()

    def probe(self):
        """Send the bare execute character and try to read one reply.

        This is the only connection check available. Raises NoResponseError on
        a bus timeout and WrongInstrumentError if the reply is not shaped like
        a meter reading -- before any 197-dialect command has been written.
        """
        try:
            self.instrument.write(K197A_EXECUTE)
            reply = self.instrument.read().strip()
        except Exception as exc:
            if pyvisa is not None and isinstance(exc, pyvisa.errors.VisaIOError):
                raise NoResponseError(
                    K197A_NO_RESPONSE_MESSAGE.format(addr=self.address))
            raise
        if not looks_like_k197a_reading(reply):
            raise WrongInstrumentError(
                K197A_WRONG_INSTRUMENT_MESSAGE.format(
                    addr=self.address, reply=reply))
        return reply

    def configure_acv(self, range_name):
        """Select AC volts and a range, in one concatenated command string."""
        if range_name not in K197A_RANGES:
            raise ValueError("Unknown 197A range %r." % (range_name,))
        # UNVERIFIED: that F and R may be concatenated and that a single
        # trailing X executes both. Confirm from the 1973/1972 manual.
        command = (K197A_FUNCTION_ACV + K197A_RANGES[range_name]
                   + K197A_EXECUTE)
        self.instrument.write(command)
        self.range_name = range_name
        return command

    def read_once(self):
        """One reading in volts, or a ValueError naming the reply."""
        # UNVERIFIED: whether the meter talks on demand (a bare read) or needs
        # a trigger command first. Confirm from the interface manual.
        raw = self.instrument.read().strip()
        value, _ = parse_k197a_reading(raw)
        if value is None:
            raise ValueError(
                "The 197A answered %r, which holds no number." % raw)
        return value

    def read_average(self, count, interval=K197A_MIN_READ_INTERVAL):
        """Mean of `count` readings, with the spread that came with them.

        The 197A cannot answer faster than 3 readings per second, so asking it
        to is not faster -- it just reads the same conversion twice. Returns
        (mean, spread, readings).
        """
        if count < 1:
            raise ValueError("At least one reading is needed.")
        gap = max(interval, K197A_MIN_READ_INTERVAL)
        readings = []
        for index in range(count):
            if index:
                time.sleep(gap)
            readings.append(self.read_once())
        mean = sum(readings) / len(readings)
        spread = max(readings) - min(readings)
        return mean, spread, readings

    def close(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the 197A: %s" % exc)
            finally:
                self.instrument = None


DETECTOR_COLUMNS = ("V rms (V),V spread (V),Readings averaged,"
                    "R magnitude (Ohm)")


def detector_row_fields(point):
    """The detector's own columns for one row of the data file."""
    return [
        "%.6E" % point['voltage'],
        "%.6E" % point['spread'],
        "%d" % point['count'],
        "%.6E" % point['resistance'],
    ]


def detector_header_lines(idn, address, settings):
    """The '#' header lines describing the meter.

    `settings` is what the module asked for: the 197A cannot be queried for
    its state, so unlike the lock-in modules these lines are the request and
    not a read-back. That difference is stated in the file itself.
    """
    return [
        "# Voltmeter: %s" % idn,
        "# Voltmeter VISA address: %s" % address,
        "# Function: AC volts (F1), range %s" % settings['range_name'],
        "# Readings averaged per point: %d" % settings['averages'],
        "# The 197A has no identify or state query: the two lines above are "
        "what this module sent, not a read-back.",
        "# No phase-sensitive detection: R is a MAGNITUDE and an upper bound. "
        "Everything in the meter's passband adds into V_rms.",
        "# 197A AC volts band assumed (UNVERIFIED): %.4g to %.4g Hz."
        % (K197A_ACV_BAND_MIN, K197A_ACV_BAND_MAX),
        "# Resistance convention: R = V_rms / I_rms, I_rms = I_peak / sqrt(2)",
    ]

# -----------------------------------------------------------------------------
# --- KEITHLEY 6221 WAVE GENERATOR ---
# 6221 manual Table 7-4 "Waveform function commands", page 7-27.
# -----------------------------------------------------------------------------

# A 6221 answers *IDN? with "KEITHLEY INSTRUMENTS INC.,MODEL 6221,s/n,ver".
# Instruments are identified by that string and never by address: the defaults
# in the address boxes are only starting values, and current-source commands
# must not be written into something that is not a current source.
K6221_IDN_MARKER = "6221"


def is_k6221_idn(idn):
    """True if a *IDN? reply came from a Keithley 6221 current source."""
    return K6221_IDN_MARKER in str(idn).upper()


# SOUR:WAVE:FREQuency <NRf>. Section 7-6 "Frequency" gives the usable setting
# range as 1 mHz to 100 kHz.
WAVE_FREQ_MIN, WAVE_FREQ_MAX = 0.001, 100000.0

# SOUR:WAVE:AMPLitude <NRf> = 2e-12 to 0.105 A PEAK. Not RMS: 6221 manual 7-7
# "Amplitude units", remote operations always receive and return peak.
WAVE_AMPL_MIN, WAVE_AMPL_MAX = 2e-12, 0.105

# SOUR:CURR:COMPliance. The 6221 output compliance is 0.1 V to 105 V.
COMPLIANCE_MIN, COMPLIANCE_MAX = 0.1, 105.0

# SOUR:WAVE:PMARk:OLINe <NRf> = 1 to 6, default 3. It may not collide with the
# trigger layer output line (default 2), or the 6221 answers -221 Settings
# Conflict (6221 manual note 7, page 7-29).
PMARK_LINE_MIN, PMARK_LINE_MAX = 1, 6
PMARK_LINE_DEFAULT = 3

# SOUR:WAVE:PMARk <NRf> = 0 to 360 degrees. 0 degrees is the zero crossing of
# the sine (6221 manual 7-9), which is the edge a lock-in wants as a reference.
PMARK_PHASE_DEFAULT = 0.0


def rms_from_peak(peak_amps):
    """The 6221 is programmed in PEAK amps; every voltage here is RMS.

    Dividing an RMS voltage by a peak current is the single easiest way to get
    a resistance that is wrong by exactly sqrt(2) = 1.414, which looks almost
    right and is not. Every division in this module goes through here.
    """
    return peak_amps / math.sqrt(2.0)


def peak_from_rms(rms_amps):
    """Inverse of rms_from_peak, for turning an RMS wish into SOUR:WAVE:AMPL."""
    return rms_amps * math.sqrt(2.0)


def validate_drive(frequency_hz, current_peak_a, compliance_v):
    """Refuse anything the 6221 would clamp or reject, before it is sent."""
    if not WAVE_FREQ_MIN <= frequency_hz <= WAVE_FREQ_MAX:
        raise ValueError(
            "Frequency %g Hz is outside the 6221 wave range %g to %g Hz."
            % (frequency_hz, WAVE_FREQ_MIN, WAVE_FREQ_MAX))
    if frequency_hz > DETECTOR_FREQ_MAX:
        raise ValueError(
            "Frequency %g Hz is above the %s limit of %g Hz."
            % (frequency_hz, DETECTOR_FREQ_LIMIT_NAME, DETECTOR_FREQ_MAX))
    if not WAVE_AMPL_MIN <= current_peak_a <= WAVE_AMPL_MAX:
        raise ValueError(
            "Current amplitude %g A peak is outside the 6221 range %g to "
            "%g A peak." % (current_peak_a, WAVE_AMPL_MIN, WAVE_AMPL_MAX))
    if not COMPLIANCE_MIN <= compliance_v <= COMPLIANCE_MAX:
        raise ValueError(
            "Compliance %g V is outside the 6221 range %g to %g V."
            % (compliance_v, COMPLIANCE_MIN, COMPLIANCE_MAX))


def linear_points(start, stop, count):
    """Evenly spaced values, inclusive of both ends. count == 1 gives [start]."""
    if count < 1:
        raise ValueError("A sweep needs at least one point.")
    if count == 1:
        return [start]
    step = (stop - start) / (count - 1)
    return [start + step * index for index in range(count)]


def log_points(start, stop, count):
    """Logarithmically spaced values. Both ends must be positive."""
    if start <= 0 or stop <= 0:
        raise ValueError("A logarithmic sweep needs positive endpoints.")
    if count < 1:
        raise ValueError("A sweep needs at least one point.")
    if count == 1:
        return [start]
    ratio = math.log10(stop / start) / (count - 1)
    return [start * (10.0 ** (ratio * index)) for index in range(count)]


class K6221WaveSource:
    """The 6221 side: a sine current, with an optional phase marker."""

    def __init__(self, visa_address):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed.")
        self.address = visa_address
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 25000
        self.output_on = False

        self.idn = self.instrument.query('*IDN?').strip()
        if not is_k6221_idn(self.idn):
            try:
                self.instrument.close()
            finally:
                self.instrument = None
            raise ConnectionError(
                "%s is not a Keithley 6221: it identifies itself as '%s'. "
                "Refusing to send current source commands. Scan the bus and "
                "use the 6221's actual address." % (visa_address, self.idn))

    def prepare(self, compliance_v, phase_marker, pmark_line, pmark_phase):
        """Reset into a known state and set what does not change per point."""
        self.instrument.write('*RST')
        time.sleep(1.0)
        self.instrument.write('*CLS')
        self.instrument.write('SOUR:CURR:COMP %.4f' % compliance_v)
        self.instrument.write('SOUR:WAVE:FUNC SIN')
        self.instrument.write('SOUR:WAVE:OFFS 0')
        # BEST picks the source range from the amplitude at arm time, so a
        # sweep that spans decades of current does not sit on one coarse range.
        self.instrument.write('SOUR:WAVE:RANG BEST')
        # INFinity: the waveform runs until it is aborted. A time duration
        # would silently switch the current off part way through a settle.
        self.instrument.write('SOUR:WAVE:DUR:TIME INF')
        if phase_marker:
            self.instrument.write('SOUR:WAVE:PMAR:STAT ON')
            self.instrument.write('SOUR:WAVE:PMAR:OLIN %d' % int(pmark_line))
            self.instrument.write('SOUR:WAVE:PMAR %.1f' % pmark_phase)
        else:
            self.instrument.write('SOUR:WAVE:PMAR:STAT OFF')

    def set_drive(self, frequency_hz, current_peak_a):
        """Set frequency and amplitude, then arm and start the waveform.

        6221 manual note 12, page 7-30: error checking happens at ARM, and the
        source is set to zero at that moment. Note 13: the output must be on
        for the waveform to reach the terminals. So the order is abort, set,
        arm, init, and then confirm the output really is on.
        """
        self.abort()
        self.instrument.write('SOUR:WAVE:FREQ %.6f' % frequency_hz)
        self.instrument.write('SOUR:WAVE:AMPL %.9E' % current_peak_a)
        self.instrument.write('SOUR:WAVE:ARM')
        self.instrument.write('SOUR:WAVE:INIT')
        self.output_on = True
        if self.instrument.query('OUTP?').strip().startswith('0'):
            self.instrument.write('OUTP ON')

    def read_error(self):
        """SYST:ERR? -> (code, text). Code 0 means the queue was empty."""
        reply = self.instrument.query('SYST:ERR?').strip()
        head, _, tail = reply.partition(',')
        try:
            code = int(float(head))
        except ValueError:
            return None, reply
        return code, tail.strip().strip('"')

    def abort(self):
        """Stop generating. Safe to call when nothing is running."""
        try:
            self.instrument.write('SOUR:WAVE:ABOR')
        except Exception as exc:
            print("Warning: SOUR:WAVE:ABOR failed: %s" % exc)

    def output_off(self):
        """Current off. Called on stop, on error and on the way out."""
        self.abort()
        try:
            self.instrument.write('OUTP OFF')
        except Exception as exc:
            print("Warning: OUTP OFF failed: %s" % exc)
        self.output_on = False

    def close(self):
        if self.instrument is not None:
            try:
                self.output_off()
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the 6221: %s" % exc)
            finally:
                self.instrument = None

# =============================================================================
# CRYOCON LINK HARDENING  (read-only; inlined so each module stays standalone)
# =============================================================================
#
# Three failures seen on a Cryo-con Model 34 Rev 3.03A, 28 Aug 2026:
#
#   1. The bus scan identified the instrument, and the very next session's
#      '*IDN?' died inside viWrite with VI_ERROR_TMO. Pressing Start again
#      connected normally. A timeout on the WRITE means the instrument stopped
#      accepting bytes for a moment, not that it is absent or at another
#      address, so the cure is to wait and ask again instead of giving up.
#      Handled by CRYOCON_OPEN_SETTLE_S plus the retry loop in
#      open_cryocon_session().
#
#   2. A reading query answered with a Cryo-con status string instead of a
#      number and float() raised, which killed the worker thread. The front
#      panel shows dashes for a sensor fault and dots for a reading that is
#      inside the instrument's range but off the sensor's calibration curve;
#      over the bus those arrive as the literal strings below. A reply can
#      also carry a trailing unit character, as in '77.350K', which float()
#      rejects outright. Handled by parse_cryocon_number(), which names the
#      condition instead of raising a bare ValueError.
#
#   3. The Cryocon was picked by address alone. It is at GPIB0::12 as of
#      29 Aug 2026, and the Lakeshore 350 now sits on GPIB1::12 -- the
#      Cryo-con's own factory address. Selection is by '*IDN?' content, so a
#      re-addressed Cryocon is still found and a stranger on the factory
#      address is not mistaken for one.
#
# Nothing in this block writes to the instrument.

# Factory address, used only as a last-resort hint when nothing answers.
CRYOCON_ADDRESS_HINT = "GPIB0::12"
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON", "CRYO CON")

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries

CRYOCON_INPUT_CHANNELS = ["A", "B", "C", "D"]

# Literal replies that are status, not data.
CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}

# Leading signed decimal, with or without an exponent. Used to peel a trailing
# unit character off replies such as '77.350K'.
_CRYOCON_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')

# The dash and dot runs are as long as the display resolution setting makes
# them, so they are matched by shape rather than by a fixed seven characters.
_CRYOCON_FAULT_RE = re.compile(r'^-{2,}$')
_CRYOCON_RANGE_RE = re.compile(r'^\.{2,}$')


# A sensor-status reply ('-------' / '.......') is an INVALID READING, not a
# communication error: the instrument answered, the sensor did not, so no
# amount of reconnecting cures it and the comm-retry path must never be
# entered on one. Almost every one of them is transient -- the Model 34 shows
# dashes for a moment while an input range switches -- so the reading is
# retried in place first. Past that the point's temperature becomes NaN: the
# electrical measurement at that point is still good and is still written,
# only the thermometry column is missing, and the run carries on so that a
# sensor which recovers at 3 a.m. resumes logging on its own.
CRYOCON_READ_RETRIES = 3            # extra tries before a point becomes NaN
CRYOCON_READ_RETRY_S = 0.3          # pause between those tries


class CryoconStatusError(ValueError):
    """A query returned a Cryo-con status string where a number was expected."""


def parse_cryocon_number(raw, what, channel=None):
    """Turn a Cryo-con reply into a float, or say precisely why it is not one.

    Handles three things a plain float() call did not: status strings, a
    trailing unit character, and multi-channel replies, which come back as
    fields separated by semicolons.
    """
    text = str(raw).strip()
    where = " on channel %s" % channel if channel else ""
    if ';' in text:
        text = text.split(';')[0].strip()
    if text in CRYOCON_STATUS_STRINGS:
        raise CryoconStatusError(
            "Cryocon %s%s returned '%s': %s."
            % (what, where, text, CRYOCON_STATUS_STRINGS[text]))
    if _CRYOCON_FAULT_RE.match(text):
        raise CryoconStatusError(
            "Cryocon %s%s returned '%s': %s."
            % (what, where, text, CRYOCON_STATUS_STRINGS['-------']))
    if _CRYOCON_RANGE_RE.match(text):
        raise CryoconStatusError(
            "Cryocon %s%s returned '%s': sensor fault, no sensor, or %s."
            % (what, where, text, CRYOCON_STATUS_STRINGS['.......']))
    if not text:
        raise CryoconStatusError(
            "Cryocon %s%s returned an empty reply." % (what, where))
    try:
        return float(text)
    except ValueError:
        pass
    match = _CRYOCON_NUMBER_RE.match(text)
    if match:
        return float(match.group(0))
    raise CryoconStatusError(
        "Cryocon %s%s returned '%s' (sensor fault, no sensor, or reading out "
        "of range)." % (what, where, text))


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


def open_cryocon_session(visa_address, log=None):
    """Open a Cryo-con session, retrying the first '*IDN?'.

    Returns (instrument, idn). Raises ConnectionError if nothing answers, or
    if what answers is not a Cryo-con.
    """
    if pyvisa is None:
        raise ConnectionError(
            "PyVISA is not available. Install pyvisa and a VISA backend "
            "(NI-VISA or pyvisa-py).")
    say = log if callable(log) else (lambda msg: print(msg))
    rm = pyvisa.ResourceManager()
    last_error = None
    for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
        inst = None
        try:
            inst = rm.open_resource(visa_address)
            inst.timeout = CRYOCON_TIMEOUT_MS
            # The Cryocon GPIB port frames lines with EOI and no EOS
            # character, so the PyVISA termination defaults are left alone.
            time.sleep(CRYOCON_OPEN_SETTLE_S)
            idn = inst.query('*IDN?').strip()
            if not idn:
                raise ConnectionError(
                    "%s accepted the command but sent no identification."
                    % visa_address)
            if not is_cryocon_idn(idn):
                inst.close()
                raise ConnectionError(
                    "%s is not a Cryo-con: it identifies itself as '%s'. Scan "
                    "the bus and pick the Cryocon's actual address (it does "
                    "not have to be %s)."
                    % (visa_address, idn, CRYOCON_ADDRESS_HINT))
            if attempt > 1:
                say("  Cryocon answered on attempt %d." % attempt)
            return inst, idn
        except ConnectionError:
            # Wrong instrument, or a silent one. Retrying will not change the
            # answer, so let it out immediately.
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            raise
        except Exception as exc:
            last_error = exc
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            if attempt < CRYOCON_CONNECT_ATTEMPTS:
                say("  Cryocon did not answer at %s (attempt %d of %d): %s. "
                    "Retrying in %.1f s."
                    % (visa_address, attempt, CRYOCON_CONNECT_ATTEMPTS,
                       type(exc).__name__, CRYOCON_RETRY_WAIT_S))
                time.sleep(CRYOCON_RETRY_WAIT_S)
    raise ConnectionError(
        "No reply from a Cryo-con at %s after %d attempts. Last error: %s. "
        "Check that the instrument is powered, that its SYS menu has RIO-Port "
        "set to GPIB rather than RS-232, and that RIO-Address matches this "
        "VISA address."
        % (visa_address, CRYOCON_CONNECT_ATTEMPTS, last_error))


class Cryocon34Monitor:
    """Read-only thermometry from a Cryo-con Model 34.

    Nothing in this class writes to the instrument: no *RST, no CONTROL/STOP,
    no setpoint. Whatever is driving the temperature keeps driving it.
    """

    def __init__(self, visa_address, channel="A", log=None):
        if channel not in CRYOCON_INPUT_CHANNELS:
            raise ValueError("Unknown Cryocon input channel %r." % (channel,))
        self.address = visa_address
        self.channel = channel
        self.instrument, self.idn = open_cryocon_session(visa_address, log)
        self.verify_units()

    def verify_units(self):
        """Confirm the channel reports Kelvin.

        INPUT? returns the reading in the channel's own display units, so a
        channel left in C or F would silently log wrong numbers. This is a
        query only -- the units are never changed from here.
        """
        units = self.instrument.query(
            'INPUT %s:UNITS?' % self.channel).strip().upper()
        if not units.startswith('K'):
            raise ValueError(
                "Cryocon channel %s is reporting in '%s', not Kelvin. Set "
                "that channel to K on the Cryocon front panel (this program "
                "never writes to it)." % (self.channel, units))
        return units

    def read_temperature(self):
        """INPUT? <channel> -> Kelvin, or NaN on a sensor fault.

        A status reply ('-------' or '.......') is retried in place, because
        dashes during an input range switch clear within a second and must
        not cost a data point. If it still will not read, NaN comes back
        rather than an exception: the resistance measurement at that point
        is still good, the stop-window comparisons are false for NaN so the
        run is never ended by a fault, and logging resumes on its own when
        the sensor does. A genuine comm failure still raises.
        """
        for attempt in range(CRYOCON_READ_RETRIES + 1):
            raw = self.instrument.query('INPUT? %s' % self.channel).strip()
            try:
                return parse_cryocon_number(raw, "temperature", self.channel)
            except CryoconStatusError as exc:
                if attempt < CRYOCON_READ_RETRIES:
                    time.sleep(CRYOCON_READ_RETRY_S)
                    continue
                self._sensor_faults = getattr(self, '_sensor_faults', 0) + 1
                if (self._sensor_faults <= 5
                        or self._sensor_faults % 25 == 0):
                    print("  Sensor fault #%d: temperature logged as NaN, "
                          "run continues. %s" % (self._sensor_faults, exc))
                return float('nan')

    def shutdown(self):
        """Nothing to undo: this module never wrote to the instrument."""

    def close(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the Cryocon: %s" % exc)
            finally:
                self.instrument = None


def thermometer_header_lines(thermometer, params):
    return [
        "# Thermometer: %s" % thermometer.idn,
        "# Thermometer VISA address: %s" % thermometer.address,
        "# Cryocon input channel: %s (INPUT?, display units verified as K)"
        % thermometer.channel,
        "# Temperature control: NONE from this module. It only reads.",
        "# Logging interval (s): %.2f" % params['interval'],
        "# Stop window (K): %.3f to %.3f"
        % (params['stop_low'], params['stop_high']),
    ]

THERMOMETER_NAME = "Cryocon 34"
THERMOMETER_CHANNELS = CRYOCON_INPUT_CHANNELS

# -----------------------------------------------------------------------------
# --- SAMPLE GEOMETRY ---
# -----------------------------------------------------------------------------

GEOMETRY_LABELS = [
    "None (report resistance only)",
    "Bar / strip: rho = R x w x t / L",
    "van der Pauw (symmetric): rho = (pi/ln2) x t x R",
]


def resistivity_from_resistance(resistance_ohm, geometry_code,
                                width_m, thickness_m, length_m):
    """Resistivity in ohm metre, or None when no geometry was given.

    Bar / strip is the four-probe case: the current runs along L through the
    cross section w x t and the voltage probes are L apart. The van der Pauw
    form is the symmetric special case, where the correction factor is 1.
    """
    if geometry_code == 0:
        return None
    if geometry_code == 1:
        if width_m <= 0 or thickness_m <= 0 or length_m <= 0:
            raise ValueError(
                "Bar geometry needs a positive width, thickness and length.")
        return resistance_ohm * width_m * thickness_m / length_m
    if geometry_code == 2:
        if thickness_m <= 0:
            raise ValueError("van der Pauw geometry needs a positive thickness.")
        return (math.pi / math.log(2.0)) * thickness_m * resistance_ohm
    raise ValueError("Unknown geometry code %r" % (geometry_code,))


def sheet_resistance(resistivity_ohm_m, thickness_m):
    """Ohm per square, or None when either input is missing."""
    if resistivity_ohm_m is None or thickness_m <= 0:
        return None
    return resistivity_ohm_m / thickness_m

# -----------------------------------------------------------------------------
# --- OUTPUT FILE ---
# One commented header, '#' on every line, then a single comma separated table.
# PlotterUtil_GUI reads this shape directly.
# -----------------------------------------------------------------------------

DATA_COLUMNS = ("Timestamp,Elapsed (s),Temperature (K),Frequency set (Hz),"
                "I peak (A),I rms (A)," + DETECTOR_COLUMNS
                + ",Resistivity (Ohm m),Sheet resistance (Ohm/sq),Flags")


def build_log_header(sample, operator, source, detector, thermometer,
                     detector_settings, params):
    """The commented header. Instrument lines come from the instruments.

    Anything that can be read back from an instrument is read back from it
    rather than copied out of a GUI box: the two can differ, and the
    instrument is the one that is true.
    """
    geometry = params['geometry']
    lines = [
        "# PICA - %s" % PROGRAM_TITLE,
        "# Module: %s, version %s" % (MODULE_NAME, PROGRAM_VERSION),
        "# Sample: %s" % sample,
        "# Operator: %s" % operator,
        "# Current source: %s" % source.idn,
        "# Current source VISA address: %s" % source.address,
    ]
    lines.extend(detector_header_lines(
        detector.idn, detector.address, detector_settings))
    if thermometer is not None:
        lines.extend(thermometer_header_lines(
            thermometer, params['temperature']))
    lines.extend([
        "# Measurement: %s" % MEASUREMENT_DESCRIPTION,
        "# Drive: %s" % params['drive_description'],
        "# Compliance (V): %.3f" % params['compliance'],
        "# Phase marker: %s" % (
            "%.1f deg on trigger link line %d"
            % (params['pmark_phase'], params['pmark_line'])
            if USE_PHASE_MARKER else "off (nothing is locked to it here)"),
        "# Settle before each point (s): %.3f" % params['settle'],
    ])
    if geometry['code'] == 0:
        lines.append("# Geometry: none, resistance only")
    else:
        lines.append("# Geometry: %s" % GEOMETRY_LABELS[geometry['code']])
        lines.append(
            "# Width (m): %.6E, Thickness (m): %.6E, Length (m): %.6E"
            % (geometry['width'], geometry['thickness'], geometry['length']))
    lines.append("# Started: %s" % datetime.now().isoformat(timespec="seconds"))
    lines.append(DATA_COLUMNS)
    lines.append("")
    return "\n".join(lines)

# -----------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -----------------------------------------------------------------------------

class ACResistanceCC34SensingGUI:
    PROGRAM_VERSION = PROGRAM_VERSION
    MODULE_NAME = MODULE_NAME
    DEFAULT_SOURCE_ADDRESS = "GPIB0::13::INSTR"
    DEFAULT_DETECTOR_ADDRESS = "GPIB0::7::INSTR"
    DEFAULT_THERMOMETER_ADDRESS = "GPIB0::12::INSTR"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 560

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Modern Dark Theme (PICA Standard) ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_READOUT = ('Segoe UI', 18, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("PICA " + PROGRAM_TITLE)
        try:
            self.root.state('zoomed')
        except tk.TclError:
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1250, 850)

        self.source = None
        self.detector = None
        self.thermometer = None
        self.io_lock = threading.Lock()
        self.action_queue = queue.Queue()
        self.data_queue = queue.Queue()
        self.is_running = False
        self.stop_requested = False
        self.start_time = None
        self.data_storage = {'x': [], 'y': [], 'sub_x': [], 'sub_y': []}
        self.file_location_path = ""
        self.data_filepath = None
        self.logo_image = None
        self.enum_widgets = {}
        self.entries = {}
        self.params = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(150, self._process_action_queue)

    # ------------------------------------------------------------- appearance
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel', background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure(
            'Sub.TLabel', background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL)
        style.configure(
            'Readout.TLabel', background=self.CLR_GRAPH_BG,
            foreground=self.CLR_ACCENT_GOLD, font=self.FONT_READOUT)
        style.configure(
            'ReadoutName.TLabel', background=self.CLR_GRAPH_BG,
            foreground=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL)
        style.configure('TCheckbutton', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_BG_DARK)])

        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK, borderwidth=0)
        style.configure(
            'TCombobox', fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK, arrowcolor=self.CLR_TEXT_DARK,
            selectbackground=self.CLR_ACCENT_GOLD,
            selectforeground=self.CLR_TEXT_DARK)

        style.configure(
            'TButton', font=self.FONT_BASE, padding=(10, 7),
            foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER,
            borderwidth=0, focusthickness=0, focuscolor='none')
        style.map(
            'TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[('active', self.CLR_TEXT_DARK),
                        ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton', font=self.FONT_BASE, padding=(10, 7),
            background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.configure(
            'Stop.TButton', font=self.FONT_BASE, padding=(10, 7),
            background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)

        style.configure('TLabelframe', background=self.CLR_BG_DARK,
                        bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure(
            'TLabelframe.Label', background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 3
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 1

    # ---------------------------------------------------------------- widgets
    def create_widgets(self):
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(
            self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)
        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_connection_frame(scrollable_frame)
        self.create_drive_frame(scrollable_frame)
        self.create_detector_frame(scrollable_frame)
        self.create_temperature_frame(scrollable_frame)
        self.create_geometry_frame(scrollable_frame)
        self.create_run_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

        self.create_readout_frame(right_panel)
        self.create_graph_frame(right_panel)

        self._set_controls_enabled(False)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        ttk.Button(
            header_frame, text="\U0001F4C8",
            command=launch_plotter_utility, width=3).pack(
            side='right', padx=10, pady=5)
        ttk.Button(
            header_frame, text="\U0001F4DF",
            command=launch_gpib_scanner, width=3).pack(
            side='right', padx=(0, 5), pady=5)

        Label(header_frame, text=PROGRAM_TITLE, bg=self.CLR_HEADER,
              fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(
            side='left', padx=20, pady=10)
        Label(header_frame, text="Version: %s" % self.PROGRAM_VERSION,
              bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
              font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
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

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame, text="UGC-DAE Consortium for Scientific Research",
            font=institute_font).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font).grid(
            row=1, column=1, padx=10, sticky='nw')
        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        ttk.Label(frame, text=INFO_TEXT, justify='left',
                  style='Sub.TLabel').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_connection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frame, text="Keithley 6221:").grid(
            row=row, column=0, padx=(10, 6), pady=(8, 3), sticky='w')
        self.source_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.source_cb.grid(row=row, column=1, padx=(0, 10), pady=(8, 3),
                            sticky='ew')
        self.source_cb.set(self.DEFAULT_SOURCE_ADDRESS)
        row += 1

        ttk.Label(frame, text="Keithley 197A:").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        self.detector_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.detector_cb.grid(row=row, column=1, padx=(0, 10), pady=3,
                              sticky='ew')
        self.detector_cb.set(self.DEFAULT_DETECTOR_ADDRESS)
        row += 1
        ttk.Label(frame, text="Cryocon 34:").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        self.thermometer_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.thermometer_cb.grid(row=row, column=1, padx=(0, 10), pady=3,
                                 sticky='ew')
        self.thermometer_cb.set(self.DEFAULT_THERMOMETER_ADDRESS)
        row += 1

        button_row = ttk.Frame(frame)
        button_row.grid(row=row, column=0, columnspan=2, padx=10, pady=(6, 6),
                        sticky='ew')
        button_row.columnconfigure((0, 1, 2), weight=1)
        ttk.Button(button_row, text="Scan",
                   command=self._scan_for_visa_instruments).grid(
            row=0, column=0, sticky='ew', padx=(0, 4))
        self.connect_button = ttk.Button(
            button_row, text="Connect", command=self.connect,
            style='Start.TButton')
        self.connect_button.grid(row=0, column=1, sticky='ew', padx=4)
        self.disconnect_button = ttk.Button(
            button_row, text="Disconnect", command=self.disconnect,
            style='Stop.TButton', state='disabled')
        self.disconnect_button.grid(row=0, column=2, sticky='ew', padx=(4, 0))
        row += 1

        self.idn_var = tk.StringVar(value="Not connected.")
        ttk.Label(frame, textvariable=self.idn_var, style='Sub.TLabel',
                  wraplength=470, justify='left').grid(
            row=row, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')

    def _add_entry(self, frame, row, key, label, default, hint=None):
        ttk.Label(frame, text=label + ":").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        entry = ttk.Entry(frame, font=self.FONT_BASE, width=16)
        entry.grid(row=row, column=1, padx=(0, 10), pady=3, sticky='ew')
        entry.insert(0, default)
        self.entries[key] = entry
        if hint:
            ttk.Label(frame, text=hint, style='Sub.TLabel').grid(
                row=row, column=2, padx=(0, 10), pady=3, sticky='w')
        return row + 1

    def create_drive_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Current Drive (Keithley 6221)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            frame, text=DRIVE_HINT, style='Sub.TLabel', wraplength=470,
            justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(6, 8), sticky='w')
        row += 1
        row = self._add_entry(frame, row, 'frequency', "Frequency (Hz)",
                              "133.0", "held for the whole run")
        row = self._add_entry(frame, row, 'current_rms', "Current (A rms)",
                              "1e-5", "sent as peak = rms x sqrt(2)")
        row = self._add_entry(frame, row, 'compliance', "Compliance (V)",
                              "10.0", "0.1 to 105 V")

    def create_detector_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Voltmeter (Keithley 197A)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            frame,
            text=("The 197A is put on AC volts (F1) at the start of a run. It "
                  "has no reference input, so R is a magnitude with no phase "
                  "and everything in the meter's passband is counted as "
                  "signal. Keep the drive inside %.4g to %.4g Hz."
                  % (K197A_ACV_BAND_MIN, K197A_ACV_BAND_MAX)),
            style='Sub.TLabel', wraplength=470, justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(6, 8), sticky='w')
        row += 1

        ttk.Label(frame, text="Range:").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        self.range_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=K197A_RANGE_NAMES)
        self.range_cb.grid(row=row, column=1, columnspan=2, padx=(0, 10),
                           pady=3, sticky='ew')
        self.range_cb.current(0)
        row += 1

        row = self._add_entry(frame, row, 'averages',
                              "Readings per point", "5",
                              "mean, and the spread is logged")
        row = self._add_entry(frame, row, 'read_interval',
                              "Gap between readings (s)", "0.4",
                              "floor %.2f s (3 readings/s)"
                              % K197A_MIN_READ_INTERVAL)
        row = self._add_entry(frame, row, 'settle_s',
                              "Settle after a setpoint (s)", "2.0",
                              "before the first reading")

    def create_temperature_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Temperature (read only)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            frame,
            text=("This module never writes to the %s. Drive the temperature "
                  "from the instrument's own front panel, from a PICA "
                  "temperature control module, or from the cryostat, and this "
                  "run simply records what the thermometer says."
                  % THERMOMETER_NAME),
            style='Sub.TLabel', wraplength=470, justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(6, 8), sticky='w')
        row += 1

        ttk.Label(frame, text="Input channel:").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        self.channel_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=THERMOMETER_CHANNELS)
        self.channel_cb.grid(row=row, column=1, columnspan=2, padx=(0, 10),
                             pady=3, sticky='ew')
        self.channel_cb.current(0)
        row += 1

        row = self._add_entry(frame, row, 'interval',
                              "Logging interval (s)", "10.0",
                              "measured from the end of a point")
        row = self._add_entry(frame, row, 'stop_low', "Stop below (K)", "1.0")
        row = self._add_entry(frame, row, 'stop_high', "Stop above (K)",
                              "400.0", "the run ends outside this window")

    def create_geometry_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Sample Geometry (optional)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frame, text="Geometry:").grid(
            row=row, column=0, padx=(10, 6), pady=(8, 3), sticky='w')
        self.geometry_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=GEOMETRY_LABELS)
        self.geometry_cb.grid(row=row, column=1, columnspan=2, padx=(0, 10),
                              pady=(8, 3), sticky='ew')
        self.geometry_cb.current(0)
        row += 1

        row = self._add_entry(frame, row, 'width', "Width (mm)", "1.0")
        row = self._add_entry(frame, row, 'thickness', "Thickness (um)",
                              "100.0")
        row = self._add_entry(frame, row, 'length',
                              "Probe separation (mm)", "1.0")
        ttk.Label(
            frame,
            text=("Length is the distance between the two VOLTAGE probes, "
                  "not the length of the sample."),
            style='Sub.TLabel', wraplength=470, justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(0, 10), sticky='w')

    def create_run_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Run')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        row = self._add_entry(frame, row, 'sample', "Sample name", "")
        row = self._add_entry(frame, row, 'operator', "Operator", "")

        ttk.Button(
            frame, text="Browse Save Location...",
            command=self._browse_file_location).grid(
            row=row, column=0, columnspan=3, padx=10, pady=(3, 6), sticky='ew')
        row += 1

        self.save_path_var = tk.StringVar(value="No save location chosen.")
        ttk.Label(frame, textvariable=self.save_path_var, style='Sub.TLabel',
                  wraplength=470, justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(0, 6), sticky='w')
        row += 1

        button_row = ttk.Frame(frame)
        button_row.grid(row=row, column=0, columnspan=3, padx=10, pady=(0, 10),
                        sticky='ew')
        button_row.columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(
            button_row, text="Start Measurement", command=self.start_run,
            style='Start.TButton', state='disabled')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.stop_button = ttk.Button(
            button_row, text="Stop and Current Off", command=self.stop_run,
            style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=(4, 0))

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output')
        frame.pack(pady=5, padx=10, fill='both', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', height=10, bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word',
            bd=0, relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Set the addresses and press Connect.")
        for line in CONSOLE_REMINDERS:
            self.log(line)
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found. Install it with "
                     "'pip install pyvisa'.")

    def create_readout_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Live Readout')
        frame.pack(side='top', fill='x', padx=5, pady=(5, 0))

        numbers = tk.Frame(frame, bg=self.CLR_GRAPH_BG)
        numbers.pack(fill='x', padx=8, pady=8)

        self.readout_vars = {}
        cells = [            ('resistance', 'R magnitude (Ohm)'),
            ('voltage', 'V rms (V)'),
            ('spread', 'V spread (V)'),
            ('freq', 'Drive f (Hz)'),
            ('temperature', 'Temperature (K)'),
        ]
        numbers.columnconfigure(tuple(range(len(cells))), weight=1)
        for index, (key, title) in enumerate(cells):
            cell = tk.Frame(numbers, bg=self.CLR_GRAPH_BG)
            cell.grid(row=0, column=index, sticky='ew', padx=6, pady=4)
            ttk.Label(cell, text=title, style='ReadoutName.TLabel').pack()
            var = tk.StringVar(value="--")
            ttk.Label(cell, textvariable=var, style='Readout.TLabel').pack()
            self.readout_vars[key] = var

        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(frame, textvariable=self.status_var, style='Sub.TLabel',
                  wraplength=900, justify='left').pack(
            fill='x', padx=10, pady=(0, 8))

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Plot')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(7, 6), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.ax_main = self.figure.add_subplot(2, 1, 1)
        self.ax_sub = self.figure.add_subplot(2, 1, 2)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4,
            linestyle='-')
        self.line_sub, = self.ax_sub.plot(
            [], [], color=self.CLR_FG_LIGHT, marker='.', markersize=4,
            linestyle='-')
        self.ax_main.set_title("Resistance vs. temperature",
                               fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("R (Ohm)")
        self.ax_sub.set_title("Temperature vs. time", fontweight='bold')
        self.ax_sub.set_xlabel("Elapsed time (s)")
        self.ax_sub.set_ylabel("Temperature (K)")
        for axis in (self.ax_main, self.ax_sub):
            axis.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout(pad=2.5)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ---------------------------------------------------------------- logging
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', "[%s] %s\n" % (timestamp, message))
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    # --------------------------------------------------------- worker plumbing
    def _process_action_queue(self):
        try:
            while True:
                kind, description, payload, callback = \
                    self.action_queue.get_nowait()
                if kind == 'connect_failed':
                    what, address = description
                    for line in diagnose_connection_failure(
                            what, address, payload):
                        self.log(line)
                    self.idn_var.set("Not connected.")
                    self.connect_button.config(state='normal')
                elif kind == 'log':
                    self.log(payload)
                elif kind == 'error':
                    self.log("ERROR (%s): %s" % (description, payload))
                else:
                    if description:
                        self.log(description)
                    if callback is not None:
                        callback(payload)
        except queue.Empty:
            pass
        self.root.after(150, self._process_action_queue)

    def _set_controls_enabled(self, connected):
        state = 'normal' if connected else 'disabled'
        self.start_button.config(state=state)
        self.connect_button.config(state='disabled' if connected else 'normal')
        self.disconnect_button.config(state=state)

    # ------------------------------------------------------------- connection
    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log("Found: %s" % (resources,))
                for combo in self._address_widgets():
                    combo['values'] = resources
            else:
                self.log("No VISA instruments found.")
        except Exception as exc:
            self.log("ERROR during VISA scan: %s" % exc)

    def _address_widgets(self):
        widgets = [self.source_cb, self.detector_cb]
        widgets.append(self.thermometer_cb)
        return widgets

    def connect(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed, cannot connect.")
            return
        addresses = [combo.get().strip() for combo in self._address_widgets()]
        if not all(addresses):
            self.log("ERROR: every VISA address box has to be filled in.")
            return
        if len(set(addresses)) != len(addresses):
            self.log("ERROR: two instruments were given the same address. "
                     "Each address belongs to exactly one instrument.")
            return

        self.connect_button.config(state='disabled')
        self.log("Connecting to %s ..." % ", ".join(addresses))
        threading.Thread(
            target=self._connect_worker, args=(addresses,), daemon=True).start()

    def _connect_worker(self, addresses):
        opened = []

        def fail(what, address, exc):
            for instrument in reversed(opened):
                try:
                    instrument.close()
                except Exception:
                    pass
            self.action_queue.put(
                ('connect_failed', (what, address), exc, None))

        try:
            with self.io_lock:
                source = K6221WaveSource(addresses[0])
            opened.append(source)
        except Exception as exc:
            fail("Keithley 6221", addresses[0], exc)
            return

        try:
            with self.io_lock:
                detector = Keithley197AMeter(addresses[1])
            opened.append(detector)
        except Exception as exc:
            fail("Keithley 197A", addresses[1], exc)
            return

        try:
            with self.io_lock:
                thermometer = Cryocon34Monitor(
                    addresses[2], self.channel_cb.get())
            opened.append(thermometer)
        except Exception as exc:
            fail("Cryocon 34", addresses[2], exc)
            return

        self.action_queue.put(
            ('ok', None, (source, detector, thermometer), self._on_connected))

    def _on_connected(self, payload):
        self.source, self.detector, self.thermometer = payload
        self.log("6221 connected.   *IDN? -> %s" % self.source.idn)
        self.log("Keithley 197A connected. -> %s"
                 % self.detector.idn)
        summary = ["%s  @  %s" % (self.source.idn, self.source.address),
                   "%s  @  %s" % (self.detector.idn, self.detector.address)]
        self.log("Cryocon 34 connected. -> %s"
                 % self.thermometer.idn)
        summary.append("%s  @  %s  (channel %s)"
                       % (self.thermometer.idn, self.thermometer.address,
                          self.thermometer.channel))
        self.idn_var.set("\n".join(summary))
        self._set_controls_enabled(True)

    def disconnect(self):
        if self.is_running:
            self.stop_run()
        for name in ('source', 'detector', 'thermometer'):
            instrument = getattr(self, name)
            if instrument is None:
                continue
            setattr(self, name, None)
            try:
                with self.io_lock:
                    instrument.close()
            except Exception as exc:
                self.log("Warning during disconnect (%s): %s" % (name, exc))
        self.idn_var.set("Not connected.")
        self._set_controls_enabled(False)
        self.log("Disconnected. The 6221 output was switched off.")

    # ---------------------------------------------------------------- reading
    def _read_parameters(self):
        """Everything the run needs, validated, or a ValueError with a reason."""
        def number(key, label):
            text = self.entries[key].get().strip()
            try:
                return float(text)
            except ValueError:
                raise ValueError("%s: '%s' is not a number." % (label, text))

        compliance = number('compliance', "Compliance")
        pmark_line = PMARK_LINE_DEFAULT
        if USE_PHASE_MARKER:
            pmark_line = int(number('pmark_line', "Phase marker line"))
            if not PMARK_LINE_MIN <= pmark_line <= PMARK_LINE_MAX:
                raise ValueError(
                    "The phase marker line must be between %d and %d."
                    % (PMARK_LINE_MIN, PMARK_LINE_MAX))
            if pmark_line == 2:
                raise ValueError(
                    "Trigger link line 2 is the 6221 trigger layer output "
                    "line. Using it for the phase marker returns -221 "
                    "Settings Conflict.")

        frequency = number('frequency', "Frequency")
        current_rms = number('current_rms', "Current")
        if current_rms <= 0:
            raise ValueError("The current must be greater than zero.")
        current_peak = peak_from_rms(current_rms)
        validate_drive(frequency, current_peak, compliance)
        drive_params = {
            'frequency': frequency,
            'current_peak': current_peak,
            'drive_description': "%.4f Hz, %.4E A rms, held for the whole run"
                                 % (frequency, current_rms),
        }

        averages = int(number('averages', "Readings per point"))
        if averages < 1:
            raise ValueError("At least one reading per point is needed.")
        read_interval = number('read_interval', "Gap between readings")
        if read_interval < K197A_MIN_READ_INTERVAL:
            raise ValueError(
                "The 197A answers at most 3 readings per second, so the gap "
                "cannot be shorter than %.2f s." % K197A_MIN_READ_INTERVAL)
        settle = number('settle_s', "Settle after a setpoint")
        if settle < 0:
            raise ValueError("The settle time cannot be negative.")
        detector_params = {
            'averages': averages,
            'read_interval': read_interval,
            'range_name': self.range_cb.get(),
        }

        interval = number('interval', "Logging interval")
        if interval < 0:
            raise ValueError("The logging interval cannot be negative.")
        stop_low = number('stop_low', "Stop below")
        stop_high = number('stop_high', "Stop above")
        if stop_low >= stop_high:
            raise ValueError(
                "The stop window is empty: 'stop below' must be less than "
                "'stop above'.")
        temperature_params = {
            'channel': self.channel_cb.get(),
            'interval': interval,
            'stop_low': stop_low,
            'stop_high': stop_high,
        }

        geometry_code = self.geometry_cb.current()
        if geometry_code == 0:
            # No resistivity is asked for, so the dimension boxes are not read
            # at all: a blank one must not stop a resistance-only run.
            geometry = {'code': 0, 'width': 0.0, 'thickness': 0.0,
                        'length': 0.0}
        else:
            geometry = {
                'code': geometry_code,
                # The boxes are in mm and um because that is what a sample is
                # measured in; everything below this line is metres.
                'width': number('width', "Width") * 1e-3,
                'thickness': number('thickness', "Thickness") * 1e-6,
                'length': number('length', "Probe separation") * 1e-3,
            }
            # Fail here rather than on the first point, with the source live.
            resistivity_from_resistance(
                1.0, geometry_code, geometry['width'], geometry['thickness'],
                geometry['length'])

        sample = self.entries['sample'].get().strip()
        if not sample:
            raise ValueError("A sample name is needed for the output file.")
        if not self.file_location_path:
            raise ValueError("Choose a save location first.")

        params = {
            'compliance': compliance,
            'pmark_line': pmark_line,
            'pmark_phase': PMARK_PHASE_DEFAULT,
            'settle': settle,
            'detector': detector_params,
            'geometry': geometry,
            'sample': sample,
            'operator': self.entries['operator'].get().strip(),
        }
        params.update(drive_params)
        params['temperature'] = temperature_params
        return params

    # -------------------------------------------------------------- the run
    def start_run(self):
        if self.source is None or self.detector is None:
            self.log("Not connected. Press Connect first.")
            return
        try:
            params = self._read_parameters()
        except ValueError as exc:
            self.log("Rejected: %s" % exc)
            messagebox.showwarning("Check the settings", str(exc))
            return
        if self.thermometer.channel != params['temperature']['channel']:
            message = ("The thermometer was opened on channel %s. Disconnect "
                       "and connect again to read channel %s."
                       % (self.thermometer.channel,
                          params['temperature']['channel']))
            self.log("Rejected: %s" % message)
            messagebox.showwarning("Check the settings", message)
            return

        self.params = params
        self.is_running = True
        self.stop_requested = False
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.disconnect_button.config(state='disabled')
        for values in self.data_storage.values():
            values.clear()
        self.line_main.set_data([], [])
        self.line_sub.set_data([], [])
        self.canvas.draw_idle()
        self.start_time = time.time()
        self.status_var.set("Configuring instruments...")
        self.log("Run started. %s" % params['drive_description'])
        self.log("Settling %.3f s at each setpoint." % params['settle'])

        threading.Thread(target=self._run_worker, daemon=True).start()
        self.root.after(100, self._process_data_queue)

    def stop_run(self):
        """Ask the worker to stop. The current goes off in the worker thread."""
        if not self.is_running:
            return
        self.stop_requested = True
        self.stop_button.config(state='disabled')
        self.log("Stop requested. Switching the 6221 output off.")

    def _open_log_file(self, detector_settings):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "%s_%s_%s.dat" % (
            self.params['sample'], stamp, FILE_SUFFIX)
        self.data_filepath = os.path.join(self.file_location_path, filename)
        with open(self.data_filepath, 'w') as handle:
            handle.write(build_log_header(
                self.params['sample'], self.params['operator'],
                self.source, self.detector, self.thermometer,
                detector_settings, self.params))
        return filename

    def _detector_settings(self):
        """What was SENT to the meter.

        The 197A has no state query on this interface, so unlike the lock-in
        modules this is the request and not a read-back. The file header says
        so in as many words.
        """
        return dict(self.params['detector'])

    def _measure_point(self, frequency, current_rms):
        """One averaged reading, converted to a resistance.

        There is no status byte on this meter, so the spread across the
        averaged readings is the only noise estimate there is; it goes into
        the file next to the mean rather than being thrown away.
        """
        params = self.params['detector']
        with self.io_lock:
            volts, spread, readings = self.detector.read_average(
                params['averages'], params['read_interval'])
        problems = meter_health(volts, spread, frequency, readings)
        resistance = volts / current_rms
        return {
            'resistance': resistance,
            'magnitude': resistance,
            'voltage': volts,
            'spread': spread,
            'count': len(readings),
            'problems': problems,
        }

    def _auto_functions(self, first_point):
        """The 197A has no auto gain or auto phase. Nothing is moved."""
        return False

    def _prepare_instruments(self):
        """Configure everything, open the file, and return its name."""
        params = self.params
        with self.io_lock:
            sent = self.detector.configure_acv(
                params['detector']['range_name'])
            self.data_queue.put(
                ('log', "197A configured with '%s' (AC volts)." % sent))
            self.source.prepare(
                params['compliance'], USE_PHASE_MARKER,
                params['pmark_line'], params['pmark_phase'])
            code, text = self.source.read_error()
            settings = self._detector_settings()
        if code:
            self.data_queue.put(
                ('log', "6221 reported error %d: %s" % (code, text)))
        return self._open_log_file(settings)

    def _apply_drive(self, frequency, current_peak):
        """Set the 6221 going, and refuse to carry on if it complained."""
        with self.io_lock:
            self.source.set_drive(frequency, current_peak)
            code, text = self.source.read_error()
        if code:
            raise RuntimeError(
                "The 6221 refused the setpoint: %d, %s" % (code, text))

    def _emit_point(self, point, frequency, current_peak, current_rms,
                    temperature):
        """Complete a raw detector reading and hand it to the GUI thread."""
        geometry = self.params['geometry']
        resistivity = resistivity_from_resistance(
            point['resistance'], geometry['code'], geometry['width'],
            geometry['thickness'], geometry['length'])
        point.update({
            'elapsed': time.time() - self.start_time,
            'frequency': frequency,
            'current_peak': current_peak,
            'current_rms': current_rms,
            'temperature': temperature,
            'resistivity': resistivity,
            'sheet': sheet_resistance(resistivity, geometry['thickness']),
        })
        self.data_queue.put(('point', point))

    def _run_worker(self):
        """Hold one drive setpoint and log R against whatever T is doing."""
        params = self.params
        temperature_params = params['temperature']
        frequency = params['frequency']
        current_peak = params['current_peak']
        current_rms = rms_from_peak(current_peak)
        first_point = True
        reason = "Stopped."
        try:
            self.data_queue.put(
                ('log', "Output file: %s" % self._prepare_instruments()))

            self._apply_drive(frequency, current_peak)
            self.data_queue.put((
                'status', "Drive on at %.4f Hz, %.4E A rms. Settling %.2f s."
                % (frequency, current_rms, params['settle'])))
            if not self._sleep_interruptibly(params['settle']):
                return
            if self._auto_functions(first_point):
                if not self._sleep_interruptibly(params['settle']):
                    return
            first_point = False

            while not self.stop_requested:
                with self.io_lock:
                    temperature = self.thermometer.read_temperature()
                point = self._measure_point(frequency, current_rms)
                self._emit_point(point, frequency, current_peak, current_rms,
                                 temperature)
                self.data_queue.put((
                    'status',
                    "Logging: T = %.3f K, R = %.5G Ohm. Next point in %.1f s."
                    % (temperature, point['resistance'],
                       temperature_params['interval'])))

                # The stop window is the only thing that ends a passive run by
                # itself: this module is not driving the temperature, so there
                # is no end setpoint to arrive at.
                if temperature <= temperature_params['stop_low']:
                    reason = ("Temperature fell to %.3f K, below the %.3f K "
                              "stop." % (temperature,
                                         temperature_params['stop_low']))
                    break
                if temperature >= temperature_params['stop_high']:
                    reason = ("Temperature rose to %.3f K, above the %.3f K "
                              "stop." % (temperature,
                                         temperature_params['stop_high']))
                    break
                if not self._sleep_interruptibly(
                        temperature_params['interval']):
                    break

            self.data_queue.put(('done', reason))
        except Exception as exc:
            self.data_queue.put(('failed', exc))
        finally:
            self._safe_shutdown()

    def _safe_shutdown(self):
        """The current comes off here, in the thread that put it on.

        Every exit path from the worker arrives here: finished, stopped, or
        thrown. The heater, where there is one, goes off with it.
        """
        try:
            with self.io_lock:
                if self.source is not None:
                    self.source.output_off()
        except Exception as exc:
            self.data_queue.put(
                ('log', "Warning: could not switch the 6221 output off: %s"
                 % exc))
        try:
            with self.io_lock:
                if self.thermometer is not None:
                    self.thermometer.shutdown()
        except Exception as exc:
            self.data_queue.put(
                ('log', "Warning during thermometry shutdown: %s" % exc))

    def _sleep_interruptibly(self, seconds):
        """Sleep in slices so Stop does not have to wait out a 10 s settle."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.stop_requested:
                return False
            time.sleep(min(0.1, max(0.0, deadline - time.time())))
        return not self.stop_requested

    def _process_data_queue(self):
        try:
            while True:
                kind, payload = self.data_queue.get_nowait()
                if kind == 'log':
                    self.log(payload)
                elif kind == 'status':
                    self.status_var.set(payload)
                elif kind == 'point':
                    self._record_point(payload)
                elif kind == 'failed':
                    self.log("RUNTIME ERROR: %s" % payload)
                    self.status_var.set("Stopped on an error. Current is off.")
                    self._finish_run()
                    return
                elif kind == 'done':
                    message = payload or "Finished."
                    self.status_var.set("%s The 6221 output is off." % message)
                    self.log("%s The 6221 output is off." % message)
                    self._finish_run()
                    return
        except queue.Empty:
            pass
        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _record_point(self, point):
        self.readout_vars['resistance'].set("%.5G" % point['resistance'])
        self.readout_vars['voltage'].set("%.4E" % point['voltage'])
        self.readout_vars['spread'].set("%.3E" % point['spread'])
        self.readout_vars['freq'].set("%.4f" % point['frequency'])
        self.readout_vars['temperature'].set(
            "%.3f" % point['temperature'])

        for problem in point['problems']:
            self.log("WARNING: %s" % problem)

        flags = "; ".join(point['problems']) if point['problems'] else "ok"
        if self.data_filepath:
            fields = [
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "%.3f" % point['elapsed'],
            ]
            fields.append("%.4f" % point['temperature'])
            fields.extend([
                "%.6f" % point['frequency'],
                "%.6E" % point['current_peak'],
                "%.6E" % point['current_rms'],
            ])
            fields.extend(detector_row_fields(point))
            fields.extend([
                "" if point['resistivity'] is None
                else "%.6E" % point['resistivity'],
                "" if point['sheet'] is None else "%.6E" % point['sheet'],
                flags.replace(',', ';'),
            ])
            with open(self.data_filepath, 'a') as handle:
                handle.write(",".join(fields) + "\n")

        self.data_storage['x'].append(point['temperature'])
        self.data_storage['y'].append(point['resistance'])
        self.data_storage['sub_x'].append(point['elapsed'])
        self.data_storage['sub_y'].append(point['temperature'])
        self.line_main.set_data(
            self.data_storage['x'], self.data_storage['y'])
        self.line_sub.set_data(
            self.data_storage['sub_x'], self.data_storage['sub_y'])
        for axis in (self.ax_main, self.ax_sub):
            axis.relim()
            axis.autoscale_view()
        self.canvas.draw_idle()

    def _finish_run(self):
        self.is_running = False
        self.stop_requested = False
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.disconnect_button.config(state='normal')

    # ------------------------------------------------------------------ misc
    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.save_path_var.set(path)
            self.log("Save location set to: %s" % path)

    def _on_closing(self):
        if self.is_running:
            if not messagebox.askyesno(
                    "Exit",
                    "A measurement is running. Stop it, switch the current "
                    "off and exit?"):
                return
            self.stop_run()
            # Give the worker a moment to reach its finally block and drop the
            # current before the VISA session is closed underneath it.
            for _ in range(50):
                if not self.is_running:
                    break
                self.root.update()
                time.sleep(0.1)
        self.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    ACResistanceCC34SensingGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
