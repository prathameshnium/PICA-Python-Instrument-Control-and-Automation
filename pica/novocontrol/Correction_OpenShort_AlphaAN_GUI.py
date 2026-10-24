"""
Module:             Correction_OpenShort_AlphaAN_GUI.py
Purpose:            Guided calibration of the Novocontrol Alpha-AN with a
                    ZG4 sample interface: check what is connected, run the
                    REF calibration, optionally the load-short calibration,
                    and measure the empty cell for a host-side open
                    correction, one screen at a time, keeping a full record
                    of every command.
                    Alpha-AN analogue of keysight/Correction_OpenShort_E4980A_GUI.py
                    v1.0: same layout, step strip, headline banner, log,
                    console record and file set; the E4980A's open/short
                    correction is replaced by what the Alpha actually does
                    (see "What changed" below).
Author:             Prathamesh Deshmukh (PICA).
Version:            V: 1.0 (Alpha-AN port, 22 Oct 2026). Not yet run on the
                    hardware.

SAFETY:             This module never uses DC bias. DCV/DCE are never
                    transmitted (the analyzer does have DC bias - see
                    INSTRUMENT). Every calibration asks for an attended
                    confirmation BEFORE it starts; once one is running there
                    is no modal dialog (headline, log and a beep only). The
                    open measurement's AC level, integration time and
                    frequency list are checked against the ZG4 ceilings
                    before a single byte is sent. Every exit path parks the
                    generator (MBK if a task may run, ACV=0, ZCONSPL=0,
                    *RST, close).

INSTRUMENT:         The lab system this module was written for, read from
                    WinALPHA's auto-detect on 22 Oct 2026:
                      Analyzer   Novocontrol ALPHA ("ALPHA, BETA
                                 Analyzer"), SN 284
                      Interface  ZG4 sample cell interface, SN 284-0
                      *IDN?      ALPHA Analyzer, Novocontrol Technologies,
                                 6.25_200_40_284_ANB_16012009_G2
                      GPIB       address 10 on the BDS PC's NI GPIB card;
                                 a Keysight E4991B shares that bus at 17
                      DC bias    present (used in WinDETA experiments);
                                 maximum voltage not yet confirmed

What changed from the E4980A module (the instrument swap forces all of it):
  * The E4980A measures OPEN/SHORT tables inside the meter. The Alpha has
    three different things, each its own step here:
      A. REF calibration (step 3). ZRUNCAL=REF_INIT (acknowledged at once),
         then ZRUNCAL=REF written, completion by SRQ, then its "OK" ack is
         read. The SAMPLE MUST BE DISCONNECTED. About 30 s. The sequence is
         Frequency_Scan_AlphaAN_GUI.py's, including the *RST + MODE=IMP +
         INTTYP? check in front of it; only the SRQ wait differs (sliced:
         an immediate error ack is read at once, Cancel works). On success the
         time is written to ~/.pica/alphaan_cal.json in exactly the
         frequency-scan module's format ({"last_ref_cal": ISO-8601 UTC}),
         so every Alpha module shows the same calibration age.
      B. Load-short calibration (step 4, opt-in, off by default). The
         command arguments come from JUMP (github.com/JMoVS/JUMP,
         MeasurementHardware.py, ALPHA._calibrate_alpha; read only, no code
         copied): ZRUNCAL=SL_INIT (acknowledged at once), then
         ZRUNCAL=SL_SHORT with the short standard connected and
         ZRUNCAL=SL_100 with the 100 Ohm load standard connected, each
         completed by SRQ followed by its ack. NOT yet checked against the
         Alpha manual (none in the repo) and not yet run on the hardware.
         The analyzer stores the result; the scan modules apply it with
         ZSLCAL=1 (parse_zre's 'CR' answer means none is stored).
      C. Open (low-capacity) correction (step 5). The Alpha has NO analyzer
         command for it: WinDETA measures the empty cell and corrects on
         the PC. This step measures Z_open over the chosen frequency list
         with the scan modules' settings (ZREFMODE=-3, ZLLCOR=1, ZSLCAL=1,
         FRS=<wires>, DRS=0 0, ACV, MTM) and writes it to a file (format
         below). No other module applies it yet.
  * Not offered: ZRUNCAL=ALL, the full converter calibration (~1 h,
    monthly maintenance). Use WinALPHA for it.
  * Removed with the E4980A: cable length, ALC, spot points, the spot-data
    backup/restore, :SYST:PRES, the residual thresholds and the live
    reading (an Alpha point is SRQ-driven and can take seconds; polling it
    from the Tk thread would freeze the window). The Alpha's calibration
    tables cannot be read back over GPIB, so there is no backup step:
    every overwrite is announced and logged instead.

Files written (one timestamp tag, chosen folder):
  <tag>_open_Zopen.txt  empty-cell impedance, format below (step 5 only)
  <tag>_log.txt         the module's own log (what you saw on screen)
  <tag>_console.txt     every command sent and every reply received

Open-correction file format (<tag>_open_Zopen.txt), "PICA_ALPHAAN_ZOPEN v1":
  ASCII, UTF-8. Lines starting with '#' are 'key: value' metadata
  (format, written, idn, acv_Vrms, mtm_s, wire_mode, instrument_settings,
  cell, temperature_K, last_ref_cal). Then one tab-separated header line
      Freq_Hz  F_actual_Hz  Zopen_re_Ohm  Zopen_im_Ohm  Gopen_S  Bopen_S
      Copen_F  Status  Ref
  and one row per requested frequency, in measurement order:
      Freq_Hz        the REQUESTED frequency: the key a scan matches on
      F_actual_Hz    the frequency the analyzer actually used
      Zopen_re/im    series impedance of the empty cell, as ZRE? reports it
      Gopen/Bopen    Yopen = 1/Zopen = Gopen + j*Bopen
      Copen_F        Bopen / (2*pi*F_actual), the stray capacitance
      Status, Ref    ZRE? status (2 = valid) and reference number
  To apply it, at the same requested frequency and the same ACV, MTM and
  wire mode:   1/Zs = 1/Zmes - 1/Zopen   (Ys = Ymes - Yopen),
  then Zs = 1/Ys. Skip rows whose Status is not 2. apply_open_correction()
  and parse_open_file() below are the reference implementation; a scan
  module that wants them copies them (PICA modules are self-contained).

PICA rules honoured: self-contained script (no shared module, the Alpha
backend is a copy of Frequency_Scan_AlphaAN_GUI.py's), minimal imports,
plain-language screens with one big headline answer, advanced parameters
hidden, new behaviours opt-in, no modal dialog except attended
confirmations before a calibration starts.
"""

# ===============================================================================
# IMPORTS
# ===============================================================================
import os
import sys
import json
import math
import time
import queue
import threading
import traceback
from datetime import datetime, timezone

import tkinter as tk
from tkinter import ttk, Label, LabelFrame, filedialog, messagebox, scrolledtext

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:            # pragma: no cover - depends on the machine
    pyvisa = None
    PYVISA_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:     # pragma: no cover
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:            # pragma: no cover
    PIL_AVAILABLE = False


# ===============================================================================
# CONSTANTS
# ===============================================================================
PROGRAM_VERSION = "1.0"

# Load-short calibration steps, in order (arguments from JUMP, see the
# module docstring). Nothing outside this tuple is ever sent as ZRUNCAL=.
LOAD_SHORT_STEPS = ("SL_SHORT", "SL_100")
LOAD_SHORT_LABELS = {"SL_SHORT": "SHORT standard",
                     "SL_100": "100 Ohm LOAD standard"}

# The load-short duration is not documented in any source we have, so the
# SRQ wait is generous (an MBK mid-task may leave half-replaced data, and
# the operator is present anyway). On timeout the task is aborted with MBK.
# The elapsed time of every part is logged, so the real duration is learned.
SRQ_TIMEOUT_SL_S = 3600.0

# A calibration task's SRQ wait is cut into slices of this length. Between
# slices the status byte is serial-polled (a poll is not a query: no command
# byte reaches the analyzer) and the Cancel flag is checked, so an
# immediate error ack (message available, no SRQ) is read at once instead of
# hanging for SRQ_TIMEOUT_SL_S, and Cancel answers within one slice.
CAL_WAIT_SLICE_S = 2.0
# IEEE-488.2 status-byte bits looked at between slices.
STB_TASK_DONE = 0x01     # Alpha: task finished (only checked after SRQ)
STB_MAV = 0x10           # message available: a response is already waiting
STB_RQS = 0x40           # service requested (SRQ raised)


class CalibrationCancelled(RuntimeError):
    """The operator pressed Cancel while a calibration task ran."""

# Defaults for the open (empty-cell) measurement: the scan modules' own.
DEFAULT_OPEN_ACV = 1.0
DEFAULT_OPEN_MTM = 0.5
DEFAULT_OPEN_WIRES = "2"

OPEN_FILE_FORMAT = "PICA_ALPHAAN_ZOPEN v1"
OPEN_HEADER = ("Freq_Hz\tF_actual_Hz\tZopen_re_Ohm\tZopen_im_Ohm\tGopen_S\t"
               "Bopen_S\tCopen_F\tStatus\tRef")


# ===============================================================================
# SAFETY CONSTANTS - hardware ceilings. NEVER raise these to "make it work".
# ===============================================================================

# Alpha-AN generator span, from the manual. Every requested frequency is
# checked against this before a single byte reaches the bus.
FREQ_HW_MIN, FREQ_HW_MAX = 3e-5, 20e6

# Generator AC amplitude ceiling (Vrms), from the ZG4 spec: 3 Vrms up to
# 4 MHz, 2 Vrms from 4 to 10 MHz (ZG4 technical data chart), 1 Vrms above
# 10 MHz. Frequency-dependent, so it is enforced per-point rather than as
# one flat number. The 2 Vrms mid tier is the conservative reading of the
# manual ("3 Vrms <= 10 MHz" in one place, "2 Vrms above 4 MHz" in the
# ZG4 chart) - we take the lower of the two.
ACV_MAX_LOW_FREQ = 3.0        # <= 4 MHz
ACV_MAX_MID_FREQ = 2.0        # 4 - 10 MHz
ACV_MAX_HIGH_FREQ = 1.0       # > 10 MHz
ACV_FREQ_MID_HZ = 4e6
ACV_FREQ_BREAK_HZ = 10e6

# Integration time bounds (s). MTM=0 is legal and means "shortest available";
# the analyzer never integrates less than one period regardless, which is
# where the recipe's "max of 0.5 s or 1 period" behaviour comes from. The
# 0.01 s floor here is conservative but harmless.
MTM_MIN_S, MTM_MAX_S = 0.01, 1000.0

# ZRE? status field. 2 = Result Valid; anything else is not trustworthy.
# Meanings verified against the Alpha manual.
STATUS_RESULT_VALID = 2
# Statuses that abort the whole sweep rather than just flagging one point.
# 6 = the signal source was disconnected mid-measurement (hardware fault).
STATUS_FATAL = frozenset({6})
STATUS_MEANINGS = {
    0: "Invalid (result buffer empty)",
    1: "Measurement still in progress",
    2: "Result valid",
    3: "Voltage V1 out of range for sample measurement (check ACV/sample)",
    4: "Current out of range for sample measurement (check ACV/sample)",
    5: "Voltage V1 out of range for reference measurement",
    6: "Signal source disconnected during measurement (hardware fault)",
}

# Acknowledgment strings a Novocontrol executable command leaves in the
# result buffer. Every set command (ACV=, MTM=, GFR=, FRS=, ...) answers
# "OK" on success or one of these codes on failure, and that response MUST
# be read - otherwise it desynchronizes every later query. (MST is the
# exception: it leaves no buffered ack; completion is SRQ + ZRE?.)
CMD_ERROR_MEANINGS = {
    "CA": "Cannot execute during an active calibration",
    "CR": "Required test-interface calibration does not exist (Recalibrate)",
    "CN": "Unavailable while the CE output of a POT/GAL interface is "
          "connected",
    "EC": "System connection test (CONCHECK) required",
    "ER": "General command error",
    "II": "Command not supported by the connected test interface",
    "IM": "Measurement type not supported in the current mode",
    "IP": "Invalid command parameter",
    "MR": "Not allowed while a measurement/calibration is running",
    "RE": "Command error (see manual)",
    "UC": "Unknown command",
}

# ZG4 interface code reported by INTTYP? (the ZG4 is code 5, not a string).
ZG4_INTERFACE_CODE = 5

# Timeouts (s) for the SRQ waits. A REF calibration takes ~30 s.
SRQ_TIMEOUT_MEASURE_S = 120.0


def point_timeout_s(freq_hz, mtm_s):
    """SRQ budget for one point.

    A point integrates the sample and (reference mode "Always") the
    reference, each for at least MTM and at least one signal period. A fixed
    120 s budget is too short below ~0.02 Hz or above MTM ~55 s, so scale it;
    never go below SRQ_TIMEOUT_MEASURE_S.
    """
    return max(SRQ_TIMEOUT_MEASURE_S,
               4.0 * max(float(mtm_s), 1.0 / float(freq_hz)) + 60.0)

SRQ_TIMEOUT_CAL_S = 300.0

# After *RST the Alpha drops off the GPIB bus while it reinitializes (no
# listener: every write fails with VI_ERROR_NLISTENERS, NI-488.2's ENOL) and
# only answers again some seconds later. Seen on hardware 22 Oct 2026: a
# fixed 1 s sleep was too short and MODE=IMP failed. Commands are retried on
# "no listeners" and on timeout, until this deadline.
# Through 64-bit Keysight VISA (23 Oct 2026) the first *IDN? after *RST did
# not fail with "no listeners" but hung in viWrite for the whole 60 s session
# timeout (VI_ERROR_TMO). While waiting, each try therefore gets a short
# timeout of its own, and a timed-out try is followed by a device clear.
# On one PC (23 Oct 2026) the Alpha answered 1.4 s after *RST through the
# 32-bit NI-488.2 edition and 4.1 s through 64-bit VISA, so the first try
# waits RESET_SETTLE_S and the deadline is generous.
# A session opened just after a park (*RST) can meet the same wait, so
# connect retries *IDN? the same way for CONNECT_READY_TIMEOUT_S.
RESET_SETTLE_S = 2.0
RESET_READY_TIMEOUT_S = 60.0
RESET_RETRY_INTERVAL_S = 0.5
RESET_POLL_TIMEOUT_MS = 3000
CONNECT_READY_TIMEOUT_S = 15.0

# VISA status codes, as pyvisa reports them in VisaIOError.error_code.
# NLISTENERS: no listener at that address; the write put no byte on the bus,
# so it is the one GPIB error that is always safe to retry.
# NCIC: the board is not Controller-In-Charge - typically left behind by
# WinALPHA / WinDETA; cleared by one Interface Clear on the board.
# TMO: the transfer did not complete in time; part of a command may have
# reached the device, so it is retried only after a device clear.
VI_ERROR_NLISTENERS = -1073807265
VI_ERROR_NCIC = -1073807264
VI_ERROR_TMO = -1073807339

# The Alpha's address on the BDS bus, as WinALPHA's auto-detect reports it
# ("Found device: Address: 10 - IDN string: ALPHA Analyzer, ..."). The
# Keysight E4991B shares that bus at 17.
DEFAULT_ALPHA_PAD = 10

EPS0_SI = 8.8541878128e-12   # F/m  - used for the geometric capacitance C0
EPS0_CM = 8.8541878128e-14   # F/cm - used only so sigma lands in S/cm

# Wire modes exposed by the ZG4 interface.
WIRE_MODES = ("2", "3", "4")


def acv_ceiling(freq_hz):
    """ZG4 AC-voltage ceiling (Vrms) at a given frequency."""
    if freq_hz <= ACV_FREQ_MID_HZ:
        return ACV_MAX_LOW_FREQ
    if freq_hz <= ACV_FREQ_BREAK_HZ:
        return ACV_MAX_MID_FREQ
    return ACV_MAX_HIGH_FREQ


def status_message(status):
    """Human-readable meaning of a ZRE? status code."""
    return STATUS_MEANINGS.get(status, f"unknown status {status}")


def parse_inttyp(response):
    """Extract the numeric interface code from an INTTYP? response.

    Format is 'INTTYP=%d1 %d2' (code, then serial); the ZG4 is code 5.
    """
    text = response.strip(" \t\r\n\x00\x10")
    if "=" in text:
        text = text.split("=", 1)[1]
    return int(text.split()[0])


def visa_error_code(exc):
    """The VISA status code carried by a pyvisa VisaIOError, else None."""
    code = getattr(exc, "error_code", None)
    return int(code) if code is not None else None


def gpib_board_of(visa_addr):
    """'GPIB0::10::INSTR' -> 0; 'GPIB::10' -> 0; None if not a GPIB address."""
    head = str(visa_addr).strip().upper().split("::", 1)[0]
    if not head.startswith("GPIB"):
        return None
    tail = head[4:]
    return int(tail) if tail.isdigit() else 0


# Frozen frequency series: 20 Hz, x1.1 per step, final point clamped to
# exactly 1 MHz. 115 points. Generated once, deliberately NOT recomputed at
# runtime and NOT user-editable, so every scan in the lab is directly
# comparable point-for-point.
FREQUENCIES_115PT_1MHZ = (
    2.000000e+01, 2.200000e+01, 2.420000e+01, 2.662000e+01, 2.928200e+01,
    3.221020e+01, 3.543122e+01, 3.897434e+01, 4.287178e+01, 4.715895e+01,
    5.187485e+01, 5.706233e+01, 6.276857e+01, 6.904542e+01, 7.594997e+01,
    8.354496e+01, 9.189946e+01, 1.010894e+02, 1.111983e+02, 1.223182e+02,
    1.345500e+02, 1.480050e+02, 1.628055e+02, 1.790860e+02, 1.969947e+02,
    2.166941e+02, 2.383635e+02, 2.621999e+02, 2.884199e+02, 3.172619e+02,
    3.489880e+02, 3.838868e+02, 4.222755e+02, 4.645031e+02, 5.109534e+02,
    5.620487e+02, 6.182536e+02, 6.800790e+02, 7.480869e+02, 8.228956e+02,
    9.051851e+02, 9.957036e+02, 1.095274e+03, 1.204801e+03, 1.325282e+03,
    1.457810e+03, 1.603591e+03, 1.763950e+03, 1.940345e+03, 2.134379e+03,
    2.347817e+03, 2.582599e+03, 2.840859e+03, 3.124945e+03, 3.437439e+03,
    3.781183e+03, 4.159301e+03, 4.575231e+03, 5.032754e+03, 5.536030e+03,
    6.089633e+03, 6.698596e+03, 7.368456e+03, 8.105301e+03, 8.915831e+03,
    9.807415e+03, 1.078816e+04, 1.186697e+04, 1.305367e+04, 1.435904e+04,
    1.579494e+04, 1.737443e+04, 1.911188e+04, 2.102306e+04, 2.312537e+04,
    2.543791e+04, 2.798170e+04, 3.077987e+04, 3.385785e+04, 3.724364e+04,
    4.096800e+04, 4.506480e+04, 4.957129e+04, 5.452841e+04, 5.998126e+04,
    6.597938e+04, 7.257732e+04, 7.983505e+04, 8.781856e+04, 9.660041e+04,
    1.062605e+05, 1.168865e+05, 1.285751e+05, 1.414327e+05, 1.555759e+05,
    1.711335e+05, 1.882469e+05, 2.070716e+05, 2.277787e+05, 2.505566e+05,
    2.756122e+05, 3.031735e+05, 3.334908e+05, 3.668399e+05, 4.035239e+05,
    4.438763e+05, 4.882639e+05, 5.370903e+05, 5.907993e+05, 6.498793e+05,
    7.148672e+05, 7.863539e+05, 8.649893e+05, 9.514882e+05, 1.000000e+06,
)

# Second frozen series: the WinDETA measurement definition
# (data_file_for_ref/mes_def_Fscan.txt, LIST ORDERING). 20 Hz, x1.4 per
# step, 40 points, final point exactly 10 MHz. Transcribed verbatim from
# the mes_def - deliberately NOT recomputed and NOT user-editable.
FREQUENCIES_40PT_10MHZ = (
    2.000000e+01, 2.800000e+01, 3.920000e+01, 5.488000e+01, 7.683200e+01,
    1.075648e+02, 1.505907e+02, 2.108270e+02, 2.951578e+02, 4.132209e+02,
    5.785093e+02, 8.099130e+02, 1.133878e+03, 1.587430e+03, 2.222401e+03,
    3.111362e+03, 4.355907e+03, 6.098269e+03, 8.537577e+03, 1.195261e+04,
    1.673365e+04, 2.342711e+04, 3.279796e+04, 4.591714e+04, 6.428399e+04,
    8.999759e+04, 1.259966e+05, 1.763953e+05, 2.469534e+05, 3.457347e+05,
    4.840286e+05, 6.776401e+05, 9.486961e+05, 1.328175e+06, 1.859444e+06,
    2.603222e+06, 3.644511e+06, 5.102316e+06, 7.143242e+06, 1.000000e+07,
)

# The only two scan recipes this program will run. Keys are the combobox
# labels; the default preset is listed first.
FREQ_PRESETS = {
    "115 pts, 20 Hz - 1 MHz (x1.1)": FREQUENCIES_115PT_1MHZ,
    "40 pts, 20 Hz - 10 MHz (x1.4)": FREQUENCIES_40PT_10MHZ,
}
DEFAULT_FREQ_PRESET = "115 pts, 20 Hz - 1 MHz (x1.1)"

# Guardrail: refuse to even import with a frequency the hardware cannot reach.
for _name, _series in FREQ_PRESETS.items():
    assert all(
        FREQ_HW_MIN <= _f <= FREQ_HW_MAX for _f in _series
    ), f"Preset {_name!r} contains a point outside the Alpha-AN range."

# Third choice in the Frequency List box: a list the user types. Not a
# preset, so it never goes through the guardrail above; every value is
# checked by parse_frequency_list instead (and again by
# validate_parameters before anything reaches the bus).
CUSTOM_FREQ_LABEL = "Custom list (type your own below)"
# Sanity cap only: at ~1 s per point this is already a 15+ minute sweep.
CUSTOM_FREQ_MAX_POINTS = 1000


def parse_frequency_list(text):
    """User-typed frequency list (Hz) -> tuple of floats, in typed order.

    Commas separate values; semicolons, spaces and new lines are accepted
    too, so a list pasted from List Maker or a spreadsheet works as is.
    Every value must lie inside the Alpha-AN generator span
    FREQ_HW_MIN-FREQ_HW_MAX. Raises ValueError naming the first bad entry.
    """
    tokens = text.replace(",", " ").replace(";", " ").split()
    if not tokens:
        raise ValueError("Custom frequency list is empty.")
    if len(tokens) > CUSTOM_FREQ_MAX_POINTS:
        raise ValueError(
            f"Custom frequency list has {len(tokens)} points; the limit is "
            f"{CUSTOM_FREQ_MAX_POINTS}."
        )
    values = []
    for tok in tokens:
        try:
            f = float(tok)
        except ValueError:
            raise ValueError(
                f"Custom frequency list: '{tok}' is not a number. "
                f"Use values in Hz such as 20, 1000, 1e6."
            ) from None
        if not math.isfinite(f):
            raise ValueError(
                f"Custom frequency list: '{tok}' is not a finite number."
            )
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(
                f"Custom frequency list: {tok} Hz is outside the Alpha-AN "
                f"range {FREQ_HW_MIN:g} Hz to {FREQ_HW_MAX:g} Hz."
            )
        values.append(f)
    return tuple(values)


# ===============================================================================
# PURE HELPERS - no VISA, no Tk.
# ===============================================================================

def parse_zre(response):
    """Parse a ZRE? response into (Zs', Zs'', f_actual, status, ref).

    The analyzer answers 'ZRE=Zs' Zs'' freq status ref'. The leading
    'ZRE=' echo is stripped if present; fields are whitespace separated.

    Responses terminate on EOI (plus an EOS byte), so strip trailing
    non-whitespace terminator bytes (CR/NUL/DLE) that .strip() misses.
    """
    text = response.strip(" \t\r\n\x00\x10")
    if "=" in text:
        text = text.split("=", 1)[1]
    fields = text.replace(",", " ").split()
    # A bare 'CR' (Recalibrate) is returned when load-short correction
    # (ZSLCAL=1) is on but no load-short calibration is stored.
    if fields and fields[0].upper() == "CR":
        raise RuntimeError(
            "Instrument returned 'CR' (Recalibrate): load-short correction "
            "(ZSLCAL=1) is enabled but no load-short calibration is stored. "
            "Run a load-short calibration, or disable ZSLCAL."
        )
    if len(fields) < 5:
        raise ValueError(f"Malformed ZRE? response: {response!r}")
    zr = float(fields[0])
    zi = float(fields[1])
    f_actual = float(fields[2])
    status = int(float(fields[3]))
    ref = int(float(fields[4]))
    return zr, zi, f_actual, status, ref


def validate_alpha_parameters(frequencies, acv, mtm, wire_mode):
    """Reject anything unsafe BEFORE a single byte reaches the Alpha.

    The ZG4 AC ceiling is frequency-dependent, so the binding limit is the
    lowest ceiling over the whole frequency list. Raises ValueError.
    """
    if not frequencies:
        raise ValueError("Frequency list is empty.")
    for f in frequencies:
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(
                f"Frequency {f} Hz is outside the Alpha-AN range "
                f"{FREQ_HW_MIN:g}-{FREQ_HW_MAX:g} Hz.")
    max_acv = min(acv_ceiling(f) for f in frequencies)
    if not (0.0 < acv <= max_acv):
        raise ValueError(
            f"AC voltage {acv} Vrms outside the 0 < V <= {max_acv} Vrms "
            f"safety limit (frequency-dependent ZG4 ceiling).")
    if not (MTM_MIN_S <= mtm <= MTM_MAX_S):
        raise ValueError(
            f"Integration time {mtm} s outside {MTM_MIN_S}-{MTM_MAX_S} s.")
    if wire_mode not in WIRE_MODES:
        raise ValueError(f"Wire mode must be one of {WIRE_MODES}.")


# --- Calibration-age bookkeeping -------------------------------------------
# Cal data lives IN the instrument and survives *RST, so a sweep on a
# previous calibration is a supported mode. We only track WHEN it happened so
# the operator can judge staleness. We never prompt and never block on it.

def cal_state_path():
    """Path of the small JSON that remembers the last REF cal timestamp."""
    return os.path.join(
        os.path.expanduser("~"), ".pica", "alphaan_cal.json"
    )


def read_last_ref_cal():
    """Return the stored ISO-8601 timestamp, or None if never recorded."""
    try:
        with open(cal_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh).get("last_ref_cal")
    except Exception:
        return None


def write_last_ref_cal(when=None):
    """Record a successful REF calibration. Returns the ISO string written."""
    stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    path = cal_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"last_ref_cal": stamp}, fh)
    return stamp


def format_cal_age(iso_stamp, now=None):
    """Human-readable cal age for the console and both file headers."""
    if not iso_stamp:
        return "Last REF calibration: unknown (not recorded by PICA)"
    try:
        then = datetime.fromisoformat(iso_stamp)
    except ValueError:
        return f"Last REF calibration: unparseable ({iso_stamp})"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    days = (now - then).total_seconds() / 86400.0
    return (
        f"Last REF calibration: {then.strftime('%Y-%m-%dT%H:%MZ')} "
        f"({days:.1f} days ago)"
    )


# --- Open (empty-cell) correction ------------------------------------------

def open_row(freq_req, zr, zi, f_actual, status, ref):
    """One empty-cell point -> dict with the admittance and C_open.

    Yopen = 1/Zopen = (zr - j*zi) / |Z|^2; Copen = Bopen / (2*pi*f_actual).
    The conversion uses f_actual, the row is keyed by the requested freq.
    """
    mag_sq = zr * zr + zi * zi
    if mag_sq > 0:
        g, b = zr / mag_sq, -zi / mag_sq
    else:
        g = b = float("nan")
    omega = 2.0 * math.pi * f_actual
    c = b / omega if omega > 0 else float("nan")
    return {"f": float(freq_req), "f_actual": float(f_actual),
            "zr": float(zr), "zi": float(zi), "g": g, "b": b, "c": c,
            "status": int(status), "ref": int(ref)}


def apply_open_correction(zr_mes, zi_mes, zr_open, zi_open):
    """Remove the empty cell: 1/Zs = 1/Zmes - 1/Zopen. Returns (Zs', Zs'').

    The open cell is in PARALLEL with the sample, so its admittance is
    subtracted. Raises ZeroDivisionError if Zmes equals Zopen (nothing is
    left of the sample).
    """
    y_s = 1.0 / complex(zr_mes, zi_mes) - 1.0 / complex(zr_open, zi_open)
    z_s = 1.0 / y_s
    return z_s.real, z_s.imag


def format_open_file(rows, meta):
    """Text of a PICA_ALPHAAN_ZOPEN v1 file (see the module docstring)."""
    lines = [f"# Alpha-AN open (empty-cell) correction data "
             f"(PICA Correction_OpenShort_AlphaAN v{PROGRAM_VERSION})",
             f"# format: {OPEN_FILE_FORMAT}"]
    for k, v in meta.items():
        lines.append(f"# {k}: {v}")
    lines.append("# apply: 1/Zs = 1/Zmes - 1/Zopen at the same requested "
                 "Freq_Hz, ACV, MTM and wire mode; skip rows with Status != 2")
    lines.append(OPEN_HEADER)
    for r in rows:
        lines.append("\t".join([
            f"{r['f']:.9g}", f"{r['f_actual']:.9g}",
            f"{r['zr']:.9g}", f"{r['zi']:.9g}",
            f"{r['g']:.9g}", f"{r['b']:.9g}", f"{r['c']:.9g}",
            str(r["status"]), str(r["ref"])]))
    return "\n".join(lines) + "\n"


def parse_open_file(text):
    """Inverse of format_open_file. Returns (meta_dict, rows list).

    Each row is a dict with keys f, f_actual, zr, zi, g, b, c, status, ref.
    """
    meta, rows = {}, []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("# ").strip()
            if ":" in body:
                k, v = body.split(":", 1)
                meta[k.strip()] = v.strip()
            continue
        if line.startswith("Freq_Hz\t"):
            continue
        parts = line.split("\t")
        if len(parts) != 9:
            raise ValueError(f"Bad open-correction row: {line[:60]}")
        rows.append({"f": float(parts[0]), "f_actual": float(parts[1]),
                     "zr": float(parts[2]), "zi": float(parts[3]),
                     "g": float(parts[4]), "b": float(parts[5]),
                     "c": float(parts[6]), "status": int(parts[7]),
                     "ref": int(parts[8])})
    if meta.get("format") != OPEN_FILE_FORMAT:
        raise ValueError(f"Not a {OPEN_FILE_FORMAT} file "
                         f"(format: {meta.get('format')!r}).")
    return meta, rows


def measure_open_cell(backend, freqs, on_point=None, stop_event=None):
    """Measure the empty cell at every requested frequency.

    `backend` must already be initialised (initialize_instrument). Returns
    the list of open_row dicts. Status 6 (signal source disconnected) aborts
    with RuntimeError; any other non-valid status is kept in the row and
    flagged by its Status column. on_point(i, n, row) is called after each
    point; a set stop_event ends the loop between points.
    """
    rows = []
    for i, f in enumerate(freqs, start=1):
        if stop_event is not None and stop_event.is_set():
            break
        zr, zi, f_actual, status, ref = backend.measure_point(f)
        if status in STATUS_FATAL:
            raise RuntimeError(
                f"ZRE? status {status} at {f:g} Hz: {status_message(status)}. "
                f"Open measurement aborted.")
        row = open_row(f, zr, zi, f_actual, status, ref)
        rows.append(row)
        if on_point:
            on_point(i, len(freqs), row)
    return rows


# ===============================================================================
# COMMAND RECORD - wraps the VISA session so every byte lands in the console
# file, without touching the copied backend.
# ===============================================================================

def _clean(reply):
    return str(reply).strip(" \t\r\n\x00\x10")


class RecordingInstrument:
    """Proxy for a pyvisa resource: forwards everything, records the
    traffic as (iso_time, kind, command, reply) through `on_record`."""

    def __init__(self, inst, on_record):
        object.__setattr__(self, "_inst", inst)
        object.__setattr__(self, "_on_record", on_record)

    def __getattr__(self, name):
        return getattr(self._inst, name)

    def __setattr__(self, name, value):
        setattr(self._inst, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _rec(self, kind, cmd, reply=""):
        entry = (datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                 kind, cmd, reply)
        try:
            self._on_record(entry)
        except Exception:
            pass

    def write(self, cmd):
        self._rec("W", cmd)
        return self._inst.write(cmd)

    def query(self, cmd):
        try:
            reply = self._inst.query(cmd)
        except Exception as e:
            self._rec("Q", cmd, f"ERROR {e}")
            raise
        text = _clean(reply)
        self._rec("Q", cmd, text if len(text) < 200
                  else text[:200] + f"... ({len(text)} chars)")
        return reply

    def read(self):
        try:
            reply = self._inst.read()
        except Exception as e:
            self._rec("R", "", f"ERROR {e}")
            raise
        self._rec("R", "", _clean(reply))
        return reply

    def wait_for_srq(self, timeout_ms=25000):
        self._rec("SRQ", f"wait up to {timeout_ms} ms")
        try:
            out = self._inst.wait_for_srq(timeout_ms)
        except Exception as e:
            self._rec("SRQ", "timeout/error", str(e))
            raise
        self._rec("SRQ", "asserted")
        return out


class RecordingRM:
    """Proxy for the ResourceManager: every opened session is recorded."""

    def __init__(self, rm, on_record):
        self._rm = rm
        self._on_record = on_record

    def __getattr__(self, name):
        return getattr(self._rm, name)

    def open_resource(self, address, *args, **kwargs):
        return RecordingInstrument(
            self._rm.open_resource(address, *args, **kwargs), self._on_record)


# ===============================================================================
# BACKEND CLASS - Instrument Control Logic
# ===============================================================================

class AlphaAN_Backend:
    """All GPIB traffic with the Novocontrol Alpha-AN mainframe.

    Not SCPI. Commands are Novocontrol proprietary high-level commands plus
    the IEEE-488.2 common set (*IDN?, *RST).
    """

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.rm = None
        # True between MST/ZRUNCAL and the corresponding SRQ. Tells the
        # shutdown path whether an MBK abort is actually needed.
        self._task_may_be_running = False
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    # --- connection ---------------------------------------------------

    def connect(self, visa_addr):
        """Open the session and confirm we are talking to an Alpha with ZG4."""
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")

        inst = self.rm.open_resource(visa_addr)
        inst.timeout = 60000  # 60 s; a long MTM at low frequency is slow
        # The Alpha-AN terminates responses with EOI (plus an EOS byte) and
        # accepts commands on EOI only - it does NOT use a newline terminator.
        inst.read_termination = ""
        inst.write_termination = ""
        inst.send_end = True
        self.instrument = inst
        is_alpha = False

        try:
            try:
                idn = self._query_idn_until_answered(
                    CONNECT_READY_TIMEOUT_S, "while connecting").strip()
            except Exception as exc:
                # A board left not-CIC by another program (WinALPHA /
                # WinDETA) fails every transfer. Reclaim it ONCE with
                # Interface Clear and retry; any other error is raised.
                if visa_error_code(exc) != VI_ERROR_NCIC:
                    raise
                board = gpib_board_of(visa_addr)
                print(f"  GPIB board {board} is not Controller-In-Charge "
                      f"(another GPIB program such as WinALPHA may have "
                      f"taken the bus). Reclaiming it with Interface "
                      f"Clear...")
                self.send_interface_clear(board)
                try:
                    idn = self._query_idn_until_answered(
                        CONNECT_READY_TIMEOUT_S, "while connecting").strip()
                except Exception as exc2:
                    if visa_error_code(exc2) == VI_ERROR_NCIC:
                        raise ConnectionError(
                            f"GPIB board {board} is still not "
                            f"Controller-In-Charge after Interface Clear. "
                            f"Close WinALPHA / WinDETA and any other GPIB "
                            f"program, then try again.") from exc2
                    raise
            # A wrong address must not get the Alpha shutdown sequence
            # (ACV=0, ZCONSPL=0, *RST) sent to another instrument.
            if not any(k in idn.upper() for k in ("ALPHA", "NOVOCONTROL")):
                raise ConnectionError(
                    f"{visa_addr} answered {idn!r}, which is not a "
                    f"Novocontrol Alpha. Check the address.")
            is_alpha = True
            # INTTYP? returns 'INTTYP=<code> <serial>'; the ZG4 is code 5,
            # NOT the literal string "ZG4".
            code = parse_inttyp(inst.query("INTTYP?"))
            if code != ZG4_INTERFACE_CODE:
                raise ConnectionError(
                    f"Expected a ZG4 sample interface (INTTYP code "
                    f"{ZG4_INTERFACE_CODE}), got code {code}. Refusing to "
                    f"continue."
                )
            # Serial-poll once to clear any stale status left on the bus, so
            # the first wait_for_srq cannot latch onto a leftover event.
            inst.read_stb()
        except Exception:
            # Never leave a half-open session behind on a failed handshake.
            if is_alpha:
                self.close_instrument()
            else:
                try:
                    inst.close()
                except Exception:
                    pass
                self.instrument = None
            raise

        print(f"  Connected: {idn} | ZG4 interface code {code}")
        return idn, code

    def send_interface_clear(self, board):
        """Assert IFC on a GPIB board and make it Controller-In-Charge.

        IFC resets the GPIB interface of every device on the bus (they are
        unaddressed); it is not a device clear and sends no command bytes,
        so instrument settings and measurements are not touched. Only ever
        sent after the board reported 'not CIC', never on a healthy board.
        """
        if board is None:
            raise ConnectionError(
                "Interface Clear needs a GPIB address (GPIBn::...).")
        intfc = self.rm.open_resource(f"GPIB{board}::INTFC")
        try:
            intfc.send_ifc()
        finally:
            intfc.close()
        print(f"  Interface Clear sent on GPIB{board}.")

    # --- command acknowledgment ---------------------------------------

    def _exec(self, cmd):
        """Send one executable command and consume its acknowledgment.

        Every Novocontrol set command (ACV=, MTM=, GFR=, FRS=, ...) writes
        'OK' (or an error code) into the result buffer, and that response
        MUST be read - unread acks desynchronize every later query, e.g.
        ZRE? would return a stale 'OK' instead of data. MST is the one
        exception (no buffered ack; completion is SRQ + ZRE?) and is
        written directly, never through here.
        """
        resp = self.instrument.query(cmd).strip(" \t\r\n\x00\x10")
        if resp != "OK":
            meaning = CMD_ERROR_MEANINGS.get(resp, "unrecognized response")
            raise RuntimeError(
                f"Command {cmd!r} failed: instrument answered {resp!r} "
                f"({meaning})."
            )

    def _query_idn_until_answered(self, timeout_s, situation):
        """*IDN?, repeated while the Alpha is away reinitializing.

        While the Alpha reinitializes after *RST it is not a listener. Through
        NI-488.2 every write then fails at once with VI_ERROR_NLISTENERS,
        which put nothing on the bus. Through 64-bit Keysight VISA the write
        hangs until the session timeout instead (VI_ERROR_TMO). Both are
        retried: each try runs on a short timeout, a timed-out try is
        followed by a device clear (flushes any half-received command), and
        the session timeout is restored afterwards. Any other error is
        raised at once.
        Returns the *IDN? answer.
        """
        inst = self.instrument
        start = time.monotonic()
        session_timeout = inst.timeout
        inst.timeout = RESET_POLL_TIMEOUT_MS
        try:
            while True:
                try:
                    return inst.query("*IDN?")
                except Exception as exc:
                    code = visa_error_code(exc)
                    if code not in (VI_ERROR_NLISTENERS, VI_ERROR_TMO):
                        raise
                    elapsed = time.monotonic() - start
                    kind = ("no listener" if code == VI_ERROR_NLISTENERS
                            else "timeout")
                    print(f"  Waiting for the Alpha {situation}: "
                          f"{elapsed:.1f} s, {kind}.")
                    if code == VI_ERROR_TMO:
                        try:
                            inst.clear()
                        except Exception as clear_exc:
                            print(f"  Device clear failed: {clear_exc}")
                    if elapsed >= timeout_s:
                        raise ConnectionError(
                            f"The Alpha did not rejoin the GPIB bus within "
                            f"{timeout_s:.0f} s {situation}. Last error: "
                            f"{exc}") from exc
                time.sleep(RESET_RETRY_INTERVAL_S)
        finally:
            inst.timeout = session_timeout

    def _reset_and_set_impedance_mode(self,
                                      timeout_s=RESET_READY_TIMEOUT_S):
        """*RST, wait until the Alpha answers again, then MODE=IMP.

        *RST leaves no Novocontrol ack. The first *IDN? goes out after
        RESET_SETTLE_S and is repeated until it is answered - the same
        handshake JUMP uses after *RST (github.com/JMoVS/JUMP,
        MeasurementHardware.py, ALPHA.initialize_instrument), including its
        extra 1 s settle before MODE=IMP.
        """
        inst = self.instrument
        inst.write("*RST")
        start = time.monotonic()
        time.sleep(RESET_SETTLE_S)
        self._query_idn_until_answered(timeout_s, "after *RST")
        print(f"  *RST complete; Alpha answering after "
              f"{time.monotonic() - start:.1f} s.")
        time.sleep(1.0)
        self._exec("MODE=IMP")

    def _exec_tolerant(self, cmd):
        """_exec for shutdown paths: never raises, reports success."""
        try:
            resp = self.instrument.query(cmd).strip(" \t\r\n\x00\x10")
        except Exception as e:
            print(f"  Warning: {cmd} failed during shutdown: {e}")
            return False
        if resp != "OK":
            print(f"  Warning: {cmd} answered {resp!r} during shutdown.")
            return False
        return True

    # --- SRQ ------------------------------------------------------------

    def _wait_for_srq(self, timeout_s, context):
        """Block until the analyzer asserts SRQ, then clear the request.

        Completion is SRQ-driven precisely so we never poll ZTSTAT?/ZRE?
        during a measurement - the manual warns that the extra bus traffic
        degrades point accuracy. On timeout we abort first, and only THEN
        query ZTSTAT? (legitimate once the measurement is stopped).
        """
        inst = self.instrument
        try:
            inst.wait_for_srq(int(timeout_s * 1000))
        except Exception as exc:
            state = self.abort_and_diagnose()
            raise TimeoutError(
                f"No SRQ within {timeout_s:.0f} s during {context}. "
                f"Aborted (MBK). ZTSTAT? reports: {state}. "
                f"Underlying: {exc}"
            )

        self._task_may_be_running = False
        stb = inst.read_stb()
        if not stb & 0x01:
            print(f"  WARNING: SRQ during {context} with STB=0x{stb:02X} "
                  f"(bit 0 not set).")
        return stb

    def _wait_for_cal_task(self, timeout_s, context, stop_event=None):
        """SRQ wait for a calibration task (REF, SL_SHORT, SL_100) that
        cannot hang: the wait runs in CAL_WAIT_SLICE_S slices, and between
        slices
          * the status byte is serial-polled: RQS ends the wait like an
            SRQ caught in a slice, and MAV (a response already waiting - e.g. an
            immediate error ack to a refused ZRUNCAL, which may never raise
            SRQ) ends it too, so the caller reads that ack at once;
          * stop_event (the screen's Cancel) aborts the task with MBK and
            raises CalibrationCancelled; the caller's error path then parks
            the generator (ACV=0, ZCONSPL=0).
        On the overall timeout it aborts and raises TimeoutError, as
        _wait_for_srq does. Measurements keep _wait_for_srq (no polling
        while a point integrates)."""
        inst = self.instrument
        deadline = time.monotonic() + timeout_s
        # _task_may_be_running stays True on every return from here: the
        # caller clears it only once the ack read() has returned, so a read
        # that times out (MAV's meaning is unconfirmed on the Alpha) still
        # goes through safe_state()'s MBK before ACV=0 / ZCONSPL=0.
        while True:
            if stop_event is not None and stop_event.is_set():
                # Status byte FIRST: if the task finished just as Cancel was
                # pressed, its ack is waiting - read it normally rather than
                # let MBK's query consume it and put the acks out of step.
                try:
                    stb = inst.read_stb()
                except Exception:
                    stb = 0
                if stb & (STB_RQS | STB_MAV):
                    print(f"  {context}: finished as Cancel was pressed "
                          f"(STB=0x{stb:02X}); reading its ack.")
                    return stb
                state = self.abort_and_diagnose()
                raise CalibrationCancelled(
                    f"{context} cancelled by the operator. Aborted (MBK). "
                    f"ZTSTAT? reports: {state}.")
            remaining = max(0.0, deadline - time.monotonic())
            slice_ms = max(1, int(min(CAL_WAIT_SLICE_S, remaining) * 1000))
            try:
                inst.wait_for_srq(slice_ms)
            except Exception:
                pass          # no SRQ in this slice: look at the status byte
            else:
                stb = inst.read_stb()
                if not stb & STB_TASK_DONE:
                    print(f"  WARNING: SRQ during {context} with "
                          f"STB=0x{stb:02X} (bit 0 not set).")
                return stb
            try:
                stb = inst.read_stb()
            except Exception as exc:
                state = self.abort_and_diagnose()
                raise TimeoutError(
                    f"Status poll failed during {context}. Aborted (MBK). "
                    f"ZTSTAT? reports: {state}. Underlying: {exc}")
            # Bit 0 alone is NOT trusted here (it may be stale from the
            # immediately-acknowledged *_INIT); RQS or MAV both mean there
            # is an answer to read, so the caller's read() cannot hang.
            if stb & (STB_RQS | STB_MAV):
                if not stb & STB_RQS:
                    print(f"  {context}: a response is waiting without SRQ "
                          f"(STB=0x{stb:02X}); reading it now.")
                return stb
            if time.monotonic() >= deadline:
                state = self.abort_and_diagnose()
                raise TimeoutError(
                    f"No SRQ within {timeout_s:.0f} s during {context}. "
                    f"Aborted (MBK). ZTSTAT? reports: {state}.")

    def abort_and_diagnose(self):
        """Abort any running task, then read the task state ONCE.

        ZTSTAT? is a post-fault diagnostic only. Calling it here is safe
        because MBK has already stopped the measurement.
        """
        state = "unavailable"
        try:
            # MBK is an executable command: read its ack like any other,
            # but tolerate a non-OK answer - we are already in a fault path.
            resp = self.instrument.query("MBK").strip(" \t\r\n\x00\x10")
            self._task_may_be_running = False
            if resp != "OK":
                state = f"MBK answered {resp!r}; "
        except Exception as e:
            return f"MBK failed: {e}"
        try:
            state = self.instrument.query("ZTSTAT?").strip()
        except Exception as e:
            state = f"ZTSTAT? failed: {e}"
        return state

    # --- calibration ----------------------------------------------------

    def run_reference_calibration(self, timeout_s=SRQ_TIMEOUT_CAL_S,
                                  stop_event=None):
        """Run ZRUNCAL REF. The SAMPLE MUST BE DISCONNECTED.

        REF calibrates the internal reference capacitors against each other
        (~30 s). It is the before-measurement subset of the full ZRUNCAL=ALL
        converter calibration (~1 h, monthly maintenance, not done here).

        CONCHECK runs automatically ahead of REF - do NOT invoke it here.

        Same sequence as Frequency_Scan_AlphaAN_GUI.py's copy, except the
        SRQ wait: _wait_for_cal_task reads an immediate error ack at once
        and honours stop_event (the screen's Cancel).
        """
        inst = self.instrument

        # The manual recommends *RST immediately before a calibration so that
        # prior user settings cannot perturb it. *RST preserves the stored
        # calibration tables; it only resets operating parameters (and parks
        # the generator at ACV=0, DCE=0).
        # *RST is IEEE-488.2 common: it leaves NO Novocontrol ack (JUMP
        # queries *IDN? right after with no stale response in between).
        self._reset_and_set_impedance_mode()

        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(f"ZG4 not present (INTTYP code {code}).")

        # REF_INIT acknowledges IMMEDIATELY (no SRQ) - it only arms the
        # calibration. REF is the actual task: its 'OK'/error ack is written
        # to the buffer only when the task finishes, signalled by SRQ.
        self._exec("ZRUNCAL=REF_INIT")

        inst.read_stb()        # clear stale status before the task starts
        self._task_may_be_running = True
        inst.write("ZRUNCAL=REF")
        self._wait_for_cal_task(timeout_s, "REF calibration", stop_event)
        ack = inst.read().strip(" \t\r\n\x00\x10")
        self._task_may_be_running = False   # the ack arrived: task over
        if ack != "OK":
            meaning = CMD_ERROR_MEANINGS.get(ack, "unrecognized response")
            raise RuntimeError(
                f"REF calibration finished with {ack!r} ({meaning})."
            )

    def begin_load_short_calibration(self):
        """*RST, confirm the ZG4, then arm the load-short calibration.

        Same preamble as run_reference_calibration (the manual recommends
        *RST immediately before a calibration; it preserves the stored
        calibration tables). ZRUNCAL=SL_INIT acknowledges IMMEDIATELY, like
        REF_INIT. Command arguments from JUMP (see the module docstring);
        not yet checked against the Alpha manual.
        """
        inst = self.instrument
        self._reset_and_set_impedance_mode()

        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(f"ZG4 not present (INTTYP code {code}).")

        self._exec("ZRUNCAL=SL_INIT")

    def run_load_short_step(self, step, timeout_s=SRQ_TIMEOUT_SL_S,
                            stop_event=None):
        """Run one load-short part: 'SL_SHORT' (short standard connected)
        or 'SL_100' (100 Ohm load standard connected), after
        begin_load_short_calibration. Like ZRUNCAL=REF: written directly,
        completion by SRQ, then the task's 'OK'/error ack is read. The
        wait cannot hang for SRQ_TIMEOUT_SL_S on an immediate refusal
        (MAV is polled) and stop_event cancels it (see _wait_for_cal_task).
        """
        if step not in LOAD_SHORT_STEPS:
            raise ValueError(f"Unknown load-short step {step!r}; "
                             f"expected one of {LOAD_SHORT_STEPS}.")
        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")

        inst.read_stb()        # clear stale status before the task starts
        self._task_may_be_running = True
        inst.write(f"ZRUNCAL={step}")
        self._wait_for_cal_task(timeout_s, f"load-short calibration ({step})",
                                stop_event)
        ack = inst.read().strip(" \t\r\n\x00\x10")
        self._task_may_be_running = False   # the ack arrived: task over
        if ack != "OK":
            meaning = CMD_ERROR_MEANINGS.get(ack, "unrecognized response")
            raise RuntimeError(
                f"Load-short calibration {step} finished with {ack!r} "
                f"({meaning})."
            )

    # --- measurement setup ----------------------------------------------

    def initialize_instrument(self, p):
        """Reset, confirm the interface, then apply measurement settings.

        *RST does NOT delete stored calibration data. Test interfaces are
        auto-identified on connection and the mainframe reloads that
        interface's latest cal set from internal memory, which is why no
        recalibration is needed after a reset or an interface swap. So a
        sweep here runs on the previous REF calibration, by design.
        """
        print("\n--- [Backend] Initializing Novocontrol Alpha-AN ---")
        self.params = p

        validate_alpha_parameters(p["frequencies"], p["acv"], p["mtm"],
                                  p["wire_mode"])

        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")

        self._reset_and_set_impedance_mode()

        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(f"ZG4 not present (INTTYP code {code}).")

        # Settings AFTER any calibration: a calibration task may change
        # MTM, ZTDEL, ACV, DCE, IAC and IRG. ZREFMODE/ZLLCOR/ZSLCAL already
        # hold these values after *RST; we write them anyway - defensive
        # against a non-default state, and it documents intent at the call
        # site. Each _exec consumes the command's 'OK' ack, which also
        # fully synchronizes the bus (no settle sleeps needed).
        #
        # ZREFMODE (verified against the manual): negative = auto reference
        # mode, magnitude = max number of internal reference capacitors
        # combined. -3 = auto, up to 3 caps = the mes_def's "Reference
        # Measurement: Always, 3 auto capacitors max".
        self._exec("ZREFMODE=-3")
        self._exec("ZLLCOR=1")      # low-loss correction on
        self._exec("ZSLCAL=1")      # apply stored load-short cal data
        # "Low capacity open calibration: Off" (mes_def): RESOLVED - there
        # is no analyzer command for it. Open calibration is a host-software
        # correction (WinDETA measures Z_open and computes
        # 1/Zs = 1/Zmes - 1/Zopen itself). This program does not implement
        # it, so the mes_def's "Off" is matched by construction.
        self._exec(f"FRS={p['wire_mode']}")
        self._exec("DRS=0 0")       # driven shields off

        self._exec(f"ACV={p['acv']:.6g}")
        self._exec(f"MTM={p['mtm']:.6g}")

        # DCV / DCE are NEVER sent: this module does not use DC bias.

        print(f"  Configured: ACV={p['acv']} Vrms, MTM={p['mtm']} s, "
              f"FRS={p['wire_mode']}-wire")

    # --- per-point -------------------------------------------------------

    def measure_point(self, freq, timeout_s=None):
        """Set frequency, trigger one point, wait for SRQ, read the result.

        Returns (Zs', Zs'', f_actual, status, ref). The caller must use
        f_actual - not the requested frequency - for the conversion.
        """
        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")
        if timeout_s is None:
            timeout_s = point_timeout_s(freq, self.params.get('mtm', 0.0))

        self._exec(f"GFR={freq:.6e}")
        # Clear any stale status immediately before triggering, so a fast
        # point cannot let wait_for_srq latch onto a leftover completion.
        inst.read_stb()
        self._task_may_be_running = True
        # MST leaves NO buffered ack (unlike set commands): completion is
        # signalled by SRQ and the result is fetched with ZRE?.
        inst.write("MST")
        self._wait_for_srq(timeout_s, f"measurement at {freq:.6g} Hz")

        # ZRE? also clears status bit 0.
        return parse_zre(inst.query("ZRE?"))

    # --- shutdown --------------------------------------------------------

    def safe_state(self):
        """Drive the generator to a safe state. Never raises.

        Safe for any error handler to call. MBK first (a task may be
        mid-flight), then park the AC generator (ACV=0) and disconnect the
        current input to high impedance (ZCONSPL=0) - the manual's defined
        safe idle state. *RST would also park the generator, but it is
        attempted separately in close_instrument so a failing *RST cannot
        prevent the ACV=0 that actually matters.
        """
        inst = self.instrument
        if inst is None:
            return
        if self._task_may_be_running:
            if self._exec_tolerant("MBK"):
                self._task_may_be_running = False
        for cmd in ("ACV=0", "ZCONSPL=0"):
            self._exec_tolerant(cmd)

    def close_instrument(self):
        """Safe-state, reset, release the session. Never leaves output live."""
        print("--- [Backend] Closing Alpha-AN connection. ---")
        if not self.instrument:
            return
        try:
            self.safe_state()
            try:
                # *RST parks the generator (ACV=0, DCE=0) and preserves the
                # stored calibration tables.
                self.instrument.write("*RST")
                time.sleep(0.2)
            except Exception as e:
                print(f"  Warning: *RST failed ({e}); escalating to RSTH.")
                try:
                    self.instrument.write("RSTH")
                    time.sleep(0.5)
                except Exception as e2:
                    print(f"  Warning: RSTH also failed: {e2}")
        except Exception as e:
            print(f"  Warning during shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  Alpha-AN connection closed.")
            except Exception as e:
                print(f"  Warning closing VISA session: {e}")
            finally:
                self.instrument = None


# ===============================================================================
# GUI
# ===============================================================================
class CalibrationGUI:
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    CLR_PASS = '#2E7D32'
    CLR_WARN = '#B23A2E'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_HEADLINE = ('Segoe UI', FONT_SIZE_BASE + 7, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    CONSOLE_MAX_LINES = 4000
    LOGO_SIZE = 90

    STEPS = ["1  Connect & check", "2  Choose", "3  REF", "4  Load-short",
             "5  Open", "6  Done"]
    # step index -> name used in self.plan
    STEP_REF, STEP_SL, STEP_OPEN, STEP_DONE = 2, 3, 4, 5

    def __init__(self, root):
        self.root = root
        self.root.title("Alpha-AN: Calibration (guided) - 64-bit (VISA)")
        self.root.geometry("1500x940")
        self.root.minsize(1200, 800)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.backend = AlphaAN_Backend()
        if self.backend.rm is not None:
            self.backend.rm = RecordingRM(self.backend.rm, self._on_record)
        self.connected = False
        self.idn = ""
        self.dest_folder = ""
        self.tag = ""
        self.paths = {}
        self._console_buffer = []          # records before the file exists
        self.worker = None
        self.busy = False
        self.queue = queue.Queue()
        self._poll_id = None
        self.open_rows = []
        self.open_freqs = []
        self.open_params = {}
        self.plan = []                     # step indices chosen in step 2
        self.results = {}                  # what happened, for the Done screen
        self.sl_phase = 0                  # index into LOAD_SHORT_STEPS
        self.stop_event = threading.Event()
        self._close_requested = False
        self._release_pending = False      # park + release once idle
        self.step = 0
        self.logo_image = None

        self._setup_styles()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        # Terminal output (print, warnings, tracebacks) also goes to the console.
        install_terminal_log(self)
        self._poll_id = self.root.after(150, self._poll_queue)

    # ------------------------------------------------------------ styles
    def _setup_styles(self):
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('TFrame', background=self.CLR_BG_DARK)
        s.configure('TLabel', background=self.CLR_BG_DARK,
                    foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        s.configure('TCheckbutton', background=self.CLR_BG_DARK,
                    foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        s.configure('TRadiobutton', background=self.CLR_BG_DARK,
                    foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        s.configure('TButton', font=self.FONT_BASE, padding=(10, 8),
                    foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        s.map('TButton', background=[('active', self.CLR_ACCENT_GOLD)],
              foreground=[('active', self.CLR_TEXT_DARK)])
        s.configure('Go.TButton', font=('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold'),
                    background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK,
                    padding=(14, 10))
        s.configure('Danger.TButton', background=self.CLR_ACCENT_RED,
                    foreground=self.CLR_FG_LIGHT)
        s.configure('TCombobox', font=self.FONT_BASE)
        s.configure('TNotebook', background=self.CLR_BG_DARK)

    # ------------------------------------------------------------ layout
    def _build(self):
        self._build_header()
        body = tk.Frame(self.root, bg=self.CLR_BG_DARK)
        body.pack(fill='both', expand=True, padx=8, pady=6)

        left = tk.Frame(body, bg=self.CLR_BG_DARK, width=640)
        left.pack(side='left', fill='both', expand=False)
        left.pack_propagate(False)
        right = tk.Frame(body, bg=self.CLR_BG_DARK)
        right.pack(side='left', fill='both', expand=True, padx=(8, 0))

        # headline banner
        self.headline = tk.Label(left, text="Not connected.", anchor='w',
                                 justify='left', wraplength=600,
                                 bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT,
                                 font=self.FONT_HEADLINE, padx=12, pady=10)
        self.headline.pack(fill='x', pady=(0, 6))

        # step strip
        strip = tk.Frame(left, bg=self.CLR_BG_DARK)
        strip.pack(fill='x')
        self.step_labels = []
        for i, name in enumerate(self.STEPS):
            lbl = tk.Label(strip, text=name, bg=self.CLR_BG_DARK,
                           fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL,
                           padx=6, pady=3)
            lbl.pack(side='left')
            self.step_labels.append(lbl)

        # stacked screens
        self.screen_host = tk.Frame(left, bg=self.CLR_BG_DARK)
        self.screen_host.pack(fill='both', expand=True, pady=(6, 0))
        self.screens = []
        for builder in (self._screen_connect, self._screen_choose,
                        self._screen_ref, self._screen_load_short,
                        self._screen_open, self._screen_done):
            fr = tk.Frame(self.screen_host, bg=self.CLR_BG_DARK)
            builder(fr)
            self.screens.append(fr)

        # right: plot + console
        self._build_plot(right)
        self._build_console(right)
        self._show_step(0)

    def _build_header(self):
        hf = tk.Frame(self.root, bg=self.CLR_HEADER)
        hf.pack(side='top', fill='x')
        self._try_logo(hf)
        Label(hf, text="Alpha-AN: Calibration   [64-bit edition, VISA]",
              bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD,
              font=('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
              ).pack(side='left', padx=16, pady=10)
        Label(hf, text=f"Version: {PROGRAM_VERSION}", bg=self.CLR_HEADER,
              fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL
              ).pack(side='right', padx=20, pady=10)

    def _try_logo(self, parent):
        if not PIL_AVAILABLE:
            return
        here = os.path.dirname(os.path.abspath(__file__))
        for rel in (("..", "..", "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"),
                    ("..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")):
            p = os.path.join(here, *rel)
            if os.path.exists(p):
                try:
                    img = Image.open(p)
                    img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER)
                    self.logo_image = ImageTk.PhotoImage(img)
                    Label(parent, image=self.logo_image, bg=self.CLR_HEADER
                          ).pack(side='left', padx=(12, 0), pady=4)
                except Exception:
                    pass
                return

    def _build_plot(self, parent):
        box = LabelFrame(parent, text='Empty cell (open correction data)',
                         bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT,
                         font=self.FONT_TITLE)
        box.pack(fill='both', expand=True)
        self.fig = Figure(figsize=(7, 4.6), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_c = self.fig.add_subplot(121)
        self.ax_g = self.fig.add_subplot(122)
        self._style_axes()
        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=box)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=4, pady=4)

    def _style_axes(self):
        self.ax_c.set_title("Empty cell: C_open", fontsize=10)
        self.ax_c.set_xscale('log')
        self.ax_c.set_ylabel("C_open (pF)")
        self.ax_g.set_title("Empty cell: |G_open|", fontsize=10)
        self.ax_g.set_xscale('log')
        self.ax_g.set_yscale('log')
        self.ax_g.set_ylabel("|G_open| (S)")
        for ax in (self.ax_c, self.ax_g):
            ax.set_xlabel("Frequency (Hz)")
            ax.grid(True, which='both', alpha=0.3)

    def _build_console(self, parent):
        box = LabelFrame(parent, text='Log', bg=self.CLR_BG_DARK,
                         fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        box.pack(fill='both', expand=False)
        self.console = scrolledtext.ScrolledText(
            box, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE, wrap='word', bd=0, height=13)
        self.console.pack(fill='both', expand=True, padx=4, pady=4)
        self.show_cmds = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Show every command and reply in this log "
                        "(always written to the console file)",
                        variable=self.show_cmds).pack(anchor='w', padx=6, pady=(0, 4))
        self.log("Ready. Choose a folder, pick the Alpha, press Connect & check.")
        self.log(format_cal_age(read_last_ref_cal()))
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not installed. pip install pyvisa")

    # ------------------------------------------------------------ screens
    def _note(self, fr, text, pady=(0, 10)):
        Label(fr, text=text, font=self.FONT_SUB_LABEL, wraplength=600,
              justify='left', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT
              ).pack(anchor='w', pady=pady)

    def _screen_connect(self, fr):
        Label(fr, text="Where should the files go?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(2, 8))
        self.folder_var = tk.StringVar(value="(no folder chosen)")
        ttk.Label(row, textvariable=self.folder_var, font=self.FONT_SUB_LABEL,
                  wraplength=430).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text="Browse…", command=self._browse).pack(side='right')

        Label(fr, text="Which instrument is the Alpha?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row2 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row2.pack(fill='x', pady=(2, 8))
        self.visa_cb = ttk.Combobox(row2, width=52, font=self.FONT_SUB_LABEL)
        self.visa_cb.pack(side='left', fill='x', expand=True)
        self.visa_cb.set(f"GPIB0::{DEFAULT_ALPHA_PAD}::INSTR")
        ttk.Button(row2, text="Scan", command=self._scan_visa).pack(side='right', padx=(6, 0))

        self.btn_connect = ttk.Button(fr, text="Connect & check  ▶", style='Go.TButton',
                                      command=self._do_connect)
        self.btn_connect.pack(anchor='w', pady=(4, 10))

        self._note(fr, "Checking reads the identity and the sample interface "
                   "(*IDN?, INTTYP?). It changes nothing in the analyzer. "
                   "Close WinALPHA / WinDETA first so nothing else uses the "
                   "GPIB bus.", pady=(0, 0))

        self.state_text = tk.Text(fr, height=9, width=70, font=self.FONT_CONSOLE,
                                  bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                  bd=0, state='disabled', wrap='word')
        self.state_text.pack(fill='x', pady=(6, 6))

        row3 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row3.pack(fill='x', pady=(4, 0))
        self.btn_next1 = ttk.Button(row3, text="Choose the calibrations  ▶",
                                    style='Go.TButton',
                                    command=lambda: self._show_step(1), state='disabled')
        self.btn_next1.pack(side='left')

    def _screen_choose(self, fr):
        Label(fr, text="What should be done?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        self.do_ref_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="REF calibration (about 30 s, sample disconnected)",
                        variable=self.do_ref_var).pack(anchor='w', pady=(4, 0))
        self.do_sl_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="Load-short calibration (short + 100 Ohm load "
                        "standards; not yet tried on this analyzer)",
                        variable=self.do_sl_var).pack(anchor='w', pady=(4, 0))
        self.do_open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="Open correction: measure the empty cell and "
                        "save it to a file", variable=self.do_open_var,
                        command=self._toggle_open).pack(anchor='w', pady=(4, 8))

        Label(fr, text="Frequencies for the open measurement", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        self.freq_cb = ttk.Combobox(fr, width=40, state='readonly',
                                    values=list(FREQ_PRESETS) + [CUSTOM_FREQ_LABEL])
        self.freq_cb.set(DEFAULT_FREQ_PRESET)
        self.freq_cb.bind("<<ComboboxSelected>>", self._on_freq_choice)
        self.freq_cb.pack(anchor='w', pady=(2, 2))
        self.custom_freq_entry = tk.Text(fr, height=3, width=70, font=self.FONT_CONSOLE,
                                         bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, bd=0)
        self.custom_freq_entry.insert('1.0', "20, 100, 1000, 10000, 100000, 1000000")
        self.custom_freq_entry.pack(fill='x', pady=(2, 4))
        self.custom_freq_entry.config(state='disabled')
        self._note(fr, "Use the list the scans will use: the open data is "
                   "matched by requested frequency.", pady=(0, 8))

        Label(fr, text="Where is the cell right now?", font=self.FONT_TITLE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        row2 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row2.pack(fill='x')
        self.where_var = tk.StringVar(value="Sample cell assembled, room temperature")
        self.where_cb = ttk.Combobox(row2, textvariable=self.where_var, width=36,
                                     values=["Sample cell assembled, room temperature",
                                             "Cell inside the cryostat, cold",
                                             "Bench cell at the ZG4",
                                             "Other (type)"])
        self.where_cb.pack(side='left')
        ttk.Label(row2, text="   Temperature (K):").pack(side='left')
        self.temp_var = tk.StringVar(value="300")
        ttk.Entry(row2, textvariable=self.temp_var, width=8).pack(side='left')

        # advanced (hidden)
        self.adv_shown = False
        self.btn_adv = ttk.Button(fr, text="Advanced ▸", command=self._toggle_adv)
        self.btn_adv.pack(anchor='w', pady=(10, 0))
        self.adv = tk.Frame(fr, bg=self.CLR_BG_DARK)
        g = self.adv
        r = 0
        self.acv_var = tk.StringVar(value=f"{DEFAULT_OPEN_ACV:g}")
        self.mtm_var = tk.StringVar(value=f"{DEFAULT_OPEN_MTM:g}")
        self.wire_var = tk.StringVar(value=DEFAULT_OPEN_WIRES)
        ttk.Label(g, text="Open measurement AC level (Vrms), must match the scans:"
                  ).grid(row=r, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.acv_var, width=8).grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(g, text="Integration time MTM (s):").grid(row=r, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.mtm_var, width=8).grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(g, text="ZG4 wire mode:").grid(row=r, column=0, sticky='w')
        ttk.Combobox(g, textvariable=self.wire_var, values=WIRE_MODES, width=6,
                     state='readonly').grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(g, text="Not offered: the full converter calibration "
                  "(ZRUNCAL=ALL, about 1 h, monthly maintenance). Use WinALPHA "
                  "for it.", wraplength=560, font=self.FONT_SUB_LABEL
                  ).grid(row=r, column=0, columnspan=3, sticky='w', pady=(6, 0))

        row3 = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row3.pack(fill='x', pady=(14, 0))
        ttk.Button(row3, text="◀ Back", command=lambda: self._show_step(0)).pack(side='left')
        ttk.Button(row3, text="Start the first step  ▶", style='Go.TButton',
                   command=self._go_plan).pack(side='left', padx=(10, 0))

    def _screen_ref(self, fr):
        Label(fr, text="REF CALIBRATION", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        Label(fr, text=("DISCONNECT the sample from the ZG4. Nothing may be "
                        "connected to the sample interface. Close WinALPHA / "
                        "WinDETA so nothing else uses the GPIB bus."),
              font=self.FONT_BASE, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(4, 8))
        self.ref_age = tk.Label(fr, text=format_cal_age(read_last_ref_cal()),
                                font=self.FONT_TITLE, bg=self.CLR_HEADER,
                                fg=self.CLR_FG_LIGHT, padx=10, pady=8,
                                anchor='w', justify='left')
        self.ref_age.pack(fill='x', pady=(0, 8))
        self._note(fr, "Continue resets the analyzer (*RST keeps the stored "
                   "calibrations), measures its internal reference capacitors "
                   "against each other (about 30 s) and REPLACES the stored REF "
                   "calibration inside the instrument. Every later sweep, from "
                   "PICA or WinDETA, uses the new one. The time is recorded for "
                   "the scan modules' calibration-age line.")
        self.progress_ref = ttk.Label(fr, text="", font=self.FONT_SUB_LABEL)
        self.progress_ref.pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(10, 0))
        ttk.Button(row, text="◀ Back", command=lambda: self._show_step(1)).pack(side='left')
        self.btn_ref_go = ttk.Button(row, text="Continue: run REF now  ▶",
                                     style='Go.TButton', command=self._do_ref)
        self.btn_ref_go.pack(side='left', padx=(10, 0))
        self.btn_ref_cancel = ttk.Button(row, text="Cancel", style='Danger.TButton',
                                         command=self._cancel_cal, state='disabled')
        self.btn_ref_cancel.pack(side='left', padx=(10, 0))
        ttk.Button(row, text="Skip", command=lambda: self._skip(self.STEP_REF)
                   ).pack(side='left', padx=(10, 0))

    def _screen_load_short(self, fr):
        Label(fr, text="LOAD-SHORT CALIBRATION", font=self.FONT_HEADLINE,
              bg=self.CLR_BG_DARK, fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        self.sl_instr = Label(fr, text="", font=self.FONT_BASE, wraplength=600,
                              justify='left', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT)
        self.sl_instr.pack(anchor='w', pady=(4, 8))
        self._note(fr, "Two parts, each REPLACING stored data inside the analyzer: "
                   "first with the short standard, then with the 100 Ohm load "
                   "standard. The scan modules apply the result with ZSLCAL=1. "
                   "Commands ZRUNCAL=SL_INIT, SL_SHORT, SL_100 are taken from "
                   "JUMP (github.com/JMoVS/JUMP); not yet checked against the "
                   "Alpha manual or run on this analyzer.")
        self.progress_sl = ttk.Label(fr, text="", font=self.FONT_SUB_LABEL)
        self.progress_sl.pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(10, 0))
        self.btn_sl_go = ttk.Button(row, text="", style='Go.TButton',
                                    command=self._do_load_short)
        self.btn_sl_go.pack(side='left')
        self.btn_sl_cancel = ttk.Button(row, text="Cancel", style='Danger.TButton',
                                        command=self._cancel_cal, state='disabled')
        self.btn_sl_cancel.pack(side='left', padx=(10, 0))
        ttk.Button(row, text="Skip", command=lambda: self._skip(self.STEP_SL)
                   ).pack(side='left', padx=(10, 0))

    def _screen_open(self, fr):
        Label(fr, text="OPEN (EMPTY CELL)", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        Label(fr, text=("Remove the sample. Leave the cell connected to the ZG4 "
                        "and assembled exactly as it will be during the "
                        "measurement (same leads, same electrode spacing if you "
                        "can), with no sample between the electrodes. Hands away."),
              font=self.FONT_BASE, wraplength=600, justify='left',
              bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(anchor='w', pady=(4, 8))
        self._note(fr, "The Alpha has no command for the open correction: this "
                   "step only MEASURES the empty cell and writes Z_open to "
                   "<tag>_open_Zopen.txt. Nothing is stored in the analyzer. A "
                   "scan applies it as 1/Zs = 1/Zmes - 1/Zopen.")
        self.progress_open = ttk.Label(fr, text="", font=self.FONT_SUB_LABEL)
        self.progress_open.pack(anchor='w')
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(10, 0))
        self.btn_open_go = ttk.Button(row, text="Continue: measure the empty cell  ▶",
                                      style='Go.TButton', command=self._do_open)
        self.btn_open_go.pack(side='left')
        self.btn_open_stop = ttk.Button(row, text="Stop", style='Danger.TButton',
                                        command=self._stop_open, state='disabled')
        self.btn_open_stop.pack(side='left', padx=(10, 0))
        ttk.Button(row, text="Skip", command=lambda: self._skip(self.STEP_OPEN)
                   ).pack(side='left', padx=(10, 0))

    def _screen_done(self, fr):
        Label(fr, text="DONE", font=self.FONT_HEADLINE, bg=self.CLR_BG_DARK,
              fg=self.CLR_ACCENT_GOLD).pack(anchor='w')
        self.done_text = tk.Text(fr, height=16, width=70, font=self.FONT_CONSOLE,
                                 bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT,
                                 bd=0, state='disabled', wrap='word')
        self.done_text.pack(fill='x', pady=(6, 6))
        row = tk.Frame(fr, bg=self.CLR_BG_DARK)
        row.pack(fill='x', pady=(6, 0))
        ttk.Button(row, text="Start again (new file set)",
                   command=self._restart).pack(side='left')
        ttk.Button(row, text="Open the folder", command=self._open_folder
                   ).pack(side='left', padx=(10, 0))

    # ------------------------------------------------------------ helpers
    def _show_step(self, i):
        self.step = i
        for fr in self.screens:
            fr.pack_forget()
        self.screens[i].pack(fill='both', expand=True)
        for j, lbl in enumerate(self.step_labels):
            lbl.config(bg=self.CLR_HEADER if j == i else self.CLR_BG_DARK,
                       fg=self.CLR_ACCENT_GOLD if j == i else self.CLR_FG_LIGHT)
        if i == self.STEP_SL:
            self._show_sl_phase()
        elif i == self.STEP_REF:
            self.ref_age.config(text=format_cal_age(read_last_ref_cal()))

    def _show_sl_phase(self):
        step = LOAD_SHORT_STEPS[self.sl_phase]
        label = LOAD_SHORT_LABELS[step]
        if self.sl_phase == 0:
            text = (f"Connect the {label} to the ZG4 sample interface in place "
                    "of the sample. Press Continue.")
        else:
            text = (f"Short part done. Replace the short with the {label}. "
                    "Press Continue.")
        self.sl_instr.config(text=text)
        self.btn_sl_go.config(text=f"Continue: calibrate with the {label}  ▶")

    def _set_headline(self, text, ok=None):
        fg = self.CLR_FG_LIGHT if ok is None else (self.CLR_PASS if ok else self.CLR_WARN)
        self.headline.config(text=text, fg=fg)

    def _set_text(self, widget, text):
        widget.config(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.config(state='disabled')

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self.console.config(state='normal')
        self.console.insert('end', line + "\n")
        try:
            n = int(self.console.index('end-1c').split('.')[0])
            if n > self.CONSOLE_MAX_LINES:
                self.console.delete('1.0', f'{n - self.CONSOLE_MAX_LINES + 1}.0')
        except tk.TclError:
            pass
        self.console.see('end')
        self.console.config(state='disabled')
        if self.paths.get("log"):
            self._durable_append(self.paths["log"],
                                 datetime.now().strftime("%Y-%m-%d ") + line + "\n")

    def _on_record(self, entry):
        """Recording-proxy callback (may run in the worker thread): queue it."""
        self.queue.put(("record", entry))

    def _write_record(self, entry):
        ts, kind, cmd, reply = entry
        line = f"{ts}\t{kind}\t{cmd}\t{reply}\n"
        if self.paths.get("console"):
            self._durable_append(self.paths["console"], line)
        else:
            self._console_buffer.append(line)
        if self.show_cmds.get():
            self.log(f"  {kind} {cmd}" + (f"  ->  {reply}" if reply else ""))

    @staticmethod
    def _durable_append(path, text):
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _durable_write(self, path, text):
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _browse(self):
        p = filedialog.askdirectory()
        if p:
            self.dest_folder = p
            self.folder_var.set(p)
            self.log(f"Folder: {p}")

    def _visa_addr(self):
        return self.visa_cb.get().split("  ->")[0].strip()

    def _scan_visa(self):
        """Identity-aware instrument scan; auto-selects a Novocontrol Alpha.
        EOI-only framing (the Alpha uses no newline terminator). A board
        left not-Controller-In-Charge by WinALPHA/WinDETA gets ONE Interface
        Clear, then the query is retried once."""
        if self.busy:
            self.log("Busy: wait for the current step to finish.")
            return
        if not PYVISA_AVAILABLE or self.backend.rm is None:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            return
        rm = self.backend.rm

        def _identify(res):
            with rm.open_resource(res) as dev:
                dev.timeout = 2000
                dev.read_termination = ""
                dev.write_termination = ""
                dev.send_end = True
                return dev.query("*IDN?").strip(" \t\r\n\x00\x10")

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        found, pick, default_label = [], None, None
        cleared_boards = set()
        try:
            for r in rm.list_resources():
                idn = "no response"
                try:
                    idn = _identify(r)
                except Exception as exc:
                    board = gpib_board_of(r)
                    if (visa_error_code(exc) == VI_ERROR_NCIC
                            and board is not None
                            and board not in cleared_boards):
                        cleared_boards.add(board)
                        self.log(f"  GPIB{board} is not Controller-In-Charge "
                                 f"(WinALPHA/WinDETA may have taken the bus). "
                                 f"Sending one Interface Clear...")
                        try:
                            self.backend.send_interface_clear(board)
                            idn = _identify(r)
                        except Exception:
                            pass
                label = f"{r}  ->  {idn}"
                found.append(label)
                self.log("  " + label)
                upper = idn.upper()
                if pick is None and ("NOVOCONTROL" in upper or "ALPHA" in upper):
                    pick = label
                if (default_label is None and r.upper().startswith("GPIB")
                        and f"::{DEFAULT_ALPHA_PAD}::" in r):
                    default_label = label
        except Exception as e:
            self.log(f"ERROR: VISA scan failed: {e}")
            return
        self.visa_cb['values'] = found
        if pick:
            self.visa_cb.set(pick)
            self.log("Alpha-AN auto-selected.")
        elif default_label:
            self.visa_cb.set(default_label)
            self.log(f"WARNING: no instrument identified itself as an Alpha; "
                     f"selected address {DEFAULT_ALPHA_PAD}. Connect will "
                     f"confirm it is an Alpha with a ZG4.")
        elif not found:
            self.log("No VISA instruments found.")

    def _on_freq_choice(self, _event=None):
        custom = self.freq_cb.get() == CUSTOM_FREQ_LABEL
        self.custom_freq_entry.config(state='normal' if custom else 'disabled')

    def _toggle_open(self):
        state = 'readonly' if self.do_open_var.get() else 'disabled'
        self.freq_cb.config(state=state)
        if not self.do_open_var.get():
            self.custom_freq_entry.config(state='disabled')
        else:
            self._on_freq_choice()

    def _selected_open_freqs(self):
        choice = self.freq_cb.get()
        if choice == CUSTOM_FREQ_LABEL:
            return parse_frequency_list(self.custom_freq_entry.get('1.0', 'end'))
        return FREQ_PRESETS[choice]

    def _toggle_adv(self):
        self.adv_shown = not self.adv_shown
        if self.adv_shown:
            self.adv.pack(fill='x', pady=(6, 0))
            self.btn_adv.config(text="Advanced ▾")
        else:
            self.adv.pack_forget()
            self.btn_adv.config(text="Advanced ▸")

    # ------------------------------------------------------------ worker plumbing
    def _run_worker(self, fn, *args):
        if self.busy:
            self.log("Busy: wait for the current step to finish.")
            return
        self.busy = True

        def target():
            try:
                fn(*args)
            except Exception as e:
                # Park the generator before reporting; never raises.
                try:
                    self.backend.safe_state()
                except Exception:
                    pass
                self.queue.put(("error", f"{e}\n{traceback.format_exc()}"))
            finally:
                self.queue.put(("idle", None))
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _poll_queue(self):
        try:
            self._drain_queue()
        finally:
            self._poll_id = self.root.after(150, self._poll_queue)

    def _drain_queue(self):
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_queue_item(kind, payload)
            except Exception as e:
                print(f"Queue handler error ({kind}): {e}")
                if kind == "idle":
                    self.busy = False

    def _handle_queue_item(self, kind, payload):
        if kind == "record":
            self._write_record(payload)
        elif kind == "log":
            self.log(payload)
        elif kind == "headline":
            self._set_headline(*payload)
        elif kind == "progress":
            widget, text = payload
            widget.config(text=text)
        elif kind == "state":
            self._show_state(payload)
        elif kind == "advance":
            self._advance(*payload)
        elif kind == "slphase":
            self.sl_phase = payload
            self._show_sl_phase()
        elif kind == "refage":
            self.ref_age.config(text=payload)
        elif kind == "plot":
            self._update_plot()
        elif kind == "disconnected":
            self.connected = False
        elif kind == "error":
            self.log("ERROR: " + payload.splitlines()[0])
            for ln in payload.splitlines()[1:]:
                self.log("    " + ln)
            self._set_headline("Something went wrong. See the log.", ok=False)
            try:
                self.root.bell()
            except Exception:
                pass
        elif kind == "idle":
            self.busy = False
            self.btn_open_stop.config(state='disabled')
            self.btn_ref_cancel.config(state='disabled')
            self.btn_sl_cancel.config(state='disabled')
            if self._release_pending:
                self._release_pending = False
                self._run_worker(self._worker_release)

    # ------------------------------------------------------------ step 1
    def _do_connect(self):
        if not self.dest_folder:
            self.log("Choose a folder first: every step writes a file.")
            return
        if self.busy:
            self.log("Busy: wait for the current step to finish.")
            return
        self.tag = "AlphaAN_Calibration_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self.dest_folder, self.tag)
        self.paths = {
            "open": base + "_open_Zopen.txt",
            "log": base + "_log.txt",
            "console": base + "_console.txt",
        }
        self._durable_write(self.paths["console"], "Time\tW/Q/R/SRQ\tCommand\tReply\n")
        for ln in self._console_buffer:
            self._durable_append(self.paths["console"], ln)
        self._console_buffer.clear()
        self._durable_write(self.paths["log"], f"# {self.tag} log\n")
        self.log(f"File set: {self.tag}_*.txt in {self.dest_folder}")
        self.results = {}
        self._run_worker(self._worker_connect, self._visa_addr())

    def _worker_connect(self, addr):
        q = self.queue
        q.put(("headline", ("Connecting…", None)))
        if self.backend.instrument is not None:
            self.backend.close_instrument()
            q.put(("disconnected", None))
        idn, code = self.backend.connect(addr)
        q.put(("log", f"Connected: {idn} | ZG4 interface code {code}"))
        q.put(("state", {"idn": idn, "code": code, "visa": addr,
                         "ref": format_cal_age(read_last_ref_cal())}))

    def _show_state(self, st):
        self.connected = True
        self.idn = st["idn"]
        head = f"Connected to the Alpha with a ZG4. {st['ref']}."
        self._set_headline(head)
        lines = [f"IDN:            {st['idn']}",
                 f"VISA:           {st['visa']}",
                 f"Interface:      ZG4 (INTTYP code {st['code']})",
                 f"{st['ref']}",
                 "Load-short data: stored or not, the analyzer cannot report",
                 "it over GPIB; a scan with ZSLCAL=1 answers 'CR' when none",
                 "is stored. Open data: a PC file, never in the analyzer."]
        self._set_text(self.state_text, "\n".join(lines))
        self.log(head)
        self.btn_next1.config(state='normal')

    # ------------------------------------------------------------ step 2 -> plan
    def _go_plan(self):
        if not self.connected:
            self.log("Connect first.")
            return
        plan = []
        if self.do_ref_var.get():
            plan.append(self.STEP_REF)
        if self.do_sl_var.get():
            plan.append(self.STEP_SL)
        if self.do_open_var.get():
            try:
                freqs = list(self._selected_open_freqs())
                acv = float(self.acv_var.get())
                mtm = float(self.mtm_var.get())
                wires = self.wire_var.get()
                # Checked here, BEFORE any open-measurement byte is sent
                # (initialize_instrument checks again).
                validate_alpha_parameters(freqs, acv, mtm, wires)
                float(self.temp_var.get())
            except (ValueError, KeyError) as e:
                self.log(f"Check the inputs: {e}")
                return
            self.open_freqs = freqs
            self.open_params = {"frequencies": freqs, "acv": acv, "mtm": mtm,
                                "wire_mode": wires}
            plan.append(self.STEP_OPEN)
        if not plan:
            self.log("Nothing chosen.")
            return
        self.plan = plan
        self.sl_phase = 0
        self.log("Plan: " + ", ".join(self.STEPS[i].split(None, 1)[1] for i in plan))
        self._show_step(plan[0])

    def _skip(self, step):
        """Skip button. Refused while a step runs on the analyzer. Skipping
        after the short part leaves the analyzer's load-short data half
        replaced, so that is recorded as INCOMPLETE, not as skipped."""
        if self.busy:
            self.log("Busy: wait for the current step to finish.")
            return
        if step == self.STEP_SL and self.sl_phase > 0:
            self.log("WARNING: load-short INCOMPLETE. The short part is stored "
                     "but the 100 Ohm part is not. Redo the load-short before "
                     "any scan that uses ZSLCAL=1.")
            self._set_headline("Load-short left INCOMPLETE. Redo it before "
                               "using ZSLCAL=1.", ok=False)
            try:
                self.root.bell()
            except Exception:
                pass
            self.sl_phase = 0
            self._show_sl_phase()
            self._advance(step, "INCOMPLETE: short stored, 100 Ohm load not; "
                                "redo load-short before any ZSLCAL=1 scan")
            return
        self._advance(step, "skipped")

    def _advance(self, finished_step, outcome):
        """Record the outcome of a step and go to the next chosen one."""
        name = self.STEPS[finished_step].split(None, 1)[1]
        self.results[name] = outcome
        if outcome == "skipped":
            self.log(f"{name}: skipped.")
        later = [s for s in self.plan if s > finished_step]
        if later:
            self._show_step(later[0])
        else:
            self._finish()

    # ------------------------------------------------------------ step 3: REF
    def _do_ref(self):
        if self.busy or not self.connected:
            self.log("Connect first (and wait for any running step).")
            return
        if not messagebox.askokcancel(
            "Reference Calibration",
            "You are about to run a REF (reference) calibration on the "
            f"Alpha-AN at {self._visa_addr()}.\n\n"
            "What it does:\n"
            "  - Measures the analyzer's internal reference capacitors "
            "against each other and stores the result INSIDE the "
            "instrument, replacing the previous REF calibration.\n"
            "  - Every later sweep (from PICA or WinDETA) uses this new "
            "calibration.\n"
            "  - Takes about 30 seconds.\n\n"
            "Before you press OK:\n"
            "  - DISCONNECT the sample from the ZG4. Nothing may be "
            "connected to the sample interface.\n"
            "  - Close WinALPHA / WinDETA so nothing else uses the GPIB "
            "bus.\n\n"
            "OK = start the calibration now.   Cancel = go back, nothing "
            "is sent.",
            icon="warning",
        ):
            return
        self.stop_event.clear()
        self.btn_ref_cancel.config(state='normal')
        self._run_worker(self._worker_ref)

    def _cancel_cal(self):
        """Cancel button of the REF and load-short screens: the running
        task is aborted (MBK) within one wait slice and the generator is
        parked (ACV=0, ZCONSPL=0). The step stays on screen to retry or
        skip."""
        if not self.busy:
            return
        self.stop_event.set()
        self.log("Cancel requested: aborting the calibration task (MBK) and "
                 "parking the analyzer…")

    def _report_cancelled(self, what, progress_widget):
        """Worker side of Cancel: park, then tell the operator (no dialog)."""
        q = self.queue
        try:
            self.backend.safe_state()
        except Exception:
            pass
        q.put(("progress", (progress_widget, f"{what} CANCELLED.")))
        q.put(("log", f"{what} cancelled: task aborted (MBK), analyzer parked "
                      "(ACV=0, ZCONSPL=0). Its stored data may be incomplete: "
                      "run it again before relying on it, or Skip."))
        q.put(("headline", (f"{what} cancelled. Run it again or Skip.", False)))

    def _worker_ref(self):
        q = self.queue
        q.put(("headline", ("REF calibration running (about 30 s)… sample "
                            "disconnected, hands away.", None)))
        q.put(("progress", (self.progress_ref, "REF running…")))
        q.put(("log", "WRITE MOMENT: ZRUNCAL=REF — the analyzer now replaces "
                      "its stored REF calibration."))
        t0 = time.time()
        try:
            self.backend.run_reference_calibration(stop_event=self.stop_event)
        except CalibrationCancelled:
            if self._close_requested:
                raise              # window closing: not a user Cancel
            self._report_cancelled("REF calibration", self.progress_ref)
            return
        except Exception:
            q.put(("progress", (self.progress_ref, "REF FAILED. See the log.")))
            raise
        if self._close_requested:
            raise RuntimeError("Window closed while the REF calibration ran; "
                               "not recorded as done.")
        stamp = write_last_ref_cal()
        age = format_cal_age(stamp)
        q.put(("refage", age))
        q.put(("progress", (self.progress_ref, f"REF done in {time.time() - t0:.0f} s.")))
        q.put(("log", f"Reference calibration complete. {age} (recorded in "
                      f"{cal_state_path()})"))
        q.put(("headline", ("REF calibration stored. Reconnect the sample "
                            "before any sweep.", True)))
        q.put(("advance", (self.STEP_REF, f"done, {age}")))

    # ------------------------------------------------------------ step 4: load-short
    def _do_load_short(self):
        if self.busy or not self.connected:
            self.log("Connect first (and wait for any running step).")
            return
        step = LOAD_SHORT_STEPS[self.sl_phase]
        label = LOAD_SHORT_LABELS[step]
        first = self.sl_phase == 0
        if not messagebox.askokcancel(
            "Load-short calibration",
            f"Is the {label} connected to the ZG4 in place of the sample?\n\n"
            + ("Continue resets the analyzer (*RST keeps the stored "
               "calibrations), arms the load-short calibration "
               "(ZRUNCAL=SL_INIT) and runs the SHORT part. "
               if first else "Continue runs the 100 Ohm LOAD part. ")
            + "The analyzer REPLACES its stored load-short data.\n\n"
            "These commands come from JUMP, not from the Alpha manual, and "
            "have not been run on this analyzer yet.\n\n"
            "OK = start now.   Cancel = go back, nothing is sent.",
            icon="warning",
        ):
            return
        self.stop_event.clear()
        self.btn_sl_cancel.config(state='normal')
        self._run_worker(self._worker_load_short, self.sl_phase)

    def _worker_load_short(self, phase):
        q = self.queue
        step = LOAD_SHORT_STEPS[phase]
        label = LOAD_SHORT_LABELS[step]
        if phase == 0:
            q.put(("log", "Arming the load-short calibration (*RST, ZRUNCAL=SL_INIT)…"))
            self.backend.begin_load_short_calibration()
        q.put(("headline", (f"Load-short: calibrating with the {label}… hands away.", None)))
        q.put(("progress", (self.progress_sl, f"ZRUNCAL={step} running…")))
        q.put(("log", f"WRITE MOMENT: ZRUNCAL={step} — the analyzer now replaces "
                      f"its stored load-short data ({label})."))
        t0 = time.time()
        try:
            self.backend.run_load_short_step(step, stop_event=self.stop_event)
        except CalibrationCancelled:
            # Start the load-short over from the short part next time.
            q.put(("slphase", 0))
            if self._close_requested:
                raise              # window closing: not a user Cancel
            self._report_cancelled(f"Load-short ({label})", self.progress_sl)
            return
        except Exception:
            # Start the load-short over from the short part next time.
            q.put(("slphase", 0))
            q.put(("progress", (self.progress_sl, f"ZRUNCAL={step} FAILED. See the log.")))
            raise
        q.put(("progress", (self.progress_sl,
                            f"{label} done in {time.time() - t0:.0f} s.")))
        q.put(("log", f"Load-short {label} part done in {time.time() - t0:.0f} s."))
        if phase + 1 < len(LOAD_SHORT_STEPS):
            q.put(("headline", (f"Short part stored. Now connect the "
                                f"{LOAD_SHORT_LABELS[LOAD_SHORT_STEPS[phase + 1]]}.", True)))
            q.put(("slphase", phase + 1))
        else:
            stamp = datetime.now().isoformat(timespec='seconds')
            q.put(("log", f"Load-short calibration complete at {stamp}. Scan "
                          "modules apply it with ZSLCAL=1. Disconnect the standard."))
            q.put(("headline", ("Load-short calibration stored. Disconnect the "
                                "load standard.", True)))
            q.put(("slphase", 0))
            q.put(("advance", (self.STEP_SL, f"done at {stamp}")))

    # ------------------------------------------------------------ step 5: open
    def _do_open(self):
        if self.busy or not self.connected:
            self.log("Connect first (and wait for any running step).")
            return
        self.open_rows = []
        self._update_plot()
        self.stop_event.clear()
        self.btn_open_stop.config(state='normal')
        self._run_worker(self._worker_open, dict(self.open_params),
                         self.where_var.get(), self.temp_var.get())

    def _stop_open(self):
        self.stop_event.set()
        self.log("Stop requested: the open measurement ends after this point.")

    def _worker_open(self, params, where, temp):
        q = self.queue
        freqs = params["frequencies"]
        q.put(("headline", (f"Measuring the empty cell at {len(freqs)} "
                            "frequencies… hands away.", None)))
        q.put(("log", f"Open measurement: {len(freqs)} points, ACV={params['acv']:g} "
                      f"Vrms, MTM={params['mtm']:g} s, {params['wire_mode']}-wire. "
                      "Nothing is stored in the analyzer."))
        self.backend.initialize_instrument(params)

        def on_point(i, n, row):
            self.open_rows.append(row)
            flag = "" if row["status"] == STATUS_RESULT_VALID else \
                f"  status {row['status']}: {status_message(row['status'])}"
            q.put(("progress", (self.progress_open,
                                f"{i}/{n}  {row['f']:g} Hz  C_open = "
                                f"{row['c'] * 1e12:.4g} pF{flag}")))
            q.put(("plot", None))

        try:
            rows = measure_open_cell(self.backend, freqs, on_point, self.stop_event)
        finally:
            self.backend.safe_state()
        complete = len(rows) == len(freqs)
        meta = {"written": datetime.now().isoformat(timespec='seconds'),
                "idn": self.backend_idn(),
                "acv_Vrms": f"{params['acv']:g}",
                "mtm_s": f"{params['mtm']:g}",
                "wire_mode": params["wire_mode"],
                "instrument_settings": "ZREFMODE=-3 ZLLCOR=1 ZSLCAL=1 DRS=0 0",
                "cell": ascii_text(where),
                "temperature_K": ascii_text(temp),
                "last_ref_cal": read_last_ref_cal() or "unknown",
                "complete": "yes" if complete else
                            f"no, stopped after {len(rows)} of {len(freqs)}"}
        self._durable_write(self.paths["open"], format_open_file(rows, meta))
        bad = sum(1 for r in rows if r["status"] != STATUS_RESULT_VALID)
        valid_c = sorted(r["c"] for r in rows
                         if r["status"] == STATUS_RESULT_VALID and math.isfinite(r["c"]))
        median = valid_c[len(valid_c) // 2] if valid_c else float("nan")
        summary = (f"{len(rows)} points, median C_open = {median * 1e12:.4g} pF"
                   + (f", {bad} not valid (Status column)" if bad else ""))
        q.put(("log", f"Open data written: {os.path.basename(self.paths['open'])} "
                      f"({summary})."))
        q.put(("progress", (self.progress_open, "Open measurement complete."
                            if complete else "Open measurement stopped.")))
        q.put(("headline", (f"Empty cell measured: {summary}.", bad == 0 and complete)))
        q.put(("advance", (self.STEP_OPEN, summary + ("" if complete else " (stopped)"))))

    def backend_idn(self):
        return ascii_text(self.idn)

    def _update_plot(self):
        for ax in (self.ax_c, self.ax_g):
            ax.cla()
        self._style_axes()
        rows = list(self.open_rows)
        if rows:
            f = [r["f"] for r in rows]
            self.ax_c.plot(f, [r["c"] * 1e12 for r in rows], 'o-',
                           color=self.CLR_ACCENT_GOLD, ms=3)
            self.ax_g.plot(f, [max(abs(r["g"]), 1e-18) for r in rows], 'o-',
                           color=self.CLR_ACCENT_GOLD, ms=3)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    # ------------------------------------------------------------ step 6
    def _finish(self):
        """All chosen steps done: park and release the analyzer, summarise."""
        # The step that just finished may still own the worker: release the
        # analyzer as soon as it is idle.
        if self.busy:
            self._release_pending = True
        else:
            self._run_worker(self._worker_release)
        txt = ["What was done:"]
        for k, v in self.results.items():
            txt.append(f"  {k:<12} {v}")
        txt += ["", format_cal_age(read_last_ref_cal()), "", "Files:"]
        for k in ("open", "log", "console"):
            if k != "open" or os.path.exists(self.paths[k]):
                txt.append("  " + os.path.basename(self.paths[k]))
        txt += ["",
                "REF and load-short data live in the analyzer and survive *RST.",
                "Open data is a PC file: a scan applies it at the same requested",
                "frequencies, AC level, integration time and wire mode.",
                "Reconnect the sample before any sweep."]
        self._set_text(self.done_text, "\n".join(txt))
        self._set_headline("Calibration finished. Reconnect the sample.", ok=True)
        self.log("Done. " + "; ".join(f"{k}: {v}" for k, v in self.results.items()))
        self._show_step(self.STEP_DONE)

    def _worker_release(self):
        self.backend.close_instrument()
        self.queue.put(("disconnected", None))
        self.queue.put(("log", "Analyzer parked (ACV=0, ZCONSPL=0, *RST) and released."))

    # ------------------------------------------------------------ misc
    def _restart(self):
        self.open_rows = []
        self.plan = []
        self.sl_phase = 0
        self._update_plot()
        self._show_step(0)
        self.btn_next1.config(state='disabled')
        self._set_headline("Press Connect & check for a fresh file set.")

    def _open_folder(self):
        if not self.dest_folder:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.dest_folder)   # noqa
            else:
                import subprocess
                subprocess.Popen(["xdg-open", self.dest_folder])
        except Exception as e:
            self.log(f"Cannot open folder: {e}")

    def _on_closing(self):
        if self.busy and not self._close_requested:
            self.log("A step is still running on the analyzer (a REF calibration "
                     "takes about 30 s); waiting for it is safer. Close again to "
                     "force: the analyzer is then aborted (MBK) and parked.")
            self._close_requested = True   # second click closes
            return
        self._close_requested = True
        self.stop_event.set()
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
        try:
            self.backend.close_instrument()
        finally:
            self.root.destroy()


def ascii_text(value):
    """Data-file metadata must stay ASCII (lab PCs are cp1252)."""
    return str(value).encode("ascii", "replace").decode("ascii")


# ===============================================================================
# ===============================================================================
# TERMINAL -> PICA CONSOLE
# Backend code reports with print() (e.g. "*RST failed; escalating to RSTH",
# shutdown warnings, tracebacks). Those lines used to reach only the black
# terminal window, so the PICA console could not show them. TerminalTee
# copies every complete stdout/stderr line into a queue that the GUI drains
# into its own console as "[terminal] ..."; the terminal still gets
# everything. The same block is in every Alpha-AN GUI - keep them in step.
# ===============================================================================

TERMINAL_LOG_POLL_MS = 250
TERMINAL_LOG_MAX_LINES_PER_POLL = 200


class TerminalTee:
    """File-like stand-in for sys.stdout / sys.stderr.

    Forwards every write to the original stream (when there is one: under
    pythonw there is none) and queues each complete line for the GUI
    console. Writes from the thread that is draining the queue are not
    queued, so the console can never echo itself."""

    def __init__(self, original, line_queue):
        self.original = original
        self.line_queue = line_queue
        self.muted_thread = None
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text):
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:
                pass
        if not text or threading.get_ident() == self.muted_thread:
            return len(text or "")
        with self._lock:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            line = line.rstrip()
            if line:
                self.line_queue.put(line)
        return len(text)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        # encoding, isatty, fileno ... come from the real stream.
        return getattr(self.original, name)


def install_terminal_log(gui):
    """Copy terminal output into gui.log() until gui.root is destroyed.

    Called once from the GUI's __init__. The queue is drained on the Tk
    thread with a stored, cancellable after() id; the original streams are
    put back when the root window goes. Returns the restore function."""
    lines = queue.Queue()
    saved = (sys.stdout, sys.stderr)
    tees = (TerminalTee(saved[0], lines), TerminalTee(saved[1], lines))
    sys.stdout, sys.stderr = tees
    state = {'after_id': None}

    def drain():
        state['after_id'] = None
        for tee in tees:
            tee.muted_thread = threading.get_ident()
        try:
            for _ in range(TERMINAL_LOG_MAX_LINES_PER_POLL):
                try:
                    line = lines.get_nowait()
                except queue.Empty:
                    break
                try:
                    gui.log(f"[terminal] {line}")
                except Exception:
                    break  # console not usable; the terminal still has it
        finally:
            for tee in tees:
                tee.muted_thread = None
        try:
            state['after_id'] = gui.root.after(TERMINAL_LOG_POLL_MS, drain)
        except Exception:
            pass  # root already destroyed

    def restore(event=None):
        if event is not None and event.widget is not gui.root:
            return  # <Destroy> also fires for every child widget
        if state['after_id'] is not None:
            try:
                gui.root.after_cancel(state['after_id'])
            except Exception:
                pass
            state['after_id'] = None
        if sys.stdout is tees[0]:
            sys.stdout = saved[0]
        if sys.stderr is tees[1]:
            sys.stderr = saved[1]

    gui.root.bind("<Destroy>", restore, add="+")
    state['after_id'] = gui.root.after(TERMINAL_LOG_POLL_MS, drain)
    return restore


def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Dependency Error",
                             "PyVISA is not installed.\n\nPlease run:\npip install pyvisa")
        return
    root = tk.Tk()
    CalibrationGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
