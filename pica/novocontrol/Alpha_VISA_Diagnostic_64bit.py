'''
Alpha_VISA_Diagnostic_64bit.py -- one-shot diagnostic log for the
Novocontrol Alpha-AN through VISA (pyvisa). Runs in 64-bit (or 32-bit)
Python; needs only pyvisa and an installed VISA library (NI-VISA).

Close WinALPHA / WinDETA and PICA first, then:

    python Alpha_VISA_Diagnostic_64bit.py               # safe checks only
    python Alpha_VISA_Diagnostic_64bit.py --reset-test  # + time *RST

Options: --address GPIB0::10::INSTR  --wait 60  --poll-timeout 3
         --no-clear  (do not send a device clear after a timed-out try)

Default checks change nothing on the analyzer: Python and VISA details,
running GPIB programs, resource list, *IDN? / INTTYP? on the Alpha.
--reset-test sends *RST (parks the generator, keeps calibration) and logs
every *IDN? try afterwards - elapsed time and VISA error - until the Alpha
answers or --wait seconds pass. It never sends ACV, DCV or MST.

Everything is printed and written to AlphaVisaDiag_<date>_<time>.log next
to this file. Send that log back.
'''
import argparse
import os
import platform
import struct
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(
    HERE, f"AlphaVisaDiag_{datetime.now():%Y%m%d_%H%M%S}.log")

VI_ERROR_TMO = -1073807339
VI_ERROR_NLISTENERS = -1073807265
VI_ERROR_NCIC = -1073807264
ERROR_NAMES = {VI_ERROR_TMO: "TMO (timeout)",
               VI_ERROR_NLISTENERS: "NLISTENERS (no listener)",
               VI_ERROR_NCIC: "NCIC (board not Controller-In-Charge)"}


def log(text=""):
    line = f"[{datetime.now():%H:%M:%S.%f}"[:-3] + f"] {text}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def section(title):
    log("")
    log("=" * 70)
    log(title)
    log("=" * 70)


def describe(exc):
    code = getattr(exc, "error_code", None)
    if code is None:
        return f"{type(exc).__name__}: {exc}"
    return f"{ERROR_NAMES.get(int(code), 'code')} [{int(code)}] {exc}"


def check_environment():
    section("1. ENVIRONMENT")
    log(f"Python {sys.version.split()[0]} {struct.calcsize('P') * 8}-bit  "
        f"exe={sys.executable}")
    log(f"OS {platform.platform()}  machine={platform.machine()}")
    log(f"Script {os.path.abspath(__file__)}")
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                                 capture_output=True, text=True,
                                 timeout=20).stdout
            hits = sorted({row.split('","')[0].strip('"')
                           for row in out.splitlines()
                           if any(k in row.lower() for k in (
                               "winalpha", "windeta", "wingeta", "nimax",
                               "ni max", "keysight", "agilent", "labview",
                               "python"))})
            log("Possibly GPIB-related processes running: "
                + (", ".join(hits) if hits else "none"))
        except Exception as e:
            log(f"tasklist failed: {e}")


def open_visa():
    section("2. VISA LIBRARY")
    try:
        import pyvisa
    except ImportError as e:
        log(f"STOP: pyvisa not importable: {e}  (pip install pyvisa)")
        return None
    log(f"pyvisa {pyvisa.__version__}")
    try:
        rm = pyvisa.ResourceManager()
    except Exception as e:
        log(f"STOP: ResourceManager() failed: {e}")
        return None
    log(f"VISA library: {rm.visalib}")
    try:
        log(f"Resources: {rm.list_resources()}")
    except Exception as e:
        log(f"list_resources failed: {describe(e)}")
    return rm


def open_alpha(rm, address):
    section(f"3. ALPHA IDENTITY at {address} (read-only queries)")
    try:
        inst = rm.open_resource(address)
    except Exception as e:
        log(f"STOP: open_resource failed: {describe(e)}")
        return None
    # Same terminations as Frequency_Scan_AlphaAN_GUI.py (shorter timeout).
    inst.timeout = 10000
    inst.read_termination = ""
    inst.write_termination = ""
    inst.send_end = True
    for cmd in ("*IDN?", "INTTYP?"):
        t = time.monotonic()
        try:
            r = inst.query(cmd)
            log(f"  {cmd:8} -> {r.strip()!r}  ({time.monotonic() - t:.2f} s)")
        except Exception as e:
            log(f"  {cmd:8} FAILED after {time.monotonic() - t:.2f} s: "
                f"{describe(e)}")
            return inst
    try:
        log(f"  status byte: 0x{inst.read_stb():02X}")
    except Exception as e:
        log(f"  read_stb failed: {describe(e)}")
    return inst


def reset_test(inst, wait_s, poll_s, use_clear):
    section("4. *RST RECOVERY TIMING")
    log(f"Per-try timeout {poll_s:.1f} s, give up after {wait_s:.0f} s, "
        f"device clear after a timeout: {'yes' if use_clear else 'no'}")
    inst.timeout = int(poll_s * 1000)
    try:
        inst.write("*RST")
    except Exception as e:
        log(f"RESULT: *RST itself failed: {describe(e)}")
        return False
    start = time.monotonic()
    log("*RST sent")
    tries = 0
    while time.monotonic() - start < wait_s:
        time.sleep(0.5)
        tries += 1
        t = time.monotonic()
        try:
            r = inst.query("*IDN?")
            log(f"RESULT: Alpha answered {time.monotonic() - start:.1f} s "
                f"after *RST ({tries} tries): {r.strip()!r}")
            return True
        except Exception as e:
            log(f"  try {tries:3d} at {t - start:5.1f} s: failed after "
                f"{time.monotonic() - t:.2f} s - {describe(e)}")
            if use_clear and getattr(e, "error_code", None) == VI_ERROR_TMO:
                t = time.monotonic()
                try:
                    inst.clear()
                    log(f"        device clear OK "
                        f"({time.monotonic() - t:.2f} s)")
                except Exception as ce:
                    log(f"        device clear failed after "
                        f"{time.monotonic() - t:.2f} s - {describe(ce)}")
    log(f"RESULT: no answer within {wait_s:.0f} s after *RST "
        f"({tries} tries). Note what the Alpha front panel shows.")
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--address", default="GPIB0::10::INSTR")
    ap.add_argument("--reset-test", action="store_true")
    ap.add_argument("--wait", type=float, default=60.0)
    ap.add_argument("--poll-timeout", type=float, default=3.0)
    ap.add_argument("--no-clear", action="store_true")
    a = ap.parse_args()

    log(f"Alpha-AN VISA diagnostic, log: {LOG_PATH}")
    log(f"Arguments: {vars(a)}")
    check_environment()
    rm = open_visa()
    if rm is None:
        return 1
    inst = open_alpha(rm, a.address)
    if inst is None:
        return 1
    try:
        if a.reset_test:
            reset_test(inst, a.wait, a.poll_timeout, not a.no_clear)
    finally:
        try:
            inst.close()
        except Exception:
            pass
    section("DONE")
    log(f"Send back: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
