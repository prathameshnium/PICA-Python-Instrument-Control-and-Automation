"""
Module:             Temprature_Scan_Passive_AlphaAN_GUI.py
Purpose:             GUI module for Temperature-Dependent Dielectric
                     Measurement (Novocontrol Alpha-AN + Lakeshore 350),
                     the Lakeshore used as a PASSIVE thermometer.
                     Port of keysight/Temprature_Scan_Passive_E4980A_GUI.py
                     v1.4: the passive Lakeshore handling, 400 K kill
                     switch, per-point temperature binding, per-frequency
                     files, T-log, plots and the whole multi-day hardening
                     (retry-forever reconnect, fsync-per-point writes,
                     bounded console, optional plot thinning, Windows
                     keep-awake, no modal dialogs on unattended events)
                     are unchanged; only the LCR layer is replaced by the
                     Alpha-AN backend from novocontrol/Frequency_Scan_
                     AlphaAN_GUI.py (EOI-only framing, read-every-ack, SRQ
                     completion, ZG4 AC ceiling, safe-state on every exit).
                     Changes forced by the swap:
                       * AC level -> ACV (Vrms), aperture -> MTM
                         integration time, new ZG4 wire-mode box; the
                         ALC, open/short correction and cable-length
                         fields have no Alpha equivalent and are gone.
                       * No DC bias field (DCV/DCE are never sent).
                       * Alpha status 6 (signal source disconnected) is a
                         hardware fault, not a comm glitch: it ends the
                         run through the no-dialog runtime-error path
                         instead of the retry-forever reconnect loop.
                       * The reconnect re-opens the Lakeshore session with
                         *IDN? only and never re-sends RANGE; the Alpha is
                         re-opened with connect() + initialize_instrument().
Original Authors:    Prathamesh Deshmukh (template programs)
Version:             V: 1.0 (Alpha-AN port, 22 Oct 2026)

SAFETY:             This module never uses DC bias. DCV/DCE are never
                    transmitted (the analyzer does have DC bias - see
                    INSTRUMENT). The Lakeshore is only read, except the
                    optional heater-off at start and the 400 K kill
                    switch, exactly as in the E4980A version. Not yet run
                    on the hardware.

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
import ctypes
import math
import platform
import traceback
import atexit
from datetime import datetime
from collections import deque
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- winsound for unattended-run alerts (Windows; optional) ---
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

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
# BACKEND: LAKESHORE 350  (passive-aware close)
# ===============================================================================

class Lakeshore350_Backend:
    """A class to control the Lakeshore Model 350 Temperature Controller."""

    def __init__(self, visa_address, passive_mode=False):
        self.instrument = None
        self.passive_mode = passive_mode   # True => never touch heater on exit
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        print(f"Lakeshore Connected: "
              f"{self.instrument.query('*IDN?').strip()}")

    def reset_and_clear(self):
        self.instrument.write('*RST')
        time.sleep(0.5)
        self.instrument.write('*CLS')
        time.sleep(1)

    def setup_heater(self, output, resistance_code, max_current_code):
        self.instrument.write(
            f'HTRSET {output},{resistance_code},{max_current_code},0,1')
        time.sleep(0.5)

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        """Configures the instrument's internal ramp generator."""
        self.instrument.write(
            f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)

    def set_setpoint(self, output, temperature_k):
        self.instrument.write(f'SETP {output},{temperature_k}')

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None:
            raise ValueError("Invalid heater range.")
        self.instrument.write(f'RANGE {output},{range_code}')

    def get_temperature(self, sensor):
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def get_heater_output(self, output):
        return float(self.instrument.query(f'HTR? {output}').strip())

    def close(self):
        if self.instrument:
            try:
                if not self.passive_mode:
                    self.set_heater_range(1, 'off')
                    time.sleep(0.5)
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


# ===============================================================================
# BACKEND: NOVOCONTROL ALPHA-AN  (copied from
# novocontrol/Frequency_Scan_AlphaAN_GUI.py - keep the two in step)
# ===============================================================================
#
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
SRQ_TIMEOUT_CAL_S = 300.0


def point_timeout_s(freq_hz, mtm_s):
    """SRQ budget for one point.

    A point integrates the sample and (reference mode "Always") the
    reference, each for at least MTM and at least one signal period. A fixed
    120 s budget is too short below ~0.02 Hz or above MTM ~55 s, so scale it;
    never go below SRQ_TIMEOUT_MEASURE_S.
    """
    return max(SRQ_TIMEOUT_MEASURE_S,
               4.0 * max(float(mtm_s), 1.0 / float(freq_hz)) + 60.0)


def freq_tag(freq_hz):
    """Frequency label for file names and the plot dropdown.

    int(f) collapsed sub-Hz and fractional frequencies (0.1 and 0.5 Hz both
    became "0"), interleaving two frequencies in one file. Integer
    frequencies keep their old form, so existing file names do not change.
    """
    f = float(freq_hz)
    return str(int(f)) if f.is_integer() else format(f, ".10g")

# After *RST the Alpha drops off the GPIB bus while it reinitializes (no
# listener: every write fails with VI_ERROR_NLISTENERS, NI-488.2's ENOL) and
# only answers again some seconds later. Seen on hardware 22 Oct 2026: a
# fixed 1 s sleep was too short and MODE=IMP failed. Commands are retried on
# "no listeners" only, until this deadline.
RESET_READY_TIMEOUT_S = 30.0
RESET_RETRY_INTERVAL_S = 0.5

# VISA status codes, as pyvisa reports them in VisaIOError.error_code.
# NLISTENERS: no listener at that address; the write put no byte on the bus,
# so it is the one GPIB error that is always safe to retry.
# NCIC: the board is not Controller-In-Charge - typically left behind by
# WinALPHA / WinDETA; cleared by one Interface Clear on the board.
VI_ERROR_NLISTENERS = -1073807265
VI_ERROR_NCIC = -1073807264

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
                idn = inst.query("*IDN?").strip()
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
                    idn = inst.query("*IDN?").strip()
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

    def _reset_and_set_impedance_mode(self,
                                      timeout_s=RESET_READY_TIMEOUT_S):
        """*RST, wait until the Alpha answers again, then MODE=IMP.

        *RST leaves no Novocontrol ack. While the Alpha reinitializes it is
        not a listener and every write fails with VI_ERROR_NLISTENERS, which
        put nothing on the bus, so *IDN? is repeated until it is answered -
        the same handshake JUMP uses after *RST (github.com/JMoVS/JUMP,
        MeasurementHardware.py, ALPHA.initialize_instrument), including its
        extra 1 s settle before MODE=IMP. Any other error is raised at once.
        """
        inst = self.instrument
        inst.write("*RST")
        start = time.monotonic()
        while True:
            time.sleep(RESET_RETRY_INTERVAL_S)
            try:
                inst.query("*IDN?")
                break
            except Exception as exc:
                if visa_error_code(exc) != VI_ERROR_NLISTENERS:
                    raise
                if time.monotonic() - start >= timeout_s:
                    raise ConnectionError(
                        f"The Alpha did not rejoin the GPIB bus within "
                        f"{timeout_s:.0f} s after *RST. Last error: "
                        f"{exc}") from exc
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

    def release_for_reconnect(self):
        """Safe-state and drop the session WITHOUT *RST. Never raises.

        Used by the reconnect path: close_instrument()'s *RST takes the
        Alpha off the bus for seconds, and the reconnect would then talk
        to it 0.2 s later. initialize_instrument() sends its own *RST and
        waits for the Alpha to rejoin the bus.
        """
        inst = self.instrument
        if inst is None:
            return
        try:
            self.safe_state()
        except Exception as e:
            print(f"  Warning: safe-state before reconnect failed: {e}")
        try:
            inst.close()
        except Exception as e:
            print(f"  Warning closing VISA session: {e}")
        finally:
            self.instrument = None

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
# BACKEND: COMBINED  (Appendix A.1 — per-point temperature binding)
#                  +  PASSIVE init + hardcoded 400 K safety kill switch
# ===============================================================================

class AlphaFatalStatusError(RuntimeError):
    """An Alpha status in STATUS_FATAL (hardware fault). Not a comm glitch:
    the worker lets it end the run instead of reconnecting forever."""


class Combined_Backend:
    """Manages the Lakeshore 350 (read-only / passive) and the Novocontrol
    Alpha-AN."""

    # HARDCODED SAFETY KILL SWITCH — do not expose in UI
    SAFETY_KILL_TEMP_K = 400.0

    def __init__(self):
        self.lakeshore = None
        self.lcr = AlphaAN_Backend()
        self.params = {}

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments (PASSIVE mode) ---")
        # Every Alpha setting is checked before a byte reaches either bus.
        # AlphaAN_Backend.initialize_instrument expects keys:
        #   acv, mtm, wire_mode (lcr_visa is opened by connect)
        validate_alpha_parameters(
            parameters['frequencies'], parameters['acv'],
            parameters['mtm'], parameters['wire_mode'])
        # passive_mode=True when the user asked us NOT to touch the range
        self.lakeshore = Lakeshore350_Backend(
            parameters['lakeshore_visa'],
            passive_mode=not parameters['zero_range'])

        # NOTE: no reset_and_clear(), no setup_heater(), no setup_ramp(),
        # no set_setpoint(). *RST is deliberately avoided because it would
        # alter the controller's current state.
        if parameters['zero_range']:
            # RANGE 1,0  => heater output 1 OFF (Lake Shore 350 manual)
            self.lakeshore.set_heater_range(1, 'off')
            print("  Heater range set to 0 (OFF). Fully passive.")
        else:
            print("  Heater range left UNTOUCHED per user request.")

        self.lcr.connect(parameters['lcr_visa'])
        self.lcr.initialize_instrument(parameters)

    def check_safety_kill(self, temperature_k):
        """Hardcoded kill switch: force heater OFF at/above 400 K.
        Always fires, regardless of the zero-range checkbox."""
        if temperature_k >= self.SAFETY_KILL_TEMP_K:
            for attempt in range(3):
                try:
                    self.lakeshore.set_heater_range(1, 'off')
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

        Each point is (T, f_requested, Zs', Zs'', status, f_actual). The
        requested frequency keys the per-frequency file; f_actual (what
        the Alpha's generator really produced) is used for the conversion.
        """
        htr = self.lakeshore.get_heater_output(1)
        points = []
        try:
            for f in frequencies:
                if stop_event is not None and stop_event.is_set():
                    break                     # abort mid-sweep, cleanly
                # T for THIS point
                temp = self.lakeshore.get_temperature('A')
                if temp >= self.SAFETY_KILL_TEMP_K:
                    self.check_safety_kill(temp)
                    points.append(
                        (temp, f, float('nan'), float('nan'), -1, f))
                    break
                if delay > 0 and stop_event is not None:
                    stop_event.wait(delay)       # optional settle per point
                R, X, f_act, status, _ref = self.lcr.measure_point(f)
                if status in STATUS_FATAL:
                    raise AlphaFatalStatusError(
                        f"Alpha-AN status {status} at {f:.6g} Hz: "
                        f"{status_message(status)}. Sweep aborted.")
                points.append((temp, f, R, X, status, f_act))
        except Exception as exc:
            # Points already measured in this sweep are real data: hand
            # them to the worker with the error so they are still saved
            # (status 6, a refused command or a comm drop mid-sweep would
            # otherwise discard the whole sweep).
            if points:
                exc.partial_cycle = {'heater': htr, 'points': points,
                                     'wall_time': datetime.now()}
            raise
        return {'heater': htr, 'points': points,
                'wall_time': datetime.now()}

    def reconnect(self):
        """Close both sessions and re-open them from the stored params.
        Used by the worker's retry-forever loop after a comm failure.
        The Alpha is re-opened with connect() + initialize_instrument(),
        which restores the full configuration (MODE=IMP, ACV, MTM, wire
        mode) even after an analyzer power-cycle. The Lakeshore session
        is dropped and re-opened with *IDN? only: nothing is re-sent to
        the controller (no RANGE at close, none at re-open), so a comm
        glitch can never change the heater state of a passive run."""
        try:
            self.lcr.release_for_reconnect()
        except Exception as e:
            print(f"  Pre-reconnect cleanup warning: {e}")
        self._reopen_lakeshore_read_only()
        self.lcr.connect(self.params['lcr_visa'])
        self.lcr.initialize_instrument(self.params)

    def _reopen_lakeshore_read_only(self):
        """Release the Lakeshore session without the heater-off that
        Lakeshore350_Backend.close() may send, then open a fresh one."""
        old, self.lakeshore = self.lakeshore, None
        if old is not None and old.instrument is not None:
            try:
                old.instrument.close()
            except Exception as e:
                print(f"  Lakeshore session close warning: {e}")
            old.instrument = None
        self.lakeshore = Lakeshore350_Backend(
            self.params['lakeshore_visa'],
            passive_mode=not self.params['zero_range'])

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
    Combines Lakeshore 350 temperature sensing with Alpha-AN
    multi-frequency impedance measurement.  The Lakeshore is treated as a PASSIVE temperature
    sensor; the heater is never ramped or setpoint-driven from this GUI.
    A hardcoded 400 K safety kill switch always forces RANGE 1,0.
    Built for unattended multi-day runs: comm errors auto-reconnect
    forever, every data row is fsync'd to disk immediately, and heating/
    cooling direction is never assumed — whatever temperature profile is
    thrown at it is recorded as-is.
    """

    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    CONSOLE_MAX_LINES = 2000   # bound console growth on multi-day runs
    PLOT_MAX_POINTS = 10000    # halve plot buffers at this size (if enabled)

    # SetThreadExecutionState flags (Windows keep-awake during a run)
    ES_CONTINUOUS      = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

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
            "Alpha-AN & L350: Dielectric vs. Temperature (Passive T-Monitor)")
        self.root.geometry("1600x980")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 880)

        # --- State flags ---
        self.is_running = False
        self.is_stabilizing = False   # retained but always False (legacy)
        self._stopping = False          # re-entrancy guard for stop
        self._close_after_stop = False  # destroy window once worker exits
        self.start_time = None
        self._last_draw_time = 0.0
        self._redraw_interval = 1.0    # seconds; 1 Hz is ample at 1-2 K/min

        # --- Backend ---
        self.backend = Combined_Backend()
        atexit.register(self.backend.close_instruments)

        # --- File / frequency state ---
        self.file_location_path = ""
        self.frequencies = []
        self.freq_filepaths = {}
        self.t_log_path = None
        self.plot_freq = None

        # Rows that failed to reach disk (network share / disk hiccup);
        # retried every cycle so measured data is never silently dropped.
        self._pending_rows = deque(maxlen=20000)
        self._write_error_logged = False

        # --- Data storage (Appendix A.4: keyed per frequency) ---
        # Plain lists: when plot thinning is enabled they are halved in
        # place at PLOT_MAX_POINTS (plot-only; files keep every point).
        self.data_storage = {
            'time': [],
            'temperature': [],
            'cp': {},   # {freq: {'T': list, 'v': list}}
            'g':  {},   # {freq: {'T': list, 'v': list}}
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
        self.stop_event = threading.Event()

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
              text="Alpha-AN & L350: Dielectric vs. Temperature (Passive T-Monitor)",
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
            "Program Name: Dielectric vs. Temperature (Passive T-Monitor)\n"
            "Instruments: Lakeshore 350 (read-only), Novocontrol Alpha-AN + ZG4\n"
            "Function: MODE=IMP (Zs', Zs''), multi-frequency scan per T point\n"
            "Safety: hardcoded 400 K heater kill switch")
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

        # --- AC Voltage | Integration Time ---
        # No DC bias field: this module never sends DCV/DCE.
        Label(frame, text="AC Voltage (Vrms):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="Integration Time (s):").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["AC Bias"] = Entry(frame, font=self.FONT_BASE)
        self.entries["AC Bias"].insert(0, "1.0")
        self.entries["AC Bias"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        # MTM: 0.5 s matches the lab's WinDETA definition ("Maximum of
        # 0.500 Seconds or 1 Periods"); the Alpha never integrates less
        # than one period.
        self.entries["MTM"] = Entry(frame, font=self.FONT_BASE)
        self.entries["MTM"].insert(0, "0.5")
        self.entries["MTM"].grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Delay | Wire Mode ---
        Label(frame, text="Delay per Freq (s):").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="ZG4 Wire Mode:").grid(
            row=r, column=1, padx=padx_val, pady=pady_val, sticky='w')
        r += 1
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].insert(0, "0")
        self.entries["Delay"].grid(
            row=r, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        self.wire_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            values=list(WIRE_MODES))
        self.wire_combobox.set("2")
        self.wire_combobox.grid(
            row=r, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        r += 1

        # --- Passive heater-range control ---
        self.var_zero_range = tk.BooleanVar(value=True)   # CHECKED by default
        ttk.Checkbutton(
            frame,
            text="Set Range to Zero (fully passive; heater OFF)",
            variable=self.var_zero_range).grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=2,
            sticky='w')
        r += 1

        # --- Plot thinning for very long runs (plot-only; files keep all) ---
        self.var_plot_thin = tk.BooleanVar(value=False)   # OFF by default
        ttk.Checkbutton(
            frame,
            text="Thin plot points on long runs (halve when full)",
            variable=self.var_plot_thin).grid(
            row=r, column=0, columnspan=2, padx=padx_val, pady=2,
            sticky='w')
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

        # --- Lakeshore VISA | LCR VISA ---
        Label(frame, text="Lakeshore VISA:").grid(
            row=r, column=0, padx=padx_val, pady=pady_val, sticky='w')
        Label(frame, text="Alpha-AN VISA:").grid(
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
        Cs_double_prime = D * Cs  # Corrected from Cp_double_prime

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
            if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
                raise ValueError(
                    f"Frequency {f} Hz outside the Alpha-AN range "
                    f"({FREQ_HW_MIN:g} Hz - {FREQ_HW_MAX:g} Hz).")
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
        # Trim to the last CONSOLE_MAX_LINES: an unbounded Text widget is
        # the main cause of Tk slowdown/freeze on multi-day runs.
        try:
            line_count = int(
                self.console_widget.index('end-1c').split('.')[0])
            if line_count > self.CONSOLE_MAX_LINES:
                self.console_widget.delete(
                    '1.0', f'{line_count - self.CONSOLE_MAX_LINES + 1}.0')
        except tk.TclError:
            pass
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _set_keep_awake(self, enable):
        """Stop Windows from sleeping mid-run (display may still sleep).
        Best-effort no-op on other platforms."""
        try:
            flags = self.ES_CONTINUOUS | (
                self.ES_SYSTEM_REQUIRED if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _beep(self, times=1):
        """Audible alert (main/Tk thread only). Beeps in a daemon thread
        so the GUI never blocks; falls back to the Tk bell. Used instead
        of modal dialogs on unattended-run events (kill switch, runtime
        error) — a messagebox nobody is there to click must never linger
        over a multi-day run."""
        if HAS_WINSOUND and platform.system() == "Windows":
            def _do_beep():
                for _ in range(max(1, times)):
                    winsound.Beep(1000, 500)
                    time.sleep(0.2)
            threading.Thread(target=_do_beep, daemon=True).start()
        else:
            try:
                self.root.bell()
            except Exception:
                pass

    def _handle_log_message(self, message):
        self.log(message)

    # ==================================================================
    # START / STOP
    # ==================================================================
    def start_measurement(self):
        t = self.measurement_thread
        if t is not None and t.is_alive():
            self.log("Previous run is still finishing; wait for it before "
                     "starting again.")
            return
        try:
            # --- Parse frequencies ---
            self.frequencies = self._parse_frequencies()

            # --- Gather parameters ---
            params = {
                'sample_name':   self.entries["Sample Name"].get(),
                'zero_range':    self.var_zero_range.get(),
                'acv':           float(self.entries["AC Bias"].get()),
                'mtm':           float(self.entries["MTM"].get()),
                'delay':         float(self.entries["Delay"].get()),
                'wire_mode':     self.wire_combobox.get(),
                'frequencies':   list(self.frequencies),
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

            if params['delay'] < 0:
                raise ValueError("Delay per Freq cannot be negative.")

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
            self.is_stabilizing, self.is_running = False, True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.scan_button.config(state='disabled')

            self.data_storage['time'] = []
            self.data_storage['temperature'] = []
            self.data_storage['cp'] = {
                f: {'T': [], 'v': []} for f in self.frequencies}
            self.data_storage['g'] = {
                f: {'T': [], 'v': []} for f in self.frequencies}
            self._pending_rows.clear()
            self._write_error_logged = False
            self._decade_ylims.clear()  # fresh dataset re-snaps decades

            for line in (self.line_main, self.line_sub1, self.line_sub2):
                line.set_data([], [])
            self.ax_main.set_title(
                f"Cp vs. T @ {freq_tag(self.plot_freq)} Hz", fontweight='bold')
            self.canvas.draw()

            self.stop_event.clear()
            self._set_keep_awake(True)   # multi-day run: no system sleep
            self.log("Starting passive temperature-sensing measurement...")

            # --- Launch worker thread ---
            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            # A failure after connect (e.g. a refused MTM=) must not leave
            # the Alpha configured with ACV live, or the sessions open.
            try:
                self.backend.close_instruments()
            except Exception as close_err:
                self.log(f"WARNING: error closing instruments: {close_err}")
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
                            f"{sample_name}-{freq_tag(f)}Hz.txt")
            for f in self.frequencies
        }
        if any(os.path.exists(p) for p in candidate_paths.values()):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sample_name = f"{sample_name}_{ts}"
            candidate_paths = {
                f: os.path.join(self.file_location_path,
                                f"{sample_name}-{freq_tag(f)}Hz.txt")
                for f in self.frequencies
            }
            self.log(f"Existing files detected. Using unique sample "
                     f"tag: {sample_name}")

        self.freq_filepaths = candidate_paths
        for f, path in self.freq_filepaths.items():
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(self.DATA_HEADER + "\n")
                fh.flush()
                os.fsync(fh.fileno())

        # Timestamped temperature log (separate file so the legacy
        # 19-column per-frequency format stays untouched).
        self.t_log_path = os.path.join(
            self.file_location_path, f"{sample_name}_T-log.txt")
        with open(self.t_log_path, 'w', encoding='utf-8') as fh:
            fh.write("DateTime\tElapsed_s\tTemperature_K\tHeater_pct\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------------------
    def _populate_plot_freq_dropdown(self):
        """
        Appendix A.4:
        Populate the plot-frequency dropdown.  Default selection is the
        lowest frequency (index 0) — the sweep measures lowest-first, so
        the live plot shows data as soon as the measurement starts.
        """
        vals = [f"{freq_tag(f)} Hz" for f in self.frequencies]
        self.plot_freq_cb.config(state='readonly')
        self.plot_freq_cb['values'] = vals
        default_index = 0
        self.plot_freq_cb.current(default_index)
        self.plot_freq = self.frequencies[default_index]
        self.log(
            f"Default live-plot frequency: {freq_tag(self.plot_freq)} Hz "
            f"(lowest of {len(self.frequencies)}).")

    # ------------------------------------------------------------------
    def _on_plot_freq_change(self, event=None):
        """
        Appendix A.4:
        Main-thread-only redraw.  Never touches the worker thread or the
        instruments, so switching frequency cannot cause measurement lag,
        errors, or a hang.
        """
        idx = self.plot_freq_cb.current()
        if idx < 0:
            return
        self.plot_freq = self.frequencies[idx]
        self._decade_ylims.clear()  # new frequency re-snaps decades
        self.ax_main.set_title(
            f"Cp vs. T @ {freq_tag(self.plot_freq)} Hz", fontweight='bold')
        self._update_live_plots(force=True)

    # ------------------------------------------------------------------
    def stop_measurement(self, from_user=True):
        # Wait for worker to finish before touching VISA. The wait is a
        # non-blocking root.after() poll so the GUI never freezes (was a
        # join(15) that stalled the window on Stop).
        if self._stopping or not (self.is_running or self.is_stabilizing):
            return
        self._stopping = True
        self.is_running, self.is_stabilizing = False, False
        self.stop_event.set()
        self.stop_button.config(state='disabled')
        if from_user:
            self.log("Stop requested; waiting for worker to finish...")

        # The worker only sees stop_event between points, so give it the
        # longest single point plus margin (as Frequency_Scan_AlphaAN does)
        # before sessions are closed under it.
        mtm = self.backend.params.get('mtm', 0.0)
        worst = max((point_timeout_s(f, mtm) for f in self.frequencies),
                    default=SRQ_TIMEOUT_MEASURE_S)
        self._stop_deadline = time.time() + worst + 15.0
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
            self.log("WARNING: worker did not exit in time; "
                     "closing sessions anyway.")
        self._finalize_stop(from_user)

    def _finalize_stop(self, from_user):
        self._set_keep_awake(False)  # allow system sleep again
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
    # WORKER THREAD  (Passive monitoring — no stabilization, no ramp,
    #                 no direction assumption; comm errors retry forever)
    # ==================================================================
    def _measurement_worker(self):
        params = self.backend.params
        try:
            self.start_time = time.time()
            self.data_queue.put("LOG:Passive monitoring started. "
                                "Measuring until stopped by user.")
            comm_failures = 0
            while self.is_running and not self.stop_event.is_set():
                try:
                    cycle = self.backend.measure_frequency_sweep(
                        self.frequencies, params['delay'], self.stop_event)
                    comm_failures = 0
                except RuntimeError as e:
                    # The Alpha refused (non-OK ack, 'CR' = no load-short
                    # cal stored) or reported a hardware fault (status 6,
                    # AlphaFatalStatusError). A reconnect cannot fix any of
                    # them: end the run via the no-dialog error path.
                    self._queue_partial_cycle(e)
                    raise
                except Exception as e:
                    # Comm glitch (GPIB/VISA/instrument power blip):
                    # never give up — log, back off, reconnect, resume.
                    self._queue_partial_cycle(e)
                    comm_failures += 1
                    self.data_queue.put(
                        f"LOG:COMM ERROR (failure #{comm_failures}): "
                        f"{traceback.format_exc(limit=3)}")
                    if not self._reconnect_with_backoff(comm_failures):
                        break   # stop requested during backoff
                    continue

                elapsed = time.time() - self.start_time

                if cycle['points']:
                    self.data_queue.put(('CYCLE', cycle, elapsed))
                else:
                    break   # stop_event aborted the sweep pre-first-point

                # Hardcoded 400 K kill switch (checks max T in cycle)
                max_temp = max(pt[0] for pt in cycle['points'])
                if self.backend.check_safety_kill(max_temp):
                    self.data_queue.put("KILL")
                    break
        except Exception as e:
            # Last-resort safety net for unexpected (non-comm) bugs only;
            # comm errors are handled by the retry loop above. The
            # traceback only exists in this thread: carry it along.
            e.pica_traceback = traceback.format_exc()
            self.data_queue.put(e)

    def _queue_partial_cycle(self, exc):
        """Worker-thread helper: queue the points a sweep measured before
        it raised, so they are written and plotted like a full sweep."""
        cycle = getattr(exc, 'partial_cycle', None)
        if not cycle or not cycle['points']:
            return
        self.data_queue.put(
            f"LOG:Sweep interrupted after {len(cycle['points'])} point(s); "
            "saving the points already measured.")
        self.data_queue.put(
            ('CYCLE', cycle, time.time() - self.start_time))

    def _reconnect_with_backoff(self, attempt):
        """Worker-thread helper: close and re-open both instruments,
        escalating the wait between tries (5 -> 10 -> 30 -> 60 s cap).
        Loops until reconnected; returns False only if Stop was
        requested while waiting or reconnecting."""
        backoffs = (5, 10, 30, 60)
        while self.is_running and not self.stop_event.is_set():
            delay_s = backoffs[min(attempt - 1, len(backoffs) - 1)]
            self.data_queue.put(
                f"LOG:Reconnect attempt in {delay_s} s "
                "(Stop stays responsive)...")
            deadline = time.time() + delay_s
            while time.time() < deadline:
                if self.stop_event.wait(1.0):
                    return False
            try:
                self.backend.reconnect()
            except RuntimeError:
                # The Alpha refused a command (non-OK ack, 'CR'): retrying
                # cannot fix that. End the run via the runtime-error path.
                raise
            except Exception as e:
                # Bus / VISA errors (incl. TimeoutError, OSError): retry.
                attempt += 1
                self.data_queue.put(f"LOG:Reconnect failed: {e}")
                continue
            if not self.is_running or self.stop_event.is_set():
                # Stop arrived while reconnecting; the main thread may
                # already have closed the old sessions, so never leave
                # the freshly opened ones live.
                try:
                    self.backend.close_instruments()
                except Exception as e:
                    self.data_queue.put(
                        f"LOG:WARNING: error closing instruments: {e}")
                return False
            self.data_queue.put(
                "LOG:Reconnected. Resuming measurement.")
            return True
        return False

    # ==================================================================
    # QUEUE PROCESSING (main thread)
    # ==================================================================
    def _process_data_queue(self):
        terminal = None
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()

                try:
                    if isinstance(data, str) and data.startswith("LOG:"):
                        self._handle_log_message(data[4:])
                    elif isinstance(data, str) and data == "KILL":
                        terminal = "KILL"          # defer; keep draining
                    elif isinstance(data, Exception):
                        terminal = data
                    elif isinstance(data, tuple) and data[0] == 'CYCLE':
                        _, cycle, elapsed = data
                        self._process_cycle(cycle, elapsed)
                except Exception:
                    # A GUI-side failure (plot/log/file) must never kill
                    # this pump: acquisition continues in the worker and
                    # the next items still get processed.
                    try:
                        self.log("GUI ERROR (non-fatal): "
                                 f"{traceback.format_exc()}")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        finally:
            # Re-scheduling lives in a finally so the pump survives any
            # unexpected error above — a frozen-looking-but-alive window
            # silently dropping data is the worst multi-day failure mode.
            try:
                if terminal == "KILL":
                    self._handle_kill_event()
                elif isinstance(terminal, Exception):
                    self._handle_runtime_error(terminal)
                elif self.is_running or self.is_stabilizing:
                    self.root.after(200, self._process_data_queue)
            except tk.TclError:
                pass   # window already destroyed

    # ------------------------------------------------------------------
    def _process_cycle(self, cycle, elapsed):
        # Log non-zero status warnings
        for (temp, f, R, X, status, f_act) in cycle['points']:
            if status not in (STATUS_RESULT_VALID, -1):
                self.log(
                    f"WARNING: f={freq_tag(f)} Hz status={status} "
                    f"({status_message(status)}) "
                    f"at T={temp:.3f} K. Point discarded.")

        last_temp = cycle['points'][-1][0]
        self.log(
            f"Cycle @ t={elapsed:.1f}s | last T={last_temp:.3f} K | "
            f"Htr={cycle['heater']:.1f}%")

        # Appendix A.3: safe per-point file writing
        self._save_cycle_to_files(cycle)

        # One timestamped T-log row per sweep (multi-day thermal history)
        self._append_t_log(cycle, elapsed)

        # Update in-memory storage for plotting
        self._update_data_storage(cycle, elapsed)

        # Redraw plots (throttled)
        self._update_live_plots()

    # ==================================================================
    # Appendix A.3: Safe per-point file writing (open / append / close)
    # + fsync so a sudden power cut cannot lose OS-buffered rows
    # ==================================================================
    def _durable_write(self, path, text):
        """Append text and force it to the physical disk immediately."""
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def _write_or_buffer(self, path, row_str):
        """Write a row durably; on disk/share hiccup, buffer it for
        retry next cycle so measured data is never silently dropped.
        While rows are pending, new rows go straight to the buffer to
        preserve per-file ordering."""
        if self._pending_rows:
            self._pending_rows.append((path, row_str))
            return
        try:
            self._durable_write(path, row_str)
        except OSError as e:
            self._pending_rows.append((path, row_str))
            if not self._write_error_logged:
                self._write_error_logged = True
                self.log(f"WRITE ERROR: {e} — buffering rows and "
                         "retrying every cycle.")

    def _flush_pending_rows(self):
        while self._pending_rows:
            path, row = self._pending_rows[0]
            try:
                self._durable_write(path, row)
            except OSError:
                return   # still failing; keep buffer, retry next cycle
            self._pending_rows.popleft()
        if self._write_error_logged:
            self._write_error_logged = False
            self.log("Write path recovered; buffered rows flushed.")

    def _save_cycle_to_files(self, cycle):
        self._flush_pending_rows()
        for (temp, f, R, X, status, f_act) in cycle['points']:
            # Not a valid result: discard, as Frequency_Scan_AlphaAN does
            # (-1 is the safety-kill NaN row, which is kept).
            if status not in (STATUS_RESULT_VALID, -1):
                continue
            try:
                calc = self.calculate_impedance_parameters(f_act, R, X)
            except Exception as calc_err:
                self.log(f"Calc error at {freq_tag(f)} Hz: {calc_err}. "
                         f"Writing NaNs.")
                calc = [float('nan')] * 18

            # Every value — including temperature — formatted as %.6E
            row_vals = [temp] + calc
            row_str = "\t".join("{:.6E}".format(v) for v in row_vals)
            self._write_or_buffer(self.freq_filepaths[f], row_str + "\n")

    def _append_t_log(self, cycle, elapsed):
        """One row per sweep: wall-clock, elapsed s, T, heater %."""
        if not self.t_log_path:
            return
        wall = cycle.get('wall_time') or datetime.now()
        last_temp = cycle['points'][-1][0]
        row = (f"{wall.strftime('%Y-%m-%d %H:%M:%S')}\t{elapsed:.1f}\t"
               f"{last_temp:.4f}\t{cycle['heater']:.2f}\n")
        self._write_or_buffer(self.t_log_path, row)

    # ==================================================================
    # DATA STORAGE UPDATE (Appendix A.4: per-frequency keyed)
    # ==================================================================
    @staticmethod
    def _thin_pair(a, b):
        """Halve two aligned plot lists (drop every other point) while
        always keeping the newest point. Plot-only: the data files keep
        every measured point. Both lists get the identical slice so
        they stay aligned."""
        if len(a) % 2 == 0:
            return a[::2] + a[-1:], b[::2] + b[-1:]
        return a[::2], b[::2]

    def _update_data_storage(self, cycle, elapsed):
        thin = self.var_plot_thin.get()
        for (temp, f, R, X, status, f_act) in cycle['points']:
            if status not in (STATUS_RESULT_VALID, -1):
                continue
            try:
                calc = self.calculate_impedance_parameters(f_act, R, X)
                cp, g = calc[4], calc[2]
            except Exception:
                cp, g = float('nan'), float('nan')

            for key, val in (('cp', cp), ('g', g)):
                store = self.data_storage[key][f]
                store['T'].append(temp)
                store['v'].append(val)
                if thin and len(store['T']) >= self.PLOT_MAX_POINTS:
                    store['T'], store['v'] = self._thin_pair(
                        store['T'], store['v'])

        self.data_storage['time'].append(elapsed)
        self.data_storage['temperature'].append(cycle['points'][-1][0])
        if thin and len(self.data_storage['time']) >= self.PLOT_MAX_POINTS:
            self.data_storage['time'], self.data_storage['temperature'] = \
                self._thin_pair(self.data_storage['time'],
                                self.data_storage['temperature'])

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
    # EVENT HANDLERS
    # ==================================================================
    def _handle_kill_event(self):
        # UNATTENDED POLICY: no modal dialog — runs execute overnight
        # with nobody at the PC. Loud console log + beeps only; the run
        # is already stopped and every row is fsync'd on disk.
        kill_t = self.backend.SAFETY_KILL_TEMP_K
        self.log(f"!!! HARDCODED SAFETY KILL ({kill_t:.0f} K) TRIGGERED — "
                 "heater forced OFF !!!")
        self._update_live_plots(force=True)
        self.stop_measurement(False)
        self.log(f"Temperature reached {kill_t:.0f} K. Heater turned OFF "
                 "and measurement stopped. Data is on disk.")
        self._beep(times=5)

    # ------------------------------------------------------------------
    def _handle_runtime_error(self, exception):
        # UNATTENDED POLICY: no modal dialog (see _handle_kill_event).
        tb = getattr(exception, 'pica_traceback', repr(exception))
        self.log(f"RUNTIME ERROR: {tb}")
        self.stop_measurement(False)
        self.log(f"A critical error occurred: {exception}. Measurement "
                 "stopped; all written data is on disk.")
        self._beep(times=3)

    # ==================================================================
    # VISA SCAN (identity-aware, for BOTH instruments)
    # ==================================================================
    def _scan_for_visa_instruments(self):
        """
        Identity-aware instrument scan, Alpha-safe. Queries *IDN? with
        EOI-only framing (the Alpha uses no newline terminator; the
        Lakeshore 350 also accepts EOI), auto-selects the device reporting
        Novocontrol/ALPHA for the Alpha combobox (falling back to GPIB
        address 10, the Alpha's address in WinALPHA) and the
        Lakeshore 350 for the controller combobox. A board left
        not-Controller-In-Charge by WinALPHA/WinDETA gets ONE Interface
        Clear, then the query is retried once.
        """
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA is not installed.")
            return

        try:
            rm = pyvisa.ResourceManager()
        except Exception as e:
            self.log(f"ERROR: Cannot create VISA Resource Manager: {e}")
            return

        def _identify(res):
            with rm.open_resource(res) as dev:
                dev.timeout = 2000
                dev.read_termination = ""
                dev.write_termination = ""
                dev.send_end = True
                return dev.query("*IDN?").strip(" \t\r\n\x00\x10")

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        resources = rm.list_resources()
        if not resources:
            self.log("No VISA instruments found.")
            return

        found = []
        ctrl_label = None
        alpha_label = None
        alpha_default = None
        cleared_boards = set()

        for res in resources:
            idn = "Unknown / no response"
            try:
                idn = _identify(res)
            except Exception as exc:
                board = gpib_board_of(res)
                if (visa_error_code(exc) == VI_ERROR_NCIC
                        and board is not None
                        and board not in cleared_boards):
                    cleared_boards.add(board)
                    self.log(f"  GPIB{board} is not Controller-In-Charge "
                             f"(WinALPHA/WinDETA may have taken the bus). "
                             f"Sending one Interface Clear...")
                    try:
                        self.backend.lcr.send_interface_clear(board)
                        idn = _identify(res)
                    except Exception:
                        pass
                # otherwise busy / non-SCPI / timeout - skip silently

            label = f"{res}  ->  {idn}"
            found.append(label)
            self.log(f"  {label}")
            upper = idn.upper()

            if ctrl_label is None and ("LSCI" in upper or "MODEL350" in upper or "MODEL 350" in upper):
                ctrl_label = label
            if alpha_label is None and (
                    "NOVOCONTROL" in upper or "ALPHA" in upper):
                alpha_label = label
            if (alpha_default is None and res.upper().startswith("GPIB")
                    and f"::{DEFAULT_ALPHA_PAD}::" in res):
                alpha_default = label

        self.lakeshore_cb['values'] = found
        self.lcr_cb['values'] = found

        if ctrl_label:
            self.lakeshore_cb.set(ctrl_label)
            self.log("Lakeshore 350 auto-selected.")
        elif found:
            self.lakeshore_cb.set(found[0])
            self.log("WARNING: No Lakeshore 350 found; "
                     "defaulted to first device.")

        if alpha_label:
            self.lcr_cb.set(alpha_label)
            self.log("Alpha-AN auto-selected.")
        elif alpha_default:
            self.lcr_cb.set(alpha_default)
            self.log(f"WARNING: no instrument identified itself as an "
                     f"Alpha; selected address {DEFAULT_ALPHA_PAD}. Start "
                     f"will confirm it is an Alpha with a ZG4.")
        elif found:
            self.lcr_cb.set(found[0])
            self.log("WARNING: No Alpha-AN found; "
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