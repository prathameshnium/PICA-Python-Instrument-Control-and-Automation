'''
===============================================================================
 PROGRAM:      PICA GPIB Lifeline Console
 PURPOSE:      Minimal fallback GPIB terminal that needs NOTHING but a stock
               Python install: it talks to the NI-488.2 driver DLL
               (gpib-32.dll / ni4882.dll) directly through ctypes, bypassing
               pyvisa entirely. Useful when the SCPI Console GUI cannot reach
               the instrument and you want to know whether the NI driver
               itself can.

               GENTLE BY DESIGN: there is no bus scan and no driver hunt.
               You state the instrument's address (--address); the tool loads
               the standard NI-488.2 DLL from System32 (or an explicit --dll
               path), sends a single read-only *IDN? to that one address, and
               opens an interactive terminal. Nothing else on the bus is ever
               touched, and every operation is one attempt only -- no retries.
               Everything is echoed to a timestamped report file.

               SAFETY: queries ('?') pass freely; state-changing writes need
               an explicit yes; DCV/DCE (DC bias -- no bias hardware on this
               mainframe) and RSTH (hard reset) are blocked outright; ':safe'
               parks the analyzer with the documented sequence MBK, ACV=0,
               ZCONSPL=0.

               NOTE: v1.0's machinery for the old Novocontrol/ines card (DLL
               hunting across vendor folders, --deep disk walk, bus sweeps)
               was removed in v1.1; it lives in git history should that card
               ever return.

 USAGE:        python GPIB_Lifeline_CLI.py --address 5
               python GPIB_Lifeline_CLI.py --address 5 --board 0
               python GPIB_Lifeline_CLI.py --address 5 --dll "C:\\path\\to\\gpib-32.dll"
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.2
===============================================================================
'''

import argparse
import ctypes
import os
import struct
import sys
import time
from datetime import datetime

# --- NI-488.2 status bits (ibsta) -------------------------------------------
ERR = 1 << 15    # error: consult iberr
TIMO = 1 << 14   # timeout
END = 1 << 13    # EOI (or EOS) seen: full response received
CMPL = 1 << 8    # I/O complete

# --- NI-488.2 timeout codes for ibdev/ibtmo ---------------------------------
TIMEOUT_CODES = {0.3: 10, 1: 11, 3: 12, 10: 13, 30: 14}

IBERR_MEANINGS = {
    0: "EDVR: system error",
    1: "ECIC: board is not Controller-In-Charge",
    2: "ENOL: no listener at that address (device off / wrong address?)",
    3: "EADR: board not addressed correctly",
    4: "EARG: invalid argument",
    5: "ESAC: board is not the system controller",
    6: "EABO: I/O aborted (timeout -- device present but silent?)",
    7: "ENEB: no such GPIB board index (driver/board configuration)",
    8: "EDMA: DMA error",
    10: "EOIP: asynchronous I/O in progress",
    11: "ECAP: capability not available",
    12: "EFSO: file system error",
    14: "EBUS: command byte transfer error",
    15: "ESTB: serial poll status byte lost",
    16: "ESRQ: SRQ line stuck on",
    20: "ETAB: table overflow",
}

# Response padding the Novocontrol Alpha adds around EOI-terminated replies.
RESPONSE_STRIP = b" \t\r\n\x00\x10"

# --- Instrument safety -------------------------------------------------------
# DC-bias writes are hard-blocked: the lab's Alpha-AN mainframe has no bias
# hardware, and pica/novocontrol never transmits DCV/DCE either. RSTH is a
# hard reset -- also blocked. Every other state-changing write requires an
# explicit yes; queries ('?') pass freely.
BLOCKED_WRITE_PREFIXES = ("DCV", "DCE", "RSTH")
# Documented Novocontrol Alpha safe idle state, same order as
# pica/novocontrol safe_state(): abort task, park generator, current input
# to high impedance.
SAFE_STATE_SEQUENCE = ("MBK", "ACV=0", "ZCONSPL=0")


def iberr_text(code):
    if code is None:
        return "iberr unavailable"
    return IBERR_MEANINGS.get(code, f"iberr={code} (unknown)")


def sta_text(sta):
    bits = []
    for mask, name in ((ERR, "ERR"), (TIMO, "TIMO"), (END, "END"),
                       (CMPL, "CMPL")):
        if sta & mask:
            bits.append(name)
    return f"0x{sta & 0xFFFF:04X} [{' '.join(bits) or 'none'}]"


class Tee:
    """Mirrors stdout into the report file so evidence survives a bad day."""

    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, text):
        self.stdout.write(text)
        self.file.write(text)

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        sys.stdout = self.stdout
        self.file.close()


# -----------------------------------------------------------------------------
# --- STEP 1: ENVIRONMENT REPORT ---
# -----------------------------------------------------------------------------

def report_environment():
    bits = struct.calcsize("P") * 8
    print("== Environment ==")
    print(f"  Python : {sys.version.split()[0]} ({bits}-bit)  "
          f"{sys.executable}")
    if bits == 64:
        print("           NOTE: a 32-bit-only GPIB stack cannot be driven "
              "from here. Re-run with 'py -3.10-32'.")
    print(f"  OS     : {sys.platform}  "
          f"{os.environ.get('OS', '')}".rstrip())
    try:
        import pyvisa
        print(f"  pyvisa : installed ({getattr(pyvisa, '__version__', '?')})")
    except Exception as exc:
        print(f"  pyvisa : NOT importable ({exc})")
    print()
    return bits


# -----------------------------------------------------------------------------
# --- STEP 2: THE STANDARD NI-488.2 DLL ---
# -----------------------------------------------------------------------------

def find_dll_candidates():
    """The standard NI-488.2 DLL locations, tried once each -- no hunting.

    On 64-bit Windows the loader redirects a 32-bit process's System32 path to
    SysWOW64, so the System32 entry is what actually delivers the 32-bit
    gpib-32.dll to a 32-bit interpreter (the only usable stack on the
    Novocontrol BDS PC). SysWOW64 is listed explicitly as well so that a
    64-bit interpreter can at least report that a 32-bit DLL exists; it will
    refuse to load it with WinError 193, which is itself the diagnosis.
    """
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    paths = (
        os.path.join(windir, "System32", "gpib-32.dll"),
        os.path.join(windir, "System32", "ni4882.dll"),
        os.path.join(windir, "SysWOW64", "gpib-32.dll"),
    )
    seen, ordered = set(), []
    for path in paths:
        key = os.path.normcase(path)
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            ordered.append(path)
    return ordered


def classify_candidates(candidates, bits):
    """Try-loads every candidate. Returns the loadable paths."""
    loadable = []
    print("== GPIB DLL candidates ==")
    if not candidates:
        print("  none found")
    for path in candidates:
        try:
            ctypes.WinDLL(path)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 193:
                print(f"  {path}\n      -> WRONG BITNESS: this is a "
                      f"{96 - bits}-bit DLL, unloadable from {bits}-bit "
                      "Python.")
            else:
                print(f"  {path}\n      -> failed to load: {exc}")
        else:
            print(f"  {path}\n      -> LOADS in this {bits}-bit Python")
            loadable.append(path)
    print()
    return loadable


# -----------------------------------------------------------------------------
# --- STEP 3: RAW NI-488.2 DRIVER ACCESS ---
# -----------------------------------------------------------------------------

def _bind(lib, name, argtypes, restype=ctypes.c_int):
    try:
        function = getattr(lib, name)
    except AttributeError:
        return None
    function.argtypes = argtypes
    function.restype = restype
    return function


class RawGpib:
    """Minimal, defensive ctypes binding of the traditional NI-488.2 API."""

    def __init__(self, dll_path):
        self.path = dll_path
        lib = self.lib = ctypes.WinDLL(dll_path)
        c_int, c_size_t = ctypes.c_int, ctypes.c_size_t

        self.ibdev = _bind(lib, "ibdev", [c_int] * 6)
        self.ibonl = _bind(lib, "ibonl", [c_int, c_int])
        self.ibwrt = _bind(lib, "ibwrt", [c_int, ctypes.c_char_p, c_size_t])
        self.ibrd = _bind(lib, "ibrd", [c_int, ctypes.c_char_p, c_size_t])
        self.ibtmo = _bind(lib, "ibtmo", [c_int, c_int])

        missing = [name for name in ("ibdev", "ibonl", "ibwrt", "ibrd")
                   if getattr(self, name) is None]
        if missing:
            raise OSError(
                f"{dll_path} lacks required exports: {', '.join(missing)}")

        # Error / byte-count readback, most portable variant first.
        self._thread_iberr = _bind(lib, "ThreadIberr", [])
        self._thread_ibcnt = (_bind(lib, "ThreadIbcntl", [], ctypes.c_long)
                              or _bind(lib, "ThreadIbcnt", [], ctypes.c_long))
        self._ibcnt_var = None
        if self._thread_ibcnt is None:
            for name in ("ibcntl", "ibcnt"):
                try:
                    self._ibcnt_var = ctypes.c_long.in_dll(lib, name)
                    break
                except Exception:
                    continue

    def error_code(self):
        if self._thread_iberr is not None:
            try:
                return self._thread_iberr()
            except Exception:
                return None
        try:
            return ctypes.c_int.in_dll(self.lib, "iberr").value
        except Exception:
            return None

    def count(self):
        if self._thread_ibcnt is not None:
            try:
                return self._thread_ibcnt()
            except Exception:
                return None
        if self._ibcnt_var is not None:
            return self._ibcnt_var.value
        return None

    # ------------------------------------------------------------- transfers
    def open_device(self, board, pad, timeout_code):
        """ibdev with EOI on writes (eot=1) and no EOS byte -- the framing
        the Novocontrol Alpha requires."""
        return self.ibdev(board, pad, 0, timeout_code, 1, 0)

    def close_device(self, handle):
        try:
            self.ibonl(handle, 0)
        except Exception:
            pass

    def write(self, handle, text):
        payload = text.encode("ascii")
        return self.ibwrt(handle, payload, len(payload))

    def read(self, handle, max_len=65536):
        buffer = ctypes.create_string_buffer(max_len)
        sta = self.ibrd(handle, buffer, max_len)
        if sta & ERR:
            return None, sta
        n = self.count()
        if n is not None and 0 <= n <= max_len:
            data = buffer.raw[:n]
        else:
            data = buffer.raw.rstrip(b"\x00")
        return data, sta


def decode(data):
    return data.strip(RESPONSE_STRIP).decode("ascii", "replace")


# -----------------------------------------------------------------------------
# --- STEP 4: INTERACTIVE TERMINAL ---
# -----------------------------------------------------------------------------

def repl(gpib, handle, resource_label):
    print(f"\n== Interactive terminal on {resource_label} ==")
    print("   Command ending in '?' -> write + read; otherwise write only.")
    print("   SAFETY: queries send freely; state-changing writes ask for "
          "confirmation; DCV/DCE (DC bias) and RSTH (hard reset) are "
          "blocked outright.")
    print("   ':safe' parks a Novocontrol Alpha (MBK, ACV=0, ZCONSPL=0), "
          "':read' forces a read, ':quit' exits.")
    print("   (Novocontrol Alpha bring-up: *IDN? then INTTYP? -- both are "
          "read-only.)")
    while True:
        try:
            command = input(f"{resource_label} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        # The typed text itself bypasses the Tee; record it in the report
        # (the prompt is already teed by input()).
        sys.stdout.file.write(command + "\n")
        if not command:
            continue
        if command.lower() in (":quit", ":q", "quit", "exit"):
            break

        if command.lower() == ":safe":
            for step in SAFE_STATE_SEQUENCE:
                sta = gpib.write(handle, step)
                verdict = ("ok" if not (sta & ERR)
                           else f"FAILED {sta_text(sta)}; "
                                f"{iberr_text(gpib.error_code())}")
                print(f"  {step:<10} -> {verdict}")
            continue

        if command.lower() != ":read" and not command.endswith("?"):
            if command.upper().startswith(BLOCKED_WRITE_PREFIXES):
                print("  BLOCKED: DCV/DCE set the DC bias (this mainframe "
                      "has no bias hardware) and RSTH is a hard reset. "
                      "Not sent.")
                continue
            try:
                answer = input(
                    f"  '{command}' changes instrument state. Send? [y/N] ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            sys.stdout.file.write(answer + "\n")
            if answer.strip().lower() not in ("y", "yes"):
                print("  not sent")
                continue

        started = time.perf_counter()
        if command.lower() == ":read":
            data, sta = gpib.read(handle)
        else:
            sta = gpib.write(handle, command)
            if sta & ERR:
                print(f"  write failed: {sta_text(sta)}; "
                      f"{iberr_text(gpib.error_code())}")
                continue
            if not command.endswith("?"):
                elapsed = (time.perf_counter() - started) * 1000
                print(f"  (write ok)  [{elapsed:.1f} ms]")
                continue
            data, sta = gpib.read(handle)

        elapsed = (time.perf_counter() - started) * 1000
        if data is None:
            print(f"  read failed: {sta_text(sta)}; "
                  f"{iberr_text(gpib.error_code())}  [{elapsed:.1f} ms]")
        else:
            print(f"  << {decode(data)}  [{elapsed:.1f} ms]")


# -----------------------------------------------------------------------------
# --- MAIN ---
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Raw NI-488.2 GPIB terminal (stdlib only, no bus scan): "
                    "one *IDN? to the given address, then an interactive "
                    "session.")
    parser.add_argument("--address", type=int, required=True,
                        help="primary GPIB address of the instrument "
                             "(e.g. 5 for GPIB0::5::INSTR)")
    parser.add_argument("--board", type=int, default=0,
                        help="GPIB board index (default 0)")
    parser.add_argument("--dll", help="explicit path to the GPIB DLL "
                                      "(default: System32 gpib-32.dll / "
                                      "ni4882.dll)")
    parser.add_argument("--timeout", type=float, default=3,
                        choices=sorted(TIMEOUT_CODES), help="I/O timeout in "
                        "seconds for the interactive session (default 3)")
    parser.add_argument("--no-repl", action="store_true",
                        help="send the single *IDN? only; no interactive "
                             "session")
    args = parser.parse_args()

    if os.name != "nt":
        print("This lifeline drives the Windows NI-488.2 DLL; on Linux use "
              "linux-gpib.")
        return 1

    report_path = os.path.abspath(
        f"gpib_lifeline_report_{datetime.now():%Y%m%d_%H%M%S}.txt")
    tee = Tee(report_path)
    sys.stdout = tee
    try:
        print("PICA GPIB Lifeline Console -- "
              f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        print(f"Report file: {report_path}\n")

        bits = report_environment()

        candidates = ([args.dll] if args.dll else find_dll_candidates())
        loadable = classify_candidates(candidates, bits)

        if not loadable:
            print("== VERDICT ==")
            print("  No loadable NI-488.2 DLL (System32\\gpib-32.dll or "
                  "ni4882.dll).")
            print("  1. Install / repair the NI-488.2 driver (it places "
                  "gpib-32.dll in System32).")
            print("  2. Or point at a specific DLL: --dll <path>.")
            return 2

        gpib = None
        for path in loadable:
            try:
                gpib = RawGpib(path)
                print(f"Using DLL: {path}\n")
                break
            except OSError as exc:
                print(f"  {path}: {exc}")
        if gpib is None:
            print("== VERDICT ==")
            print("  A DLL loads but none exposes the NI-488.2 entry points "
                  "(ibdev/ibwrt/ibrd). This is not a usable 488.2 driver -- "
                  "repair the NI-488.2 installation.")
            return 2

        label = f"GPIB{args.board}::{args.address}"
        handle = gpib.open_device(args.board, args.address,
                                  TIMEOUT_CODES[args.timeout])
        if handle < 0:
            print(f"Could not open {label} "
                  f"({iberr_text(gpib.error_code())}).")
            return 4

        try:
            # Single attempt, read-only. Nothing else on the bus is touched.
            idn = None
            sta = gpib.write(handle, "*IDN?")
            if sta & ERR:
                print(f"{label} *IDN? write failed: {sta_text(sta)}; "
                      f"{iberr_text(gpib.error_code())}")
            else:
                data, sta = gpib.read(handle)
                if data is None:
                    print(f"{label} *IDN? read failed: {sta_text(sta)}; "
                          f"{iberr_text(gpib.error_code())}")
                else:
                    idn = decode(data)
                    print(f"{label} *IDN? -> {idn}")

            print("\n== VERDICT ==")
            if idn:
                print("  COMMUNICATION ESTABLISHED at the raw driver level.")
                print("  The SCPI Console GUI (Term = EOI for the Alpha) "
                      "should work too.")
            else:
                print("  No *IDN? reply (single attempt, no retry). Check: "
                      "instrument power, GPIB cable, address "
                      f"(--address {args.address}), and that no other GPIB "
                      "program is running.")
            if not args.no_repl:
                repl(gpib, handle, label)
        finally:
            gpib.close_device(handle)
        return 0

    except Exception as exc:   # never die silently; the report must exist
        print(f"\nUNEXPECTED ERROR: {exc!r}")
        return 5
    finally:
        print(f"\nFull transcript saved to: {report_path}")
        tee.close()


if __name__ == "__main__":
    sys.exit(main())
