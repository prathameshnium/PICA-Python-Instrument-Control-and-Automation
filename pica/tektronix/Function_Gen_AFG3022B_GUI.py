"""
Module: Function_Gen_AFG3022B_GUI.py
Purpose: Direct-control GUI module for a Tektronix AFG 3022B arbitrary /
         function generator. It sets and reads back the drive on both
         channels; it takes no measurement and writes no data file of its
         own -- it is the source another PICA module measures against.

  Instrument: Tektronix AFG 3022B, 2 channels, 25 MHz, 250 MS/s, 14 bit
  Interface:  GPIB (rear panel), USB-TMC or LAN, spoken to through PyVISA
              as an ordinary SCPI instrument (*IDN? answers
              "TEKTRONIX,AFG3022B,<serial>,SCPI:99.0 FV:<x.xx>").

  What this module deliberately does NOT do:

    * It never sends *RST. The generator is often already driving a
      running experiment when somebody opens this window to look at it;
      a reset would silently blank both channels, drop the outputs and
      lose whatever a colleague set up at the front panel.
    * It never switches an output on by itself. Connecting reads the
      instrument and fills the form with what is already set -- the
      outputs change state only when the Output button for that channel
      is pressed.
    * It touches one channel at a time. Apply writes only the channel
      whose panel it belongs to, so channel 2 cannot be disturbed while
      channel 1 is being trimmed.

  SCPI used (AFG3000 series programmer manual), <n> = 1 or 2:

      SOURce<n>:FUNCtion:SHAPe {SIN|SQU|RAMP|PULS|PRNoise|DC|SINC|GAUS|
                                LOR|ERIS|EDEC|HAV|USER1..4}
      SOURce<n>:FREQuency:FIXed <Hz>
      SOURce<n>:VOLTage:UNIT {VPP|VRMS|DBM}
      SOURce<n>:VOLTage:LEVel:IMMediate:AMPLitude <value>
      SOURce<n>:VOLTage:LEVel:IMMediate:OFFSet <volts>
      SOURce<n>:PHASe:ADJust <deg>DEG
      SOURce<n>:FUNCtion:RAMP:SYMMetry <percent>     (ramp only)
      SOURce<n>:PULSe:DCYCle <percent>               (pulse only)
      SOURce<n>:PHASe:INITiate                       (align CH1 and CH2)
      OUTPut<n>:STATe {ON|OFF}
      OUTPut<n>:IMPedance {50|INFinity}
      SYSTem:ERRor?                                  (read after every write)

  Square duty cycle and ramp symmetry are not the same control on this
  box: the AFG 3022B's square wave is fixed at 50 %, duty cycle belongs
  to the PULSe shape, and symmetry belongs to RAMP. The panel enables
  only the field that applies to the shape in front of it.

  Limits are guarded twice. This module checks the value against the
  AFG 3022B specification before writing it, and after every Apply it
  reads the channel back and shows what the generator actually accepted
  -- a value the instrument clipped or rejected shows up in the form and
  in SYSTem:ERRor?, never as a silent difference between the screen and
  the cable.
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import os
import traceback
import threading
import queue
from datetime import datetime
import numpy as np
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
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
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
        # Go up 1 level: tektronix -> pica
        plotter_path = os.path.join(
            script_dir, "..", "utils", "PlotterUtil_GUI.py")
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
        # Go up 1 level: tektronix -> pica
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
# --- INSTRUMENT FACTS AND PURE HELPERS ---
# -------------------------------------------------------------------------------
# Everything here is module-level and instrument-free so the limit checks and
# the waveform maths can be exercised by the test suite with no AFG on the
# bench -- these are the parts that decide what gets written to a generator
# somebody else's experiment may be hanging off.

CHANNELS = (1, 2)

# GUI label -> SCPI shape mnemonic. The order is the order of the dropdown:
# the six shapes an experiment actually reaches for first, the built-in
# library after them, the four user arbitraries last.
SHAPES = [
    ("Sine", "SIN"),
    ("Square", "SQU"),
    ("Ramp / Triangle", "RAMP"),
    ("Pulse", "PULS"),
    ("Noise", "PRN"),
    ("DC", "DC"),
    ("Sinc", "SINC"),
    ("Gaussian", "GAUS"),
    ("Lorentz", "LOR"),
    ("Exponential Rise", "ERIS"),
    ("Exponential Decay", "EDEC"),
    ("Haversine", "HAV"),
    ("Arbitrary (USER1)", "USER1"),
    ("Arbitrary (USER2)", "USER2"),
    ("Arbitrary (USER3)", "USER3"),
    ("Arbitrary (USER4)", "USER4"),
]
SHAPE_LABELS = [label for label, _ in SHAPES]
LABEL_TO_SHAPE = {label: code for label, code in SHAPES}
SHAPE_TO_LABEL = {code: label for label, code in SHAPES}

# Upper frequency per shape for the AFG 3022B, in Hz. Sine, square, pulse
# and ramp are the published headline figures; the built-in library shapes
# and the user arbitraries carry a deliberately CONSERVATIVE guard, because
# the number that matters there varies with the record and being optimistic
# would let this module ask for a frequency the box cannot synthesise. The
# generator enforces its own limits regardless -- the read-back after Apply
# is what proves what it actually did.
MAX_FREQUENCY_HZ = {
    "SIN": 25.0e6,
    "SQU": 12.5e6,
    "PULS": 12.5e6,
    "RAMP": 500.0e3,
    "USER1": 12.5e6,
    "USER2": 12.5e6,
    "USER3": 12.5e6,
    "USER4": 12.5e6,
}
DEFAULT_MAX_FREQUENCY_HZ = 5.0e6   # library shapes: sinc, gaussian, ...
MIN_FREQUENCY_HZ = 1.0e-6          # 1 uHz, the AFG 3000 series floor

# Shapes with no frequency of their own.
SHAPES_WITHOUT_FREQUENCY = ("DC", "PRN")
# Shapes whose amplitude the generator ignores (DC is offset only).
SHAPES_WITHOUT_AMPLITUDE = ("DC",)

# Amplitude window, Vp-p, by load. Into 50 ohm the AFG 3022B delivers
# 20 mVp-p to 10 Vp-p; an open circuit sees exactly twice that, which is
# why the load setting has to be right before the numbers mean anything.
AMPLITUDE_LIMITS_VPP = {
    "50": (0.020, 10.0),
    "INF": (0.040, 20.0),
}
# |offset| + amplitude/2 must stay inside this rail, same doubling rule.
MAX_PEAK_V = {"50": 5.0, "INF": 10.0}

VOLTAGE_UNITS = ("VPP", "VRMS", "DBM")
LOAD_LABELS = {"50": "50 ohm", "INF": "High Z (open)"}


def shape_code(label):
    """Dropdown label -> SCPI mnemonic; an unknown label falls back to sine."""
    return LABEL_TO_SHAPE.get(label, "SIN")


def shape_label(code):
    """SCPI mnemonic (as the instrument spells it back) -> dropdown label.

    The AFG answers a shape query with the form it prefers, e.g. "SIN",
    "SQU", "PRN", "USER1". Anything unrecognised keeps its own text
    rather than being quietly turned into a sine.
    """
    key = str(code).strip().upper().strip('"')
    if key in SHAPE_TO_LABEL:
        return SHAPE_TO_LABEL[key]
    # Long forms the instrument may return instead of the short mnemonic.
    for mnemonic, label in SHAPE_TO_LABEL.items():
        if key.startswith(mnemonic):
            return label
    return key


def max_frequency(code):
    """Highest frequency this module will ask for on the given shape."""
    return MAX_FREQUENCY_HZ.get(str(code).upper(), DEFAULT_MAX_FREQUENCY_HZ)


def load_key(text):
    """Normalise a load setting ('50', 'INF', 'High Z', '9.9E+37') to a key.

    The AFG reports a high-impedance load as 9.9E+37 rather than the word
    INFinity, and that is the form a read-back has to recognise before it
    can fill the form correctly -- get it wrong and every amplitude on
    screen is out by a factor of two.
    """
    raw = str(text).strip().upper()
    if not raw:
        return "50"
    if raw.startswith("INF") or "HIGH" in raw:
        return "INF"
    try:
        if float(raw) > 1.0e6:
            return "INF"
    except ValueError:
        return "50"
    return "50"


def amplitude_limits(load):
    """(min, max) amplitude in Vp-p for the given load."""
    return AMPLITUDE_LIMITS_VPP[load_key(load)]


def validate_channel(params):
    """Check one channel's settings against the AFG 3022B specification.

    Returns a list of plain-language problems; an empty list means the
    settings are safe to write. Nothing is sent to the instrument until
    that list is empty, so a typo in an exponent cannot reach a sample
    sitting on the other end of the cable.

    params keys: shape, frequency, amplitude, unit, offset, phase,
                 duty, symmetry, load.
    """
    problems = []
    shape = str(params.get('shape', 'SIN')).upper()
    load = load_key(params.get('load', '50'))
    unit = str(params.get('unit', 'VPP')).upper()

    if shape not in SHAPES_WITHOUT_FREQUENCY:
        freq = params.get('frequency')
        if freq is None:
            problems.append("Frequency is required.")
        elif freq < MIN_FREQUENCY_HZ:
            problems.append(
                f"Frequency {freq:g} Hz is below the 1 uHz minimum.")
        elif freq > max_frequency(shape):
            problems.append(
                f"Frequency {freq:g} Hz exceeds the {max_frequency(shape):g} Hz "
                f"limit for {shape_label(shape)} on an AFG 3022B.")

    if shape not in SHAPES_WITHOUT_AMPLITUDE:
        amplitude = params.get('amplitude')
        if amplitude is None:
            problems.append("Amplitude is required.")
        elif unit == "VPP":
            # Only Vp-p can be checked against the rails without knowing the
            # shape's crest factor; Vrms and dBm are left to the instrument,
            # which reports its own error and is read back afterwards.
            lo, hi = amplitude_limits(load)
            if amplitude < lo or amplitude > hi:
                problems.append(
                    f"Amplitude {amplitude:g} Vpp is outside the {lo:g}-{hi:g} "
                    f"Vpp range into {LOAD_LABELS[load]}.")

    offset = params.get('offset')
    if offset is None:
        problems.append("Offset is required.")
    elif unit == "VPP":
        amplitude = params.get('amplitude') or 0.0
        if shape in SHAPES_WITHOUT_AMPLITUDE:
            amplitude = 0.0
        peak = abs(offset) + amplitude / 2.0
        rail = MAX_PEAK_V[load]
        if peak > rail + 1e-9:
            problems.append(
                f"Offset {offset:g} V plus half the amplitude reaches "
                f"{peak:g} V, past the {rail:g} V peak the AFG 3022B delivers "
                f"into {LOAD_LABELS[load]}.")

    phase = params.get('phase')
    if phase is not None and not (-360.0 <= phase <= 360.0):
        problems.append("Phase must be between -360 and +360 degrees.")

    if shape == "PULS":
        duty = params.get('duty')
        if duty is None or not (0.1 <= duty <= 99.9):
            problems.append("Pulse duty cycle must be between 0.1 and 99.9 %.")
    if shape == "RAMP":
        symmetry = params.get('symmetry')
        if symmetry is None or not (0.0 <= symmetry <= 100.0):
            problems.append("Ramp symmetry must be between 0 and 100 %.")

    return problems


def format_eng(value, unit=""):
    """Engineering notation with the usual lab prefixes.

    A generator spans microhertz to tens of megahertz and millivolts to
    tens of volts; '1.000 kHz' is read at a glance where '1000.0 Hz' and
    '1e+03' both have to be counted.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return f"--- {unit}".strip()
    if value == 0:
        return f"0 {unit}".strip()
    prefixes = [(1e9, 'G'), (1e6, 'M'), (1e3, 'k'), (1.0, ''),
                (1e-3, 'm'), (1e-6, 'u'), (1e-9, 'n')]
    magnitude = abs(value)
    for scale, prefix in prefixes:
        if magnitude >= scale:
            return f"{value / scale:.4g} {prefix}{unit}".strip()
    return f"{value:.4g} {unit}".strip()


def channel_summary(state):
    """One-line description of a channel, for the big status readout."""
    if not state:
        return "not read yet"
    shape = shape_label(state.get('shape', ''))
    output = "OUTPUT ON" if state.get('output') else "output off"
    if str(state.get('shape', '')).upper().startswith("DC"):
        return f"{shape} · {format_eng(state.get('offset'), 'V')} · {output}"
    unit = str(state.get('unit', 'VPP')).upper().replace("VPP", "Vpp")
    amplitude = state.get('amplitude')
    amplitude_text = ("---" if amplitude is None else f"{amplitude:.4g}")
    return (f"{shape} · {format_eng(state.get('frequency'), 'Hz')} · "
            f"{amplitude_text} {unit} · {output}")


def preview_waveform(shape, frequency, amplitude_vpp, offset=0.0, phase_deg=0.0,
                     duty=50.0, symmetry=50.0, cycles=3.0, points=1500):
    """Compute (t, y) for the on-screen preview of one channel.

    This is drawn from the numbers in the form, not from the instrument:
    it is the sanity check on what is about to be applied -- is that the
    polarity I meant, does that offset clip -- and it updates whether or
    not an AFG is connected. Arbitrary (USERn) records live in the
    generator's own memory and are not read here, so they preview as a
    flat line at the offset.
    """
    code = str(shape).upper()
    frequency = float(frequency) if frequency else 1.0
    if frequency <= 0:
        frequency = 1.0
    half = float(amplitude_vpp) / 2.0
    offset = float(offset)

    period = 1.0 / frequency
    t = np.linspace(0.0, cycles * period, int(points))

    if code.startswith("DC") or code.startswith("USER") or code == "EMEM":
        return t, np.full_like(t, offset)

    x = np.mod(t * frequency + float(phase_deg) / 360.0, 1.0)  # phase, 0..1

    if code == "SIN":
        y = np.sin(2.0 * np.pi * x)
    elif code == "SQU":
        # Fixed 50 % on the AFG 3022B -- duty cycle belongs to PULSe.
        y = np.where(x < 0.5, 1.0, -1.0)
    elif code == "PULS":
        y = np.where(x < float(duty) / 100.0, 1.0, -1.0)
    elif code == "RAMP":
        s = min(max(float(symmetry) / 100.0, 0.0), 1.0)
        if s <= 0.0:
            y = 1.0 - 2.0 * x
        elif s >= 1.0:
            y = -1.0 + 2.0 * x
        else:
            y = np.where(x < s, -1.0 + 2.0 * x / s,
                         1.0 - 2.0 * (x - s) / (1.0 - s))
    elif code == "PRN":
        # Deterministic: the preview must not flicker on every redraw.
        rng = np.random.default_rng(12345)
        y = np.clip(rng.standard_normal(t.size) / 3.0, -1.0, 1.0)
    elif code == "SINC":
        y = np.sinc(8.0 * (x - 0.5))
    elif code == "GAUS":
        y = 2.0 * np.exp(-0.5 * (6.0 * (x - 0.5)) ** 2) - 1.0
    elif code == "LOR":
        y = 2.0 / (1.0 + (10.0 * (x - 0.5)) ** 2) - 1.0
    elif code == "ERIS":
        y = 2.0 * (1.0 - np.exp(-5.0 * x)) / (1.0 - np.exp(-5.0)) - 1.0
    elif code == "EDEC":
        y = 2.0 * (np.exp(-5.0 * x) - np.exp(-5.0)) / (1.0 - np.exp(-5.0)) - 1.0
    elif code == "HAV":
        y = (1.0 - np.cos(2.0 * np.pi * x)) - 1.0
    else:
        y = np.sin(2.0 * np.pi * x)

    return t, y * half + offset


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class AFG3022B_Backend:
    """SCPI link to a Tektronix AFG 3022B.

    Connecting is a read-only act: *IDN? identifies the box, both
    channels are read, and nothing about its state is changed. Every
    method that does write a setting reads SYSTem:ERRor? afterwards and
    raises if the generator complained, so a rejected value is never
    mistaken for an accepted one.
    """

    TIMEOUT_MS = 5000

    def __init__(self, visa_address):
        self.visa_address = visa_address
        self.instrument = None
        self.identity = ""
        self._rm = None
        self.connect()

    # ------------------------------------------------------------------ link
    def connect(self):
        """Open the VISA session and confirm an AFG is on the other end."""
        self._rm = pyvisa.ResourceManager()
        self.instrument = self._rm.open_resource(self.visa_address)
        self.instrument.timeout = self.TIMEOUT_MS
        self.instrument.write_termination = '\n'
        self.instrument.read_termination = '\n'

        self.identity = self.instrument.query('*IDN?').strip()
        if 'AFG' not in self.identity.upper():
            # Refusing here is the point: the next thing this class would do
            # is write SOURce commands, and writing them into whatever else
            # answers at that address is how a Keithley ends up in a state
            # nobody can explain.
            address = self.visa_address
            self.close()
            raise IOError(
                f"{address} answered '{self.identity}', which is not a "
                "Tektronix AFG. No commands were sent to it.")
        print(f"AFG 3022B Connected: {self.identity}")
        # Clear anything the last program left in the error queue so the
        # first Apply of this session reports only its own problems.
        self.drain_errors()

    def close(self):
        """Close the VISA session. Never raises, and never changes a setting.

        Closing the window must not stop the generator: an experiment can
        be running off it long after this panel is gone.
        """
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception as e:
                print(f"Warning: issue during AFG 3022B shutdown: {e}")
            finally:
                self.instrument = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            finally:
                self._rm = None

    def reconnect(self):
        """Close and re-open the session, then re-identify."""
        try:
            self.close()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        self.connect()

    # ---------------------------------------------------------------- errors
    def drain_errors(self, limit=20):
        """Empty SYSTem:ERRor? and return the messages found.

        The AFG keeps a queue, so one bad Apply can leave several entries
        behind; they are all pulled out, because an error read next time
        would otherwise be blamed on the wrong command.
        """
        messages = []
        for _ in range(limit):
            try:
                reply = self.instrument.query('SYSTem:ERRor?').strip()
            except Exception as e:
                messages.append(f"error queue unreadable: {e}")
                break
            if not reply or reply.startswith('0,') or reply.startswith('+0,'):
                break
            messages.append(reply)
        return messages

    def _write_checked(self, command):
        """Send one command and raise if the generator objected to it."""
        self.instrument.write(command)
        errors = self.drain_errors()
        if errors:
            raise IOError(f"AFG rejected '{command}': {'; '.join(errors)}")

    # --------------------------------------------------------------- reading
    def read_channel(self, channel):
        """Read everything this panel shows for one channel.

        Queries only -- safe to run against a generator that is mid-run,
        which is exactly what happens when somebody opens this window to
        check what the sample is being driven with.
        """
        source = f"SOURce{channel}"
        shape = self.instrument.query(f"{source}:FUNCtion:SHAPe?").strip()
        state = {
            'shape': shape.upper().strip('"'),
            'frequency': float(self.instrument.query(f"{source}:FREQuency?")),
            'unit': self.instrument.query(f"{source}:VOLTage:UNIT?").strip().upper(),
            'amplitude': float(self.instrument.query(
                f"{source}:VOLTage:LEVel:IMMediate:AMPLitude?")),
            'offset': float(self.instrument.query(
                f"{source}:VOLTage:LEVel:IMMediate:OFFSet?")),
            'phase_rad': float(self.instrument.query(f"{source}:PHASe:ADJust?")),
            'load': load_key(self.instrument.query(
                f"OUTPut{channel}:IMPedance?").strip()),
            'output': self.instrument.query(
                f"OUTPut{channel}:STATe?").strip() in ('1', 'ON', 'on'),
        }
        # PHASe:ADJust? answers in radians whatever unit it was set in.
        state['phase'] = np.degrees(state['phase_rad'])

        # The shape-specific fields exist on the instrument at all times, but
        # only one of them is meaningful, and querying the other on a box
        # that does not implement it would put an error in the queue for no
        # reason. Each is read only for the shape it belongs to.
        state['symmetry'] = None
        state['duty'] = None
        try:
            if state['shape'].startswith('RAMP'):
                state['symmetry'] = float(self.instrument.query(
                    f"{source}:FUNCtion:RAMP:SYMMetry?"))
            elif state['shape'].startswith('PULS'):
                state['duty'] = float(self.instrument.query(
                    f"{source}:PULSe:DCYCle?"))
        except Exception as e:
            print(f"  CH{channel} shape parameter not readable: {e}")
        self.drain_errors()
        return state

    # --------------------------------------------------------------- writing
    def apply_channel(self, channel, params):
        """Write one channel's settings, in an order that cannot clip.

        The load goes first: amplitude and offset both mean something
        different into 50 ohm than into an open circuit, and setting them
        against the old load would have the generator scale them a
        second time when the new load arrives. Frequency and shape follow,
        then the levels, then phase.
        """
        source = f"SOURce{channel}"
        shape = str(params['shape']).upper()

        impedance = "INFinity" if load_key(params.get('load')) == "INF" else "50"
        self._write_checked(f"OUTPut{channel}:IMPedance {impedance}")
        self._write_checked(f"{source}:FUNCtion:SHAPe {shape}")

        if shape not in SHAPES_WITHOUT_FREQUENCY:
            self._write_checked(f"{source}:FREQuency:FIXed {params['frequency']:.10G}")

        if shape == "RAMP" and params.get('symmetry') is not None:
            self._write_checked(
                f"{source}:FUNCtion:RAMP:SYMMetry {params['symmetry']:.4G}")
        if shape == "PULS" and params.get('duty') is not None:
            self._write_checked(f"{source}:PULSe:DCYCle {params['duty']:.4G}")

        if shape not in SHAPES_WITHOUT_AMPLITUDE:
            self._write_checked(f"{source}:VOLTage:UNIT {params['unit']}")
            self._write_checked(
                f"{source}:VOLTage:LEVel:IMMediate:AMPLitude {params['amplitude']:.6G}")
        self._write_checked(
            f"{source}:VOLTage:LEVel:IMMediate:OFFSet {params['offset']:.6G}")

        if params.get('phase') is not None and shape not in SHAPES_WITHOUT_FREQUENCY:
            self._write_checked(f"{source}:PHASe:ADJust {params['phase']:.4G}DEG")

        return self.read_channel(channel)

    def set_output(self, channel, on):
        """Switch one output on or off."""
        self._write_checked(f"OUTPut{channel}:STATe {'ON' if on else 'OFF'}")
        return bool(on)

    def all_outputs_off(self):
        """Drop both outputs. The panic button behind the panel.

        Each channel is attempted on its own so a failure on one still
        leaves the other switched off.
        """
        failures = []
        for channel in CHANNELS:
            try:
                self.set_output(channel, False)
            except Exception as e:
                failures.append(f"CH{channel}: {e}")
        if failures:
            raise IOError("; ".join(failures))

    def align_phase(self):
        """Re-align CH1 and CH2 (SOURce1:PHASe:INITiate).

        The two channels drift apart in phase after independent frequency
        changes; this is the command that puts the requested phase
        difference back, and it changes no other setting.
        """
        self._write_checked("SOURce1:PHASe:INITiate")


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------

class AFG3022BControlGUI:
    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

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
    CLR_CH1 = '#BA6B5E'
    CLR_CH2 = '#4F6D7A'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 16, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("Tektronix AFG 3022B Function Generator Control")
        try:
            self.root.state('zoomed')  # Launch maximized
        except tk.TclError:
            # 'zoomed' is a Windows-only window state; X11 (including the
            # xvfb display CI runs under) rejects it. Not being maximised is
            # not a reason to refuse to open.
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.backend = None
        self.connected = False
        self.stop_event = threading.Event()
        self.cmd_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.worker_thread = None
        self.logo_image = None
        self.file_location_path = ""
        # The Information panel is built before the console exists and can
        # already have something to say (a missing logo), so log() buffers
        # until the console widget is there to take it.
        self.console_widget = None
        self._pending_log = []
        self.channel_state = {ch: {} for ch in CHANNELS}
        self.widgets = {ch: {} for ch in CHANNELS}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._refresh_preview()

    # -------------------------------------------------------------- styling
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
            'Status.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_STATUS)
        style.configure(
            'StatusSub.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL)
        style.configure('TCheckbutton',
                        background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT,
                        font=self.FONT_BASE)
        style.map('TCheckbutton',
                  background=[('active', self.CLR_BG_DARK)])

        # --- Style for Entry and Combobox widgets for better visibility ---
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
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])

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

    # --------------------------------------------------------------- layout
    def create_widgets(self):
        self.create_header()
        self.create_banner()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
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
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_connection_frame(scrollable_frame)
        for channel in CHANNELS:
            self.create_channel_frame(scrollable_frame, channel)
        self.create_global_frame(scrollable_frame)
        self.create_status_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

        self.create_graph_frame(right_panel)

        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        """sashpos() has no effect until the PanedWindow is mapped -- an early
        call fails SILENTLY -- so measure the real content width and retry
        until the position verifiably sticks."""
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

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        self.header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        self.header_frame.pack(side='top', fill='x')

        plotter_button = ttk.Button(
            self.header_frame, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        gpib_button = ttk.Button(
            self.header_frame, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            self.header_frame,
            text="Function Generator Control",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(
            self.header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_banner(self):
        """A one-line alert strip under the header.

        An output that is live, or a command the generator refused, has to
        be visible without reading the console -- this is the strip that
        says so.
        """
        self.banner_var = tk.StringVar(value="")
        self.banner = tk.Label(
            self.root, textvariable=self.banner_var, bg=self.CLR_ACCENT_RED,
            fg=self.CLR_HEADER, font=self.FONT_TITLE, anchor='w', padx=12)
        # Not packed yet -- it appears only when there is something to say.

    def _show_banner(self, message):
        self.banner_var.set(message)
        if not self.banner.winfo_ismapped():
            self.banner.pack(side='top', fill='x', after=self.header_frame)

    def _clear_banner(self):
        self.banner_var.set("")
        if self.banner.winfo_ismapped():
            self.banner.pack_forget()

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
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, text="LOGO\nERROR",
                    font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, text="LOGO\nMISSING",
                font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame, text="UGC-DAE Consortium for Scientific Research",
            font=institute_font, background=self.CLR_BG_DARK).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(
            frame, text="Mumbai Centre", font=institute_font,
            background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = ("Program Name: Function Generator Direct Control\n"
                        "Instrument: Tektronix AFG 3022B (2 ch, 25 MHz)\n"
                        "Connecting only reads the generator -- no reset, and\n"
                        "no output is switched on until you press its button")
        ttk.Label(frame, text=details_text, justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_connection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Connection')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="AFG 3022B VISA Address:").grid(
            row=0, column=0, columnspan=2, padx=10, pady=(5, 0), sticky='w')
        # Editable, not readonly: an address VISA does not enumerate can still
        # be typed in by hand (GPIB0::11::INSTR), which is the difference
        # between a working evening and a re-install of the VISA backend.
        self.address_cb = ttk.Combobox(frame, font=self.FONT_BASE)
        self.address_cb.grid(row=1, column=0, columnspan=2, padx=10,
                             pady=(0, 8), sticky='ew')

        self.scan_button = ttk.Button(
            frame, text="Scan for Instruments", command=self._scan_for_instruments)
        self.scan_button.grid(row=2, column=0, columnspan=2, padx=10, pady=4,
                              sticky='ew')

        button_row = ttk.Frame(frame)
        button_row.grid(row=3, column=0, columnspan=2, padx=10, pady=(4, 10),
                        sticky='ew')
        button_row.columnconfigure((0, 1), weight=1)
        self.connect_button = ttk.Button(
            button_row, text="Connect", command=self.connect_instrument,
            style='Start.TButton')
        self.connect_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.disconnect_button = ttk.Button(
            button_row, text="Disconnect", command=self.disconnect_instrument,
            state='disabled')
        self.disconnect_button.grid(row=0, column=1, sticky='ew', padx=(5, 0))

    def create_channel_frame(self, parent, channel):
        """One identical parameter block per channel.

        The two channels are independent on this instrument, so they are
        independent here: each block has its own Apply, its own read-back
        and its own output button, and nothing in one block writes to the
        other channel.
        """
        widgets = self.widgets[channel]
        frame = ttk.LabelFrame(parent, text=f'Channel {channel}')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(1, weight=1)

        def row_entry(row, label, default, key):
            ttk.Label(frame, text=label).grid(
                row=row, column=0, padx=(10, 5), pady=3, sticky='w')
            entry = ttk.Entry(frame, font=self.FONT_BASE)
            entry.insert(0, default)
            entry.grid(row=row, column=1, padx=(0, 10), pady=3, sticky='ew')
            widgets[key] = entry
            return entry

        ttk.Label(frame, text="Waveform:").grid(
            row=0, column=0, padx=(10, 5), pady=3, sticky='w')
        shape_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly',
                                values=SHAPE_LABELS)
        shape_cb.set("Sine")
        shape_cb.grid(row=0, column=1, padx=(0, 10), pady=3, sticky='ew')
        shape_cb.bind('<<ComboboxSelected>>',
                      lambda e, ch=channel: self._on_shape_changed(ch))
        widgets['shape'] = shape_cb

        row_entry(1, "Frequency (Hz):", "1000", 'frequency')
        row_entry(2, "Amplitude:", "1.0", 'amplitude')

        ttk.Label(frame, text="Amplitude Unit:").grid(
            row=3, column=0, padx=(10, 5), pady=3, sticky='w')
        unit_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly',
                               values=VOLTAGE_UNITS)
        unit_cb.set("VPP")
        unit_cb.grid(row=3, column=1, padx=(0, 10), pady=3, sticky='ew')
        widgets['unit'] = unit_cb

        row_entry(4, "Offset (V):", "0.0", 'offset')
        row_entry(5, "Phase (deg):", "0.0", 'phase')
        row_entry(6, "Pulse Duty (%):", "50.0", 'duty')
        row_entry(7, "Ramp Symmetry (%):", "50.0", 'symmetry')

        ttk.Label(frame, text="Output Load:").grid(
            row=8, column=0, padx=(10, 5), pady=3, sticky='w')
        load_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly',
                               values=[LOAD_LABELS["50"], LOAD_LABELS["INF"]])
        load_cb.set(LOAD_LABELS["50"])
        load_cb.grid(row=8, column=1, padx=(0, 10), pady=3, sticky='ew')
        widgets['load'] = load_cb

        button_row = ttk.Frame(frame)
        button_row.grid(row=9, column=0, columnspan=2, padx=10, pady=(8, 10),
                        sticky='ew')
        button_row.columnconfigure((0, 1, 2), weight=1)
        widgets['apply'] = ttk.Button(
            button_row, text="Apply", state='disabled',
            command=lambda ch=channel: self.apply_channel(ch))
        widgets['apply'].grid(row=0, column=0, sticky='ew', padx=(0, 4))
        widgets['read'] = ttk.Button(
            button_row, text="Read Back", state='disabled',
            command=lambda ch=channel: self.read_channel(ch))
        widgets['read'].grid(row=0, column=1, sticky='ew', padx=4)
        widgets['output'] = ttk.Button(
            button_row, text=f"Output {channel}: OFF", state='disabled',
            style='Start.TButton',
            command=lambda ch=channel: self.toggle_output(ch))
        widgets['output'].grid(row=0, column=2, sticky='ew', padx=(4, 0))

        self._on_shape_changed(channel)

    def create_global_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Both Channels')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure((0, 1), weight=1)

        self.align_button = ttk.Button(
            frame, text="Align CH1 / CH2 Phase", state='disabled',
            command=self.align_phase)
        self.align_button.grid(row=0, column=0, padx=(10, 5), pady=(8, 4),
                               sticky='ew')
        self.all_off_button = ttk.Button(
            frame, text="ALL OUTPUTS OFF", state='disabled',
            style='Stop.TButton', command=self.all_outputs_off)
        self.all_off_button.grid(row=0, column=1, padx=(5, 10), pady=(8, 4),
                                 sticky='ew')

        self.snapshot_button = ttk.Button(
            frame, text="Save Settings Snapshot...", state='disabled',
            command=self.save_snapshot)
        self.snapshot_button.grid(row=1, column=0, columnspan=2, padx=10,
                                  pady=(4, 10), sticky='ew')

    def create_status_frame(self, parent):
        """What the generator says it is doing, in one line per channel."""
        frame = ttk.LabelFrame(parent, text='Live Status')
        frame.pack(pady=5, padx=10, fill='x')

        inner = ttk.Frame(frame, style='TFrame')
        inner.pack(fill='x', expand=True, padx=5, pady=5)

        self.status_vars = {}
        for channel in CHANNELS:
            var = tk.StringVar(value=f"CH{channel}: not connected")
            ttk.Label(inner, textvariable=var, style='Status.TLabel',
                      anchor='w').pack(pady=(6, 0), fill='x')
            self.status_vars[channel] = var

        self.identity_var = tk.StringVar(value="No instrument connected")
        ttk.Label(inner, textvariable=self.identity_var, style='StatusSub.TLabel',
                  anchor='w').pack(pady=(6, 8), fill='x')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output', style='TLabelframe')
        frame.pack(pady=5, padx=10, fill='x', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', bd=0, relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        # Replay anything the panels above had to say before this existed.
        if self._pending_log:
            self.console_widget.config(state='normal')
            for line in self._pending_log:
                self.console_widget.insert('end', line + "\n")
            self.console_widget.config(state='disabled')
            self._pending_log = []
        self.log("Console initialized. Pick the AFG 3022B address, then Connect.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Waveform Preview')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_ch1, = self.ax_main.plot(
            [], [], color=self.CLR_CH1, linewidth=1.8, label='CH1')
        self.line_ch2, = self.ax_main.plot(
            [], [], color=self.CLR_CH2, linewidth=1.8, linestyle='--', label='CH2')
        self.ax_main.set_title("Programmed Waveform (from the form, not measured)",
                               fontweight='bold')
        self.ax_main.set_xlabel("Time (ms)")
        self.ax_main.set_ylabel("Output (V)")
        self.ax_main.grid(True, which='both', linestyle='--', alpha=0.6)
        self.ax_main.legend(loc='upper right')

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        if self.console_widget is None:
            # Too early for the console: keep the line and print it, so a
            # startup problem is never lost and never raises out of the
            # constructor either.
            self._pending_log.append(f"[{timestamp}] {message}")
            print(message)
            return
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    # ------------------------------------------------------- form <-> params
    def _on_shape_changed(self, channel):
        """Grey out the fields the selected shape has no use for.

        A duty-cycle box that accepts a number the generator will ignore
        is worse than no box: it reads as if the square wave were being
        set to 30 %, which on an AFG 3022B it cannot be.
        """
        widgets = self.widgets[channel]
        code = shape_code(widgets['shape'].get())

        def enable(key, on):
            try:
                widgets[key].configure(state=('normal' if on else 'disabled'))
            except tk.TclError:
                pass

        enable('frequency', code not in SHAPES_WITHOUT_FREQUENCY)
        enable('amplitude', code not in SHAPES_WITHOUT_AMPLITUDE)
        enable('phase', code not in SHAPES_WITHOUT_FREQUENCY)
        enable('duty', code == "PULS")
        enable('symmetry', code == "RAMP")
        try:
            widgets['unit'].configure(
                state=('readonly' if code not in SHAPES_WITHOUT_AMPLITUDE
                       else 'disabled'))
        except tk.TclError:
            pass
        self._refresh_preview()

    def _read_form(self, channel):
        """Collect one channel's form into a params dict.

        Raises ValueError with the offending field named -- 'Frequency'
        beats 'could not convert string to float'.
        """
        widgets = self.widgets[channel]
        code = shape_code(widgets['shape'].get())

        def number(key, label, default=0.0):
            text = widgets[key].get().strip()
            if not text:
                return default
            try:
                return float(text)
            except ValueError:
                raise ValueError(f"CH{channel} {label}: '{text}' is not a number.")

        load = ("INF" if widgets['load'].get() == LOAD_LABELS["INF"] else "50")
        return {
            'shape': code,
            'frequency': number('frequency', "Frequency", 1000.0),
            'amplitude': number('amplitude', "Amplitude", 0.0),
            'unit': widgets['unit'].get().strip().upper() or "VPP",
            'offset': number('offset', "Offset", 0.0),
            'phase': number('phase', "Phase", 0.0),
            'duty': number('duty', "Pulse duty", 50.0),
            'symmetry': number('symmetry', "Ramp symmetry", 50.0),
            'load': load,
        }

    def _fill_form(self, channel, state):
        """Write an instrument read-back back into the form.

        This is what makes the panel honest: after every Apply the fields
        show the values the generator actually holds, not the ones that
        were typed.
        """
        widgets = self.widgets[channel]

        def put(key, value, fmt="{:.6g}"):
            widget = widgets[key]
            was = widget.cget('state')
            widget.configure(state='normal')
            widget.delete(0, 'end')
            widget.insert(0, fmt.format(value) if value is not None else "")
            widget.configure(state=was)

        widgets['shape'].set(shape_label(state.get('shape', 'SIN')))
        self._on_shape_changed(channel)
        put('frequency', state.get('frequency'), "{:.10g}")
        put('amplitude', state.get('amplitude'))
        widgets['unit'].set(str(state.get('unit', 'VPP')).upper())
        put('offset', state.get('offset'))
        put('phase', state.get('phase'), "{:.4g}")
        if state.get('duty') is not None:
            put('duty', state['duty'], "{:.4g}")
        if state.get('symmetry') is not None:
            put('symmetry', state['symmetry'], "{:.4g}")
        widgets['load'].set(LOAD_LABELS[load_key(state.get('load', '50'))])

        self.channel_state[channel] = state
        self._update_status(channel)
        self._refresh_preview()

    def _update_status(self, channel):
        state = self.channel_state.get(channel) or {}
        self.status_vars[channel].set(
            f"CH{channel}: {channel_summary(state)}")
        button = self.widgets[channel]['output']
        on = bool(state.get('output'))
        button.config(text=f"Output {channel}: {'ON' if on else 'OFF'}",
                      style='Stop.TButton' if on else 'Start.TButton')
        live = [ch for ch in CHANNELS
                if (self.channel_state.get(ch) or {}).get('output')]
        if live:
            self._show_banner(
                "OUTPUT LIVE on " + ", ".join(f"CH{ch}" for ch in live) +
                " - the generator is driving the cable.")
        else:
            self._clear_banner()

    # -------------------------------------------------------------- preview
    def _refresh_preview(self):
        """Redraw both traces from whatever the form currently says.

        Errors are swallowed to a console line: a half-typed exponent must
        not raise out of a keystroke handler, it just means there is
        nothing sensible to draw yet.
        """
        # Channel 1's panel is built before channel 2's, and building a panel
        # asks for a redraw: until both exist (and the figure with them) there
        # is nothing to draw yet.
        if getattr(self, 'canvas', None) is None:
            return
        if not all(self.widgets[ch].get('shape') for ch in CHANNELS):
            return
        try:
            traces = {}
            for channel in CHANNELS:
                params = self._read_form(channel)
                if str(params['unit']).upper() != "VPP":
                    # The preview is drawn in volts; Vrms and dBm need the
                    # crest factor and the load to become volts, so rather
                    # than guess, that channel is left off the plot.
                    traces[channel] = None
                    continue
                traces[channel] = params

            usable = [p for p in traces.values() if p]
            if not usable:
                self.line_ch1.set_data([], [])
                self.line_ch2.set_data([], [])
                self.canvas.draw_idle()
                return

            # Both channels share one time axis, set by the slower of the
            # two, so a 1 kHz and a 10 kHz drive are seen against each other
            # the way an oscilloscope would show them.
            frequencies = [p['frequency'] for p in usable
                           if p['shape'] not in SHAPES_WITHOUT_FREQUENCY
                           and p['frequency'] > 0]
            base = min(frequencies) if frequencies else 1000.0

            for channel, line in ((1, self.line_ch1), (2, self.line_ch2)):
                params = traces.get(channel)
                if not params:
                    line.set_data([], [])
                    continue
                shape = params['shape']
                frequency = (base if shape in SHAPES_WITHOUT_FREQUENCY
                             else params['frequency'])
                cycles = 3.0 * (frequency / base if base else 1.0)
                t, y = preview_waveform(
                    shape, frequency, params['amplitude'], params['offset'],
                    params['phase'], params['duty'], params['symmetry'],
                    cycles=max(min(cycles, 200.0), 0.01))
                line.set_data(t * 1e3, y)   # ms on the x-axis

            self.ax_main.relim()
            self.ax_main.autoscale_view()
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw_idle()
        except Exception as e:
            self.log(f"Preview not drawn: {e}")

    # ------------------------------------------------------------ connection
    def _scan_for_instruments(self):
        """List the VISA resources that could be the generator.

        Resources are listed, never opened: identification happens at
        Connect, with *IDN?, on the one address the user picked. Serial
        (ASRL) resources are left out -- the AFG 3022B has no RS-232 port,
        and probing a serial line that belongs to something else is how a
        pressure gauge ends up mid-exchange.
        """
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA resources...")
            try:
                resources = [r for r in rm.list_resources()
                             if not r.upper().startswith("ASRL")]
            finally:
                rm.close()
            if resources:
                self.log(f"Found: {resources}")
                self.address_cb['values'] = resources
                if not self.address_cb.get():
                    self.address_cb.set(resources[0])
            else:
                self.log("No GPIB/USB/LAN resources found. Type the address by "
                         "hand, e.g. GPIB0::11::INSTR, if you know it.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def connect_instrument(self):
        address = self.address_cb.get().strip()
        try:
            if not address:
                raise ValueError("Pick or type the AFG 3022B VISA address first.")
            if not PYVISA_AVAILABLE:
                raise RuntimeError("PyVISA is not installed.")

            self.backend = AFG3022B_Backend(address)
            self.connected = True
            self.identity_var.set(self.backend.identity)
            self.log(f"Connected: {self.backend.identity}")
            self.log("Reading the current state of both channels "
                     "(nothing was changed).")

            self.stop_event.clear()
            self.worker_thread = threading.Thread(
                target=self._worker, daemon=True)
            self.worker_thread.start()
            self.root.after(100, self._process_result_queue)

            self._set_controls_enabled(True)
            for channel in CHANNELS:
                self.cmd_queue.put(('read', channel, None))

        except Exception as e:
            self.log(f"ERROR during connection: {traceback.format_exc()}")
            # Connecting is the one moment somebody IS at the keyboard, so a
            # dialog here is fair -- nothing is running yet to be blocked.
            messagebox.showerror("Connection Error",
                                 f"Could not connect to the AFG 3022B.\n{e}")
            if self.backend:
                self.backend.close()
            self.backend = None
            self.connected = False

    def disconnect_instrument(self):
        """Drop the VISA session, leaving the generator exactly as it is.

        The outputs are NOT switched off here. Closing a control panel is
        not a reason to stop driving a sample -- use ALL OUTPUTS OFF for
        that, deliberately.
        """
        if not self.connected:
            return
        self.stop_event.set()
        self.connected = False
        if self.backend:
            self.backend.close()
            self.backend = None
        self._set_controls_enabled(False)
        self.identity_var.set("No instrument connected")
        for channel in CHANNELS:
            self.status_vars[channel].set(f"CH{channel}: not connected")
        self._clear_banner()
        self.log("Disconnected. The generator keeps whatever it was set to.")

    def _set_controls_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for channel in CHANNELS:
            for key in ('apply', 'read', 'output'):
                self.widgets[channel][key].config(state=state)
        self.align_button.config(state=state)
        self.all_off_button.config(state=state)
        self.snapshot_button.config(state=state)
        self.connect_button.config(state='disabled' if enabled else 'normal')
        self.disconnect_button.config(state=state)

    # --------------------------------------------------------------- actions
    def apply_channel(self, channel):
        """Validate the form, then hand the write to the worker thread."""
        try:
            params = self._read_form(channel)
        except ValueError as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Invalid Value", str(e))
            return

        problems = validate_channel(params)
        if problems:
            for problem in problems:
                self.log(f"REJECTED (CH{channel}): {problem}")
            messagebox.showerror(
                f"Channel {channel} settings out of range",
                "Nothing was sent to the generator:\n\n- " +
                "\n- ".join(problems))
            return

        self.log(f"CH{channel}: applying {shape_label(params['shape'])} at "
                 f"{format_eng(params['frequency'], 'Hz')}, "
                 f"{params['amplitude']:g} {params['unit']}, offset "
                 f"{params['offset']:g} V into {LOAD_LABELS[params['load']]}.")
        self.cmd_queue.put(('apply', channel, params))

    def read_channel(self, channel):
        self.cmd_queue.put(('read', channel, None))

    def toggle_output(self, channel):
        state = self.channel_state.get(channel) or {}
        self.cmd_queue.put(('output', channel, not bool(state.get('output'))))

    def align_phase(self):
        self.cmd_queue.put(('align', None, None))

    def all_outputs_off(self):
        self.cmd_queue.put(('all_off', None, None))

    def save_snapshot(self):
        """Write what the generator currently reports to a text file.

        A drive setting is part of a measurement's record; this is the
        one-click way to keep it next to the data it produced.
        """
        if not any(self.channel_state.values()):
            self.log("Nothing to save yet -- read the channels back first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save AFG 3022B settings snapshot",
            defaultextension=".txt",
            initialdir=self.file_location_path or None,
            initialfile=f"AFG3022B_settings_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write("# Tektronix AFG 3022B settings snapshot\n")
                f.write(f"# Instrument: {self.identity_var.get()}\n")
                f.write(f"# Saved: "
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                for channel in CHANNELS:
                    state = self.channel_state.get(channel) or {}
                    f.write(f"\n[Channel {channel}]\n")
                    if not state:
                        f.write("not read\n")
                        continue
                    f.write(f"Waveform    = {shape_label(state.get('shape'))}\n")
                    f.write(f"Frequency   = "
                            f"{format_eng(state.get('frequency'), 'Hz')}\n")
                    f.write(f"Amplitude   = {state.get('amplitude')} "
                            f"{state.get('unit')}\n")
                    f.write(f"Offset      = {state.get('offset')} V\n")
                    f.write(f"Phase       = {state.get('phase'):.4g} deg\n")
                    if state.get('duty') is not None:
                        f.write(f"Duty cycle  = {state['duty']} %\n")
                    if state.get('symmetry') is not None:
                        f.write(f"Symmetry    = {state['symmetry']} %\n")
                    f.write(f"Load        = "
                            f"{LOAD_LABELS[load_key(state.get('load'))]}\n")
                    f.write(f"Output      = "
                            f"{'ON' if state.get('output') else 'OFF'}\n")
            self.file_location_path = os.path.dirname(path)
            self.log(f"Settings snapshot written to {os.path.basename(path)}")
        except Exception as e:
            self.log(f"ERROR: could not write the snapshot: {e}")

    # ---------------------------------------------------------------- worker
    def _worker(self):
        """Serialise every instrument exchange onto one thread.

        GPIB is not re-entrant and Tk must not block: the buttons only
        queue work, and this loop is the single place that talks to the
        generator. It exits when Disconnect sets the stop event.
        """
        while not self.stop_event.is_set():
            try:
                task, channel, payload = self.cmd_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if task == 'apply':
                    state = self.backend.apply_channel(channel, payload)
                    self.result_queue.put(('STATE', channel, state))
                    self.result_queue.put(
                        ('LOG', None, f"CH{channel} applied and read back."))
                elif task == 'read':
                    state = self.backend.read_channel(channel)
                    self.result_queue.put(('STATE', channel, state))
                elif task == 'output':
                    self.backend.set_output(channel, payload)
                    self.result_queue.put(
                        ('LOG', None,
                         f"CH{channel} output switched {'ON' if payload else 'OFF'}."))
                    self.result_queue.put(
                        ('STATE', channel, self.backend.read_channel(channel)))
                elif task == 'align':
                    self.backend.align_phase()
                    self.result_queue.put(
                        ('LOG', None, "CH1 and CH2 phase re-aligned."))
                elif task == 'all_off':
                    self.backend.all_outputs_off()
                    self.result_queue.put(
                        ('LOG', None, "ALL OUTPUTS OFF -- both channels are dark."))
                    for ch in CHANNELS:
                        self.result_queue.put(
                            ('STATE', ch, self.backend.read_channel(ch)))
            except Exception as e:
                self.result_queue.put(('ERROR', channel, str(e)))

    def _process_result_queue(self):
        """Drain the worker's queue on the main thread and update the GUI."""
        try:
            while not self.result_queue.empty():
                kind, channel, payload = self.result_queue.get_nowait()
                if kind == 'STATE':
                    self._fill_form(channel, payload)
                elif kind == 'LOG':
                    self.log(payload)
                elif kind == 'ERROR':
                    where = f"CH{channel}: " if channel else ""
                    self.log(f"ERROR: {where}{payload}")
                    self._show_banner(f"COMMAND FAILED - {where}{payload}")
        except queue.Empty:
            pass  # This is normal

        if self.connected:
            self.root.after(200, self._process_result_queue)

    # ---------------------------------------------------------------- closing
    def _on_closing(self):
        live = [ch for ch in CHANNELS
                if (self.channel_state.get(ch) or {}).get('output')]
        if live and self.connected:
            channels = ", ".join(f"CH{ch}" for ch in live)
            answer = messagebox.askyesnocancel(
                "Outputs are live",
                f"{channels} is still driving its cable.\n\n"
                "Yes  - switch the outputs off, then close\n"
                "No   - leave the generator running and close\n"
                "Cancel - stay here")
            if answer is None:
                return
            if answer:
                try:
                    self.backend.all_outputs_off()
                    self.log("Outputs switched off before closing.")
                except Exception as e:
                    self.log(f"ERROR: could not switch the outputs off: {e}")
        self.stop_event.set()
        self.connected = False
        if self.backend:
            self.backend.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    AFG3022BControlGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
