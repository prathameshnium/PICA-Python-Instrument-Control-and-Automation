"""
===============================================================================
 PROGRAM:      PICA AC Resistivity - Keithley 6221 + SR830
 PURPOSE:      Four-probe AC resistance and resistivity. The Keithley 6221
               sources a sine current into the sample and the SR830 lock-in
               measures the voltage it develops across the voltage probes.

               HOW THE RESISTANCE IS OBTAINED
               ------------------------------
               The 6221 is a true current source, so the current through the
               sample is known from the setting, not from a measurement:

                   SOUR:WAVE:AMPL is a PEAK amplitude (6221 manual 7-5 and
                   note 3 on page 7-29: "sets the peak amplitude ... the peak
                   amplitude is one-half the peak-to-peak value"), while the
                   SR830 reports the RMS amplitude of the fundamental it
                   detects. The two must be brought to the same convention
                   before they are divided:

                       I_rms = I_peak / sqrt(2)

                   R_in-phase = X / I_rms      <- the resistive part
                   R_magnitude = sqrt(X^2+Y^2) / I_rms

               X is the component in phase with the current, so X / I_rms is
               the resistance. The magnitude sqrt(X^2+Y^2) is quoted next to
               it because it also contains the out-of-phase (cable and sample
               capacitance, thermal EMF pickup at the wrong phase) part and
               any noise: X and the magnitude agreeing to within a fraction of
               a percent is the evidence that the phasing is right and the
               contact is ohmic. Theta should sit near zero after Auto Phase.

               REFERENCE: WHY THE TRIGGER LINK CABLE MATTERS
               ---------------------------------------------
               A lock-in only measures at the frequency of its reference. The
               6221 does not put out a sine reference, but it does put out a
               1 us TTL phase-marker pulse on the trigger link, once per
               cycle, at a programmable phase (6221 manual 7-9 "Phase marker"
               and note 7 on page 7-29). That pulse is the reference:

                   6221 TRIGGER LINK (default line 3, via a trigger-link to
                   BNC adapter)  ->  SR830 REF IN

                   SR830: FMOD 0 (external reference)
                          RSLP 1 (TTL rising edge)
                          HARM 1

               Without that cable the SR830 has nothing to lock to, LIAS bit 3
               "Reference unlock" comes up, and every number the lock-in
               returns is meaningless. This module reads LIAS? and compares
               the SR830's measured FREQ? against the frequency programmed
               into the 6221 on every point, so an unplugged reference shows
               up as an error rather than as a plausible looking resistance.

               CAN THE SR830 DO IT ALONE?
               --------------------------
               Yes, but not the way this module does it. The SR830 has a sine
               OUTPUT (SLVL, 4 mV to 5 Vrms) and no current source, so the
               standard trick is a ballast resistor R_b in series with the
               sample, R_b >> R_sample:

                   I_rms = SLVL / R_b            (approximately)
                   R_sample = X / I_rms

               That works, and it is what a lock-in-only AC resistance
               measurement is, but the current is then only as accurate and
               as stable as R_b, the approximation fails as soon as R_sample
               stops being negligible against R_b, and R_b has its own
               temperature coefficient. The 6221 replaces R_b with a real
               current source: the current is set, compliance limited, and
               flat in frequency. That is why this module pairs them.

               NOTE ON THE SISTER MODULE
               -------------------------
               Comms_SR830_GUI.py is a communication panel. The "R" it plots
               is the lock-in magnitude in VOLTS, not a resistance; it does
               not divide by a current and it is not an AC resistance
               measurement. This module is the one that measures resistance.

 REFERENCES:   SR830 manual ch.5 "Remote Programming", DETAILED COMMAND LIST
                 https://www.thinksrs.com/downloads/pdfs/manuals/SR830m.pdf
               Model 6220/6221 Reference Manual, Section 7 "Wave Functions"
                 (SCPI commands - wave functions, page 7-27 onward)
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


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: sr830 -> lockin -> pica
        plotter_path = os.path.join(
            script_dir, "..", "..", "utils", "PlotterUtil_GUI.py")
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
        # Go up 2 levels: sr830 -> lockin -> pica
        scanner_path = os.path.join(
            script_dir, "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}")


# An SR830 answers *IDN? with "Stanford_Research_Systems,SR830,s/n,ver" and a
# 6221 with "KEITHLEY INSTRUMENTS INC.,MODEL 6221,s/n,ver". Instruments are
# identified by this string, never by address: the defaults in the address
# boxes are only starting values and any address can be re-set from an
# instrument's front panel. Getting the two addresses the wrong way round is
# the easiest mistake to make in a two-instrument module, and current source
# commands must not go to a lock-in.
SR830_IDN_MARKER = "SR830"
K6221_IDN_MARKER = "6221"


def is_sr830_idn(idn):
    """True if a *IDN? reply came from an SR830 lock-in amplifier."""
    return SR830_IDN_MARKER in str(idn).upper()


def is_k6221_idn(idn):
    """True if a *IDN? reply came from a Keithley 6221 current source."""
    return K6221_IDN_MARKER in str(idn).upper()


# -----------------------------------------------------------------------------
# --- ENUMERATED CODE TABLES (SR830) ---
# The SR830 reports and accepts integer codes, not physical values. Index into
# each list with the code to get the value a human should read. A dropdown that
# reads "24" where it should read "300 ms" is how a setting gets logged wrong.
# -----------------------------------------------------------------------------

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ISRC: A i=0, A-B i=1,
# I (1 MOhm) i=2, I (100 MOhm) i=3. A four-probe voltage measurement uses
# A-B: the pair of voltage probes drives the differential input and the
# current return does not appear in the reading.
ISRC_LABELS = ["A", "A-B", "I (1 MOhm, 1e6 V/A)", "I (100 MOhm, 1e8 V/A)"]
ISRC_SHORT = ["A", "A-B", "I 1e6", "I 1e8"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ICPL: AC i=0, DC i=1.
ICPL_LABELS = ["AC", "DC"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, IGND: Float i=0, Ground i=1.
IGND_LABELS = ["Float", "Ground"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ILIN: no filters i=0,
# line notch in i=1, 2xline notch in i=2, both notch filters in i=3.
ILIN_LABELS = ["No filters", "Line notch", "2x Line notch", "Both notches"]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, SYNC: off i=0,
# synchronous filtering below 200 Hz i=1.
SYNC_LABELS = ["Off", "On (below 200 Hz)"]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, SENS table, codes 0-26.
SENS_LABELS = [
    "2 nV/fA", "5 nV/fA", "10 nV/fA", "20 nV/fA", "50 nV/fA", "100 nV/fA",
    "200 nV/fA", "500 nV/fA", "1 uV/pA", "2 uV/pA", "5 uV/pA", "10 uV/pA",
    "20 uV/pA", "50 uV/pA", "100 uV/pA", "200 uV/pA", "500 uV/pA",
    "1 mV/nA", "2 mV/nA", "5 mV/nA", "10 mV/nA", "20 mV/nA", "50 mV/nA",
    "100 mV/nA", "200 mV/nA", "500 mV/nA", "1 V/uA",
]

# The same 27 codes as full scale volts, so a measured X can be compared with
# the range it was taken on and a point sitting above 90% of full scale can be
# flagged before it clips.
SENS_VOLTS = [
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3, 1.0,
]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, OFLT table, codes 0-19.
OFLT_LABELS = [
    "10 us", "30 us", "100 us", "300 us", "1 ms", "3 ms", "10 ms", "30 ms",
    "100 ms", "300 ms", "1 s", "3 s", "10 s", "30 s", "100 s", "300 s",
    "1 ks", "3 ks", "10 ks", "30 ks",
]

# The same 20 codes in seconds. The settling wait before a point is read is
# computed from this, not guessed.
OFLT_SECONDS = [
    10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 3e-3, 10e-3, 30e-3,
    0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0,
    1e3, 3e3, 10e3, 30e3,
]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, OFSL: 6 dB/oct i=0,
# 12 dB/oct i=1, 18 dB/oct i=2, 24 dB/oct i=3.
OFSL_LABELS = ["6 dB/oct", "12 dB/oct", "18 dB/oct", "24 dB/oct"]
OFSL_DB = [6, 12, 18, 24]

# How many time constants to wait for the output to settle, per filter slope.
# A steeper filter is a longer effective settling: SR830 manual ch.3, "the
# time required to reach the final value increases with the filter slope".
# 5 time constants on a 6 dB/oct filter reaches ~99%; a 24 dB/oct filter needs
# roughly twice that for the same approach.
SETTLE_TIME_CONSTANTS = [5, 7, 9, 10]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, RMOD: High Reserve i=0,
# Normal i=1, Low Noise (minimum) i=2.
RMOD_LABELS = ["High Reserve", "Normal", "Low Noise"]

# SR830 manual ch.5, STATUS BYTE DEFINITIONS, LIA STATUS BYTE.
LIA_STATUS_BITS = [
    "INPUT/RESRV overload", "FILTR overload", "OUTPT overload",
    "Reference unlock", "Detection frequency range switched",
    "Time constant changed indirectly", "Data storage triggered", "unused",
]

# The three bits that invalidate a reading outright, and the bit that says the
# lock-in is not locked to the 6221 phase marker at all.
LIA_OVERLOAD_MASK = 0b0000_0111
LIA_UNLOCK_BIT = 3

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS: HARM is an integer from 1
# to 19999, PHAS is -360.00 to 729.99 degrees. The external reference input
# covers 0.001 Hz to 102 kHz, which is wider than the 6221 wave generator, so
# the 6221 sets the usable frequency range of the pair.
HARM_MIN, HARM_MAX = 1, 19999
PHAS_MIN, PHAS_MAX = -360.0, 729.99
SR830_EXT_FREQ_MAX = 102000.0

# The SR830 auto gain function does nothing when the time constant is above
# 1 second (SR830 manual ch.5, AUTO FUNCTIONS, AGAN). Sending it there is not
# an error, it just silently does not happen, so the module declines instead.
AGAN_MAX_OFLT_CODE = 10  # 1 s

# -----------------------------------------------------------------------------
# --- KEITHLEY 6221 WAVE GENERATOR LIMITS ---
# 6221 manual Table 7-4 "Waveform function commands", page 7-27.
# -----------------------------------------------------------------------------

# SOUR:WAVE:FREQuency <NRf> = 0 to 1e5 Hz. Section 7-6 "Frequency" gives the
# usable setting range as 1 mHz to 100 kHz.
WAVE_FREQ_MIN, WAVE_FREQ_MAX = 0.001, 100000.0

# SOUR:WAVE:AMPLitude <NRf> = 2e-12 to 0.105 A PEAK. Not RMS: 6221 manual
# 7-7 "Amplitude units", remote operations always receive and return peak.
WAVE_AMPL_MIN, WAVE_AMPL_MAX = 2e-12, 0.105

# SOUR:CURR:COMPliance. The 6221 output compliance is 0.1 V to 105 V.
COMPLIANCE_MIN, COMPLIANCE_MAX = 0.1, 105.0

# SOUR:WAVE:PMARk:OLINe <NRf> = 1 to 6, default 3. It may not collide with
# the trigger layer output line (default 2) or the waveform external trigger
# input line, or the 6221 answers -221 Settings Conflict (6221 manual note 7,
# page 7-29).
PMARK_LINE_MIN, PMARK_LINE_MAX = 1, 6
PMARK_LINE_DEFAULT = 3

# SOUR:WAVE:PMARk <NRf> = 0 to 360 degrees. 0 degrees is the zero crossing of
# the sine (6221 manual 7-9), which is the phase a lock-in wants as its
# reference edge: the residual offset is then taken out with Auto Phase.
PMARK_PHASE_DEFAULT = 0.0

# -----------------------------------------------------------------------------
# --- SAMPLE GEOMETRY ---
# -----------------------------------------------------------------------------

GEOMETRY_LABELS = [
    "None (report resistance only)",
    "Bar / strip: rho = R x w x t / L",
    "van der Pauw (symmetric): rho = (pi/ln2) x t x R",
]

MODE_LABELS = [
    "Continuous (fixed frequency and current)",
    "Current sweep (ohmic / linearity check)",
    "Frequency sweep",
]

# The x axis each mode is plotted against.
MODE_X_LABELS = [
    "Elapsed Time (s)",
    "Current Amplitude (A rms)",
    "Frequency (Hz)",
]


def rms_from_peak(peak_amps):
    """The 6221 is programmed in peak amps, the SR830 answers in RMS volts.

    Dividing an RMS voltage by a peak current is the single easiest way to
    get a resistance that is wrong by exactly sqrt(2) = 1.414, which looks
    almost right and is not. Every division in this module goes through here.
    """
    return peak_amps / math.sqrt(2.0)


def peak_from_rms(rms_amps):
    """Inverse of rms_from_peak, for turning a user's RMS wish into SOUR:WAVE:AMPL."""
    return rms_amps * math.sqrt(2.0)


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


def resistivity_from_resistance(resistance_ohm, geometry_code,
                                width_m, thickness_m, length_m):
    """Resistivity in ohm metre, or None when no geometry was given.

    Bar / strip is the four-probe case: the current runs along L through the
    cross section w x t and the voltage probes are L apart.
    The van der Pauw form is the symmetric special case, where the two
    configurations are equal and the correction factor is 1.
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


def settle_seconds(oflt_code, ofsl_code, extra_seconds=0.0):
    """How long to wait after changing the drive before the point is read."""
    return (OFLT_SECONDS[oflt_code] * SETTLE_TIME_CONSTANTS[ofsl_code]
            + max(0.0, extra_seconds))


def decode_status_byte(value, names):
    """Return the names of the set bits in a status byte, or ['none']."""
    flags = [names[bit] for bit in range(8) if value & (1 << bit)]
    return flags if flags else ["none"]


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


def build_setpoints(mode_code, frequency_hz, current_peak_a,
                    sweep_start, sweep_stop, sweep_points, logarithmic):
    """The list of (frequency Hz, current peak A) the run will step through.

    Continuous mode returns the single setpoint; the acquisition loop repeats
    it until it is stopped. The two sweeps vary one axis and hold the other.
    """
    if mode_code == 0:
        return [(frequency_hz, current_peak_a)]
    spacing = log_points if logarithmic else linear_points
    values = spacing(sweep_start, sweep_stop, sweep_points)
    if mode_code == 1:
        return [(frequency_hz, value) for value in values]
    if mode_code == 2:
        return [(value, current_peak_a) for value in values]
    raise ValueError("Unknown measurement mode %r" % (mode_code,))


def validate_drive(frequency_hz, current_peak_a, compliance_v):
    """Refuse anything the 6221 would clamp or reject, before it is sent."""
    if not WAVE_FREQ_MIN <= frequency_hz <= WAVE_FREQ_MAX:
        raise ValueError(
            "Frequency %g Hz is outside the 6221 wave range %g to %g Hz."
            % (frequency_hz, WAVE_FREQ_MIN, WAVE_FREQ_MAX))
    if frequency_hz > SR830_EXT_FREQ_MAX:
        raise ValueError(
            "Frequency %g Hz is above the SR830 external reference limit of "
            "%g Hz." % (frequency_hz, SR830_EXT_FREQ_MAX))
    if not WAVE_AMPL_MIN <= current_peak_a <= WAVE_AMPL_MAX:
        raise ValueError(
            "Current amplitude %g A peak is outside the 6221 range %g to "
            "%g A peak." % (current_peak_a, WAVE_AMPL_MIN, WAVE_AMPL_MAX))
    if not COMPLIANCE_MIN <= compliance_v <= COMPLIANCE_MAX:
        raise ValueError(
            "Compliance %g V is outside the 6221 range %g to %g V."
            % (compliance_v, COMPLIANCE_MIN, COMPLIANCE_MAX))


DATA_COLUMNS = (
    "Timestamp,Elapsed (s),Frequency set (Hz),Frequency locked (Hz),"
    "I peak (A),I rms (A),X (V),Y (V),R lockin (V),Theta (deg),"
    "R in-phase (Ohm),R magnitude (Ohm),Resistivity (Ohm m),"
    "Sheet resistance (Ohm/sq),Flags"
)


def build_log_header(module_name, version, sample, operator,
                     lockin_idn, lockin_address, source_idn, source_address,
                     settings, drive, geometry):
    """The commented header, '#' on every line, for PlotterUtil_GUI.

    Every lock-in line comes from the instrument itself at the moment the file
    is opened, never from the GUI widgets. The two can differ and the
    instrument is the truth.
    """
    ilin = settings["ilin"]
    lines = [
        "# PICA - AC resistivity, Keithley 6221 current + SR830 lock-in",
        "# Module: %s, version %s" % (module_name, version),
        "# Sample: %s" % sample,
        "# Operator: %s" % operator,
        "# Lock-in: %s" % lockin_idn,
        "# Lock-in VISA address: %s" % lockin_address,
        "# Current source: %s" % source_idn,
        "# Current source VISA address: %s" % source_address,
        "# Measurement mode: %s" % MODE_LABELS[drive["mode"]],
        "# Drive: %.4f Hz, %.6E A peak (%.6E A rms), compliance %.3f V"
        % (drive["frequency"], drive["current_peak"],
           rms_from_peak(drive["current_peak"]), drive["compliance"]),
        "# Phase marker: %.1f deg on trigger link line %d -> SR830 REF IN"
        % (drive["pmark_phase"], drive["pmark_line"]),
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
        "# Settle before each point (s): %.3f" % drive["settle"],
        "# Resistance convention: R = X / I_rms, I_rms = I_peak / sqrt(2)",
    ]
    if geometry["code"] == 0:
        lines.append("# Geometry: none, resistance only")
    else:
        lines.append("# Geometry: %s" % GEOMETRY_LABELS[geometry["code"]])
        lines.append(
            "# Width (m): %.6E, Thickness (m): %.6E, Length (m): %.6E"
            % (geometry["width"], geometry["thickness"], geometry["length"]))
    lines.append("# Started: %s" % datetime.now().isoformat(timespec="seconds"))
    lines.append(DATA_COLUMNS)
    lines.append("")
    return "\n".join(lines)


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


# -----------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -----------------------------------------------------------------------------

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
        """FREQ? in external mode returns the frequency the SR830 has locked to.

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


class K6221WaveSource:
    """The 6221 side: a sine current with a phase marker for the lock-in."""

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

    def prepare(self, compliance_v, pmark_line, pmark_phase):
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
        # would silently switch the current off part way through a long
        # settling period.
        self.instrument.write('SOUR:WAVE:DUR:TIME INF')
        self.instrument.write('SOUR:WAVE:PMAR:STAT ON')
        self.instrument.write('SOUR:WAVE:PMAR:OLIN %d' % int(pmark_line))
        self.instrument.write('SOUR:WAVE:PMAR %.1f' % pmark_phase)

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
# --- FRONT END (GUI) ---
# -----------------------------------------------------------------------------

class ACResistivityGUI:
    PROGRAM_VERSION = "1.0"
    MODULE_NAME = "AC_Resistivity_K6221_SR830_GUI.py"
    DEFAULT_LOCKIN_ADDRESS = "GPIB0::8::INSTR"
    DEFAULT_SOURCE_ADDRESS = "GPIB0::13::INSTR"
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
    FONT_READOUT = ('Segoe UI', 20, 'bold')

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
        self.root.title("PICA AC Resistivity - Keithley 6221 + SR830")
        try:
            self.root.state('zoomed')
        except tk.TclError:
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1250, 850)

        self.lockin = None
        self.source = None
        self.io_lock = threading.Lock()
        self.action_queue = queue.Queue()
        self.data_queue = queue.Queue()
        self.is_running = False
        self.stop_requested = False
        self.start_time = None
        self.data_storage = {'x': [], 'r': []}
        self.file_location_path = ""
        self.data_filepath = None
        self.logo_image = None
        self.enum_widgets = {}
        self.entries = {}

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
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure(
            'Sub.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL)
        style.configure(
            'Readout.TLabel',
            background=self.CLR_GRAPH_BG,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_READOUT)
        style.configure(
            'ReadoutName.TLabel',
            background=self.CLR_GRAPH_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL)
        style.configure('TCheckbutton',
                        background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT,
                        font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_BG_DARK)])

        style.configure('TEntry',
                        fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK,
                        borderwidth=0)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            arrowcolor=self.CLR_TEXT_DARK,
            selectbackground=self.CLR_ACCENT_GOLD,
            selectforeground=self.CLR_TEXT_DARK)

        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(10, 7),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD),
                ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Small.TButton',
            font=self.FONT_SUB_LABEL,
            padding=(6, 4),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0)
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(10, 7),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(10, 7),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)

        style.configure(
            'TLabelframe',
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
            borderwidth=1)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

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
        self.create_lockin_frame(scrollable_frame)
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

        Label(
            header_frame,
            text="AC Resistivity - Keithley 6221 + SR830 Lock-in",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(
            header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
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

        details_text = (
            "Program: AC Resistivity (four probe)\n"
            "Instruments: Keithley 6221 (AC current), SRS SR830 (voltage)\n"
            "R = X / I_rms, with I_rms = I_peak / sqrt(2)\n\n"
            "WIRING\n"
            "  6221 OUTPUT  -> sample current leads (I+, I-)\n"
            "  Voltage probes -> SR830 A and B (differential, A-B)\n"
            "  6221 TRIGGER LINK line 3 -> SR830 REF IN (TTL)\n"
            "The reference cable is not optional: without it the lock-in has\n"
            "nothing to lock to and every reading is meaningless.")
        ttk.Label(frame, text=details_text, justify='left',
                  style='Sub.TLabel').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_connection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="SR830 lock-in:").grid(
            row=0, column=0, padx=(10, 6), pady=(8, 3), sticky='w')
        self.lockin_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.lockin_cb.grid(row=0, column=1, padx=(0, 10), pady=(8, 3),
                            sticky='ew')
        self.lockin_cb.set(self.DEFAULT_LOCKIN_ADDRESS)

        ttk.Label(frame, text="Keithley 6221:").grid(
            row=1, column=0, padx=(10, 6), pady=3, sticky='w')
        self.source_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.source_cb.grid(row=1, column=1, padx=(0, 10), pady=3, sticky='ew')
        self.source_cb.set(self.DEFAULT_SOURCE_ADDRESS)

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, columnspan=2, padx=10, pady=(6, 6),
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

        self.idn_var = tk.StringVar(value="Not connected.")
        ttk.Label(frame, textvariable=self.idn_var, style='Sub.TLabel',
                  wraplength=470, justify='left').grid(
            row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')

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
        ttk.Label(frame, text="Measurement mode:").grid(
            row=row, column=0, padx=(10, 6), pady=(8, 3), sticky='w')
        self.mode_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly', values=MODE_LABELS)
        self.mode_cb.grid(row=row, column=1, columnspan=2, padx=(0, 10),
                          pady=(8, 3), sticky='ew')
        self.mode_cb.current(0)
        self.mode_cb.bind('<<ComboboxSelected>>', lambda e: self._on_mode_changed())
        row += 1

        row = self._add_entry(frame, row, 'frequency', "Frequency (Hz)",
                              "133.0", "1 mHz to 100 kHz")
        row = self._add_entry(frame, row, 'current_rms',
                              "Current (A rms)", "1e-5",
                              "sent as peak = rms x sqrt(2)")
        row = self._add_entry(frame, row, 'compliance', "Compliance (V)",
                              "10.0", "0.1 to 105 V")
        row = self._add_entry(frame, row, 'pmark_line',
                              "Phase marker line", str(PMARK_LINE_DEFAULT),
                              "trigger link 1-6, not 2")

        ttk.Separator(frame, orient='horizontal').grid(
            row=row, column=0, columnspan=3, sticky='ew', padx=10, pady=6)
        row += 1

        self.sweep_hint = ttk.Label(
            frame,
            text=("Sweep settings, used by the two sweep modes only."),
            style='Sub.TLabel', wraplength=470, justify='left')
        self.sweep_hint.grid(row=row, column=0, columnspan=3, padx=10,
                             pady=(0, 4), sticky='w')
        row += 1

        row = self._add_entry(frame, row, 'sweep_start', "Sweep start",
                              "1e-6", "A rms or Hz")
        row = self._add_entry(frame, row, 'sweep_stop', "Sweep stop",
                              "1e-4", "A rms or Hz")
        row = self._add_entry(frame, row, 'sweep_points', "Sweep points", "11")

        self.log_spacing_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Logarithmic spacing",
            variable=self.log_spacing_var).grid(
            row=row, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='w')
        self.sweep_widget_keys = ('sweep_start', 'sweep_stop', 'sweep_points')
        self._on_mode_changed()

    def create_lockin_frame(self, parent):
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
        row = self._add_entry(frame, row, 'thickness', "Thickness (um)", "100.0")
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
        self.log("Console initialized. Set both addresses and press Connect.")
        self.log("Reminder: 6221 trigger link -> SR830 REF IN, or the lock-in "
                 "has no reference.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found. Install it with "
                     "'pip install pyvisa'.")

    def create_readout_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Live Readout')
        frame.pack(side='top', fill='x', padx=5, pady=(5, 0))

        numbers = tk.Frame(frame, bg=self.CLR_GRAPH_BG)
        numbers.pack(fill='x', padx=8, pady=8)
        numbers.columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.readout_vars = {}
        cells = [
            ('resistance', 'R in-phase (Ohm)'),
            ('resistivity', 'Resistivity (Ohm m)'),
            ('x', 'X (V)'),
            ('theta', 'Theta (deg)'),
            ('freq', 'Locked f (Hz)'),
        ]
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
        graph_container = ttk.LabelFrame(parent, text='Resistance')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(7, 4), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4,
            linestyle='-')
        self.ax_main.set_title("R in-phase vs. Time", fontweight='bold')
        self.ax_main.set_xlabel(MODE_X_LABELS[0])
        self.ax_main.set_ylabel("R (Ohm)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=5, pady=5)

    # ---------------------------------------------------------------- logging
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
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
                elif kind == 'error':
                    self.log(f"ERROR ({description}): {payload}")
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

    def _on_mode_changed(self):
        """Grey the sweep boxes out in continuous mode so they cannot mislead."""
        sweeping = self.mode_cb.current() != 0
        state = 'normal' if sweeping else 'disabled'
        for key in getattr(self, 'sweep_widget_keys', ()):
            self.entries[key].config(state=state)

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
                self.log(f"Found: {resources}")
                self.lockin_cb['values'] = resources
                self.source_cb['values'] = resources
                for res in resources:
                    if "::8::" in res:
                        self.lockin_cb.set(res)
                    if "::13::" in res:
                        self.source_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def connect(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed, cannot connect.")
            return
        lockin_address = self.lockin_cb.get().strip()
        source_address = self.source_cb.get().strip()
        if not lockin_address or not source_address:
            self.log("ERROR: both VISA addresses are needed.")
            return
        if lockin_address == source_address:
            self.log("ERROR: the two addresses are the same. One is the "
                     "SR830, the other the 6221.")
            return

        self.connect_button.config(state='disabled')
        self.log(f"Connecting: SR830 at {lockin_address}, "
                 f"6221 at {source_address} ...")

        def worker():
            lockin = None
            try:
                with self.io_lock:
                    lockin = SR830Lockin(lockin_address)
            except Exception as exc:
                self.action_queue.put(
                    ('connect_failed', ("SR830", lockin_address), exc, None))
                return
            try:
                with self.io_lock:
                    source = K6221WaveSource(source_address)
            except Exception as exc:
                try:
                    lockin.close()
                except Exception:
                    pass
                self.action_queue.put(
                    ('connect_failed', ("Keithley 6221", source_address),
                     exc, None))
                return
            self.action_queue.put(('ok', None, (lockin, source),
                                   self._on_connected))

        threading.Thread(target=worker, daemon=True).start()

    def _on_connected(self, payload):
        lockin, source = payload
        self.lockin = lockin
        self.source = source
        self.log(f"SR830 connected. *IDN? -> {lockin.idn}")
        self.log(f"6221 connected.  *IDN? -> {source.idn}")
        self.idn_var.set(f"{lockin.idn}  @  {lockin.address}\n"
                         f"{source.idn}  @  {source.address}")
        self._set_controls_enabled(True)

    def disconnect(self):
        if self.is_running:
            self.stop_run()
        for name in ('source', 'lockin'):
            instrument = getattr(self, name)
            if instrument is None:
                continue
            setattr(self, name, None)
            try:
                with self.io_lock:
                    instrument.close()
            except Exception as exc:
                self.log(f"Warning during disconnect ({name}): {exc}")
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

        mode = self.mode_cb.current()
        frequency = number('frequency', "Frequency")
        current_rms = number('current_rms', "Current")
        if current_rms <= 0:
            raise ValueError("The current must be greater than zero.")
        current_peak = peak_from_rms(current_rms)
        compliance = number('compliance', "Compliance")
        pmark_line = int(number('pmark_line', "Phase marker line"))
        if not PMARK_LINE_MIN <= pmark_line <= PMARK_LINE_MAX:
            raise ValueError(
                "The phase marker line must be between %d and %d."
                % (PMARK_LINE_MIN, PMARK_LINE_MAX))
        if pmark_line == 2:
            raise ValueError(
                "Trigger link line 2 is the 6221 trigger layer output line. "
                "Using it for the phase marker returns -221 Settings Conflict.")

        sweep_start = sweep_stop = 0.0
        sweep_points = 1
        if mode != 0:
            sweep_start = number('sweep_start', "Sweep start")
            sweep_stop = number('sweep_stop', "Sweep stop")
            sweep_points = int(number('sweep_points', "Sweep points"))
            if sweep_points < 1:
                raise ValueError("A sweep needs at least one point.")
            if mode == 1:
                # The sweep boxes are in A rms for a current sweep, so they go
                # through the same peak conversion as the fixed current.
                sweep_start = peak_from_rms(sweep_start)
                sweep_stop = peak_from_rms(sweep_stop)

        setpoints = build_setpoints(
            mode, frequency, current_peak, sweep_start, sweep_stop,
            sweep_points, self.log_spacing_var.get())
        for point_frequency, point_current in setpoints:
            validate_drive(point_frequency, point_current, compliance)

        harmonic = int(number('harmonic', "Harmonic"))
        phase = number('phase', "Phase offset")
        if not HARM_MIN <= harmonic <= HARM_MAX:
            raise ValueError(
                "Harmonic must be between %d and %d." % (HARM_MIN, HARM_MAX))
        if not PHAS_MIN <= phase <= PHAS_MAX:
            raise ValueError(
                "Phase must be between %g and %g degrees."
                % (PHAS_MIN, PHAS_MAX))

        lockin_codes = {key: combo.current()
                        for key, combo in self.enum_widgets.items()}
        extra_settle = number('extra_settle', "Extra settle")
        if extra_settle < 0:
            raise ValueError("The extra settle time cannot be negative.")
        settle = settle_seconds(
            lockin_codes['oflt'], lockin_codes['ofsl'], extra_settle)

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

        return {
            'mode': mode,
            'frequency': frequency,
            'current_peak': current_peak,
            'compliance': compliance,
            'pmark_line': pmark_line,
            'pmark_phase': PMARK_PHASE_DEFAULT,
            'setpoints': setpoints,
            'harmonic': harmonic,
            'phase': phase,
            'lockin_codes': lockin_codes,
            'settle': settle,
            'geometry': geometry,
            'sample': sample,
            'operator': self.entries['operator'].get().strip(),
            'auto_gain': self.auto_gain_var.get(),
            'auto_phase': self.auto_phase_var.get(),
        }

    # ------------------------------------------------------------- the run
    def start_run(self):
        if self.lockin is None or self.source is None:
            self.log("Not connected. Press Connect first.")
            return
        try:
            params = self._read_parameters()
        except ValueError as exc:
            self.log(f"Rejected: {exc}")
            messagebox.showwarning("Check the settings", str(exc))
            return

        if params['auto_gain'] and \
                params['lockin_codes']['oflt'] > AGAN_MAX_OFLT_CODE:
            self.log(
                "Auto Gain is switched off for this run: AGAN does nothing "
                "above a 1 s time constant (SR830 manual, AUTO FUNCTIONS).")
            params['auto_gain'] = False

        self.params = params
        self.is_running = True
        self.stop_requested = False
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.disconnect_button.config(state='disabled')
        for values in self.data_storage.values():
            values.clear()
        self.line_main.set_data([], [])
        self.ax_main.set_xlabel(MODE_X_LABELS[params['mode']])
        self.ax_main.set_title(
            "R in-phase vs. %s" % MODE_X_LABELS[params['mode']].split(' (')[0],
            fontweight='bold')
        self.canvas.draw_idle()
        self.start_time = time.time()
        self.status_var.set("Configuring instruments...")
        self.log("Run started: %s" % MODE_LABELS[params['mode']])
        self.log("Settling %.3f s at each setpoint (%d x %s)."
                 % (params['settle'],
                    SETTLE_TIME_CONSTANTS[params['lockin_codes']['ofsl']],
                    OFLT_LABELS[params['lockin_codes']['oflt']]))

        threading.Thread(target=self._run_worker, daemon=True).start()
        self.root.after(100, self._process_data_queue)

    def stop_run(self):
        """Ask the worker to stop. The current goes off in the worker thread."""
        if not self.is_running:
            return
        self.stop_requested = True
        self.stop_button.config(state='disabled')
        self.log("Stop requested. Switching the 6221 output off.")

    def _open_log_file(self, settings):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "%s_%s_AC_resistivity.dat" % (self.params['sample'], stamp)
        self.data_filepath = os.path.join(self.file_location_path, filename)
        with open(self.data_filepath, 'w') as handle:
            handle.write(build_log_header(
                self.MODULE_NAME, self.PROGRAM_VERSION,
                self.params['sample'], self.params['operator'],
                self.lockin.idn, self.lockin.address,
                self.source.idn, self.source.address,
                settings, self.params, self.params['geometry']))
        return filename

    def _run_worker(self):
        """Configure, then walk the setpoints. The only thread that measures."""
        params = self.params
        codes = params['lockin_codes']
        geometry = params['geometry']
        phased = False
        try:
            with self.io_lock:
                self.lockin.configure_for_external_reference(
                    params['harmonic'], params['phase'],
                    codes['isrc'], codes['icpl'], codes['ignd'], codes['ilin'],
                    codes['sync'], codes['sens'], codes['oflt'], codes['ofsl'],
                    codes['rmod'])
                self.source.prepare(
                    params['compliance'], params['pmark_line'],
                    params['pmark_phase'])
                code, text = self.source.read_error()
                settings = self.lockin.read_settings()
            if code:
                self.data_queue.put(
                    ('log', "6221 reported error %d: %s" % (code, text)))

            self.data_queue.put(('log', "Output file: %s"
                                 % self._open_log_file(settings)))

            index = 0
            setpoints = params['setpoints']
            while not self.stop_requested:
                frequency, current_peak = setpoints[index % len(setpoints)]
                current_rms = rms_from_peak(current_peak)

                with self.io_lock:
                    self.source.set_drive(frequency, current_peak)
                    code, text = self.source.read_error()
                if code:
                    raise RuntimeError(
                        "The 6221 refused the setpoint: %d, %s" % (code, text))

                self.data_queue.put((
                    'status',
                    "Setpoint %d of %d: %.4f Hz, %.4E A rms. Settling %.2f s."
                    % (index + 1, len(setpoints), frequency, current_rms,
                       params['settle'])))
                if not self._sleep_interruptibly(params['settle']):
                    break

                with self.io_lock:
                    if params['auto_phase'] and not phased:
                        self.lockin.auto_phase()
                    if params['auto_gain']:
                        self.lockin.auto_gain()
                if params['auto_gain'] or (params['auto_phase'] and not phased):
                    phased = True
                    # An auto function moves the output, so settle again.
                    if not self._sleep_interruptibly(params['settle']):
                        break

                with self.io_lock:
                    locked_hz = self.lockin.read_reference_frequency()
                    x_volts, y_volts, r_volts, theta = self.lockin.snap()
                    lia = self.lockin.read_lia_status()
                    sens_code = self.lockin.read_sensitivity()

                problems = lockin_health(
                    lia, frequency, locked_hz, x_volts, sens_code)
                resistance, magnitude, measured_theta = \
                    resistance_from_lockin(x_volts, y_volts, current_rms)
                resistivity = resistivity_from_resistance(
                    resistance, geometry['code'], geometry['width'],
                    geometry['thickness'], geometry['length'])
                sheet = sheet_resistance(resistivity, geometry['thickness'])

                elapsed = time.time() - self.start_time
                x_axis = {0: elapsed, 1: current_rms, 2: frequency}[
                    params['mode']]
                self.data_queue.put(('point', {
                    'elapsed': elapsed,
                    'x_axis': x_axis,
                    'frequency': frequency,
                    'locked_hz': locked_hz,
                    'current_peak': current_peak,
                    'current_rms': current_rms,
                    'x': x_volts, 'y': y_volts, 'r_volts': r_volts,
                    # Theta comes from the same SNAP? instant as X and Y;
                    # measured_theta is the same angle recomputed locally and
                    # is only used as a cross-check.
                    'theta': theta,
                    'theta_computed': measured_theta,
                    'resistance': resistance,
                    'magnitude': magnitude,
                    'resistivity': resistivity,
                    'sheet': sheet,
                    'problems': problems,
                }))

                index += 1
                if params['mode'] != 0 and index >= len(setpoints):
                    break
            self.data_queue.put(('done', None))
        except Exception as exc:
            self.data_queue.put(('failed', exc))
        finally:
            # The current comes off here, in the thread that put it on, on
            # every exit path: finished, stopped, or thrown.
            try:
                with self.io_lock:
                    if self.source is not None:
                        self.source.output_off()
            except Exception as exc:
                self.data_queue.put(('log', "Warning: could not switch the "
                                            "6221 output off: %s" % exc))

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
                    self.status_var.set("Finished. The 6221 output is off.")
                    self.log("Run finished. The 6221 output is off.")
                    self._finish_run()
                    return
        except queue.Empty:
            pass
        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _record_point(self, point):
        self.readout_vars['resistance'].set("%.5G" % point['resistance'])
        self.readout_vars['resistivity'].set(
            "--" if point['resistivity'] is None
            else "%.5G" % point['resistivity'])
        self.readout_vars['x'].set("%.4E" % point['x'])
        self.readout_vars['theta'].set("%.3f" % point['theta'])
        self.readout_vars['freq'].set("%.4f" % point['locked_hz'])

        for problem in point['problems']:
            self.log("WARNING: %s" % problem)

        flags = "; ".join(point['problems']) if point['problems'] else "ok"
        if self.data_filepath:
            with open(self.data_filepath, 'a') as handle:
                handle.write(
                    "%s,%.3f,%.6f,%.6f,%.6E,%.6E,%.6E,%.6E,%.6E,%.4f,"
                    "%.6E,%.6E,%s,%s,%s\n" % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        point['elapsed'], point['frequency'],
                        point['locked_hz'], point['current_peak'],
                        point['current_rms'], point['x'], point['y'],
                        point['r_volts'], point['theta'],
                        point['resistance'], point['magnitude'],
                        "" if point['resistivity'] is None
                        else "%.6E" % point['resistivity'],
                        "" if point['sheet'] is None
                        else "%.6E" % point['sheet'],
                        flags.replace(',', ';')))

        self.data_storage['x'].append(point['x_axis'])
        self.data_storage['r'].append(point['resistance'])
        self.line_main.set_data(self.data_storage['x'], self.data_storage['r'])
        self.ax_main.relim()
        self.ax_main.autoscale_view()
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
            self.log(f"Save location set to: {path}")

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
    ACResistivityGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
