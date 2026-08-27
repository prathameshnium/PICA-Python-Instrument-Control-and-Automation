"""
Headless twin of Monitor_K197A_GUI.py.

Logs readings from a Keithley 197A Autoranging Microvolt DMM through a Model
1973A / 1972A IEEE-488 interface card. The 197A is pre-SCPI: there is no
identify query, no reset and no colon-prefixed command anywhere in here.

Any flag not given on the command line is prompted for, with the default
shown in brackets, so a bare Enter accepts it.
"""

import argparse
import csv
import os
import re
import time
from datetime import datetime

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

# ---------------------------------------------------------------------------
# Keithley 197A device-dependent commands, via the Model 1973A / 1972A
# IEEE-488 interface.
#
# !! UNVERIFIED !!  These letters have NOT been confirmed against hardware or
# against a machine-readable manual. The authoritative source is the printed
# Model 1973/1972 IEEE-488 Interface Instruction Manual. Correct this table
# from that manual before trusting any reading, and change nothing else.
#
# Every command is terminated with 'X' to execute. Several may be concatenated
# into one string, e.g. "F0R0X".
# ---------------------------------------------------------------------------
CMD = {
    "function": {          # F<n>X
        "DC volts":    "F0",
        "AC volts":    "F1",
        "2-wire ohms": "F2",
        "DC amps":     "F3",
        "AC amps":     "F4",
        "dB":          "F5",
        "4-wire ohms": "F6",   # verify: may not exist as its own function code
    },
    "range": {             # R<n>X ; R0 is autorange on this family
        "auto": "R0", "1": "R1", "2": "R2", "3": "R3",
        "4": "R4", "5": "R5", "6": "R6", "7": "R7",
    },
    "execute": "X",
}

# Unit label per function. Derived from the selected function, never from the
# reply string, because the reply format is itself unverified.
FUNCTION_UNITS = {
    "DC volts": "V",
    "AC volts": "V",
    "2-wire ohms": "Ohm",
    "4-wire ohms": "Ohm",
    "DC amps": "A",
    "AC amps": "A",
    "dB": "dB",
}

FUNCTION_NAMES = [
    "DC volts", "AC volts", "2-wire ohms", "4-wire ohms",
    "DC amps", "AC amps", "dB",
]

RANGE_NAMES = ["auto", "1", "2", "3", "4", "5", "6", "7"]

# The 197A specifications (Rev. B) give a maximum of 3 readings per second.
MIN_POLL_INTERVAL = 0.34
DEFAULT_POLL_INTERVAL = 1.0

# Only one address on this rack is confirmed (SR830 at GPIB0::8::INSTR).
# The 197A address is a placeholder; run the GPIB Scanner if it is wrong.
DEFAULT_VISA_ADDRESS = "GPIB0::7::INSTR"

PROGRAM_VERSION = "1.0"

# UNVERIFIED: the reply format of the 197A is not documented to us. This
# regex pulls the first floating point number out of whatever comes back.
# Confirm the real format from the 1973/1972 interface manual.
NUMBER_RE = re.compile(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?')

NO_RESPONSE_MESSAGE = (
    "No response from GPIB address {addr}. The 197A has no built-in IEEE-488 "
    "interface; check that a Model 1973A or 1972A card is fitted, that the "
    "address switches on the card match, and that the meter is in remote. "
    "Run the GPIB Scanner utility to list what is actually on the bus.")


def parse_reading(raw):
    """Pulls the first floating point number out of a 197A reply string.

    Returns (value, raw) where value is None if nothing numeric was found.
    """
    if raw is None:
        return None, ""
    match = NUMBER_RE.search(raw)
    if not match:
        return None, raw
    try:
        return float(match.group(0)), raw
    except ValueError:
        return None, raw


class NoResponseError(Exception):
    """Raised when the address answers nothing at all, so the caller can
    print a diagnosis instead of a traceback."""


class Keithley197A_Backend:
    """Talks to a Keithley 197A through a Model 1973A / 1972A interface."""

    def __init__(self, visa_address):
        self.visa_address = visa_address
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.instrument.timeout = 5000

    def probe(self):
        """Sends the bare execute character and tries to read one reply.

        This is the only connection check available: there is no identify
        query on this interface. Raises NoResponseError on a bus timeout.
        """
        try:
            self.instrument.write(CMD["execute"])
            return self.instrument.read().strip()
        except Exception as e:
            if pyvisa is not None and isinstance(e, pyvisa.errors.VisaIOError):
                raise NoResponseError(
                    NO_RESPONSE_MESSAGE.format(addr=self.visa_address))
            raise

    def configure(self, function_name, range_name):
        """Sets function and range in one concatenated command string."""
        # UNVERIFIED: that F and R may be concatenated and that a single
        # trailing X executes both. Confirm from the 1973/1972 manual.
        command = (CMD["function"][function_name]
                   + CMD["range"][range_name]
                   + CMD["execute"])
        self.instrument.write(command)
        return command

    def read_raw(self):
        """Reads one reading from the meter as a raw, unparsed string."""
        # UNVERIFIED: whether the meter talks on demand (a bare read) or
        # needs a trigger command first. Confirm from the interface manual.
        return self.instrument.read().strip()

    def close(self):
        """Closes the connection to the instrument."""
        if self.instrument:
            try:
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during 197A shutdown: {e}")
            finally:
                self.instrument = None


def ask(prompt_text, default):
    """Prompts with the default in brackets and accepts a bare Enter."""
    try:
        answer = input(f"{prompt_text} [{default}]: ").strip()
    except EOFError:
        return default
    return answer if answer else default


def clamp_interval(value):
    """Clamps the poll interval to the meter's own maximum reading rate."""
    if value < MIN_POLL_INTERVAL:
        print(f"[WARN] Poll interval {value} s is faster than the 197A can "
              f"answer (3 readings per second). Clamped to "
              f"{MIN_POLL_INTERVAL} s.")
        return MIN_POLL_INTERVAL
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        description="Keithley 197A monitor (needs a Model 1973A / 1972A "
                    "IEEE-488 interface card).")
    parser.add_argument("--address", help=f"VISA address of the 197A. "
                                          f"Default {DEFAULT_VISA_ADDRESS}.")
    parser.add_argument("--function", choices=FUNCTION_NAMES,
                        help="Measurement function.")
    parser.add_argument("--range", dest="range_name", choices=RANGE_NAMES,
                        help="Range: auto or 1 through 7.")
    parser.add_argument("--interval", type=float,
                        help=f"Seconds between readings. Default "
                             f"{DEFAULT_POLL_INTERVAL}, floor "
                             f"{MIN_POLL_INTERVAL}.")
    parser.add_argument("--duration", type=float,
                        help="Total run time in seconds. 0 means run until "
                             "Ctrl-C.")
    parser.add_argument("--sample", help="Sample name for the file header.")
    parser.add_argument("--path", help="Output folder. Defaults to a 'data' "
                                       "folder beside the script.")
    return parser


def main():
    args = build_parser().parse_args()

    if not PYVISA_AVAILABLE:
        print("[ERROR] PyVISA is not installed.")
        return

    default_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data")

    address = args.address if args.address else ask(
        "VISA address", DEFAULT_VISA_ADDRESS)
    function_name = args.function if args.function else ask(
        f"Function {FUNCTION_NAMES}", FUNCTION_NAMES[0])
    if function_name not in FUNCTION_NAMES:
        print(f"[ERROR] Unknown function: {function_name}")
        return
    range_name = args.range_name if args.range_name else ask(
        "Range (auto or 1..7)", "auto")
    if range_name not in RANGE_NAMES:
        print(f"[ERROR] Unknown range: {range_name}")
        return

    if args.interval is not None:
        interval = args.interval
    else:
        interval = float(ask("Poll interval (s)", DEFAULT_POLL_INTERVAL))
    interval = clamp_interval(interval)

    if args.duration is not None:
        duration = args.duration
    else:
        duration = float(ask("Duration (s), 0 for until Ctrl-C", 0))

    sample_name = args.sample if args.sample else ask("Sample name", "Sample")
    save_dir = args.path if args.path else ask("Output folder", default_path)

    unit = FUNCTION_UNITS[function_name]

    # --- Connect. There is no identify query to fall back on, so a bare
    # execute character plus one read is the whole check. ---
    try:
        backend = Keithley197A_Backend(address)
    except Exception as e:
        print(f"[ERROR] Could not open {address}: {e}")
        return

    try:
        reply = backend.probe()
    except NoResponseError as e:
        print(str(e))
        backend.close()
        return
    except Exception as e:
        print(f"[ERROR] While probing {address}: {e}")
        backend.close()
        return

    print(f"[INFO] Address {address} answered. Raw reply: {reply!r}")
    print("[INFO] There is no identify query on this interface, so the reply "
          "above does not prove the instrument is a 197A.")

    command = backend.configure(function_name, range_name)
    print(f"[INFO] Configured with: {command}")

    if not os.path.isdir(save_dir):
        os.makedirs(save_dir)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(save_dir, f"{sample_name}_{ts}_K197A_monitor.dat")

    with open(file_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["# PICA - Keithley 197A monitor"])
        writer.writerow(
            [f"# Module: Monitor_K197A_Instrument_Control.py version "
             f"{PROGRAM_VERSION}"])
        writer.writerow([f"# Sample: {sample_name}"])
        writer.writerow([f"# Instrument: Keithley 197A at {address}"])
        writer.writerow(
            ["# Interface: Model 1973A / 1972A IEEE-488 (assumed; not "
             "identified by the instrument)"])
        writer.writerow([f"# Function: {function_name}"])
        writer.writerow([f"# Range: {range_name}"])
        writer.writerow([f"# Poll interval (s): {interval}"])
        writer.writerow(["# Command table verified against manual: NO"])
        writer.writerow(
            [f"# Started: {datetime.now().isoformat(timespec='seconds')}"])
        writer.writerow(
            ["Timestamp", "Elapsed (s)", "Reading", "Unit", "Raw response"])

    print(f"[INFO] Writing to {file_path}")
    print(f"[INFO] Logging {function_name} every {interval} s. Ctrl-C to stop.")

    start_time = time.time()
    try:
        while True:
            raw = backend.read_raw()
            elapsed = time.time() - start_time
            value, raw_text = parse_reading(raw)

            if value is None:
                # A reply we cannot read as a number is printed and written
                # with a blank value, so a bad parse never kills a run.
                print(f"[WARN] Unparsed reply: {raw_text!r}")
                value_text = ""
            else:
                print(f"{elapsed:8.2f} s  {value:.6g} {unit}")
                value_text = f"{value:.6e}"

            with open(file_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    f"{elapsed:.2f}",
                    value_text,
                    unit,
                    raw_text])

            if duration and elapsed >= duration:
                print("[INFO] Duration reached.")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        backend.close()
        print(f"[INFO] Data saved to {file_path}")


if __name__ == "__main__":
    main()
