"""
===============================================================================
 PROGRAM:      PICA SR830 Communication and Control (headless twin)
 PURPOSE:      Talk to a Stanford Research Systems SR830 DSP lock-in amplifier
               from a terminal: identify it, dump every important setting with
               the physical value beside the raw code, change settings, and
               watch X, Y, R and Theta.

               This is a communication and control module, NOT a measurement
               module. There is deliberately no sweep and no acquisition loop.

               Every enumerated code table below is transcribed from the SR830
               manual, chapter 5 "Remote Programming", DETAILED COMMAND LIST:
               https://www.thinksrs.com/downloads/pdfs/manuals/SR830m.pdf
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.0
===============================================================================
"""

import argparse
import os
import time
from datetime import datetime

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


PROGRAM_VERSION = "1.0"
MODULE_NAME = "Comms_SR830_Instrument_Control.py"
DEFAULT_ADDRESS = "GPIB0::8::INSTR"

# An SR830 answers *IDN? with "Stanford_Research_Systems,SR830,s/n,ver".
# Identification is by this string, never by address: the default above is
# only a starting value in the address box.
SR830_IDN_MARKER = "SR830"


def is_sr830_idn(idn):
    """True if a *IDN? reply came from an SR830 lock-in amplifier."""
    return SR830_IDN_MARKER in str(idn).upper()
DEFAULT_INTERVAL = 0.5

# -----------------------------------------------------------------------------
# --- ENUMERATED CODE TABLES ---
# The SR830 reports and accepts integer codes, not physical values. Index into
# each list with the code to get the value a human should read.
# -----------------------------------------------------------------------------

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS, FMOD: external i=0,
# internal i=1.
FMOD_LABELS = ["External", "Internal"]

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS, RSLP: external reference
# trigger, sine zero crossing i=0, TTL rising edge i=1, TTL falling edge i=2.
RSLP_LABELS = ["Sine zero crossing", "TTL rising edge", "TTL falling edge"]

# SR830 manual ch.5, INPUT and FILTER COMMANDS, ISRC: A i=0, A-B i=1,
# I (1 MOhm) i=2, I (100 MOhm) i=3. The two current settings are the
# 1e6 V/A and 1e8 V/A transimpedance gains.
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

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, OFLT table, codes 0-19.
OFLT_LABELS = [
    "10 us", "30 us", "100 us", "300 us", "1 ms", "3 ms", "10 ms", "30 ms",
    "100 ms", "300 ms", "1 s", "3 s", "10 s", "30 s", "100 s", "300 s",
    "1 ks", "3 ks", "10 ks", "30 ks",
]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, OFSL: 6 dB/oct i=0,
# 12 dB/oct i=1, 18 dB/oct i=2, 24 dB/oct i=3.
OFSL_LABELS = ["6 dB/oct", "12 dB/oct", "18 dB/oct", "24 dB/oct"]
OFSL_DB = [6, 12, 18, 24]

# SR830 manual ch.5, GAIN and TIME CONSTANT COMMANDS, RMOD: High Reserve i=0,
# Normal i=1, Low Noise (minimum) i=2.
RMOD_LABELS = ["High Reserve", "Normal", "Low Noise"]

# SR830 manual ch.5, STATUS BYTE DEFINITIONS, LIA STATUS BYTE.
LIA_STATUS_BITS = [
    "INPUT/RESRV overload", "FILTR overload", "OUTPT overload",
    "Reference unlock", "Detection frequency range switched",
    "Time constant changed indirectly", "Data storage triggered", "unused",
]

# SR830 manual ch.5, STATUS BYTE DEFINITIONS, ERROR STATUS BYTE.
ERROR_STATUS_BITS = [
    "unused", "Backup error", "RAM error", "unused", "ROM error",
    "GPIB error", "DSP error", "Math error",
]

# SR830 manual ch.5, REFERENCE and PHASE COMMANDS: FREQ is limited to
# 0.001 <= f <= 102000 Hz, SLVL to 0.004 <= x <= 5.000 Vrms, PHAS to
# -360.00 <= x <= 729.99 degrees and HARM to an integer from 1 to 19999.
# SR830 manual ch.5, AUX INPUT and OUTPUT COMMANDS: AUXV is limited to
# -10.500 <= x <= 10.500 V.
FREQ_MIN, FREQ_MAX = 0.001, 102000.0
SLVL_MIN, SLVL_MAX = 0.004, 5.0
PHAS_MIN, PHAS_MAX = -360.0, 729.99
HARM_MIN, HARM_MAX = 1, 19999
AUXV_MIN, AUXV_MAX = -10.5, 10.5

# The order in which the instrument state is read. Kept in one place so the
# settings dump and the log header always see the same sequence.
SETTINGS_QUERIES = [
    ("fmod", "FMOD?"),
    ("freq", "FREQ?"),
    ("harm", "HARM?"),
    ("slvl", "SLVL?"),
    ("phas", "PHAS?"),
    ("rslp", "RSLP?"),
    ("isrc", "ISRC?"),
    ("icpl", "ICPL?"),
    ("ignd", "IGND?"),
    ("ilin", "ILIN?"),
    ("sync", "SYNC?"),
    ("sens", "SENS?"),
    ("oflt", "OFLT?"),
    ("ofsl", "OFSL?"),
    ("rmod", "RMOD?"),
]

FLOAT_KEYS = ("freq", "slvl", "phas")

# key -> (SR830 mnemonic, kind, table of labels or (low, high) bounds)
SETTABLE = {
    "fmod": ("FMOD", "enum", FMOD_LABELS),
    "freq": ("FREQ", "float", (FREQ_MIN, FREQ_MAX)),
    "slvl": ("SLVL", "float", (SLVL_MIN, SLVL_MAX)),
    "phas": ("PHAS", "float", (PHAS_MIN, PHAS_MAX)),
    "harm": ("HARM", "int", (HARM_MIN, HARM_MAX)),
    "rslp": ("RSLP", "enum", RSLP_LABELS),
    "isrc": ("ISRC", "enum", ISRC_LABELS),
    "icpl": ("ICPL", "enum", ICPL_LABELS),
    "ignd": ("IGND", "enum", IGND_LABELS),
    "ilin": ("ILIN", "enum", ILIN_LABELS),
    "sync": ("SYNC", "enum", SYNC_LABELS),
    "sens": ("SENS", "enum", SENS_LABELS),
    "oflt": ("OFLT", "enum", OFLT_LABELS),
    "ofsl": ("OFSL", "enum", OFSL_LABELS),
    "rmod": ("RMOD", "enum", RMOD_LABELS),
    "auxv1": ("AUXV 1,", "float", (AUXV_MIN, AUXV_MAX)),
    "auxv2": ("AUXV 2,", "float", (AUXV_MIN, AUXV_MAX)),
    "auxv3": ("AUXV 3,", "float", (AUXV_MIN, AUXV_MAX)),
    "auxv4": ("AUXV 4,", "float", (AUXV_MIN, AUXV_MAX)),
}


def build_set_command(key, value):
    """Validate one KEY=VALUE pair and return (command, human readable text).

    Raises ValueError with a plain message when the key is unknown or the
    value falls outside what the SR830 accepts. Rejecting here is better than
    sending it and letting the instrument silently clamp.
    """
    key = str(key).strip().lower()
    if key not in SETTABLE:
        raise ValueError(
            "Unknown setting '%s'. Known keys: %s"
            % (key, ", ".join(sorted(SETTABLE))))
    mnemonic, kind, table = SETTABLE[key]
    text = str(value).strip()

    if kind == "enum":
        try:
            code = int(float(text))
        except ValueError:
            raise ValueError(
                "%s expects an integer code, got '%s'" % (key, text))
        if not 0 <= code < len(table):
            raise ValueError(
                "%s code %d is out of range 0..%d"
                % (key, code, len(table) - 1))
        return ("%s %d" % (mnemonic, code),
                "%s = %d (%s)" % (key, code, table[code]))

    if kind == "int":
        try:
            number = int(float(text))
        except ValueError:
            raise ValueError("%s expects an integer, got '%s'" % (key, text))
        low, high = table
        if not low <= number <= high:
            raise ValueError(
                "%s must be between %d and %d" % (key, low, high))
        return "%s %d" % (mnemonic, number), "%s = %d" % (key, number)

    try:
        number = float(text)
    except ValueError:
        raise ValueError("%s expects a number, got '%s'" % (key, text))
    low, high = table
    if not low <= number <= high:
        raise ValueError("%s must be between %g and %g" % (key, low, high))
    if mnemonic.endswith(","):
        return ("%s%.4f" % (mnemonic, number), "%s = %g" % (key, number))
    return "%s %.6g" % (mnemonic, number), "%s = %g" % (key, number)


def decode_status_byte(value, names):
    """Return the names of the set bits in a status byte, or ['none']."""
    flags = [names[bit] for bit in range(8) if value & (1 << bit)]
    return flags if flags else ["none"]


def describe_settings(settings):
    """Turn a settings dict into readable lines, raw code beside the value."""
    return [
        "Reference source      FMOD %-5d %s"
        % (settings["fmod"], FMOD_LABELS[settings["fmod"]]),
        "Reference frequency   FREQ       %.4f Hz" % settings["freq"],
        "Detection harmonic    HARM %-5d n x f = %.4f Hz"
        % (settings["harm"], settings["harm"] * settings["freq"]),
        "Sine amplitude        SLVL       %.3f Vrms" % settings["slvl"],
        "Phase offset          PHAS       %.2f deg" % settings["phas"],
        "External trigger      RSLP %-5d %s"
        % (settings["rslp"], RSLP_LABELS[settings["rslp"]]),
        "Input configuration   ISRC %-5d %s"
        % (settings["isrc"], ISRC_LABELS[settings["isrc"]]),
        "Input coupling        ICPL %-5d %s"
        % (settings["icpl"], ICPL_LABELS[settings["icpl"]]),
        "Input shield          IGND %-5d %s"
        % (settings["ignd"], IGND_LABELS[settings["ignd"]]),
        "Notch filters         ILIN %-5d %s"
        % (settings["ilin"], ILIN_LABELS[settings["ilin"]]),
        "Synchronous filter    SYNC %-5d %s"
        % (settings["sync"], SYNC_LABELS[settings["sync"]]),
        "Sensitivity           SENS %-5d %s"
        % (settings["sens"], SENS_LABELS[settings["sens"]]),
        "Time constant         OFLT %-5d %s"
        % (settings["oflt"], OFLT_LABELS[settings["oflt"]]),
        "Filter slope          OFSL %-5d %s"
        % (settings["ofsl"], OFSL_LABELS[settings["ofsl"]]),
        "Dynamic reserve       RMOD %-5d %s"
        % (settings["rmod"], RMOD_LABELS[settings["rmod"]]),
    ]


def build_log_header(module_name, version, sample, operator, idn, address,
                     settings, interval):
    """The fuller commented header, '#' on every line for PlotterUtil_GUI.

    Every settings line comes from the instrument itself at the moment the
    file is opened, never from the GUI widgets. The two can differ and the
    instrument is the truth.
    """
    ilin = settings["ilin"]
    return "\n".join([
        "# PICA - SR830 lock-in monitor",
        "# Module: %s, version %s" % (module_name, version),
        "# Sample: %s" % sample,
        "# Operator: %s" % operator,
        "# Instrument: %s" % idn,
        "# VISA address: %s" % address,
        "# Reference: %s, %.4f Hz, harmonic %d"
        % (FMOD_LABELS[settings["fmod"]].lower(), settings["freq"],
           settings["harm"]),
        "# Sine amplitude (Vrms): %.3f" % settings["slvl"],
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
        "# Poll interval (s): %g" % interval,
        "# Started: %s" % datetime.now().isoformat(timespec="seconds"),
        "Timestamp,Elapsed (s),X (V),Y (V),R (V),Theta (deg)",
        "",
    ])


def diagnose_connection_failure(address, error):
    """A clean diagnosis, not a traceback."""
    return [
        "Could not talk to an SR830 at '%s'." % address,
        "Details: %s" % error,
        "Check that:",
        "  - the instrument is powered on and the GPIB cable is seated,",
        "  - the address matches the SR830 [Setup] GPIB address,",
        "  - NI-488.2 / NI-VISA or the Keysight IO Libraries are installed,",
        "  - no other program is currently holding the GPIB board.",
    ]


# -----------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -----------------------------------------------------------------------------

class SR830Backend:
    """A thin wrapper around the SR830 over VISA."""

    def __init__(self, visa_address):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed.")
        self.address = visa_address
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 5000

        # SR830 manual ch.5, SETUP COMMANDS: the SR830 sends responses to only
        # ONE interface. OUTX 1 selects GPIB, OUTX 0 selects RS232. This has to
        # go out before any query or queries time out on a good connection.
        self.instrument.write('OUTX 1')

        # Unlike some older instruments the SR830 does answer *IDN?, so it
        # doubles as the connection check.
        self.idn = self.instrument.query('*IDN?').strip()

        # This module writes SLVL (sine amplitude into the sample) and AUXV
        # (up to +/-10.5 V on the rear DC outputs). GPIB addresses get
        # changed, so confirm what actually answered before any of that can
        # be sent to it.
        if not is_sr830_idn(self.idn):
            try:
                self.instrument.close()
            finally:
                self.instrument = None
            raise ConnectionError(
                "%s is not an SR830: it identifies itself as '%s'. Refusing "
                "to send lock-in commands. Scan the bus and use the SR830's "
                "actual address." % (visa_address, self.idn))

    def read_settings(self):
        """Read the whole instrument state in the canonical order."""
        settings = {}
        for key, command in SETTINGS_QUERIES:
            reply = self.instrument.query(command).strip()
            if key in FLOAT_KEYS:
                settings[key] = float(reply)
            else:
                settings[key] = int(float(reply))
        return settings

    def read_status(self):
        """Read the LIA status byte and the error status byte."""
        lias = int(float(self.instrument.query('LIAS?').strip()))
        errs = int(float(self.instrument.query('ERRS?').strip()))
        return lias, errs

    def snap(self):
        """One SNAP? query for X, Y, R and Theta.

        SR830 manual ch.5, DATA TRANSFER COMMANDS: SNAP? records the requested
        parameters at a single instant. Four separate OUTP? calls would return
        four numbers taken at four different moments.
        """
        reply = self.instrument.query('SNAP? 1,2,3,4').strip()
        parts = [float(value) for value in reply.split(',')]
        return parts[0], parts[1], parts[2], parts[3]

    def read_aux_inputs(self):
        """Read the four Aux Inputs with OAUX?."""
        return [float(self.instrument.query('OAUX? %d' % i).strip())
                for i in (1, 2, 3, 4)]

    def read_display(self, channel):
        """Read the CH1 or CH2 display value with OUTR?."""
        return float(self.instrument.query('OUTR? %d' % channel).strip())

    def read_single(self, parameter):
        """Read one of X, Y, R or Theta with OUTP? i (i = 1, 2, 3 or 4)."""
        return float(self.instrument.query('OUTP? %d' % parameter).strip())

    def apply_setting(self, key, value):
        command, text = build_set_command(key, value)
        self.instrument.write(command)
        return command, text

    def auto_function(self, command):
        """Run AGAN, ARSV, APHS or 'AOFF i'. All of them are write only."""
        self.instrument.write(command)

    def reset(self):
        """Send *RST, then re-select GPIB because the reset clears OUTX."""
        self.instrument.write('*RST')
        time.sleep(1.0)
        self.instrument.write('OUTX 1')

    def close(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as exc:
                print("Warning: issue while closing the SR830: %s" % exc)
            finally:
                self.instrument = None


# -----------------------------------------------------------------------------
# --- COMMAND LINE FRONT END ---
# -----------------------------------------------------------------------------

def prompt_default(label, default):
    """Ask for a value with the default in brackets. A bare Enter accepts it."""
    try:
        reply = input("%s [%s]: " % (label, default)).strip()
    except EOFError:
        return default
    return reply if reply else default


def default_data_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def do_identify(backend):
    """The first thing to run at the rack: everything, readably."""
    print("\n--- SR830 Identification ---")
    print("VISA address : %s" % backend.address)
    print("*IDN?        : %s" % backend.idn)

    settings = backend.read_settings()
    print("\n--- Current Settings ---")
    for line in describe_settings(settings):
        print("  " + line)

    lias, errs = backend.read_status()
    print("\n--- Status ---")
    print("  LIA status byte   LIAS? %-4d %s"
          % (lias, ", ".join(decode_status_byte(lias, LIA_STATUS_BITS))))
    print("  Error status byte ERRS? %-4d %s"
          % (errs, ", ".join(decode_status_byte(errs, ERROR_STATUS_BITS))))
    print("")
    return settings


def do_set(backend, items):
    for item in items:
        if "=" not in item:
            print("[ERROR] '%s' is not KEY=VALUE, skipped." % item)
            continue
        key, value = item.split("=", 1)
        try:
            command, text = backend.apply_setting(key, value)
        except ValueError as exc:
            print("[ERROR] %s" % exc)
            continue
        print("[SET] %-32s -> %s" % (text, command))


def do_monitor(backend, interval, sample, path):
    settings = backend.read_settings()
    handle = None
    if sample:
        if not os.path.isdir(path):
            os.makedirs(path)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(
            path, "%s_%s_SR830_monitor.dat" % (sample, stamp))
        handle = open(filepath, "w")
        handle.write(build_log_header(
            MODULE_NAME, PROGRAM_VERSION, sample, "", backend.idn,
            backend.address, settings, interval))
        handle.flush()
        print("Logging to %s" % filepath)
    else:
        print("No sample name given, running without a log file.")

    print("\nPolling SNAP? every %g s. Press Ctrl-C to stop.\n" % interval)
    print("%-11s %14s %14s %14s %13s"
          % ("Elapsed (s)", "X (V)", "Y (V)", "R (V)", "Theta (deg)"))
    started = time.time()
    try:
        while True:
            x, y, r, theta = backend.snap()
            elapsed = time.time() - started
            print("%-11.2f %14.6E %14.6E %14.6E %13.4f"
                  % (elapsed, x, y, r, theta))
            if handle is not None:
                handle.write("%s,%.3f,%.6E,%.6E,%.6E,%.4f\n" % (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    elapsed, x, y, r, theta))
                handle.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped by user.")
    finally:
        if handle is not None:
            handle.close()


def main():
    parser = argparse.ArgumentParser(
        description="SR830 lock-in communication and control: connect, "
                    "inspect, set and monitor. Not a measurement module.")
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS,
        help="VISA resource address (default %s)." % DEFAULT_ADDRESS)
    parser.add_argument(
        "--identify", action="store_true",
        help="Connect, print *IDN? and a full settings dump, then exit.")
    parser.add_argument(
        "--set", dest="set_items", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set one parameter, repeatable, e.g. --set freq=1000 --set slvl=0.1.")
    parser.add_argument(
        "--monitor", action="store_true",
        help="Poll SNAP? and print X, Y, R and Theta until Ctrl-C.")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL,
        help="Poll interval in seconds (default %g)." % DEFAULT_INTERVAL)
    parser.add_argument(
        "--sample", default="",
        help="Sample name used in the log file header.")
    parser.add_argument(
        "--path", default="",
        help="Output folder, defaults to a 'data' folder beside this script.")
    args = parser.parse_args()

    address = args.address
    identify = args.identify
    set_items = list(args.set_items)
    monitor = args.monitor
    interval = args.interval
    sample = args.sample
    path = args.path or default_data_dir()

    # Any flag not supplied falls back to an interactive prompt that shows the
    # default in brackets and accepts a bare Enter.
    if not (identify or set_items or monitor):
        address = prompt_default("VISA address", address)
        action = prompt_default(
            "Action: identify, set or monitor", "identify").lower()
        if action == "set":
            entered = prompt_default(
                "Settings as KEY=VALUE, comma separated", "")
            set_items = [part.strip()
                         for part in entered.split(",") if part.strip()]
        elif action == "monitor":
            monitor = True
            interval = float(
                prompt_default("Poll interval (s)", str(interval)))
            sample = prompt_default(
                "Sample name (blank for no log file)", "")
            path = prompt_default("Output folder", path)
        else:
            identify = True

    if not PYVISA_AVAILABLE:
        print("[ERROR] PyVISA is not installed. Try 'pip install pyvisa'.")
        return

    backend = None
    try:
        backend = SR830Backend(address)
        print("Connected: %s" % backend.idn)
    except Exception as exc:
        for line in diagnose_connection_failure(address, exc):
            print("[ERROR] " + line)
        return

    try:
        if identify:
            do_identify(backend)
            return
        if set_items:
            do_set(backend, set_items)
        if monitor:
            do_monitor(backend, interval, sample, path)
    finally:
        if backend is not None:
            backend.close()
            print("Connection closed.")


if __name__ == "__main__":
    main()
