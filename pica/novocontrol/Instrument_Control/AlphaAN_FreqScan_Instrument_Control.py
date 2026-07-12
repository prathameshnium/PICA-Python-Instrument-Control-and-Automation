"""
Module: Novocontrol Alpha-AN Frequency Scan (headless)
Purpose: Command-line broadband dielectric frequency scan on a Novocontrol
         Alpha-AN mainframe with a ZG4 sample interface, over direct GPIB.
         Sweeps a frozen 20 Hz - 1 MHz logarithmic series at fixed
         temperature and writes a WinDETA-compatible file.

         Commands are Novocontrol proprietary (not SCPI), taken from the
         Alpha-AN Impedance Analyzer manual.

         SAFETY: this mainframe has no DC bias hardware -- DCV/DCE are never
         transmitted. Completion is SRQ-driven with a mandatory timeout, and
         every exit path drives the generator to a safe state before the VISA
         session is released.

         This script is deliberately self-contained (PICA convention): it
         duplicates the conversion and safety logic from
         Frequency_Scan_AlphaAN_GUI.py rather than importing it.

Example:
    python AlphaAN_FreqScan_Instrument_Control.py \
        --gpib-address GPIB0::5::INSTR --diameter 10.0 --thickness 1.0 \
        --filename CoO_SiO2 --path ./data
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
WINDETA_HEADER = (
    " Freq. [Hz]\t Eps'    \t Eps''   \t Modulus'  \t Modulus''  \t"
    " Zp' [Ohms]\t Zp'' [Ohms]\t Sig' [S/cm]\t Sig'' [S/cm]"
)

# 20 Hz, x1.1 per step, final point clamped to exactly 1 MHz. 115 points.
# Frozen literal, deliberately NOT recomputed at runtime, and identical to the
# tuple in Frequency_Scan_AlphaAN_GUI.py -- so a GUI scan and a CLI scan
# request exactly the same grid and stay directly comparable point-for-point.
FREQUENCIES_HZ = (
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


def validate(acv, mtm, wire_mode, diameter, thickness):
    """Reject anything unsafe BEFORE a byte reaches the instrument."""
    max_acv = min(acv_ceiling(f) for f in FREQUENCIES_HZ)
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
    for f in FREQUENCIES_HZ:
        if not (FREQ_HW_MIN <= f <= FREQ_HW_MAX):
            raise ValueError(f"Frequency {f} Hz outside the Alpha-AN range.")


def main():
    parser = argparse.ArgumentParser(
        description="Novocontrol Alpha-AN broadband frequency scan"
    )
    parser.add_argument("--gpib-address", default="GPIB0::5::INSTR",
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
    args = parser.parse_args()

    validate(args.acv, args.mtm, args.wire_mode, args.diameter,
             args.thickness)
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
        inst = rm.open_resource(args.gpib_address)
        inst.timeout = 60000
        # The Alpha-AN terminates on EOI (plus an EOS byte) and accepts
        # commands on EOI only -- no newline terminator.
        inst.read_termination = ""
        inst.write_termination = ""
        inst.send_end = True

        idn = inst.query("*IDN?").strip()
        # INTTYP? reports a numeric interface code; the ZG4 is code 5.
        code = parse_inttyp(inst.query("INTTYP?"))
        if code != ZG4_INTERFACE_CODE:
            raise ConnectionError(
                f"Expected a ZG4 sample interface (INTTYP code "
                f"{ZG4_INTERFACE_CODE}), got code {code}."
            )
        inst.read_stb()   # clear any stale status on the bus
        print(f"Connected: {idn} | ZG4 interface code {code}")

        # *RST preserves the stored calibration tables; it only resets
        # operating parameters. A scan therefore runs on the previous REF
        # calibration, by design. Run REF from the GUI when it is stale.
        # *RST is IEEE-488.2 common: it leaves NO Novocontrol ack.
        inst.write("*RST")
        time.sleep(1.0)

        # Settings AFTER the reset: *RST (and ZRUNCAL) reset ACV and MTM.
        # exec_cmd consumes each command's mandatory 'OK' acknowledgment,
        # which also fully synchronizes the bus (no settle sleeps needed).
        # ZREFMODE=-3 = auto reference mode, up to 3 caps (verified).
        exec_cmd(inst, "MODE=IMP")
        exec_cmd(inst, "ZREFMODE=-3")
        exec_cmd(inst, "ZLLCOR=1")
        exec_cmd(inst, "ZSLCAL=1")
        exec_cmd(inst, f"FRS={args.wire_mode}")
        exec_cmd(inst, "DRS=0 0")
        exec_cmd(inst, f"ACV={args.acv:.6g}")
        exec_cmd(inst, f"MTM={args.mtm:.6g}")
        # DCV / DCE are NEVER sent: no bias hardware on this mainframe.
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
        for i, freq in enumerate(FREQUENCIES_HZ, 1):
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
                print(f"[{i:3d}/{len(FREQUENCIES_HZ)}] {freq:12.4g} Hz "
                      f"-> {status_message(status)} (status {status}), "
                      f"discarded")
                continue

            # Convert with the ACTUAL frequency the analyzer used.
            row = impedance_to_dielectric(zr, zi, f_actual, c0)
            with open(out_path, "a", encoding="utf-8", newline="\r\n") as fh:
                fh.write("\t".join(f"{v:.5e}" for v in row) + "\n")
                fh.flush()

            print(f"[{i:3d}/{len(FREQUENCIES_HZ)}] {f_actual:12.4g} Hz "
                  f"| eps' {row[1]:.4e} | eps'' {row[2]:.4e}")

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
