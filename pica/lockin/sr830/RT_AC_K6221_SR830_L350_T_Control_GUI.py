"""
===============================================================================
 PROGRAM:      AC R-T (T Control) - Keithley 6221 + SR830 + Lakeshore 350
 PURPOSE:      AC resistance against temperature, with this module driving
               the Lakeshore 350 ramp. Four-probe R(T) at one fixed
               frequency and current amplitude.

               HOW THE RESISTANCE IS OBTAINED
               ------------------------------
               The 6221 is a true current source, so the current through the
               sample is known from the setting and not from a measurement.
               SOUR:WAVE:AMPL is a PEAK amplitude (6221 manual 7-7) while the
               SR830 reports the RMS amplitude of the fundamental it detects,
               so the two are brought to the same convention before they are
               divided:

                   I_rms = I_peak / sqrt(2)
                   R     = X / I_rms          <- the in-phase, resistive part

               X is the component in phase with the current. The magnitude
               sqrt(X^2+Y^2) is logged beside it because it also carries the
               out-of-phase part and the noise: the two agreeing to within a
               fraction of a percent is the evidence that the phasing is right
               and the contact is ohmic.

               THE TRIGGER LINK CABLE IS NOT OPTIONAL
               --------------------------------------
               A lock-in only measures at the frequency of its reference. The
               6221 does not put out a sine reference, but it does put out a
               1 us TTL phase-marker pulse on the trigger link, once per cycle
               (6221 manual 7-9). That pulse is the reference:

                   6221 TRIGGER LINK (line 3, through a trigger-link to BNC
                   adapter)  ->  SR830 REF IN
                   SR830: FMOD 0 (external), RSLP 1 (TTL rising edge), HARM 1

               Every point reads LIAS? and compares the SR830's FREQ? against
               the frequency programmed into the 6221, so an unplugged
               reference shows up as an error rather than as a plausible
               looking resistance.

               TEMPERATURE
               -----------
               This module DRIVES the temperature. It stabilises at the start
               temperature, arms SETP and RAMP on the Lakeshore 350, opens the
               heater, and logs all the way along the ramp. The heater is put
               back to off when the run ends, is stopped, or throws -- in the
               same thread that opened it, on every exit path. A safety cutoff
               beyond the end temperature stops the run if the ramp overshoots.
 REFERENCES:   SR830 manual ch.5 "Remote Programming"
                 https://www.thinksrs.com/downloads/pdfs/manuals/SR830m.pdf
               Model 6220/6221 Reference Manual, Section 7 "Wave Functions"
               Lake Shore Model 350 User Manual, ch.6 "Remote Operation"
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.0
===============================================================================
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import math
import os
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
MODULE_NAME = "RT_AC_K6221_SR830_L350_T_Control_GUI.py"
PROGRAM_TITLE = "AC R-T (T Control) - Keithley 6221 + SR830 + Lakeshore 350"
FILE_SUFFIX = "AC_RT_Control"

# One line for the file header, saying what the run actually was.
MEASUREMENT_DESCRIPTION = (
    "AC R(T) along a Lakeshore 350 ramp driven by this module")

# The hint above the drive boxes, and the axis labels the detector
# decides.
DRIVE_HINT = (
    "One frequency and one current amplitude, held for the whole "
    "ramp. Pick them with the AC I-V and frequency scan modules "
    "first.")
VOLTAGE_AXIS_LABEL = "X, in phase"
SUB_AXIS_KEY = "theta"
SUB_AXIS_LABEL = "Theta (deg)"

INFO_TEXT = (
    "Program: AC R-T, active temperature control\n"
    "Instruments: Keithley 6221 (AC current), SRS SR830\n"
    "  (voltage), Lakeshore 350\n"
    "R = X / I_rms, with I_rms = I_peak / sqrt(2)\n"
    "\n"
    "WIRING\n"
    "  6221 OUTPUT  -> sample current leads (I+, I-)\n"
    "  Voltage probes -> SR830 A and B (differential, A-B)\n"
    "  6221 TRIGGER LINK line 3 -> SR830 REF IN (TTL)\n"
    "The reference cable is not optional: without it the lock-in\n"
    "has nothing to lock to and every reading is meaningless.\n"
    "\n"
    "The heater goes back to off when the run ends, is stopped,\n"
    "or throws.")

CONSOLE_REMINDERS = [
    "Reminder: 6221 TRIGGER LINK line 3 -> SR830 REF IN. Without that "
    "cable the lock-in has no reference and every number is "
    "meaningless."
]


# -----------------------------------------------------------------------------
# --- SR830 LOCK-IN: ENUMERATED CODE TABLES ---
# The SR830 reports and accepts integer codes, not physical values. Index into
# each list with the code to get the value a human should read. A dropdown that
# reads "24" where it should read "300 ms" is how a setting gets logged wrong.
# Every table below is from the SR830 manual ch.5, DETAILED COMMAND LIST.
# -----------------------------------------------------------------------------

# An SR830 answers *IDN? with "Stanford_Research_Systems,SR830,s/n,ver".
SR830_IDN_MARKER = "SR830"


def is_sr830_idn(idn):
    """True if a *IDN? reply came from an SR830 lock-in amplifier."""
    return SR830_IDN_MARKER in str(idn).upper()


# ISRC: A i=0, A-B i=1, I (1 MOhm) i=2, I (100 MOhm) i=3. A four-probe voltage
# measurement uses A-B: the pair of voltage probes drives the differential
# input and the current return does not appear in the reading.
ISRC_LABELS = ["A", "A-B", "I (1 MOhm, 1e6 V/A)", "I (100 MOhm, 1e8 V/A)"]
ISRC_SHORT = ["A", "A-B", "I 1e6", "I 1e8"]

ICPL_LABELS = ["AC", "DC"]                       # ICPL: AC i=0, DC i=1
IGND_LABELS = ["Float", "Ground"]                # IGND: Float i=0, Ground i=1
ILIN_LABELS = ["No filters", "Line notch", "2x Line notch", "Both notches"]
SYNC_LABELS = ["Off", "On (below 200 Hz)"]

# SENS table, codes 0-26.
SENS_LABELS = [
    "2 nV/fA", "5 nV/fA", "10 nV/fA", "20 nV/fA", "50 nV/fA", "100 nV/fA",
    "200 nV/fA", "500 nV/fA", "1 uV/pA", "2 uV/pA", "5 uV/pA", "10 uV/pA",
    "20 uV/pA", "50 uV/pA", "100 uV/pA", "200 uV/pA", "500 uV/pA",
    "1 mV/nA", "2 mV/nA", "5 mV/nA", "10 mV/nA", "20 mV/nA", "50 mV/nA",
    "100 mV/nA", "200 mV/nA", "500 mV/nA", "1 V/uA",
]

# The same 27 codes as full scale volts, so a measured X can be compared with
# the range it was taken on and a point above 90% of full scale is flagged
# before it clips.
SENS_VOLTS = [
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3, 1.0,
]

# OFLT table, codes 0-19, and the same 20 codes in seconds. The settling wait
# before a point is read is computed from this, not guessed.
OFLT_LABELS = [
    "10 us", "30 us", "100 us", "300 us", "1 ms", "3 ms", "10 ms", "30 ms",
    "100 ms", "300 ms", "1 s", "3 s", "10 s", "30 s", "100 s", "300 s",
    "1 ks", "3 ks", "10 ks", "30 ks",
]
OFLT_SECONDS = [
    10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 3e-3, 10e-3, 30e-3,
    0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0,
    1e3, 3e3, 10e3, 30e3,
]

OFSL_LABELS = ["6 dB/oct", "12 dB/oct", "18 dB/oct", "24 dB/oct"]
OFSL_DB = [6, 12, 18, 24]

# How many time constants to wait for the output to settle, per filter slope.
# A steeper filter is a longer effective settling: SR830 manual ch.3, "the time
# required to reach the final value increases with the filter slope". Five time
# constants on a 6 dB/oct filter reaches ~99%; a 24 dB/oct filter needs roughly
# twice that for the same approach.
SETTLE_TIME_CONSTANTS = [5, 7, 9, 10]

RMOD_LABELS = ["High Reserve", "Normal", "Low Noise"]

# STATUS BYTE DEFINITIONS, LIA STATUS BYTE.
LIA_STATUS_BITS = [
    "INPUT/RESRV overload", "FILTR overload", "OUTPT overload",
    "Reference unlock", "Detection frequency range switched",
    "Time constant changed indirectly", "Data storage triggered", "unused",
]

# The three bits that invalidate a reading outright, and the bit that says the
# lock-in is not locked to the 6221 phase marker at all.
LIA_OVERLOAD_MASK = 0b0000_0111
LIA_UNLOCK_BIT = 3

HARM_MIN, HARM_MAX = 1, 19999
PHAS_MIN, PHAS_MAX = -360.0, 729.99

# The SR830 external reference input covers 0.001 Hz to 102 kHz, which is
# wider than the 6221 wave generator, so in this pairing the 6221 sets the
# usable top end and this limit is never the binding one. It is checked
# anyway, because a limit that is only true today is still worth stating.
DETECTOR_FREQ_MAX = 102000.0
DETECTOR_FREQ_LIMIT_NAME = "SR830 external reference"

# AGAN does nothing when the time constant is above 1 second (SR830 manual
# ch.5, AUTO FUNCTIONS). Sending it there is not an error, it just silently
# does not happen, so this module declines instead of pretending.
AGAN_MAX_OFLT_CODE = 10  # 1 s

# The 6221 phase marker is this pairing's reference cable, so the marker is
# switched on at every run.
USE_PHASE_MARKER = True


def settle_seconds(oflt_code, ofsl_code, extra_seconds=0.0):
    """How long to wait after changing the drive before the point is read."""
    return (OFLT_SECONDS[oflt_code] * SETTLE_TIME_CONSTANTS[ofsl_code]
            + max(0.0, extra_seconds))


def resistance_from_lockin(x_volts, y_volts, current_rms):
    """Return (R_in_phase, R_magnitude, theta_deg) in ohms and degrees.

    X is the component in phase with the reference, so X / I_rms is the
    resistive part. The magnitude also carries the quadrature part and the
    noise, so it is only ever equal to or larger than the in-phase value.
    """
    if current_rms <= 0:
        raise ValueError("The source current must be greater than zero.")
    magnitude = math.hypot(x_volts, y_volts)
    theta = math.degrees(math.atan2(y_volts, x_volts))
    return x_volts / current_rms, magnitude / current_rms, theta


def lockin_health(lia_status, programmed_hz, measured_hz, x_volts, sens_code,
                  frequency_tolerance=0.01, headroom=0.9):
    """Everything that makes a reading untrustworthy, as a list of strings.

    An empty list means the point is good. This is the check that catches a
    trigger link cable that was never plugged in: the SR830 free-runs, LIAS
    reports Reference unlock, and FREQ? disagrees with what the 6221 was told
    to generate.
    """
    problems = []
    if lia_status & LIA_OVERLOAD_MASK:
        problems.extend(
            name for bit, name in enumerate(LIA_STATUS_BITS)
            if bit < 3 and lia_status & (1 << bit))
    if lia_status & (1 << LIA_UNLOCK_BIT):
        problems.append(
            "Reference unlock: the SR830 is not locked to the 6221 phase "
            "marker. Check the trigger link cable into REF IN.")
    if programmed_hz > 0 and measured_hz is not None:
        error = abs(measured_hz - programmed_hz) / programmed_hz
        if error > frequency_tolerance:
            problems.append(
                "Reference frequency mismatch: 6221 set to %.4f Hz, SR830 "
                "sees %.4f Hz." % (programmed_hz, measured_hz))
    full_scale = SENS_VOLTS[sens_code]
    if abs(x_volts) > headroom * full_scale:
        problems.append(
            "X is %.0f%% of the %s full scale: the sensitivity is too low."
            % (100.0 * abs(x_volts) / full_scale, SENS_LABELS[sens_code]))
    return problems


class SR830Lockin:
    """The SR830 side: reference configuration and the voltage reading."""

    def __init__(self, visa_address):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed.")
        self.address = visa_address
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 10000

        # SR830 manual ch.5, SETUP COMMANDS: the SR830 sends responses to only
        # ONE interface. OUTX 1 selects GPIB, OUTX 0 selects RS232. This has to
        # go out before any query or queries time out on a good connection.
        self.instrument.write('OUTX 1')

        self.idn = self.instrument.query('*IDN?').strip()
        if not is_sr830_idn(self.idn):
            try:
                self.instrument.close()
            finally:
                self.instrument = None
            raise ConnectionError(
                "%s is not an SR830: it identifies itself as '%s'. Refusing "
                "to send lock-in commands. Scan the bus and use the SR830's "
                "actual address." % (visa_address, self.idn))

    def configure_for_external_reference(self, harmonic, phase, isrc, icpl,
                                         ignd, ilin, sync, sens, oflt, ofsl,
                                         rmod):
        """Put the lock-in on the 6221 phase marker and set the front end.

        FMOD 0 selects the external reference and RSLP 1 the TTL rising edge,
        which is what the 1 us phase marker pulse presents (6221 manual 7-9).
        Anything else here and the lock-in is measuring at its own frequency
        while the sample is driven at the 6221's.
        """
        if not HARM_MIN <= harmonic <= HARM_MAX:
            raise ValueError(
                "Harmonic must be between %d and %d." % (HARM_MIN, HARM_MAX))
        if not PHAS_MIN <= phase <= PHAS_MAX:
            raise ValueError(
                "Phase must be between %g and %g degrees."
                % (PHAS_MIN, PHAS_MAX))
        for command in (
                'FMOD 0',            # external reference
                'RSLP 1',            # TTL rising edge
                'HARM %d' % harmonic,
                'PHAS %.2f' % phase,
                'ISRC %d' % isrc,
                'ICPL %d' % icpl,
                'IGND %d' % ignd,
                'ILIN %d' % ilin,
                'SYNC %d' % sync,
                'RMOD %d' % rmod,
                'OFSL %d' % ofsl,
                'OFLT %d' % oflt,
                'SENS %d' % sens):
            self.instrument.write(command)

    def read_settings(self):
        """Read back the state that goes into the file header."""
        keys = [("fmod", "FMOD?"), ("harm", "HARM?"), ("phas", "PHAS?"),
                ("rslp", "RSLP?"), ("isrc", "ISRC?"), ("icpl", "ICPL?"),
                ("ignd", "IGND?"), ("ilin", "ILIN?"), ("sync", "SYNC?"),
                ("sens", "SENS?"), ("oflt", "OFLT?"), ("ofsl", "OFSL?"),
                ("rmod", "RMOD?")]
        settings = {}
        for key, command in keys:
            reply = self.instrument.query(command).strip()
            settings[key] = float(reply) if key == "phas" else int(float(reply))
        return settings

    def read_reference_frequency(self):
        """FREQ? in external mode returns the frequency the SR830 locked to.

        That makes it an independent witness of the 6221's output: it is read
        over GPIB from the lock-in, but it came in over the trigger link.
        """
        return float(self.instrument.query('FREQ?').strip())

    def snap(self):
        """One SNAP? query for X, Y, R and Theta.

        SR830 manual ch.5, DATA TRANSFER COMMANDS: SNAP? records the requested
        parameters at a single instant. Four separate OUTP? calls would return
        four numbers taken at four different moments, which is exactly the
        error a resistance built out of X and Y must not contain.
        """
        reply = self.instrument.query('SNAP? 1,2,3,4').strip()
        parts = [float(value) for value in reply.split(',')]
        return parts[0], parts[1], parts[2], parts[3]

    def read_lia_status(self):
        """LIAS? clears on read, so one caller only, once per point."""
        return int(float(self.instrument.query('LIAS?').strip()))

    def read_sensitivity(self):
        return int(float(self.instrument.query('SENS?').strip()))

    def auto_gain(self):
        """AGAN. Does nothing above a 1 s time constant, so the caller checks."""
        self.instrument.write('AGAN')

    def auto_phase(self):
        """APHS: zero the phase against whatever the reference edge is now."""
        self.instrument.write('APHS')

    def close(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the SR830: %s" % exc)
            finally:
                self.instrument = None


DETECTOR_COLUMNS = ("Frequency locked (Hz),X (V),Y (V),R lockin (V),"
                    "Theta (deg),R in-phase (Ohm),R magnitude (Ohm)")


def detector_row_fields(point):
    """The detector's own columns for one row of the data file."""
    return [
        "%.6f" % point['locked_hz'],
        "%.6E" % point['x'],
        "%.6E" % point['y'],
        "%.6E" % point['r_volts'],
        "%.4f" % point['theta'],
        "%.6E" % point['resistance'],
        "%.6E" % point['magnitude'],
    ]


def detector_header_lines(idn, address, settings):
    """The '#' header lines describing the lock-in, read from the lock-in."""
    ilin = settings["ilin"]
    return [
        "# Lock-in: %s" % idn,
        "# Lock-in VISA address: %s" % address,
        "# Reference: external, TTL rising edge, harmonic %d" % settings["harm"],
        "# Phase offset (deg): %.2f" % settings["phas"],
        "# Input: %s, coupling %s, shield %s"
        % (ISRC_SHORT[settings["isrc"]],
           ICPL_LABELS[settings["icpl"]],
           IGND_LABELS[settings["ignd"]].lower()),
        "# Filters: line %s, 2x line %s, sync %s"
        % ("on" if ilin & 1 else "off",
           "on" if ilin & 2 else "off",
           "on" if settings["sync"] else "off"),
        "# Sensitivity: %s" % SENS_LABELS[settings["sens"]],
        "# Time constant: %s, slope %d dB/oct, reserve %s"
        % (OFLT_LABELS[settings["oflt"]], OFSL_DB[settings["ofsl"]],
           RMOD_LABELS[settings["rmod"]]),
        "# Resistance convention: R = X / I_rms, I_rms = I_peak / sqrt(2)",
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

# -----------------------------------------------------------------------------
# --- LAKESHORE MODEL 350 THERMOMETRY ---
# A Lakeshore answers *IDN? with "LSCI,MODEL350,s/n,ver". The temperature is
# the axis this whole run is indexed by, so reading it off the wrong
# instrument is worse than not running at all: the address is checked by
# identity, never assumed.
#
# The Lakeshore 350 now sits at GPIB1::12 -- the Cryo-con's own factory
# address -- so an address that "looks like the Lakeshore's" proves nothing.
# -----------------------------------------------------------------------------

LAKESHORE_IDN_MARKERS = ("LSCI", "LAKESHORE", "LAKE SHORE")
LAKESHORE_INPUT_CHANNELS = ["A", "B", "C", "D"]


def is_lakeshore_idn(idn):
    """True if a *IDN? reply came from a Lakeshore temperature instrument."""
    return any(marker in str(idn).upper() for marker in LAKESHORE_IDN_MARKERS)

# RANGE <output>,<code>: 0 off, 1 low, 2 medium, 3 high on outputs 3 and 4;
# on the 25 ohm heater outputs 1 and 2 the codes run 0 off, 1..5. The map
# below is the one the other PICA R-T modules use for output 1.
HEATER_RANGE_LABELS = ["Off", "Low", "Medium", "High"]
HEATER_RANGE_CODES = {"Off": 0, "Low": 2, "Medium": 4, "High": 5}

# RAMP <output>,<on/off>,<K per minute>. The 350 accepts 0.1 to 100 K/min.
RAMP_RATE_MIN, RAMP_RATE_MAX = 0.001, 100.0


class Lakeshore350Controller:
    """Set the temperature on a Lakeshore 350 and read it back.

    This class writes to the instrument, so it owns the undoing: shutdown()
    puts the heater range back to off on every exit path, including the ones
    that arrive by way of an exception.
    """

    def __init__(self, visa_address, channel="A", output=1):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed.")
        if channel not in LAKESHORE_INPUT_CHANNELS:
            raise ValueError("Unknown Lakeshore input channel %r." % (channel,))
        self.address = visa_address
        self.channel = channel
        self.output = int(output)
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 10000

        self.idn = self.instrument.query('*IDN?').strip()
        if not is_lakeshore_idn(self.idn):
            try:
                self.instrument.close()
            finally:
                self.instrument = None
            raise ConnectionError(
                "%s is not a Lakeshore: it identifies itself as '%s'. "
                "Refusing to send heater commands. Scan the bus and use the "
                "Lakeshore's actual address." % (visa_address, self.idn))

    def prepare(self):
        """HTRSET for the 25 ohm, 1 A heater, with the heater left off.

        The heater is deliberately not switched on here. It comes on in the
        stabilise step, once the run knows which side of the start
        temperature the sample is on.
        """
        self.instrument.write('HTRSET %d,1,2,0,1' % self.output)
        self.set_heater_range("Off")

    def read_temperature(self):
        """KRDG? <channel> -> Kelvin."""
        return float(
            self.instrument.query('KRDG? %s' % self.channel).strip())

    def set_heater_range(self, label):
        code = HEATER_RANGE_CODES.get(str(label).title())
        if code is None:
            raise ValueError("Unknown heater range %r." % (label,))
        self.instrument.write('RANGE %d,%d' % (self.output, code))

    def set_setpoint(self, temperature_k):
        self.instrument.write('SETP %d,%.4f' % (self.output, temperature_k))

    def start_ramp(self, end_temp_k, rate_k_per_min, heater_range):
        """Setpoint first, then the ramp, then the heater.

        RAMP is armed before the heater range is opened so that the 350 never
        sees a live heater with the old setpoint still in it.
        """
        self.set_setpoint(end_temp_k)
        self.instrument.write(
            'RAMP %d,1,%.4f' % (self.output, abs(rate_k_per_min)))
        self.set_heater_range(heater_range)

    def stop_ramp(self):
        self.instrument.write('RAMP %d,0,0' % self.output)

    def shutdown(self):
        """Heater off and ramping disarmed. Safe to call more than once."""
        if self.instrument is None:
            return
        for command in ('RAMP %d,0,0' % self.output,
                        'RANGE %d,0' % self.output):
            try:
                self.instrument.write(command)
            except Exception as exc:
                print("Warning: '%s' failed on the Lakeshore: %s"
                      % (command, exc))

    def close(self):
        if self.instrument is not None:
            try:
                self.shutdown()
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the Lakeshore: %s" % exc)
            finally:
                self.instrument = None


def thermometer_header_lines(thermometer, params):
    return [
        "# Temperature controller: %s" % thermometer.idn,
        "# Controller VISA address: %s" % thermometer.address,
        "# Lakeshore input channel: %s (KRDG?, Kelvin), output %d"
        % (thermometer.channel, thermometer.output),
        "# Ramp: %.3f K to %.3f K at %.4f K/min, heater range %s"
        % (params['start_temp'], params['end_temp'], params['rate'],
           params['heater_range']),
        "# Safety cutoff (K): %.3f" % params['cutoff'],
        "# Stabilisation window (K): %.3f" % params['stabilise_window'],
        "# Logging interval (s): %.2f" % params['interval'],
    ]

THERMOMETER_NAME = "Lakeshore 350"
THERMOMETER_CHANNELS = LAKESHORE_INPUT_CHANNELS

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

class ACResistanceTControlGUI:
    PROGRAM_VERSION = PROGRAM_VERSION
    MODULE_NAME = MODULE_NAME
    DEFAULT_SOURCE_ADDRESS = "GPIB0::13::INSTR"
    DEFAULT_DETECTOR_ADDRESS = "GPIB0::8::INSTR"
    DEFAULT_THERMOMETER_ADDRESS = "GPIB1::12::INSTR"
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
    # Lock-in front end settings, in the order they are laid out.
    LOCKIN_ENUM_ROWS = [
        ("isrc", "Input configuration", ISRC_LABELS),
        ("icpl", "Input coupling", ICPL_LABELS),
        ("ignd", "Input shield", IGND_LABELS),
        ("ilin", "Notch filters", ILIN_LABELS),
        ("sync", "Synchronous filter", SYNC_LABELS),
        ("sens", "Sensitivity", SENS_LABELS),
        ("oflt", "Time constant", OFLT_LABELS),
        ("ofsl", "Filter slope", OFSL_LABELS),
        ("rmod", "Dynamic reserve", RMOD_LABELS),
    ]

    # A four-probe measurement on a differential input, AC coupled, floating
    # shield, both line notches in, 300 ms and 24 dB/oct. Conservative and
    # slow rather than fast and clipped.
    LOCKIN_DEFAULTS = {
        "isrc": 1, "icpl": 0, "ignd": 0, "ilin": 3, "sync": 1,
        "sens": 20, "oflt": 9, "ofsl": 3, "rmod": 1,
    }

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

        ttk.Label(frame, text="SR830 lock-in:").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        self.detector_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.detector_cb.grid(row=row, column=1, padx=(0, 10), pady=3,
                              sticky='ew')
        self.detector_cb.set(self.DEFAULT_DETECTOR_ADDRESS)
        row += 1
        ttk.Label(frame, text="Lakeshore 350:").grid(
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
        row = self._add_entry(frame, row, 'pmark_line',
                              "Phase marker line", str(PMARK_LINE_DEFAULT),
                              "trigger link 1-6, not 2")
    def create_detector_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Lock-in Front End (SR830)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            frame,
            text=("Written to the SR830 at the start of a run, together with "
                  "FMOD 0 (external reference) and RSLP 1 (TTL rising edge)."),
            style='Sub.TLabel', wraplength=470, justify='left').grid(
            row=row, column=0, columnspan=3, padx=10, pady=(6, 8), sticky='w')
        row += 1

        for key, label, labels in self.LOCKIN_ENUM_ROWS:
            ttk.Label(frame, text=label + ":").grid(
                row=row, column=0, padx=(10, 6), pady=3, sticky='w')
            combo = ttk.Combobox(
                frame, font=self.FONT_BASE, state='readonly', values=labels)
            combo.grid(row=row, column=1, columnspan=2, padx=(0, 10), pady=3,
                       sticky='ew')
            combo.current(self.LOCKIN_DEFAULTS[key])
            self.enum_widgets[key] = combo
            row += 1

        row = self._add_entry(frame, row, 'harmonic', "Harmonic (n)", "1")
        row = self._add_entry(frame, row, 'phase', "Phase offset (deg)", "0.00")
        row = self._add_entry(frame, row, 'extra_settle',
                              "Extra settle (s)", "0.0",
                              "on top of N x time constant")

        self.auto_gain_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Auto Gain (AGAN) at each setpoint",
            variable=self.auto_gain_var).grid(
            row=row, column=0, columnspan=3, padx=10, pady=(4, 0), sticky='w')
        row += 1
        self.auto_phase_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Auto Phase (APHS) once at the first setpoint",
            variable=self.auto_phase_var).grid(
            row=row, column=0, columnspan=3, padx=10, pady=(0, 10), sticky='w')

    def create_temperature_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Temperature Ramp (Lakeshore 350)')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(
            frame,
            text=("The run stabilises at the start temperature, then ramps to "
                  "the end temperature while the AC resistance is logged. The "
                  "heater is put back to off when the run ends, stops or "
                  "throws."),
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

        ttk.Label(frame, text="Heater range:").grid(
            row=row, column=0, padx=(10, 6), pady=3, sticky='w')
        self.heater_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=HEATER_RANGE_LABELS)
        self.heater_cb.grid(row=row, column=1, columnspan=2, padx=(0, 10),
                            pady=3, sticky='ew')
        self.heater_cb.current(3)
        row += 1

        row = self._add_entry(frame, row, 'start_temp', "Start temp (K)",
                              "300.0")
        row = self._add_entry(frame, row, 'end_temp', "End temp (K)", "100.0")
        row = self._add_entry(frame, row, 'rate', "Ramp rate (K/min)", "1.0",
                              "magnitude; direction is start -> end")
        # The default is a cooling ramp, 300 K down to 100 K, so the cutoff
        # sits BELOW the end temperature: it has to be beyond the end in the
        # direction of travel or the run trips before it has finished.
        row = self._add_entry(frame, row, 'cutoff', "Safety cutoff (K)",
                              "90.0", "beyond the end, in the ramp direction")
        row = self._add_entry(frame, row, 'stabilise_window',
                              "Stabilise window (K)", "2.0")
        row = self._add_entry(frame, row, 'interval',
                              "Logging interval (s)", "10.0")

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
        cells = [            ('resistance', 'R in-phase (Ohm)'),
            ('voltage', 'X (V)'),
            ('theta', 'Theta (deg)'),
            ('freq', 'Locked f (Hz)'),
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
                detector = SR830Lockin(addresses[1])
            opened.append(detector)
        except Exception as exc:
            fail("SR830", addresses[1], exc)
            return

        try:
            with self.io_lock:
                thermometer = Lakeshore350Controller(
                    addresses[2], self.channel_cb.get())
            opened.append(thermometer)
        except Exception as exc:
            fail("Lakeshore 350", addresses[2], exc)
            return

        self.action_queue.put(
            ('ok', None, (source, detector, thermometer), self._on_connected))

    def _on_connected(self, payload):
        self.source, self.detector, self.thermometer = payload
        self.log("6221 connected.   *IDN? -> %s" % self.source.idn)
        self.log("SR830 connected. -> %s"
                 % self.detector.idn)
        summary = ["%s  @  %s" % (self.source.idn, self.source.address),
                   "%s  @  %s" % (self.detector.idn, self.detector.address)]
        self.log("Lakeshore 350 connected. -> %s"
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

        harmonic = int(number('harmonic', "Harmonic"))
        phase = number('phase', "Phase offset")
        if not HARM_MIN <= harmonic <= HARM_MAX:
            raise ValueError(
                "Harmonic must be between %d and %d." % (HARM_MIN, HARM_MAX))
        if not PHAS_MIN <= phase <= PHAS_MAX:
            raise ValueError(
                "Phase must be between %g and %g degrees."
                % (PHAS_MIN, PHAS_MAX))
        detector_codes = {key: combo.current()
                          for key, combo in self.enum_widgets.items()}
        extra_settle = number('extra_settle', "Extra settle")
        if extra_settle < 0:
            raise ValueError("The extra settle time cannot be negative.")
        settle = settle_seconds(
            detector_codes['oflt'], detector_codes['ofsl'], extra_settle)
        detector_params = {
            'harmonic': harmonic,
            'phase': phase,
            'codes': detector_codes,
            'auto_gain': self.auto_gain_var.get(),
            'auto_phase': self.auto_phase_var.get(),
        }

        start_temp = number('start_temp', "Start temp")
        end_temp = number('end_temp', "End temp")
        rate = abs(number('rate', "Ramp rate"))
        cutoff = number('cutoff', "Safety cutoff")
        stabilise_window = number('stabilise_window', "Stabilise window")
        interval = number('interval', "Logging interval")
        if not RAMP_RATE_MIN <= rate <= RAMP_RATE_MAX:
            raise ValueError(
                "The ramp rate must be between %g and %g K/min."
                % (RAMP_RATE_MIN, RAMP_RATE_MAX))
        if start_temp == end_temp:
            raise ValueError(
                "The start and end temperatures are the same: there is "
                "nothing to ramp.")
        if stabilise_window <= 0:
            raise ValueError("The stabilise window must be positive.")
        if interval < 0:
            raise ValueError("The logging interval cannot be negative.")
        # The cutoff has to be beyond the end temperature in the direction of
        # travel, or the run trips before it has finished.
        if end_temp > start_temp and cutoff <= end_temp:
            raise ValueError(
                "Heating from %g K to %g K: the safety cutoff must be above "
                "the end temperature." % (start_temp, end_temp))
        if end_temp < start_temp and cutoff >= end_temp:
            raise ValueError(
                "Cooling from %g K to %g K: the safety cutoff must be below "
                "the end temperature." % (start_temp, end_temp))
        temperature_params = {
            'channel': self.channel_cb.get(),
            'heater_range': self.heater_cb.get(),
            'start_temp': start_temp,
            'end_temp': end_temp,
            'rate': rate,
            'cutoff': cutoff,
            'stabilise_window': stabilise_window,
            'interval': interval,
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
        if params['detector']['auto_gain'] and \
                params['detector']['codes']['oflt'] > AGAN_MAX_OFLT_CODE:
            self.log(
                "Auto Gain is switched off for this run: AGAN does nothing "
                "above a 1 s time constant (SR830 manual, AUTO FUNCTIONS).")
            params['detector']['auto_gain'] = False
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
        """The lock-in's own state, read back from the lock-in itself."""
        return self.detector.read_settings()

    def _measure_point(self, frequency, current_rms):
        """One reading, converted to a resistance. Called from the worker only.

        The auto functions move the output, so anything that calls them has to
        settle again before it reads: that is the caller's job, not this one's.
        """
        with self.io_lock:
            locked_hz = self.detector.read_reference_frequency()
            x_volts, y_volts, r_volts, theta = self.detector.snap()
            lia = self.detector.read_lia_status()
            sens_code = self.detector.read_sensitivity()
        problems = lockin_health(
            lia, frequency, locked_hz, x_volts, sens_code)
        resistance, magnitude, _theta_check = resistance_from_lockin(
            x_volts, y_volts, current_rms)
        return {
            'resistance': resistance,
            'magnitude': magnitude,
            'voltage': abs(x_volts),
            'locked_hz': locked_hz,
            'x': x_volts, 'y': y_volts, 'r_volts': r_volts, 'theta': theta,
            'problems': problems,
        }

    def _auto_functions(self, first_point):
        """AGAN / APHS if they were asked for. True if the output was moved."""
        params = self.params['detector']
        moved = False
        with self.io_lock:
            if params['auto_phase'] and first_point:
                self.detector.auto_phase()
                moved = True
            if params['auto_gain']:
                self.detector.auto_gain()
                moved = True
        return moved

    def _prepare_instruments(self):
        """Configure everything, open the file, and return its name."""
        params = self.params
        with self.io_lock:
            codes = params['detector']['codes']
            self.detector.configure_for_external_reference(
                params['detector']['harmonic'], params['detector']['phase'],
                codes['isrc'], codes['icpl'], codes['ignd'], codes['ilin'],
                codes['sync'], codes['sens'], codes['oflt'], codes['ofsl'],
                codes['rmod'])
            self.thermometer.prepare()
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
        """Stabilise, ramp, and log R against T all the way along the ramp."""
        params = self.params
        control = params['temperature']
        frequency = params['frequency']
        current_peak = params['current_peak']
        current_rms = rms_from_peak(current_peak)
        heating = control['end_temp'] > control['start_temp']
        first_point = True
        reason = "Stopped."
        try:
            self.data_queue.put(
                ('log', "Output file: %s" % self._prepare_instruments()))

            # --- 1. Stabilise at the start temperature -----------------------
            # The heater is only opened once the sample is at or below the
            # start temperature. Above it, the run waits for the cryostat to
            # bring it down: pushing the heater at a sample that is already
            # too hot only makes the wait longer.
            self.data_queue.put((
                'status', "Stabilising at %.3f K." % control['start_temp']))
            while not self.stop_requested:
                with self.io_lock:
                    temperature = self.thermometer.read_temperature()
                error = temperature - control['start_temp']
                if abs(error) <= control['stabilise_window']:
                    self.data_queue.put((
                        'log', "Stabilised at %.3f K (window %.3f K)."
                        % (temperature, control['stabilise_window'])))
                    break
                with self.io_lock:
                    if error > control['stabilise_window']:
                        self.thermometer.set_heater_range("Off")
                    else:
                        self.thermometer.set_setpoint(control['start_temp'])
                        self.thermometer.set_heater_range(
                            control['heater_range'])
                self.data_queue.put((
                    'status',
                    "Stabilising: T = %.3f K, target %.3f K (%s)."
                    % (temperature, control['start_temp'],
                       "cooling, heater off" if error > 0 else "heating")))
                if not self._sleep_interruptibly(
                        max(2.0, control['interval'])):
                    break
            if self.stop_requested:
                return

            # --- 2. Drive on, and settle before the ramp starts --------------
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

            # --- 3. Ramp, logging as it goes --------------------------------
            with self.io_lock:
                self.thermometer.start_ramp(
                    control['end_temp'], control['rate'],
                    control['heater_range'])
            self.data_queue.put((
                'log', "Ramp armed: %.3f K at %.4f K/min, heater %s."
                % (control['end_temp'], control['rate'],
                   control['heater_range'])))
            self.start_time = time.time()

            while not self.stop_requested:
                with self.io_lock:
                    temperature = self.thermometer.read_temperature()
                point = self._measure_point(frequency, current_rms)
                self._emit_point(point, frequency, current_peak, current_rms,
                                 temperature)
                self.data_queue.put((
                    'status',
                    "Ramping to %.3f K: T = %.3f K, R = %.5G Ohm."
                    % (control['end_temp'], temperature,
                       point['resistance'])))

                # The cutoff is checked before the end temperature, because a
                # run that has passed its cutoff is a run whose heater is to
                # be shut regardless of how close to the end it looks.
                if ((heating and temperature >= control['cutoff'])
                        or (not heating and temperature <= control['cutoff'])):
                    reason = ("Safety cutoff reached at %.3f K."
                              % temperature)
                    break
                if ((heating and temperature >= control['end_temp'])
                        or (not heating
                            and temperature <= control['end_temp'])):
                    reason = "End temperature reached at %.3f K." % temperature
                    break
                if not self._sleep_interruptibly(control['interval']):
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
        self.readout_vars['voltage'].set("%.4E" % point['x'])
        self.readout_vars['theta'].set("%.3f" % point['theta'])
        self.readout_vars['freq'].set("%.4f" % point['locked_hz'])
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
    ACResistanceTControlGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
