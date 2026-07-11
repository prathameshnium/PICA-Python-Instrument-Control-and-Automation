'''
 PROGRAM:      Novocontrol Alpha-AN Broadband Dielectric Frequency Scan GUI
 PURPOSE:      Fixed-temperature broadband frequency scan on a Novocontrol
               Alpha-AN mainframe with a ZG4 sample interface, over direct
               GPIB. Sweeps a frozen 20 Hz - 1 MHz logarithmic series,
               converts the measured complex impedance to permittivity,
               electric modulus and AC conductivity, and writes a
               WinDETA-compatible file alongside a PICA .dat for the
               built-in Plotter.

               Commands are Novocontrol proprietary (not SCPI), taken from
               the Alpha-AN Impedance Analyzer manual. Command flow was
               cross-checked against JUMP (github.com/JMoVS/JUMP, MIT);
               no code was copied.

               SAFETY: this mainframe has no DC bias hardware. DCV/DCE are
               never transmitted. Every exit path drives the generator to a
               safe state before releasing the VISA session.
'''

import tkinter as tk
from tkinter import (
    ttk,
    Label,
    Entry,
    LabelFrame,
    filedialog,
    messagebox,
    scrolledtext,
    Canvas,
)
import os
import json
import math
import time
import queue
import atexit
import runpy
import threading
import traceback
from datetime import datetime, timezone
from multiprocessing import Process

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


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
            script_dir, "..", "utils", "PlotterUtil_GUI.py"
        )
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}",
            )
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch Plotter Utility: {e}"
        )


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py"
        )
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}",
            )
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror(
            "Launch Error", f"Failed to launch GPIB Scanner: {e}"
        )


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

# --- Packages for Back end ---
try:
    import pyvisa

    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# SAFETY CONSTANTS - hardware ceilings. NEVER raise these to "make it work".
# ===============================================================================

# Alpha-AN generator span, from the manual. Every requested frequency is
# checked against this before a single byte reaches the bus.
FREQ_HW_MIN, FREQ_HW_MAX = 3e-5, 20e6

# Generator AC amplitude ceiling (Vrms), from the ZG4 spec: up to 3 Vrms at
# or below 10 MHz, dropping to 1 Vrms above 10 MHz. Frequency-dependent, so
# it is enforced per-point rather than as one flat number.
ACV_MAX_LOW_FREQ = 3.0        # <= 10 MHz
ACV_MAX_HIGH_FREQ = 1.0       # > 10 MHz
ACV_FREQ_BREAK_HZ = 10e6

# Integration time bounds (s). MTM=0 is legal and means "shortest available";
# the analyzer never integrates less than one period regardless, which is
# where the recipe's "max of 0.5 s or 1 period" behaviour comes from. The
# 0.01 s floor here is conservative but harmless.
MTM_MIN_S, MTM_MAX_S = 0.01, 1000.0

# ZRE? status field. 2 = Result Valid; anything else is not trustworthy.
STATUS_RESULT_VALID = 2
# Statuses that abort the whole sweep rather than just flagging one point.
# 6 = the signal source was disconnected mid-measurement (hardware fault).
STATUS_FATAL = frozenset({6})
STATUS_MEANINGS = {
    2: "Result valid",
    3: "Signal out of range (check ACV or sample)",
    4: "Signal out of range (check ACV or sample)",
    5: "Signal out of range (check ACV or sample)",
    6: "Signal source disconnected during measurement (hardware fault)",
}

# ZG4 interface code reported by INTTYP? (the ZG4 is code 5, not a string).
ZG4_INTERFACE_CODE = 5

# Timeouts (s) for the SRQ waits. A REF calibration takes ~30 s.
SRQ_TIMEOUT_MEASURE_S = 120.0
SRQ_TIMEOUT_CAL_S = 300.0

EPS0_SI = 8.8541878128e-12   # F/m  - used for the geometric capacitance C0
EPS0_CM = 8.8541878128e-14   # F/cm - used only so sigma lands in S/cm

# Wire modes exposed by the ZG4 interface.
WIRE_MODES = ("2", "3", "4")


def acv_ceiling(freq_hz):
    """ZG4 AC-voltage ceiling (Vrms) at a given frequency."""
    return ACV_MAX_LOW_FREQ if freq_hz <= ACV_FREQ_BREAK_HZ else ACV_MAX_HIGH_FREQ


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


# WinDETA column header. Order and names are FIXED - WinFIT parses this
# positionally. Do not add, remove or reorder columns. Zp comes BEFORE Sig,
# matching the WinDETA export paired with the mes_def
# (data_file_for_ref/Fscan_data_Novo_Windeta.dat).
WINDETA_HEADER = (
    " Freq. [Hz]\t Eps'    \t Eps''   \t Modulus'  \t Modulus''  \t"
    " Zp' [Ohms]\t Zp'' [Ohms]\t Sig' [S/cm]\t Sig'' [S/cm]"
)

# Same nine quantities, PICA-style header for the built-in Plotter.
PICA_HEADER = (
    "Frequency\tEps'\tEps''\tModulus'\tModulus''\t"
    "Zp'\tZp''\tSig'\tSig''"
)


# ===============================================================================
# PURE HELPERS - no VISA, no Tk. These are what the numerical tests exercise.
# ===============================================================================

def compute_c0(diameter_mm, thickness_mm):
    """Geometric (empty-cell) capacitance of a round-plate electrode, in F.

    A = pi * (D/2)^2 ;  C0 = eps0 * A / d
    """
    if diameter_mm <= 0 or thickness_mm <= 0:
        raise ValueError("Diameter and thickness must both be positive.")
    d_m = diameter_mm * 1e-3
    t_m = thickness_mm * 1e-3
    area = math.pi * (d_m / 2.0) ** 2
    return EPS0_SI * area / t_m


def compute_c0_from_area(area_cm2, thickness_mm):
    """Geometric (empty-cell) capacitance from electrode AREA, in F.

    For irregular electrodes where the area is known directly.
    C0 = eps0 * A / d, with A in cm^2 and d in mm.
    """
    if area_cm2 <= 0 or thickness_mm <= 0:
        raise ValueError("Area and thickness must both be positive.")
    a_m2 = area_cm2 * 1e-4
    t_m = thickness_mm * 1e-3
    return EPS0_SI * a_m2 / t_m


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


def impedance_to_dielectric(zr, zi, f_actual, c0):
    """Convert one complex-impedance point to the nine WinDETA quantities.

    Z* = R + jX with R = Zs', X = Zs'' (Zs'' is negative for a capacitive
    sample). eps* = eps' - i*eps''.

        eps'  = -X / (w * C0 * |Z|^2)
        eps'' =  R / (w * C0 * |Z|^2)
        M'    =  eps'  / (eps'^2 + eps''^2)
        M''   =  eps'' / (eps'^2 + eps''^2)
        sig'  =  w * eps0 * eps''        (eps0 in F/cm -> sigma in S/cm)
        sig'' = -w * eps0 * (eps' - 1)

    WinDETA's conductivity is sigma* = i*w*eps0*(eps* - 1): the vacuum
    displacement is subtracted, so sig'' uses (eps' - 1), NOT eps'.
    Verified against both reference exports - with bare eps' the .dat
    disagrees by 12% at 10 MHz (eps' ~ 8); with (eps' - 1) every row of
    both files matches to < 1e-4. sig' is unaffected (vacuum is lossless).

    The WinDETA Zp' / Zp'' columns are the PARALLEL-equivalent impedance,
    NOT the series R / X (verified against the Novocontrol WinDETA exports
    data_file_for_ref/Sample_Fscan_RT.TXT and Fscan_data_Novo_Windeta.dat,
    to < 1e-4):

        Zp'  = 1/G =  (R^2 + X^2) / R      # parallel resistance  Rp
        Zp'' = 1/B = -(R^2 + X^2) / X      # parallel reactance   1/(w*Cp)

    with admittance Y = G + jB = 1/Z. Both are geometry-independent (no C0).

    Returns a 9-tuple in WINDETA_HEADER column order.
    """
    omega = 2.0 * math.pi * f_actual
    omega_safe = omega if omega != 0 else 1e-20

    z_mag_sq = zr ** 2 + zi ** 2
    z_mag_sq_safe = z_mag_sq if z_mag_sq != 0 else 1e-20

    c0_safe = c0 if c0 != 0 else 1e-20
    denom = omega_safe * c0_safe * z_mag_sq_safe

    eps1 = -zi / denom
    eps2 = zr / denom

    eps_sq = eps1 ** 2 + eps2 ** 2
    eps_sq_safe = eps_sq if eps_sq != 0 else 1e-20
    m1 = eps1 / eps_sq_safe
    m2 = eps2 / eps_sq_safe

    sig1 = omega * EPS0_CM * eps2
    # (eps1 - 1): WinDETA's sigma* = i*w*eps0*(eps* - 1), see docstring.
    sig2 = -omega * EPS0_CM * (eps1 - 1.0)

    # Parallel-equivalent impedance (1/G and 1/B), matching WinDETA. These are
    # geometry-independent, so they do not use C0.
    zr_safe = zr if zr != 0 else 1e-20
    zi_safe = zi if zi != 0 else 1e-20
    zp1 = z_mag_sq / zr_safe    # 1/G = parallel resistance
    zp2 = -z_mag_sq / zi_safe   # 1/B = parallel reactance

    return (f_actual, eps1, eps2, m1, m2, zp1, zp2, sig1, sig2)


def validate_parameters(params):
    """Reject anything unsafe BEFORE a single byte reaches the instrument.

    Raises ValueError on the first violation.
    """
    frequencies = params["frequencies"]
    if not frequencies:
        raise ValueError("Frequency list is empty.")

    acv = params["acv"]
    # The ZG4 ceiling is frequency-dependent (3 Vrms <=10 MHz, 1 Vrms above),
    # so the binding limit is the lowest ceiling over the whole sweep.
    max_acv = min(acv_ceiling(f) for f in frequencies)
    if not (0.0 < acv <= max_acv):
        raise ValueError(
            f"AC voltage {acv} Vrms outside the 0 < V <= {max_acv} Vrms "
            f"safety limit (frequency-dependent ZG4 ceiling)."
        )

    mtm = params["mtm"]
    if not (MTM_MIN_S <= mtm <= MTM_MAX_S):
        raise ValueError(
            f"Integration time {mtm} s outside {MTM_MIN_S}-{MTM_MAX_S} s."
        )

    if params["wire_mode"] not in WIRE_MODES:
        raise ValueError(f"Wire mode must be one of {WIRE_MODES}.")

    if params["geometry_mode"] == "area":
        if params["area_cm2"] <= 0 or params["thickness_mm"] <= 0:
            raise ValueError("Area and thickness must both be positive.")
    else:
        if params["diameter_mm"] <= 0 or params["thickness_mm"] <= 0:
            raise ValueError("Diameter and thickness must both be positive.")

    if params["delay"] < 0:
        raise ValueError("Initial delay cannot be negative.")

    for f in frequencies:
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(
                f"Frequency {f} Hz is outside the Alpha-AN range "
                f"{FREQ_HW_MIN}-{FREQ_HW_MAX} Hz."
            )


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

        try:
            idn = inst.query("*IDN?").strip()
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
            self.close_instrument()
            raise

        print(f"  Connected: {idn} | ZG4 interface code {code}")
        return idn, code

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
            self.instrument.write("MBK")
            self._task_may_be_running = False
        except Exception as e:
            return f"MBK failed: {e}"
        try:
            state = self.instrument.query("ZTSTAT?").strip()
        except Exception as e:
            state = f"ZTSTAT? failed: {e}"
        return state

    # --- calibration ----------------------------------------------------

    def run_reference_calibration(self, timeout_s=SRQ_TIMEOUT_CAL_S):
        """Run ZRUNCAL REF. The SAMPLE MUST BE DISCONNECTED.

        REF calibrates the internal reference capacitors against each other
        (~30 s). It is the before-measurement subset of the full ZRUNCAL=ALL
        converter calibration (~1 h, monthly maintenance, not done here).

        CONCHECK runs automatically ahead of REF - do NOT invoke it here.
        """
        inst = self.instrument

        # The manual recommends *RST immediately before a calibration so that
        # prior user settings cannot perturb it. *RST preserves the stored
        # calibration tables; it only resets operating parameters (and parks
        # the generator at ACV=0, DCE=0).
        inst.write("*RST")
        time.sleep(1.0)
        inst.write("MODE=IMP")

        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(f"ZG4 not present (INTTYP code {code}).")

        self._task_may_be_running = True
        inst.write("ZRUNCAL=REF_INIT")
        self._wait_for_srq(timeout_s, "REF_INIT")

        self._task_may_be_running = True
        inst.write("ZRUNCAL=REF")
        self._wait_for_srq(timeout_s, "REF calibration")

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

        validate_parameters(p)

        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")

        inst.write("*RST")
        time.sleep(1.0)  # graceful reset
        inst.write("MODE=IMP")

        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(f"ZG4 not present (INTTYP code {code}).")

        # Settings AFTER any calibration: ZRUNCAL resets ACV, DCE and MTM.
        # ZREFMODE/ZLLCOR/ZSLCAL already hold these values after *RST; we
        # write them anyway - defensive against a non-default state, and it
        # documents intent at the call site.
        #
        # OPEN ITEM (manual unverified): ZREFMODE=-3 is believed to encode
        # the mes_def's "Reference Measurement: Always, 3 auto capacitors
        # max", but this mapping has not been re-checked against the
        # Alpha-AN manual. Verify when the manual is at hand.
        inst.write("ZREFMODE=-3")   # auto reference caps, up to 3
        inst.write("ZLLCOR=1")      # low-loss correction on
        inst.write("ZSLCAL=1")      # apply stored load-short cal data
        # OPEN ITEM (manual unverified): the mes_def specifies "Low capacity
        # open calibration: Off". That is the *RST default, so no command is
        # sent - but the exact disable command could not be verified from the
        # Alpha-AN manual or JUMP. Per the lab's gentle-bus policy we never
        # send a guessed command; once the manual confirms the command name,
        # send an explicit "=0" here beside ZSLCAL.
        inst.write(f"FRS={p['wire_mode']}")
        inst.write("DRS=0 0")       # driven shields off
        time.sleep(0.2)

        inst.write(f"ACV={p['acv']:.6g}")
        inst.write(f"MTM={p['mtm']:.6g}")
        time.sleep(0.2)

        # DCV / DCE are NEVER sent: this mainframe has no bias hardware.

        print(f"  Configured: ACV={p['acv']} Vrms, MTM={p['mtm']} s, "
              f"FRS={p['wire_mode']}-wire")

    # --- per-point -------------------------------------------------------

    def measure_point(self, freq, timeout_s=SRQ_TIMEOUT_MEASURE_S):
        """Set frequency, trigger one point, wait for SRQ, read the result.

        Returns (Zs', Zs'', f_actual, status, ref). The caller must use
        f_actual - not the requested frequency - for the conversion.
        """
        inst = self.instrument
        if inst is None:
            raise ConnectionError("Instrument is not connected.")

        inst.write(f"GFR={freq:.6e}")
        # Clear any stale status immediately before triggering, so a fast
        # point cannot let wait_for_srq latch onto a leftover completion.
        inst.read_stb()
        self._task_may_be_running = True
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
            try:
                inst.write("MBK")
                self._task_may_be_running = False
            except Exception as e:
                print(f"  Warning: MBK failed during safe_state: {e}")
        for cmd in ("ACV=0", "ZCONSPL=0"):
            try:
                inst.write(cmd)
            except Exception as e:
                print(f"  Warning: {cmd} failed during safe_state: {e}")

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
# FRONTEND CLASS - The Main GUI Application
# ===============================================================================

class AlphaAN_FreqScan_GUI:
    """The main GUI application class for the Alpha-AN frequency scan."""

    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 480

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theme Colours ---
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_ACCENT_GOLD = "#BA6B5E"
    CLR_ACCENT_GREEN = "#B68B6E"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    FONT_SIZE_BASE = 11
    FONT_BASE = ("Segoe UI", FONT_SIZE_BASE)
    FONT_TITLE = ("Segoe UI", FONT_SIZE_BASE + 2, "bold")
    FONT_CONSOLE = ("Consolas", 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Novocontrol Alpha-AN Frequency Scan")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.is_calibrating = False
        self._stopping = False          # re-entrancy guard for stop_sweep
        self._close_after_stop = False  # destroy window once worker exits
        self.backend = AlphaAN_Backend()
        self.file_location_path = ""
        self.txt_filepath = ""
        self.dat_filepath = ""

        # Threading components
        self.data_queue = queue.Queue()
        self.cal_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.cal_thread = None

        # Forced cleanup: close the backend on crash/exit.
        atexit.register(self.backend.close_instrument)

        self.data_storage = {"freq": [], "eps1": [], "eps2": []}
        self.logo_image = None
        self.sweep_index = 0
        self.sweep_frequencies = FREQ_PRESETS[DEFAULT_FREQ_PRESET]
        self.c0_farads = None
        self.cal_age_str = format_cal_age(read_last_ref_cal())

        # Decade log autoscale state: eps' and eps'' span several decades
        # over a broadband sweep, so log-Y is on by default.
        self.log_y_var = tk.BooleanVar(value=True)
        self._decade_ylims = {}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
        style.configure(
            "TLabel",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure(
            "TCheckbutton",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure(
            "TLabelframe",
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE,
        )
        style.configure(
            "TButton",
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor="none",
        )
        style.map(
            "TButton",
            background=[
                ("active", self.CLR_ACCENT_GOLD),
                ("hover", self.CLR_ACCENT_GOLD),
            ],
            foreground=[
                ("active", self.CLR_TEXT_DARK),
                ("hover", self.CLR_TEXT_DARK),
            ],
        )
        style.configure(
            "Start.TButton",
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK,
        )
        style.configure(
            "Stop.TButton",
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT,
        )
        style.configure(
            "green.Horizontal.TProgressbar",
            background=self.CLR_ACCENT_GREEN,
        )

        mpl.rcParams.update(
            {
                "font.family": "Segoe UI",
                "font.size": self.FONT_SIZE_BASE,
                "axes.titlesize": self.FONT_SIZE_BASE + 2,
                "axes.labelsize": self.FONT_SIZE_BASE,
                "figure.facecolor": self.CLR_GRAPH_BG,
            }
        )

    def create_widgets(self):
        font_title_italic = (
            "Segoe UI",
            self.FONT_SIZE_BASE + 2,
            "bold",
            "italic",
        )
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side="top", fill="x")

        Label(
            header_frame,
            text="Novocontrol Alpha-AN: Broadband Frequency Scan",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=font_title_italic,
        ).pack(side="left", padx=20, pady=10)

        ttk.Button(
            header_frame, text="📈", command=launch_plotter_utility, width=3
        ).pack(side="right", padx=10, pady=5)
        ttk.Button(
            header_frame, text="📟", command=launch_gpib_scanner, width=3
        ).pack(side="right", padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(
            self.main_pane, width=self.LEFT_PANEL_WIDTH
        )
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        canvas = Canvas(
            left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0
        )
        scrollbar = ttk.Scrollbar(
            left_panel_container, orient="vertical", command=canvas.yview
        )
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw"
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width),
        )
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill="x", expand=True, padx=10, pady=5)

        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill="x", expand=True, padx=10, pady=5)

        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

        # sashpos() silently no-ops before the PanedWindow is mapped.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1)
                )
        except tk.TclError:
            if attempt < 10:
                self.root.after(
                    100, lambda: self._set_default_sash_position(attempt + 1)
                )

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0,
        )
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                    image=self.logo_image,
                )
            except Exception:
                pass

        institute_font = ("Segoe UI", self.FONT_SIZE_BASE + 2, "bold")
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=0, column=1, padx=10, pady=(10, 0), sticky="sw")
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK,
        ).grid(row=1, column=1, padx=10, sticky="nw")

        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Experiment Parameters")
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        padx = 10

        def _vfloat(P):
            if P in ("", "-", ".", "-."):
                return True
            try:
                float(P)
                return True
            except ValueError:
                return False

        self._vfloat_cmd = (frame.register(_vfloat), "%P")

        # No DC bias hardware on this mainframe - DCV/DCE are never sent.
        # (Deliberately not shown in the UI; see the SAFETY note in the
        # module docstring and initialize_instrument.)

        self._add_entry(
            frame, "Sample Name", "sample_name", 1, 0, colspan=2,
            default="Sample_AlphaAN",
        )
        self._add_entry(
            frame, "Comment (WinDETA header)", "comment", 3, 0, colspan=2,
            default="Alpha-AN frequency scan",
        )
        self._add_entry(
            frame, "AC Voltage (Vrms)", "acv", 5, 0, default="1.0"
        )
        self._add_entry(
            frame, "Integration Time (s)", "mtm", 5, 1, default="0.5"
        )

        # Geometry can be given either as a round-plate DIAMETER or directly
        # as an electrode AREA (irregular electrodes). Default is area mode
        # with A = 1 cm^2, d = 1 mm.
        self.geom_mode_var = tk.StringVar(value="area")
        geom_radio_frame = ttk.Frame(frame)
        geom_radio_frame.grid(
            row=7, column=0, columnspan=2, padx=padx, pady=(2, 0), sticky="w"
        )
        Label(
            geom_radio_frame, text="Geometry input:", font=self.FONT_BASE
        ).pack(side="left")
        ttk.Radiobutton(
            geom_radio_frame, text="Area (cm²)", value="area",
            variable=self.geom_mode_var, command=self._on_geom_mode_change,
        ).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(
            geom_radio_frame, text="Diameter (mm)", value="diameter",
            variable=self.geom_mode_var, command=self._on_geom_mode_change,
        ).pack(side="left", padx=(10, 0))

        self.lbl_geom = self._add_entry(
            frame, "Electrode Area (cm²)", "geom_value", 8, 0,
            default="1.0",
        )
        self._add_entry(
            frame, "Sample Thickness (mm)", "thickness_mm", 8, 1,
            default="1.0",
        )
        self._add_entry(
            frame, "Initial Delay (s)", "delay", 10, 0, default="1.0"
        )

        Label(
            frame, text="ZG4 Wire Mode (FRS):", font=self.FONT_BASE
        ).grid(row=10, column=1, padx=padx, pady=(2, 0), sticky="w")
        self.wire_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly",
            values=list(WIRE_MODES),
        )
        self.wire_combobox.set("2")
        self.wire_combobox.grid(
            row=11, column=1, padx=padx, pady=(0, 10), sticky="ew"
        )

        for key in ("acv", "mtm", "geom_value", "thickness_mm", "delay"):
            self.entries[key].config(
                validate="key", validatecommand=self._vfloat_cmd
            )

        # Live C0 readout, recomputed as the geometry is typed.
        self.lbl_c0 = ttk.Label(
            frame, text="C0 = --", font=self.FONT_BASE,
            foreground=self.CLR_TEXT_DARK,
        )
        self.lbl_c0.grid(row=12, column=0, columnspan=2, padx=padx, sticky="w")
        for key in ("geom_value", "thickness_mm"):
            self.entries[key].bind("<KeyRelease>", self._update_c0_label)
        self._update_c0_label()

        # Two frozen scan recipes only - the lists themselves are never
        # user-editable, so runs stay comparable point-for-point.
        Label(
            frame, text="Frequency List:", font=self.FONT_BASE
        ).grid(
            row=13, column=0, columnspan=2, padx=padx, pady=(10, 2), sticky="w"
        )
        self.freq_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly",
            values=list(FREQ_PRESETS),
        )
        self.freq_combobox.set(DEFAULT_FREQ_PRESET)
        self.freq_combobox.grid(
            row=14, column=0, columnspan=2, padx=padx, pady=(0, 4),
            sticky="ew",
        )

        Label(
            frame, text="Alpha-AN VISA (GPIB):", font=self.FONT_BASE
        ).grid(
            row=15, column=0, columnspan=2, padx=padx, pady=(10, 2), sticky="w"
        )
        self.visa_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state="readonly"
        )
        self.visa_combobox.grid(
            row=16, column=0, columnspan=2, padx=padx, pady=(0, 10),
            sticky="ew",
        )

        self.scan_button = ttk.Button(
            frame, text="Scan Instruments", command=self._scan_for_visa
        )
        self.scan_button.grid(row=17, column=0, padx=padx, pady=5, sticky="ew")

        ttk.Button(
            frame, text="Browse Save Loc...",
            command=self._browse_file_location,
        ).grid(row=17, column=1, padx=padx, pady=5, sticky="ew")

        # REF calibration is a DELIBERATE, SEPARATE action. Start never
        # calibrates: the sample may be permanently mounted in the cell.
        self.cal_button = ttk.Button(
            frame, text="Run REF Calibration",
            command=self.run_reference_calibration,
        )
        self.cal_button.grid(
            row=18, column=0, columnspan=2, padx=padx, pady=(10, 2),
            sticky="ew",
        )

        self.lbl_cal_age = ttk.Label(
            frame, text=self.cal_age_str, font=("Segoe UI", 9),
            foreground=self.CLR_TEXT_DARK,
        )
        self.lbl_cal_age.grid(
            row=19, column=0, columnspan=2, padx=padx, sticky="w"
        )

        self.start_button = ttk.Button(
            frame, text="Start Sweep", command=self.start_sweep,
            style="Start.TButton",
        )
        self.start_button.grid(
            row=20, column=0, padx=(padx, 5), pady=15, sticky="ew"
        )
        self.stop_button = ttk.Button(
            frame, text="Stop", command=self.stop_sweep,
            style="Stop.TButton", state="disabled",
        )
        self.stop_button.grid(
            row=20, column=1, padx=(5, padx), pady=15, sticky="ew"
        )

        self.lbl_status = ttk.Label(
            frame, text="Measuring: -- Hz",
            font=("Segoe UI", 12, "bold"),
            foreground=self.CLR_ACCENT_RED,
        )
        self.lbl_status.grid(row=21, column=0, columnspan=2, pady=5)

        self.progress_bar = ttk.Progressbar(
            frame, orient="horizontal", mode="determinate",
            style="green.Horizontal.TProgressbar",
        )
        self.progress_bar.grid(
            row=22, column=0, columnspan=2, padx=padx, pady=(5, 10),
            sticky="ew",
        )

        return frame

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text="Console Output",
            relief="groove",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state="disabled",
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap="word",
            bd=0,
            height=8,
        )
        self.console_widget.pack(pady=5, padx=5, fill="both", expand=True)
        self.log(
            f"Alpha-AN Frequency Scan initialized. "
            f"{len(FREQ_PRESETS)} frozen frequency presets available."
        )
        self.log(self.cal_age_str)
        return frame

    def create_graph_frame(self, parent):
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)

        self.ax_eps1 = self.figure.add_subplot(2, 1, 1)
        self.line_eps1, = self.ax_eps1.plot(
            [], [], color="#C00000", marker="o", markersize=3, linestyle="-"
        )
        self.ax_eps1.set_ylabel("Permittivity, eps'")
        self.ax_eps1.set_xscale("log")
        self.ax_eps1.grid(True, linestyle="--", alpha=0.7)

        self.ax_eps2 = self.figure.add_subplot(2, 1, 2)
        self.line_eps2, = self.ax_eps2.plot(
            [], [], color="#2A6B3A", marker="s", markersize=3, linestyle="-"
        )
        self.ax_eps2.set_xlabel("Frequency (Hz)")
        self.ax_eps2.set_ylabel("Dielectric Loss, eps''")
        self.ax_eps2.set_xscale("log")
        self.ax_eps2.grid(True, linestyle="--", alpha=0.7)

        self.figure.subplots_adjust(
            left=0.10, right=0.98, top=0.98, bottom=0.07, hspace=0.15
        )

        ttk.Checkbutton(
            parent,
            text="Log Y scale (decade autoscale)",
            variable=self.log_y_var,
            command=self._on_log_y_toggle,
        ).pack(anchor="w", padx=5, pady=(5, 0))

        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=0, pady=0
        )

    def _on_log_y_toggle(self):
        """Re-snap axes from scratch when the log-Y checkbox flips."""
        self._decade_ylims.clear()
        self._update_sweep_plot()

    def _decade_autoscale_y(self, ax, values, key):
        """LabVIEW-style decade autoscale: snap y-limits to
        [10^floor(log10(min_pos)), 10^ceil(log10(max_pos))]; expand only,
        whole decades at a time, so the scale never jitters per point.
        Linear fallback if no positive finite data."""
        pos = [v for v in values if isinstance(v, (int, float))
               and math.isfinite(v) and v > 0]
        if not pos:
            ax.set_yscale('linear')
            ax.relim()
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

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state="normal")
        self.console_widget.insert("end", f"[{timestamp}] {message}\n")
        self.console_widget.see("end")
        self.console_widget.config(state="disabled")

    def _add_entry(self, parent, text, dict_key, r, c, colspan=1, default=""):
        label = Label(parent, text=f"{text}:", font=self.FONT_BASE)
        label.grid(row=r, column=c, padx=10, pady=(2, 0), sticky="w")
        entry = Entry(parent, font=self.FONT_BASE)
        entry.grid(
            row=r + 1, column=c, columnspan=colspan, padx=10,
            pady=(0, 10), sticky="ew",
        )
        entry.insert(0, default)
        self.entries[dict_key] = entry
        return label

    def _on_geom_mode_change(self):
        """Relabel the geometry entry when the Diameter/Area toggle flips."""
        if self.geom_mode_var.get() == "area":
            self.lbl_geom.config(text="Electrode Area (cm²):")
        else:
            self.lbl_geom.config(text="Electrode Diameter (mm):")
        self._update_c0_label()

    def _compute_c0_from_inputs(self, geom_value, thickness_mm):
        """C0 from the current geometry mode and typed values."""
        if self.geom_mode_var.get() == "area":
            return compute_c0_from_area(geom_value, thickness_mm)
        return compute_c0(geom_value, thickness_mm)

    def _update_c0_label(self, _event=None):
        try:
            g = float(self.entries["geom_value"].get())
            t = float(self.entries["thickness_mm"].get())
            self.c0_farads = self._compute_c0_from_inputs(g, t)
            self.lbl_c0.config(text=f"C0 = {self.c0_farads:.5e} F")
        except (ValueError, ZeroDivisionError):
            self.c0_farads = None
            self.lbl_c0.config(text="C0 = -- (check geometry)")

    # --- VISA helpers ----------------------------------------------------

    def _selected_visa_address(self):
        raw = self.visa_combobox.get()
        if "  ->  " in raw:
            return raw.split("  ->  ")[0].strip()
        return raw.strip()

    def _scan_for_visa(self):
        """Identity-aware instrument scan; auto-selects a Novocontrol Alpha."""
        if not PYVISA_AVAILABLE or self.backend.rm is None:
            self.log("ERROR: PyVISA/VISA manager unavailable.")
            return

        self.log("Scanning for VISA instruments (querying *IDN?)...")
        rm = self.backend.rm
        found = []
        alpha_label = None

        try:
            for res in rm.list_resources():
                idn = "Unknown / no response"
                try:
                    with rm.open_resource(res) as dev:
                        dev.timeout = 2000
                        # EOI-only, matching the Alpha-AN, so it is
                        # identifiable here (it uses no newline terminator).
                        dev.read_termination = ""
                        dev.write_termination = ""
                        dev.send_end = True
                        idn = dev.query("*IDN?").strip()
                except Exception:
                    pass  # busy / non-SCPI / timeout - skip silently

                label = f"{res}  ->  {idn}"
                found.append(label)
                upper = idn.upper()
                if alpha_label is None and (
                    "NOVOCONTROL" in upper or "ALPHA" in upper
                ):
                    alpha_label = label
                self.log(f"  {label}")

            self.visa_combobox["values"] = found

            if alpha_label:
                self.visa_combobox.set(alpha_label)
                self.log("Alpha-AN auto-selected.")
            elif found:
                self.visa_combobox.set(found[0])
                self.log(
                    "WARNING: No Novocontrol Alpha found; "
                    "defaulted to first device."
                )
            else:
                self.log("No VISA instruments found.")

        except Exception as e:
            self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    # --- REF calibration --------------------------------------------------

    def run_reference_calibration(self):
        """Prompt, then run ZRUNCAL REF in a worker thread.

        Deliberately never invoked from start_sweep: with a permanently
        mounted cell the operator cannot disconnect the sample on demand.
        """
        if self.is_running or self.is_calibrating:
            return

        visa_addr = self._selected_visa_address()
        if not visa_addr:
            messagebox.showerror(
                "Calibration", "Select the Alpha-AN VISA address first."
            )
            return

        if not messagebox.askokcancel(
            "Reference Calibration",
            "DISCONNECT the sample from the ZG4 now.\n\n"
            "Reference calibration takes about 30 seconds and must run "
            "with nothing connected to the sample interface.\n\n"
            "Press OK once the sample is disconnected.",
        ):
            return

        self.is_calibrating = True
        self.cal_button.config(state="disabled")
        self.start_button.config(state="disabled")
        self.scan_button.config(state="disabled")
        self.lbl_status.config(text="Calibrating: REF...")
        self.log("Starting reference calibration (sample disconnected)...")

        self.cal_thread = threading.Thread(
            target=self._cal_loop, args=(visa_addr,), daemon=True
        )
        self.cal_thread.start()
        self.root.after(200, self._poll_cal_queue)

    def _cal_loop(self, visa_addr):
        """Worker thread: connect, calibrate, disconnect. Never raises out."""
        try:
            self.backend.connect(visa_addr)
            self.backend.run_reference_calibration()
            self.cal_queue.put(("CAL_DONE", None))
        except Exception as e:
            self.backend.safe_state()
            self.cal_queue.put(("CAL_ERROR", (e, traceback.format_exc())))
        finally:
            try:
                self.backend.close_instrument()
            except Exception:
                pass

    def _poll_cal_queue(self):
        try:
            while not self.cal_queue.empty():
                kind, payload = self.cal_queue.get_nowait()
                if kind == "CAL_DONE":
                    self._finish_calibration(None)
                    return
                if kind == "CAL_ERROR":
                    self._finish_calibration(payload)
                    return
        except queue.Empty:
            pass

        if self.is_calibrating:
            self.root.after(200, self._poll_cal_queue)

    def _finish_calibration(self, error):
        self.is_calibrating = False
        self.cal_button.config(state="normal")
        self.start_button.config(state="normal")
        self.scan_button.config(state="normal")

        if error is not None:
            exc, tb = error
            self.lbl_status.config(text="Calibration: FAILED")
            self.log(f"CALIBRATION ERROR: {exc}\n{tb}")
            messagebox.showerror(
                "Calibration Failed",
                f"Reference calibration failed:\n\n{exc}\n\n"
                "See console for the full traceback.",
            )
            return

        stamp = write_last_ref_cal()
        self.cal_age_str = format_cal_age(stamp)
        self.lbl_cal_age.config(text=self.cal_age_str)
        self.lbl_status.config(text="Calibration: DONE")
        self.log(f"Reference calibration complete. {self.cal_age_str}")
        messagebox.showinfo(
            "Calibration Complete",
            "Reference calibration finished.\n\n"
            "RECONNECT the sample to the ZG4 before starting a sweep.",
        )

    # --- sweep ------------------------------------------------------------

    def start_sweep(self):
        try:
            if self.is_calibrating:
                raise RuntimeError("A calibration is still running.")

            geom_mode = self.geom_mode_var.get()
            params = {
                "sample_name": self.entries["sample_name"].get().strip(),
                "comment": self.entries["comment"].get().strip(),
                "acv": float(self.entries["acv"].get()),
                "mtm": float(self.entries["mtm"].get()),
                "geometry_mode": geom_mode,
                "thickness_mm": float(self.entries["thickness_mm"].get()),
                "delay": float(self.entries["delay"].get()),
                "wire_mode": self.wire_combobox.get(),
                "visa": self._selected_visa_address(),
                "freq_preset": self.freq_combobox.get(),
                "frequencies": FREQ_PRESETS[self.freq_combobox.get()],
            }
            geom_value = float(self.entries["geom_value"].get())
            if geom_mode == "area":
                params["area_cm2"] = geom_value
            else:
                params["diameter_mm"] = geom_value
            if not all([params["sample_name"], params["visa"],
                        self.file_location_path]):
                raise ValueError(
                    "Sample Name, VISA address, and Save Location "
                    "are required."
                )

            # Validate BEFORE opening the session: nothing unsafe ever
            # reaches the bus.
            validate_parameters(params)
            self.c0_farads = self._compute_c0_from_inputs(
                geom_value, params["thickness_mm"]
            )

            self.cal_age_str = format_cal_age(read_last_ref_cal())
            self.lbl_cal_age.config(text=self.cal_age_str)
            self.log(self.cal_age_str)

            self.backend.connect(params["visa"])
            self.backend.initialize_instrument(params)

            self._open_output_files(params)

            try:
                self.is_running = True
                self.start_button.config(state="disabled")
                self.cal_button.config(state="disabled")
                self.scan_button.config(state="disabled")
                self.stop_button.config(state="normal")

                for key in self.data_storage:
                    self.data_storage[key].clear()
                self.line_eps1.set_data([], [])
                self.line_eps2.set_data([], [])
                self._decade_ylims.clear()
                self.canvas.draw()

                self.sweep_index = 0
                self.sweep_frequencies = params["frequencies"]
                self.progress_bar["value"] = 0
                self.progress_bar["maximum"] = len(self.sweep_frequencies)

                self.sweep_delay = params["delay"]
                self.log(
                    f"Starting frequency sweep: {params['freq_preset']}, "
                    f"C0 = {self.c0_farads:.5e} F"
                )

                self.stop_event.clear()
                self.worker_thread = threading.Thread(
                    target=self._sweep_loop, daemon=True
                )
                self.worker_thread.start()
                self.root.after(100, self._poll_queue)

            except Exception as sweep_err:
                self.backend.close_instrument()
                raise sweep_err

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            self.backend.close_instrument()
            messagebox.showerror(
                "Initialization Error", f"Could not start sweep.\n\n{e}"
            )

    def _open_output_files(self, params):
        """Create both output files and write their headers."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{params['sample_name']}_{timestamp}_AlphaAN_FreqScan"
        self.txt_filepath = os.path.join(self.file_location_path, base + ".txt")
        self.dat_filepath = os.path.join(self.file_location_path, base + ".dat")

        now = datetime.now()
        # Line 1 is the comment ONLY, exactly like the WinDETA exports; the
        # cal age lives in the PICA .dat header and the console instead.
        # Date/time matches Fscan_data_Novo_Windeta.dat: space-padded day,
        # zero-padded month and minute (e.g. " 9.07.2026, 20:00").
        date_str = f"{now.day:2d}.{now.month:02d}.{now.year}"
        time_str = f"{now.hour}:{now.minute:02d}"
        with open(self.txt_filepath, "w", encoding="utf-8") as fh:
            fh.write(f"{params['comment']}, {date_str}, {time_str}\n")
            fh.write(
                f"Fixed value(s) :  AC Volt  [Vrms]={params['acv']:.4e}\n"
            )
            fh.write(WINDETA_HEADER + "\n")

        if params["geometry_mode"] == "area":
            geometry_str = f"A = {params['area_cm2']} cm^2"
        else:
            geometry_str = f"D = {params['diameter_mm']} mm"
        with open(self.dat_filepath, "w", encoding="utf-8") as fh:
            fh.write(
                f"# Sample: {params['sample_name']} | "
                f"ACV: {params['acv']} Vrms | MTM: {params['mtm']} s | "
                f"FRS: {params['wire_mode']}-wire | "
                f"List: {params['freq_preset']}\n"
            )
            fh.write(
                f"# Geometry: {geometry_str}, "
                f"d = {params['thickness_mm']} mm, "
                f"C0 = {self.c0_farads:.5e} F\n"
            )
            fh.write(f"# {self.cal_age_str}\n")
            fh.write(PICA_HEADER + "\n")

        self.log(f"Output files: {os.path.basename(self.txt_filepath)} "
                 f"(WinDETA) + .dat (PICA)")

    def _sweep_loop(self):
        """Worker thread: SRQ-driven per-point acquisition.

        Never polls ZTSTAT?/ZRE? during a measurement - the extra bus traffic
        degrades point accuracy. On any failure the generator is driven to a
        safe state HERE, before the error reaches the GUI.
        """
        try:
            if self.sweep_delay > 0:
                time.sleep(self.sweep_delay)

            for i, target_f in enumerate(self.sweep_frequencies):
                if self.stop_event.is_set():
                    break

                zr, zi, f_actual, status, ref = self.backend.measure_point(
                    target_f
                )

                if status != STATUS_RESULT_VALID:
                    if status in STATUS_FATAL:
                        # e.g. signal source disconnected mid-sweep: abort
                        # the whole run, do not just flag one point.
                        raise RuntimeError(
                            f"f={target_f:.6g} Hz: {status_message(status)} "
                            f"(status {status})."
                        )
                    self.data_queue.put(("FLAG", (target_f, status)))
                    continue

                # Convert with the ACTUAL frequency the analyzer used.
                row = impedance_to_dielectric(
                    zr, zi, f_actual, self.c0_farads
                )
                self.data_queue.put(("POINT", (i, row, ref)))

            if not self.stop_event.is_set():
                self.data_queue.put(("DONE", None))

        except Exception as e:
            # CRITICAL SAFETY: park the generator before surfacing the error.
            self.backend.safe_state()
            # Traceback must be captured HERE (worker thread) - the main
            # thread's format_exc() would just print "NoneType: None".
            self.data_queue.put(("ERROR", (e, traceback.format_exc())))

    def _poll_queue(self):
        """Main thread: drain the worker's queue."""
        try:
            while not self.data_queue.empty():
                kind, payload = self.data_queue.get_nowait()

                if kind == "DONE":
                    self._handle_sweep_completion()
                    return
                if kind == "ERROR":
                    exc, tb = payload
                    self._handle_sweep_error(exc, tb)
                    return
                if kind == "FLAG":
                    f, status = payload
                    self.log(
                        f"WARNING: f = {f:,.6g} Hz -> {status_message(status)} "
                        f"(status {status}). Point discarded."
                    )
                    continue

                idx, row, ref = payload
                self.sweep_index = idx + 1
                self.lbl_status.config(text=f"Measuring: {row[0]:,.6g} Hz")
                self._process_sweep_point(row, ref)
                self._update_sweep_plot()
        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(100, self._poll_queue)

    def _process_sweep_point(self, row, ref):
        f_actual, eps1, eps2 = row[0], row[1], row[2]
        self.log(
            f"f: {f_actual:,.6g} Hz | eps': {eps1:.4e} | "
            f"eps'': {eps2:.4e} | ref: {ref}"
        )
        self._write_row(row)

        self.data_storage["freq"].append(f_actual)
        self.data_storage["eps1"].append(eps1)
        self.data_storage["eps2"].append(eps2)

    def _write_row(self, row):
        """Append one point to BOTH files and flush.

        Single source of truth for the row, formatted twice, so the WinDETA
        file and the PICA file can never disagree. Write-on-acquisition: a
        crash or abort still leaves every acquired point on disk.
        """
        line = "\t".join(f"{v:.5e}" for v in row)
        for path in (self.txt_filepath, self.dat_filepath):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()

    def _update_sweep_plot(self):
        self.line_eps1.set_data(
            self.data_storage["freq"], self.data_storage["eps1"]
        )
        self.line_eps2.set_data(
            self.data_storage["freq"], self.data_storage["eps2"]
        )

        for ax, key, data in (
            (self.ax_eps1, "eps1", self.data_storage["eps1"]),
            (self.ax_eps2, "eps2", self.data_storage["eps2"]),
        ):
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
            if self.log_y_var.get():
                self._decade_autoscale_y(ax, data, key)
            else:
                ax.set_yscale('linear')
                ax.autoscale_view(scaley=True)

        self.canvas.draw_idle()
        self.progress_bar["value"] = self.sweep_index

    # --- stop / teardown --------------------------------------------------

    def stop_sweep(self, reason=""):
        """Signal the worker to stop, then finish cleanup asynchronously.

        The instrument is closed only after the worker thread has exited
        (non-blocking root.after() poll), so the worker never touches a
        closed VISA handle and the GUI never freezes while a slow SRQ wait
        drains. The worker is NEVER hard-killed: cancel and SRQ timeout must
        both unwind through the safe-state path.
        """
        if self._stopping or not self.is_running:
            return
        self._stopping = True
        self.is_running = False
        self.stop_event.set()
        self.lbl_status.config(text="Measuring: STOPPING…")
        self.stop_button.config(state="disabled")

        if reason:
            self.log(f"Sweep stopped: {reason}")
        else:
            self.log("Sweep stopped by user.")
        self.log("Waiting for worker thread to finish...")

        # A single point can legitimately take a long MTM at low frequency,
        # so allow the full measurement SRQ timeout plus slack.
        self._stop_deadline = time.time() + SRQ_TIMEOUT_MEASURE_S + 15.0
        self._poll_worker_stopped(reason)

    def _poll_worker_stopped(self, reason):
        t = self.worker_thread
        if t is not None and t.is_alive() and time.time() < self._stop_deadline:
            self.root.after(200, lambda: self._poll_worker_stopped(reason))
            return
        if t is not None and t.is_alive():
            self.log(
                "WARNING: worker did not exit within timeout; "
                "forcing instrument to safe state and closing anyway."
            )
        self._finalize_stop(reason)

    def _finalize_stop(self, reason):
        try:
            self.backend.close_instrument()
        except Exception as e:
            self.log(f"WARNING: error closing instrument: {e}")

        self.lbl_status.config(text="Measuring: STOPPED")
        self.start_button.config(state="normal")
        self.scan_button.config(state="normal")
        self.cal_button.config(state="normal")
        self._stopping = False

        if self._close_after_stop:
            self.root.destroy()
            return

        if not reason:
            messagebox.showinfo(
                "Info", "Sweep stopped and instrument disconnected."
            )

    def _handle_sweep_completion(self):
        self.lbl_status.config(text="Measuring: DONE")
        self.log("Sweep finished successfully.")
        self.stop_sweep("Sweep naturally complete.")
        messagebox.showinfo("Finished", "Frequency sweep is complete.")

    def _handle_sweep_error(self, exception, tb=None):
        self.log(f"RUNTIME ERROR: {exception}\n{tb or ''}")
        self.stop_sweep("A critical hardware or measurement error occurred.")
        messagebox.showerror(
            "Runtime Error",
            f"An error occurred during the sweep:\n\n{exception}\n\n"
            "See console for the full traceback.",
        )

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Sweep is running. Stop and exit?"):
                # Destroy is deferred to _finalize_stop so the worker can
                # exit cleanly through the safe-state path.
                self._close_after_stop = True
                self.stop_sweep("User closed application.")
        elif self._stopping:
            self._close_after_stop = True
        elif self.is_calibrating:
            messagebox.showwarning(
                "Calibration Running",
                "A reference calibration is in progress. "
                "Wait for it to finish before closing.",
            )
        else:
            self.backend.close_instrument()
            self.root.destroy()


def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed.\n\nPlease run:\npip install pyvisa",
        )
        return

    root = tk.Tk()
    AlphaAN_FreqScan_GUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
