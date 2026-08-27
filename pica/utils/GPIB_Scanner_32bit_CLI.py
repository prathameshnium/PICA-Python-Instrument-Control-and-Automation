'''
===============================================================================
 PROGRAM:      PICA GPIB Bus Scanner (32-bit safe, raw NI-488.2)
 PURPOSE:      Find out which GPIB addresses are occupied on a PC where pyvisa
               sees nothing -- typically a machine whose only working GPIB
               stack is a 32-bit gpib-32.dll with no usable VISA layer (the
               Novocontrol BDS PC). Needs NOTHING but a stock Python install:
               stdlib ctypes straight onto the driver DLL, no pyvisa, no VISA.

               Runs identically under 32-bit and 64-bit Python and says which
               it is; on the Novocontrol PC use the 32-bit interpreter:

                   py -3.10-32 GPIB_Scanner_32bit_CLI.py

               GENTLE BY DESIGN. The census uses ibln only, which asserts a
               listen address and watches the NDAC handshake line. It sends no
               data, no *IDN?, no device clear and no interface clear, so a
               silent or proprietary instrument (the Novocontrol Alpha answers
               its own command set, not SCPI) is detected without being
               spoken to. Nothing is ever written to any instrument by this
               tool -- --idn is read-only and only visits addresses you name.

 USAGE:        py -3.10-32 GPIB_Scanner_32bit_CLI.py
               py -3.10-32 GPIB_Scanner_32bit_CLI.py --board 0 --board 1
               py -3.10-32 GPIB_Scanner_32bit_CLI.py --idn 24
               py -3.10-32 GPIB_Scanner_32bit_CLI.py --dll "C:\\path\\gpib-32.dll"
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.0
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
ERR = 1 << 15    # error: consult iberr -- and ONLY then, it is stale otherwise
TIMO = 1 << 14   # timeout
END = 1 << 13    # EOI (or EOS) seen: full response received
CMPL = 1 << 8    # I/O complete

# --- NI-488.2 timeout codes for ibdev/ibtmo ---------------------------------
TIMEOUT_CODES = {0.3: 10, 1: 11, 3: 12, 10: 13, 30: 14}

IBERR_MEANINGS = {
    0: "EDVR: system error (board index not present / driver not bound)",
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
    28: "EPWR: interface has lost power",
}

# Response padding the Novocontrol Alpha adds around EOI-terminated replies.
RESPONSE_STRIP = b" \t\r\n\x00\x10"

# GPIB primary addresses that may hold an instrument. 0 is the controller
# board itself and 31 is UNL/UNT, so neither is probed.
SCAN_ADDRESSES = range(1, 31)


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
    print(f"  OS     : {sys.platform}")
    print()
    return bits


# -----------------------------------------------------------------------------
# --- STEP 2: LOCATE A LOADABLE DRIVER DLL ---
# -----------------------------------------------------------------------------

def find_dll_candidates():
    """The standard GPIB DLL locations, tried once each -- no disk hunting.

    On 64-bit Windows the loader redirects a 32-bit process's System32 path
    to SysWOW64, so the System32 entry below is what actually delivers the
    32-bit gpib-32.dll to a 32-bit interpreter. SysWOW64 is listed explicitly
    as well so that a 64-bit interpreter can at least report that a 32-bit
    DLL exists (it will refuse to load it, WinError 193, which is itself the
    diagnosis).
    """
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    paths = [
        os.path.join(windir, "System32", "gpib-32.dll"),
        os.path.join(windir, "System32", "ni4882.dll"),
        os.path.join(windir, "SysWOW64", "gpib-32.dll"),
    ]
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
            stamp = datetime.fromtimestamp(os.path.getmtime(path))
            print(f"  {path}  ({os.path.getsize(path)} bytes, "
                  f"{stamp:%Y-%m-%d})")
        except OSError:
            print(f"  {path}")
        try:
            ctypes.WinDLL(path)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 193:
                print(f"      -> WRONG BITNESS: unloadable from {bits}-bit "
                      "Python.")
            else:
                print(f"      -> failed to load: {exc}")
        else:
            print(f"      -> LOADS in this {bits}-bit Python")
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
    """Minimal, defensive ctypes binding of the traditional NI-488.2 API.

    Deliberately does not touch ibfind: the 32-bit library on the Novocontrol
    PC exports ibfindA/ibfindW only, so board-level work here goes through the
    board index directly (0-3 are valid board descriptors).
    """

    def __init__(self, dll_path):
        self.path = dll_path
        lib = self.lib = ctypes.WinDLL(dll_path)
        c_int, c_size_t = ctypes.c_int, ctypes.c_size_t

        self.ibdev = _bind(lib, "ibdev", [c_int] * 6)
        self.ibonl = _bind(lib, "ibonl", [c_int, c_int])
        self.ibwrt = _bind(lib, "ibwrt", [c_int, ctypes.c_char_p, c_size_t])
        self.ibrd = _bind(lib, "ibrd", [c_int, ctypes.c_char_p, c_size_t])
        self.ibln = _bind(
            lib, "ibln",
            [c_int, c_int, c_int, ctypes.POINTER(ctypes.c_short)])

        missing = [name for name in ("ibdev", "ibonl", "ibwrt", "ibrd", "ibln")
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

    # ------------------------------------------------------------- the census
    def listener_at(self, board, pad):
        """ibln: assert a listen address and watch NDAC. Sends no data.

        Returns (present, ibsta). 'present' is None when the call itself
        errored, which means the board is unusable -- not that the address is
        empty.
        """
        flag = ctypes.c_short(0)
        sta = self.ibln(board, pad, 0, ctypes.byref(flag))
        if sta & ERR:
            return None, sta
        return bool(flag.value), sta

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
# --- STEP 4: THE BUS CENSUS ---
# -----------------------------------------------------------------------------

def census(gpib, board):
    """Probe every primary address on one board. Writes nothing to anything."""
    print(f"== Bus census on board {board} ==")
    print("   ibln only: listen-address assertion, no data sent, no *IDN?, "
          "no device or interface clear.")

    probe, sta = gpib.listener_at(board, 1)
    if probe is None:
        print(f"   board {board} UNUSABLE: {sta_text(sta)}; "
              f"{iberr_text(gpib.error_code())}")
        print()
        return []

    found = []
    for pad in SCAN_ADDRESSES:
        present, sta = gpib.listener_at(board, pad)
        if present is None:
            print(f"   address {pad:2d}: probe failed {sta_text(sta)}; "
                  f"{iberr_text(gpib.error_code())}")
            continue
        if present:
            found.append(pad)

    if found:
        for pad in found:
            print(f"   LISTENER at GPIB{board}::{pad}::INSTR")
    else:
        print("   no listeners found on this board")
    print()
    return found


def identify(gpib, board, pad, timeout):
    """One read-only *IDN? to one address the operator named. No retries.

    Silence is not a fault for a Novocontrol Alpha: it uses its own command
    set and may simply not implement *IDN?. Presence in the census is the
    real evidence that it is on the bus.
    """
    label = f"GPIB{board}::{pad}"
    handle = gpib.open_device(board, pad, TIMEOUT_CODES[timeout])
    if handle < 0:
        print(f"   {label}: could not open ({iberr_text(gpib.error_code())})")
        return None
    try:
        started = time.perf_counter()
        sta = gpib.write(handle, "*IDN?")
        if sta & ERR:
            print(f"   {label}: *IDN? write failed {sta_text(sta)}; "
                  f"{iberr_text(gpib.error_code())}")
            return None
        data, sta = gpib.read(handle)
        elapsed = (time.perf_counter() - started) * 1000
        if data is None:
            print(f"   {label}: no reply {sta_text(sta)}; "
                  f"{iberr_text(gpib.error_code())}  [{elapsed:.0f} ms]")
            print("            (a Novocontrol Alpha may legitimately ignore "
                  "*IDN?; try INTTYP? in the Lifeline console)")
            return None
        text = decode(data)
        print(f"   {label}: {text}  [{elapsed:.0f} ms]")
        return text
    finally:
        gpib.close_device(handle)


# -----------------------------------------------------------------------------
# --- MAIN ---
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Raw NI-488.2 GPIB bus scanner (stdlib only, no pyvisa, "
                    "no VISA). Lists occupied addresses without sending "
                    "anything to any instrument.")
    parser.add_argument("--board", type=int, action="append",
                        help="GPIB board index to scan (repeatable; "
                             "default 0 and 1)")
    parser.add_argument("--idn", type=int, action="append", metavar="ADDR",
                        help="after the census, send ONE read-only *IDN? to "
                             "this address on the first scanned board "
                             "(repeatable). Omit to send nothing at all.")
    parser.add_argument("--dll", help="explicit path to the GPIB DLL "
                                      "(default: System32 gpib-32.dll / "
                                      "ni4882.dll, SysWOW64 gpib-32.dll)")
    parser.add_argument("--timeout", type=float, default=3,
                        choices=sorted(TIMEOUT_CODES),
                        help="I/O timeout in seconds for --idn (default 3)")
    parser.add_argument("--no-report", action="store_true",
                        help="print to the console only; write no report file")
    args = parser.parse_args()

    if os.name != "nt":
        print("This scanner drives the Windows NI-488.2 DLL; on Linux use "
              "linux-gpib.")
        return 1

    boards = args.board if args.board else [0, 1]

    report_path = os.path.abspath(
        f"gpib_scan_report_{datetime.now():%Y%m%d_%H%M%S}.txt")
    tee = None if args.no_report else Tee(report_path)
    if tee is not None:
        sys.stdout = tee
    try:
        print("PICA GPIB Bus Scanner (32-bit safe) -- "
              f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        if tee is not None:
            print(f"Report file: {report_path}")
        print()

        bits = report_environment()

        candidates = [args.dll] if args.dll else find_dll_candidates()
        loadable = classify_candidates(candidates, bits)

        if not loadable:
            print("== VERDICT ==")
            print("  No loadable GPIB DLL in this interpreter.")
            print("  1. If a DLL was listed as WRONG BITNESS, re-run under "
                  "the matching Python (py -3.10-32).")
            print("  2. Otherwise install / repair the GPIB driver, or point "
                  "at a specific DLL with --dll <path>.")
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
                  "(ibdev/ibwrt/ibrd/ibln). This is not a usable 488.2 "
                  "driver.")
            return 2

        total = {}
        for board in boards:
            total[board] = census(gpib, board)

        occupied = sum(len(pads) for pads in total.values())
        print("== VERDICT ==")
        if occupied:
            for board, pads in total.items():
                if pads:
                    print(f"  board {board}: " + ", ".join(
                        f"GPIB{board}::{pad}::INSTR" for pad in pads))
            print("  These addresses answered the listen handshake. That is "
                  "proof of a powered, cabled, correctly addressed "
                  "instrument -- independent of whether it speaks SCPI.")
        else:
            print("  No listeners on any scanned board. Check instrument "
                  "power, cable seating, the address set on the instrument, "
                  "and that no other GPIB program (WinDETA, NI MAX) holds "
                  "the bus.")
        print()

        if args.idn:
            print("== Identification (read-only, one attempt each) ==")
            for pad in args.idn:
                identify(gpib, boards[0], pad, args.timeout)
            print()
        return 0

    except Exception as exc:   # never die silently; the report must exist
        print(f"\nUNEXPECTED ERROR: {exc!r}")
        return 5
    finally:
        if tee is not None:
            print(f"\nFull transcript saved to: {report_path}")
            tee.close()


if __name__ == "__main__":
    sys.exit(main())
