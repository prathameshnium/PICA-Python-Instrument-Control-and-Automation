'''
===============================================================================
 PROGRAM:      PICA GPIB Lifeline Console
 PURPOSE:      Last-resort GPIB diagnostic and terminal that needs NOTHING
               but a stock Python install: it talks to the NI-488.2 style
               driver DLL (gpib-32.dll) directly through ctypes, bypassing
               pyvisa, pyvisa-py and gpib-ctypes entirely. Built to bring up
               a Novocontrol Alpha analyzer on Novocontrol's own (ines) GPIB
               PCI card, but works with any NI-488.2 compatible stack.

               What it does, in order:
                 1. Reports the Python/OS environment and package pitfalls
                    (64- vs 32-bit, the 'pygpib' name-squat shadow, ...).
                 2. Hunts for candidate GPIB DLLs (System32, C:\\GPIB, ines /
                    Novocontrol / WinDETA folders; --deep walks all of C:\\)
                    and classifies each: loads / wrong bitness / broken.
                 3. Scans the bus for listeners and asks each for *IDN?.
                 4. Opens an interactive terminal on the instrument.
               Everything is echoed to a timestamped report file, so a
               failed attempt still produces evidence to debug from.

               Being stdlib-only is deliberate: if only a 32-bit driver DLL
               exists, this same file runs unmodified on a bare 32-bit
               Python (py -3-32) with no pip and no internet.

               SAFETY: automatically sends nothing but *IDN? (a read-only
               IEEE-488.2 query). In the terminal, queries pass freely,
               state-changing writes require an explicit yes, DCV/DCE (DC
               bias -- no bias hardware on this mainframe) are blocked
               outright, and ':safe' parks the analyzer with the documented
               sequence MBK, ACV=0, ZCONSPL=0.

 USAGE:        python GPIB_Lifeline_CLI.py
               python GPIB_Lifeline_CLI.py --deep
               python GPIB_Lifeline_CLI.py --address 5
               python GPIB_Lifeline_CLI.py --dll "C:\\path\\to\\gpib-32.dll"
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

DLL_BASENAMES = ("gpib-32.dll", "gpib32.dll", "ni4882.dll")

# --- Instrument safety -------------------------------------------------------
# DC-bias writes are hard-blocked: the lab's Alpha-AN mainframe has no bias
# hardware, and pica/novocontrol never transmits DCV/DCE either. Every other
# state-changing write requires an explicit yes; queries ('?') pass freely.
BLOCKED_WRITE_PREFIXES = ("DCV", "DCE")
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
    print(f"  Python     : {sys.version.split()[0]} ({bits}-bit)  "
          f"{sys.executable}")
    print(f"  OS         : {sys.platform}  "
          f"{os.environ.get('OS', '')}".rstrip())
    for package in ("pyvisa", "pyvisa_py", "gpib_ctypes", "serial"):
        try:
            module = __import__(package)
            version = getattr(module, "__version__", "?")
            print(f"  {package:<11}: installed ({version})")
        except Exception as exc:
            print(f"  {package:<11}: NOT importable ({exc})")
    # The empty name-squatting 'pygpib' package shadows real gpib bindings.
    try:
        import gpib as gpib_probe
        if not (hasattr(gpib_probe, "ibln")
                or hasattr(gpib_probe, "find_listeners")):
            print("  WARNING    : a stub 'gpib' module is installed (likely "
                  "the empty 'pygpib' package). Run: pip uninstall pygpib")
    except Exception:
        pass
    print()
    return bits


# -----------------------------------------------------------------------------
# --- STEP 2: DLL HUNT ---
# -----------------------------------------------------------------------------

def find_dll_candidates(deep=False):
    """Ordered list of existing candidate GPIB DLL paths."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        os.path.join(windir, "System32", "gpib-32.dll"),
        os.path.join(windir, "SysWOW64", "gpib-32.dll"),
        r"C:\GPIB\gpib-32.dll",
    ]

    roots = {os.environ.get(key) for key in
             ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")}
    for base in [root for root in roots if root] + ["C:\\"]:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            lowered = entry.lower()
            if not any(key in lowered for key in
                       ("ines", "novocontrol", "windeta", "gpib",
                        "national instruments")):
                continue
            for dirpath, _dirs, files in os.walk(os.path.join(base, entry)):
                for fname in files:
                    if fname.lower() in DLL_BASENAMES:
                        candidates.append(os.path.join(dirpath, fname))

    if deep:
        print("  (--deep: walking C:\\ for GPIB DLLs, this can take a few "
              "minutes...)")
        skip = {"winsxs", "$recycle.bin", "windows.old", "node_modules",
                ".git", "servicing"}
        for dirpath, dirs, files in os.walk("C:\\"):
            dirs[:] = [d for d in dirs if d.lower() not in skip]
            for fname in files:
                if fname.lower() in DLL_BASENAMES:
                    candidates.append(os.path.join(dirpath, fname))

    unique, seen = [], set()
    for path in candidates:
        key = os.path.normcase(path)
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            unique.append(path)
    return unique


def classify_candidates(candidates, bits):
    """Try-loads every candidate. Returns (loadable_paths, printed table)."""
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
        self.ibln = _bind(lib, "ibln",
                          [c_int, c_int, c_int,
                           ctypes.POINTER(ctypes.c_short)])
        self.ibask = _bind(lib, "ibask",
                           [c_int, c_int, ctypes.POINTER(c_int)])

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


def scan_bus(gpib, boards, probe_timeout_code):
    """Finds listeners and their *IDN? replies. Returns [(board, pad, idn)].

    Prefers ibln (fast, no timeouts); falls back to per-address *IDN?
    probing, where a missing device fails fast with ENOL.
    """
    found = []
    for board in boards:
        controller_pad = None
        if gpib.ibask is not None:
            value = ctypes.c_int(0)
            sta = gpib.ibask(board, 0x01, ctypes.byref(value))   # IbaPAD
            if sta & ERR:
                print(f"  GPIB{board}: no such board "
                      f"({iberr_text(gpib.error_code())})")
                continue
            controller_pad = value.value
            print(f"  GPIB{board}: board present, controller at PAD "
                  f"{controller_pad}")

        pads = None
        if gpib.ibln is not None:
            pads = []
            for pad in range(0, 31):
                if pad == controller_pad:
                    continue
                flag = ctypes.c_short(0)
                sta = gpib.ibln(board, pad, 0, ctypes.byref(flag))
                if sta & ERR:
                    print(f"  GPIB{board}: ibln failed at PAD {pad} "
                          f"({iberr_text(gpib.error_code())}); switching "
                          "to write-probing.")
                    pads = None
                    break
                if flag.value:
                    pads.append(pad)

        if pads is None:   # ibln unavailable or failing: probe by writing
            pads = []
            for pad in range(0, 31):
                if pad == controller_pad:
                    continue
                handle = gpib.open_device(board, pad, probe_timeout_code)
                if handle < 0:
                    continue
                try:
                    sta = gpib.write(handle, "*IDN?")
                    if not (sta & ERR):
                        pads.append(pad)
                finally:
                    gpib.close_device(handle)

        for pad in pads:
            handle = gpib.open_device(board, pad, probe_timeout_code)
            idn = ""
            if handle >= 0:
                try:
                    sta = gpib.write(handle, "*IDN?")
                    if not (sta & ERR):
                        data, sta = gpib.read(handle)
                        if data is not None:
                            idn = decode(data)
                finally:
                    gpib.close_device(handle)
            label = idn or "(listener present, no *IDN? reply)"
            print(f"  GPIB{board}::{pad}  ->  {label}")
            found.append((board, pad, idn))
    return found


# -----------------------------------------------------------------------------
# --- STEP 4: INTERACTIVE TERMINAL ---
# -----------------------------------------------------------------------------

def repl(gpib, handle, resource_label):
    print(f"\n== Interactive terminal on {resource_label} ==")
    print("   Command ending in '?' -> write + read; otherwise write only.")
    print("   SAFETY: queries send freely; state-changing writes ask for "
          "confirmation; DCV/DCE (DC bias) writes are blocked outright.")
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
                print("  BLOCKED: DCV/DCE set the DC bias; this mainframe "
                      "has no bias hardware and PICA never sends them.")
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
        description="Raw NI-488.2 GPIB diagnostic + terminal (stdlib only)")
    parser.add_argument("--dll", help="explicit path to the GPIB DLL")
    parser.add_argument("--board", type=int, default=None,
                        help="GPIB board index (default: scan 0-3)")
    parser.add_argument("--address", type=int, default=None,
                        help="primary address to connect to (default: first "
                             "listener found)")
    parser.add_argument("--timeout", type=float, default=3,
                        choices=sorted(TIMEOUT_CODES), help="I/O timeout in "
                        "seconds for the interactive session (default 3)")
    parser.add_argument("--deep", action="store_true",
                        help="walk all of C:\\ when hunting for GPIB DLLs")
    parser.add_argument("--no-repl", action="store_true",
                        help="diagnose and scan only; no interactive session")
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

        candidates = ([args.dll] if args.dll
                      else find_dll_candidates(deep=args.deep))
        loadable = classify_candidates(candidates, bits)

        if not loadable:
            print("== VERDICT ==")
            print("  No loadable NI-488.2 DLL. In order of preference:")
            print("  1. Install the ines GPIB driver for x64 with its "
                  "NI-488.2 compatibility component (Novocontrol cards are "
                  "ines cards; driver via Novocontrol support / ines).")
            print("  2. If only a 32-bit DLL exists: install 32-bit Python "
                  "(py -3-32) and rerun THIS script with it -- it needs no "
                  "pip packages at all.")
            print("  3. Rerun with --deep to search the whole disk, then "
                  "with --dll <path> to force a specific DLL.")
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
                  "install the ines NI-488.2 compatibility component.")
            return 2

        print("== Bus scan ==")
        boards = [args.board] if args.board is not None else [0, 1, 2, 3]
        probe_code = TIMEOUT_CODES[1]
        listeners = scan_bus(gpib, boards, probe_code)
        print()

        if not listeners and args.address is None:
            print("== VERDICT ==")
            print("  Driver DLL works, but no instrument answered on the "
                  "bus. Check, in order:")
            print("  1. WinDETA (or any other GPIB program) is CLOSED.")
            print("  2. The analyzer is powered on and the GPIB cable is "
                  "seated at both ends.")
            print("  3. The board is configured as GPIB0 (ines/driver "
                  "configuration utility, or try --board 1).")
            print("  4. Retry with an explicit address: --address 5 (or "
                  "whatever WinDETA's device settings show).")
            return 4

        if args.address is not None:
            board = args.board if args.board is not None else 0
            target = (board, args.address)
        else:
            with_idn = [entry for entry in listeners if entry[2]]
            chosen = (with_idn or listeners)[0]
            target = (chosen[0], chosen[1])

        label = f"GPIB{target[0]}::{target[1]}"
        handle = gpib.open_device(target[0], target[1],
                                  TIMEOUT_CODES[args.timeout])
        if handle < 0:
            print(f"Could not open {label} "
                  f"({iberr_text(gpib.error_code())}).")
            return 4

        try:
            sta = gpib.write(handle, "*IDN?")
            if not (sta & ERR):
                data, sta = gpib.read(handle)
                if data is not None:
                    print(f"{label} *IDN? -> {decode(data)}")
            print("\n== VERDICT ==")
            print("  COMMUNICATION ESTABLISHED at the raw driver level.")
            print("  The SCPI Console GUI (pyvisa-py backend, Term = EOI) "
                  "should now work; if it does not, its scan log will say "
                  "which layer disagrees.")
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
