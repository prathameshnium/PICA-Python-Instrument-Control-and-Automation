"""
Module: Novocontrol Alpha-AN Frequency Scan (headless)
Purpose: Command-line broadband dielectric frequency scan on a Novocontrol
         Alpha-AN mainframe with a ZG4 sample interface, over direct GPIB.
         Sweeps a WinDETA-style Start/End/Factor logarithmic list (default
         20 Hz x1.4 to 3 MHz) at fixed temperature and writes an 11-column
         WinDETA-compatible file (Tan(Delta) and Phi included).

         Commands are Novocontrol proprietary (not SCPI), taken from the
         Alpha-AN Impedance Analyzer manual.

         SAFETY: this script never uses DC bias -- DCV/DCE are never
         transmitted (the analyzer does have DC bias - see Instrument).
         Completion is SRQ-driven with a mandatory timeout, and
         every exit path drives the generator to a safe state before the VISA
         session is released.

Instrument: the lab system this script was written for, read from
         WinALPHA's auto-detect on 22 Oct 2026:
           Analyzer   Novocontrol ALPHA ("ALPHA, BETA Analyzer"), SN 284
           Interface  ZG4 sample cell interface, SN 284-0
           *IDN?      ALPHA Analyzer, Novocontrol Technologies,
                      6.25_200_40_284_ANB_16012009_G2
           GPIB       address 10 on the BDS PC's NI GPIB card; a Keysight
                      E4991B shares that bus at 17
           DC bias    present (used in WinDETA experiments); maximum
                      voltage not yet confirmed

         This script is deliberately self-contained (PICA convention): it
         duplicates the conversion and safety logic from
         Frequency_Scan_AlphaAN_GUI.py rather than importing it.

Example:
    python AlphaAN_FreqScan_Instrument_Control.py \
        --gpib-address GPIB0::10::INSTR --diameter 10.0 --thickness 1.0 \
        --filename sample01 --path ./data
"""

import argparse
import math
import os
import sys
import time
from datetime import datetime

import pyvisa


# --- Safety constants. NEVER raise these to "make it work". -----------------
FREQ_HW_MIN, FREQ_HW_MAX = 3e-5, 20e6
# ZG4 AC-voltage ceiling: 3 Vrms up to 4 MHz, 2 Vrms from 4 to 10 MHz (ZG4
# technical data chart - conservative reading), 1 Vrms above 10 MHz.
# Frequency-dependent, so enforced per-point.
ACV_MAX_LOW_FREQ = 3.0        # <= 4 MHz
ACV_MAX_MID_FREQ = 2.0        # 4 - 10 MHz
ACV_MAX_HIGH_FREQ = 1.0       # > 10 MHz
ACV_FREQ_MID_HZ = 4e6
ACV_FREQ_BREAK_HZ = 10e6
MTM_MIN_S, MTM_MAX_S = 0.01, 1000.0
STATUS_RESULT_VALID = 2
STATUS_FATAL = frozenset({6})   # signal source disconnected -> abort sweep
STATUS_MEANINGS = {
    0: "Invalid (result buffer empty)",
    1: "Measurement still in progress",
    2: "Result valid",
    3: "Voltage V1 out of range for sample measurement (check ACV/sample)",
    4: "Current out of range for sample measurement (check ACV/sample)",
    5: "Voltage V1 out of range for reference measurement",
    6: "Signal source disconnected during measurement (hardware fault)",
}
ZG4_INTERFACE_CODE = 5          # INTTYP? reports a numeric code, not "ZG4"
SRQ_TIMEOUT_MEASURE_S = 120.0

# Every Novocontrol executable command (ACV=, MTM=, GFR=, FRS=, ...) writes
# 'OK' or one of these error codes into the result buffer; the response MUST
# be read or later queries desynchronize. MST is the exception (no buffered
# ack; completion is SRQ + ZRE?).
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

EPS0_SI = 8.8541878128e-12   # F/m  - geometric capacitance C0
EPS0_CM = 8.8541878128e-14   # F/cm - so sigma lands in S/cm

# The Alpha's address on the BDS bus (WinALPHA auto-detect: "Address: 10").
# The Keysight E4991B shares that bus at 17; address 5 is not the Alpha.
DEFAULT_GPIB_ADDRESS = "GPIB0::10::INSTR"

# After *RST the Alpha drops off the GPIB bus while it reinitializes (no
# listener: every write fails with VI_ERROR_NLISTENERS, NI-488.2's ENOL) and
# only answers again some seconds later. Seen on hardware 22 Oct 2026: a
# fixed 1 s sleep was too short and MODE=IMP failed. Commands are retried on
# "no listeners" and on timeout, until this deadline. Through 64-bit
# Keysight VISA (23 Oct 2026) the first *IDN? after *RST hung for the whole
# session timeout (VI_ERROR_TMO), so each try gets a short timeout and a
# timed-out try is followed by a device clear. On one PC the Alpha
# answered 1.4 s after *RST through NI-488.2 and 4.1 s through 64-bit
# VISA. Same values as the GUI.
RESET_SETTLE_S = 2.0
RESET_READY_TIMEOUT_S = 60.0
RESET_RETRY_INTERVAL_S = 0.5
RESET_POLL_TIMEOUT_MS = 3000
CONNECT_READY_TIMEOUT_S = 15.0
# NLISTENERS: no listener at that address; the write put no byte on the bus,
# so it is the one GPIB error that is always safe to retry.
# TMO: the transfer did not complete in time; part of a command may have
# reached the device, so it is retried only after a device clear.
VI_ERROR_NLISTENERS = -1073807265
VI_ERROR_TMO = -1073807339


def acv_ceiling(freq_hz):
    """ZG4 AC-voltage ceiling (Vrms) at a given frequency."""
    if freq_hz <= ACV_FREQ_MID_HZ:
        return ACV_MAX_LOW_FREQ
    if freq_hz <= ACV_FREQ_BREAK_HZ:
        return ACV_MAX_MID_FREQ
    return ACV_MAX_HIGH_FREQ


def exec_cmd(inst, cmd):
    """Send one executable command and consume its 'OK' acknowledgment.

    Raises RuntimeError on an error-code answer. Never use for MST (which
    leaves no buffered ack) or for queries.
    """
    resp = inst.query(cmd).strip(" \t\r\n\x00\x10")
    if resp != "OK":
        meaning = CMD_ERROR_MEANINGS.get(resp, "unrecognized response")
        raise RuntimeError(
            f"Command {cmd!r} failed: instrument answered {resp!r} "
            f"({meaning})."
        )


def exec_tolerant(inst, cmd):
    """exec_cmd for shutdown paths: never raises, reports success."""
    try:
        resp = inst.query(cmd).strip(" \t\r\n\x00\x10")
    except Exception as e:
        print(f"  Warning: {cmd} failed during shutdown: {e}")
        return False
    if resp != "OK":
        print(f"  Warning: {cmd} answered {resp!r} during shutdown.")
        return False
    return True


def status_message(status):
    """Human-readable meaning of a ZRE? status code."""
    return STATUS_MEANINGS.get(status, f"unknown status {status}")


def parse_inttyp(response):
    """Interface code from an 'INTTYP=<code> <serial>' response (ZG4 = 5)."""
    text = response.strip(" \t\r\n\x00\x10")
    if "=" in text:
        text = text.split("=", 1)[1]
    return int(text.split()[0])

# Zp BEFORE Sig, matching the WinDETA export paired with the mes_def
# (../data_file_for_ref/Fscan_data_Novo_Windeta.dat) and the GUI program.
# Tan(Delta) follows Eps'' and the phase angle closes the row: the same
# 11-column layout as Frequency_Scan_AlphaAN_GUI.py.
WINDETA_HEADER = (
    " Freq. [Hz]\t Eps'    \t Eps''   \t Tan(Delta)\t Modulus'  \t"
    " Modulus''  \t Zp' [Ohms]\t Zp'' [Ohms]\t Sig' [S/cm]\t Sig'' [S/cm]\t"
    " Phi [Deg]"
)

# WinDETA "Measurement Points by Steps", logarithmic division: Start, End
# and Factor in, list out. Same defaults as the GUI Fscan (today's WinDETA
# Fscan list: 20 Hz x1.4 to 3 MHz, 37 points). Override with --start,
# --end and --factor.
START_HZ = 20.0
END_HZ = 3e6
FACTOR = 1.4
# Sanity cap only: at ~1 s per point this is already a 15+ minute sweep.
FREQ_MAX_POINTS = 1000


def build_log_frequency_list(start, end, factor):
    """WinDETA logarithmic value list: start, start*factor, start*factor^2 ...
    while short of End, then End itself as the last point (WinDETA help:
    the multiplier is constant 'except the last point').

    Each point is start*factor^k rounded to 7 significant digits, the
    precision WinDETA stores. start > end gives a descending list (divide
    by factor). Raises ValueError with a readable reason. Copied from
    Frequency_Scan_AlphaAN_GUI.py.
    """
    for name, v in (("Start", start), ("End", end), ("Factor", factor)):
        if not math.isfinite(v):
            raise ValueError(f"{name} is not a finite number.")
    for name, v in (("Start", start), ("End", end)):
        if not (FREQ_HW_MIN <= v <= FREQ_HW_MAX):
            raise ValueError(
                f"{name} {v:g} Hz is outside the Alpha-AN range "
                f"{FREQ_HW_MIN:g} Hz to {FREQ_HW_MAX:g} Hz."
            )
    if factor <= 1.0:
        raise ValueError("Factor must be greater than 1 (e.g. 1.4).")
    if start == end:
        raise ValueError("Start and End are the same frequency.")
    step = factor if end > start else 1.0 / factor
    rising = end > start
    values = []
    k = 0
    while True:
        f = float(f"{start * step ** k:.6e}")
        # Stop once the next point reaches End (1e-9 slack: no duplicate
        # End when the series lands on it exactly, e.g. 20 Hz x10 to 2 MHz).
        if (f >= end * (1 - 1e-9)) if rising else (f <= end * (1 + 1e-9)):
            break
        if values and f == values[-1]:
            raise ValueError(
                f"Factor {factor:g} is too close to 1: points repeat at "
                f"7-digit resolution. Use a larger factor."
            )
        values.append(f)
        k += 1
        if len(values) >= FREQ_MAX_POINTS:
            raise ValueError(
                f"Factor {factor:g} gives more than "
                f"{FREQ_MAX_POINTS} points; use a larger factor."
            )
    values.append(float(end))
    return tuple(values)


# The default sweep, built once from the constants above.
FREQUENCIES_HZ = build_log_frequency_list(START_HZ, END_HZ, FACTOR)
assert all(FREQ_HW_MIN <= f <= FREQ_HW_MAX for f in FREQUENCIES_HZ)


def compute_c0(diameter_mm, thickness_mm):
    """Geometric (empty-cell) capacitance of a round-plate electrode, in F."""
    if diameter_mm <= 0 or thickness_mm <= 0:
        raise ValueError("Diameter and thickness must both be positive.")
    area = math.pi * ((diameter_mm * 1e-3) / 2.0) ** 2
    return EPS0_SI * area / (thickness_mm * 1e-3)


def parse_zre(response):
    """Parse 'ZRE=Zs' Zs'' freq status ref' into a 5-tuple.

    Responses terminate on EOI plus an EOS byte, so strip trailing
    CR/NUL/DLE that .strip() would miss. A bare 'CR' means Recalibrate.
    """
    text = response.strip(" \t\r\n\x00\x10")
    if "=" in text:
        text = text.split("=", 1)[1]
    fields = text.replace(",", " ").split()
    if fields and fields[0].upper() == "CR":
        raise RuntimeError(
            "Instrument returned 'CR' (Recalibrate): load-short correction "
            "(ZSLCAL=1) is on but no load-short calibration is stored. Run a "
            "load-short calibration, or disable ZSLCAL."
        )
    if len(fields) < 5:
        raise ValueError(f"Malformed ZRE? response: {response!r}")
    return (
        float(fields[0]), float(fields[1]), float(fields[2]),
        int(float(fields[3])), int(float(fields[4])),
    )


def impedance_to_dielectric(zr, zi, f_actual, c0):
    """Convert one complex-impedance point to the nine WinDETA quantities.

    Z* = R + jX with R = Zs', X = Zs''.  eps* = eps' - i*eps''.
    Returns (f, eps', eps'', M', M'', Zp', Zp'', sig', sig'').

    The Zp' / Zp'' columns are the PARALLEL-equivalent impedance (1/G, 1/B),
    NOT series R / X -- verified against a WinDETA export to < 1e-5.
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
    # WinDETA's sigma* = i*w*eps0*(eps* - 1): the vacuum displacement is
    # subtracted, so sig'' uses (eps' - 1), NOT eps'. Verified against the
    # reference exports in ../data_file_for_ref (12% off at 10 MHz otherwise).
    sig2 = -omega * EPS0_CM * (eps1 - 1.0)

    # Parallel-equivalent impedance (1/G, 1/B), matching WinDETA.
    zr_safe = zr if zr != 0 else 1e-20
    zi_safe = zi if zi != 0 else 1e-20
    zp1 = z_mag_sq / zr_safe
    zp2 = -z_mag_sq / zi_safe

    return (f_actual, eps1, eps2, m1, m2, zp1, zp2, sig1, sig2)


def loss_phase_cp_g(zr, zi, f_actual):
    """The extra WinDETA columns plus Cp, G: (tan_delta, phi_deg, Cp, G).

        tan(delta) = eps''/eps' = -R/X     (WinDETA 'Tan(Delta)')
        phi        = atan2(X, R) in deg    (phase of Z, negative for a
                                            capacitor)
        Cp = B/w = -X / (w*|Z|^2)          parallel capacitance, F
        G  = R / |Z|^2                     parallel conductance, S

    Geometry-independent. Only tan_delta and phi are written here. Copied
    from Frequency_Scan_AlphaAN_GUI.py.
    """
    omega = 2.0 * math.pi * f_actual
    omega_safe = omega if omega != 0 else 1e-20
    z_mag_sq = zr ** 2 + zi ** 2
    z_mag_sq_safe = z_mag_sq if z_mag_sq != 0 else 1e-20
    zi_safe = zi if zi != 0 else 1e-20

    tan_d = -zr / zi_safe
    phi_deg = math.degrees(math.atan2(zi, zr))
    cp = -zi / (omega_safe * z_mag_sq_safe)
    g = zr / z_mag_sq_safe
    return (tan_d, phi_deg, cp, g)


def windeta_row(row, extra):
    """impedance_to_dielectric's 9-tuple + loss_phase_cp_g's 4-tuple -> the
    11 values in WINDETA_HEADER order (Tan(Delta) after Eps'', Phi last).
    Same order as file_rows in Frequency_Scan_AlphaAN_GUI.py."""
    tan_d, phi_deg = extra[0], extra[1]
    return tuple(row[:3]) + (tan_d,) + tuple(row[3:]) + (phi_deg,)


def abort_and_diagnose(inst):
    """MBK first, then ZTSTAT? exactly once.

    ZTSTAT? is a post-fault diagnostic only: the manual warns that querying
    it during a measurement adds bus traffic and degrades point accuracy.
    Calling it here is safe because MBK has already stopped the task.
    """
    try:
        # MBK is an executable command: read its ack, but tolerate a
        # non-OK answer - we are already in a fault path.
        inst.query("MBK")
    except Exception as e:
        return f"MBK failed: {e}"
    try:
        return inst.query("ZTSTAT?").strip()
    except Exception as e:
        return f"ZTSTAT? failed: {e}"


def wait_for_srq(inst, timeout_s, context):
    """Block until SRQ, then clear the request. Abort + diagnose on timeout."""
    try:
        inst.wait_for_srq(int(timeout_s * 1000))
    except Exception as exc:
        state = abort_and_diagnose(inst)
        raise TimeoutError(
            f"No SRQ within {timeout_s:.0f} s during {context}. "
            f"Aborted (MBK). ZTSTAT? reports: {state}. Underlying: {exc}"
        )
    stb = inst.read_stb()
    if not stb & 0x01:
        print(f"  WARNING: SRQ during {context} with STB=0x{stb:02X}.")
    return stb


def safe_state(inst):
    """Drive the generator to the defined safe idle state. Never raises.

    MBK (abort) -> ACV=0 (park generator) -> ZCONSPL=0 (current input to
    high impedance).
    """
    if inst is None:
        return
    for cmd in ("MBK", "ACV=0", "ZCONSPL=0"):
        exec_tolerant(inst, cmd)


def shutdown(inst):
    """Safe-state, reset, release. Never leaves the analyzer driving."""
    if inst is None:
        return
    try:
        safe_state(inst)
        try:
            # *RST parks the generator (ACV=0, DCE=0) and PRESERVES the
            # stored calibration tables.
            inst.write("*RST")
            time.sleep(0.2)
        except Exception as e:
            print(f"  Warning: *RST failed ({e}); escalating to RSTH.")
            try:
                inst.write("RSTH")
                time.sleep(0.5)
            except Exception as e2:
                print(f"  Warning: RSTH also failed: {e2}")
    finally:
        try:
            inst.close()
            print("  Alpha-AN connection closed.")
        except Exception as e:
            print(f"  Warning closing VISA session: {e}")


def visa_error_code(exc):
    """The VISA status code carried by a pyvisa VisaIOError, else None."""
    code = getattr(exc, "error_code", None)
    return int(code) if code is not None else None


def open_alpha(rm, address):
    """Open the session and confirm it is an Alpha with a ZG4.

    Returns (inst, idn, code). Until *IDN? has identified a Novocontrol
    Alpha, a failure releases the session WITHOUT sending a single command:
    a wrong address (e.g. the E4991B at 17) must never receive the Alpha
    shutdown sequence (MBK, ACV=0, ZCONSPL=0, *RST). Only a confirmed Alpha
    that then fails the handshake goes through shutdown().
    """
    inst = rm.open_resource(address)
    try:
        inst.timeout = 60000
        # The Alpha-AN terminates on EOI (plus an EOS byte) and accepts
        # commands on EOI only -- no newline terminator.
        inst.read_termination = ""
        inst.write_termination = ""
        inst.send_end = True
        idn = query_idn_until_answered(
            inst, CONNECT_READY_TIMEOUT_S, "while connecting").strip()
        if not any(k in idn.upper() for k in ("ALPHA", "NOVOCONTROL")):
            raise ConnectionError(
                f"{address} answered {idn!r}, which is not a Novocontrol "
                f"Alpha. Check the address.")
    except Exception:
        try:
            inst.close()
        except Exception:
            pass
        raise

    try:
        # INTTYP? reports a numeric interface code; the ZG4 is code 5.
        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(
                f"Expected a ZG4 sample interface (INTTYP code "
                f"{ZG4_INTERFACE_CODE}), got code {code}."
            )
        inst.read_stb()   # clear any stale status on the bus
    except Exception:
        shutdown(inst)
        raise
    return inst, idn, code


def query_idn_until_answered(inst, timeout_s, situation):
    """*IDN?, repeated while the Alpha is away reinitializing.

    Retries VI_ERROR_NLISTENERS (nothing reached the bus) and VI_ERROR_TMO
    (followed by a device clear), each try on a short timeout; the session
    timeout is restored afterwards. Any other error is raised at once.
    Copied from Frequency_Scan_AlphaAN_GUI.py (_query_idn_until_answered).
    """
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


def reset_and_set_impedance_mode(inst, timeout_s=RESET_READY_TIMEOUT_S):
    """*RST, wait until the Alpha answers again, then MODE=IMP.

    *RST leaves no Novocontrol ack. The first *IDN? goes out after
    RESET_SETTLE_S and is repeated until it is answered - the same
    handshake JUMP uses after *RST, including its extra 1 s settle before
    MODE=IMP. Copied from Frequency_Scan_AlphaAN_GUI.py
    (_reset_and_set_impedance_mode).
    """
    inst.write("*RST")
    start = time.monotonic()
    time.sleep(RESET_SETTLE_S)
    query_idn_until_answered(inst, timeout_s, "after *RST")
    print(f"*RST complete; Alpha answering after "
          f"{time.monotonic() - start:.1f} s.")
    time.sleep(1.0)
    exec_cmd(inst, "MODE=IMP")


def validate(acv, mtm, wire_mode, diameter, thickness,
             frequencies=FREQUENCIES_HZ):
    """Reject anything unsafe BEFORE a byte reaches the instrument."""
    if not frequencies:
        raise ValueError("Frequency list is empty.")
    max_acv = min(acv_ceiling(f) for f in frequencies)
    if not (0.0 < acv <= max_acv):
        raise ValueError(
            f"AC voltage {acv} Vrms outside 0 < V <= {max_acv} Vrms "
            f"(frequency-dependent ZG4 ceiling)."
        )
    if not (MTM_MIN_S <= mtm <= MTM_MAX_S):
        raise ValueError(
            f"Integration time {mtm} s outside {MTM_MIN_S}-{MTM_MAX_S} s."
        )
    if str(wire_mode) not in ("2", "3", "4"):
        raise ValueError("Wire mode must be 2, 3 or 4.")
    if diameter <= 0 or thickness <= 0:
        raise ValueError("Diameter and thickness must both be positive.")
    for f in frequencies:
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(f"Frequency {f} Hz outside the Alpha-AN range.")


def main():
    parser = argparse.ArgumentParser(
        description="Novocontrol Alpha-AN broadband frequency scan"
    )
    parser.add_argument("--gpib-address", default=DEFAULT_GPIB_ADDRESS,
                        help="VISA resource string of the Alpha-AN mainframe")
    parser.add_argument("--filename", default="AlphaAN_FreqScan",
                        help="Base name of the output file")
    parser.add_argument("--path", default=".", help="Output directory")
    parser.add_argument("--comment", default="Alpha-AN frequency scan",
                        help="Free text for the WinDETA header line")
    parser.add_argument("--acv", type=float, default=1.0,
                        help="AC voltage in Vrms")
    parser.add_argument("--mtm", type=float, default=0.5,
                        help="Integration time in seconds")
    parser.add_argument("--wire-mode", default="2", choices=["2", "3", "4"],
                        help="ZG4 wire mode (FRS)")
    parser.add_argument("--diameter", type=float, required=True,
                        help="Electrode diameter in mm")
    parser.add_argument("--thickness", type=float, required=True,
                        help="Sample thickness in mm")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Initial settling delay in seconds")
    parser.add_argument("--start", type=float, default=START_HZ,
                        help="First frequency in Hz (WinDETA Start)")
    parser.add_argument("--end", type=float, default=END_HZ,
                        help="Last frequency in Hz (WinDETA End)")
    parser.add_argument("--factor", type=float, default=FACTOR,
                        help="Multiplier between points (WinDETA Factor)")
    args = parser.parse_args()

    freqs = build_log_frequency_list(args.start, args.end, args.factor)
    validate(args.acv, args.mtm, args.wire_mode, args.diameter,
             args.thickness, freqs)
    print(f"{len(freqs)} points, {freqs[0]:g} Hz - {freqs[-1]:g} Hz "
          f"(x{args.factor:g})")
    c0 = compute_c0(args.diameter, args.thickness)
    print(f"C0 = {c0:.5e} F")

    os.makedirs(args.path, exist_ok=True)
    stamp = datetime.now()
    out_path = os.path.join(
        args.path,
        f"{args.filename}_{stamp.strftime('%Y%m%d_%H%M%S')}_AlphaAN.txt",
    )

    inst = None
    try:
        rm = pyvisa.ResourceManager()
        # open_alpha releases the session itself on any handshake failure
        # (without a command byte unless the Alpha is confirmed), so inst
        # stays None here and the handlers below send nothing.
        inst, idn, code = open_alpha(rm, args.gpib_address)
        print(f"Connected: {idn} | ZG4 interface code {code}")

        # *RST preserves the stored calibration tables; it only resets
        # operating parameters. A scan therefore runs on the previous REF
        # calibration, by design. Run REF from the GUI when it is stale.
        # *RST is IEEE-488.2 common: it leaves NO Novocontrol ack, and the
        # Alpha drops off the bus until it has reinitialized.
        reset_and_set_impedance_mode(inst)

        # Settings AFTER the reset: *RST (and ZRUNCAL) reset ACV and MTM.
        # exec_cmd consumes each command's mandatory 'OK' acknowledgment,
        # which also fully synchronizes the bus (no settle sleeps needed).
        # ZREFMODE=-3 = auto reference mode, up to 3 caps (verified).
        exec_cmd(inst, "ZREFMODE=-3")
        exec_cmd(inst, "ZLLCOR=1")
        exec_cmd(inst, "ZSLCAL=1")
        exec_cmd(inst, f"FRS={args.wire_mode}")
        exec_cmd(inst, "DRS=0 0")
        exec_cmd(inst, f"ACV={args.acv:.6g}")
        exec_cmd(inst, f"MTM={args.mtm:.6g}")
        # DCV / DCE are NEVER sent: this script does not use DC bias.
        # "Low capacity open calibration" has no analyzer command - it is a
        # host-software correction (not implemented here), matching the
        # WinDETA mes_def's "Off" by construction.

        # Date/time matching the WinDETA export style: space-padded day,
        # zero-padded month and minute (e.g. " 9.07.2026, 20:00").
        date_str = f"{stamp.day:2d}.{stamp.month:02d}.{stamp.year}"
        time_str = f"{stamp.hour}:{stamp.minute:02d}"
        # newline="\r\n": WinDETA exports use CRLF on every platform.
        with open(out_path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(f"{args.comment}, {date_str}, {time_str}\n")
            fh.write(f"Fixed value(s) :  AC Volt  [Vrms]={args.acv:.4e}\n")
            fh.write(WINDETA_HEADER + "\n")

        if args.delay > 0:
            time.sleep(args.delay)

        n_flagged = 0
        for i, freq in enumerate(freqs, 1):
            exec_cmd(inst, f"GFR={freq:.6e}")
            inst.read_stb()   # clear stale status before triggering
            # MST leaves NO buffered ack: completion is SRQ + ZRE?.
            inst.write("MST")
            wait_for_srq(inst, SRQ_TIMEOUT_MEASURE_S,
                         f"measurement at {freq:.6g} Hz")
            zr, zi, f_actual, status, ref = parse_zre(inst.query("ZRE?"))

            if status != STATUS_RESULT_VALID:
                if status in STATUS_FATAL:
                    raise RuntimeError(
                        f"f={freq:.6g} Hz: {status_message(status)} "
                        f"(status {status})."
                    )
                n_flagged += 1
                print(f"[{i:3d}/{len(freqs)}] {freq:12.4g} Hz "
                      f"-> {status_message(status)} (status {status}), "
                      f"discarded")
                continue

            # Convert with the ACTUAL frequency the analyzer used.
            row = windeta_row(impedance_to_dielectric(zr, zi, f_actual, c0),
                              loss_phase_cp_g(zr, zi, f_actual))
            with open(out_path, "a", encoding="utf-8", newline="\r\n") as fh:
                fh.write("\t".join(f"{v:.5e}" for v in row) + "\n")
                fh.flush()

            print(f"[{i:3d}/{len(freqs)}] {f_actual:12.4g} Hz "
                  f"| eps' {row[1]:.4e} | eps'' {row[2]:.4e} "
                  f"| tan d {row[3]:.4e}")

        print(f"\nSweep complete. {n_flagged} point(s) discarded.")
        print(f"Data written to: {out_path}")

    except Exception as e:
        # Fail safe on any comms loss or measurement fault: never silently
        # retry into hardware.
        print(f"\nERROR: {e}", file=sys.stderr)
        safe_state(inst)
        raise
    finally:
        shutdown(inst)


if __name__ == "__main__":
    main()
