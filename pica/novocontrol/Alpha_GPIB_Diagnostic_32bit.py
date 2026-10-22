'''
Alpha_GPIB_Diagnostic_32bit.py -- one-shot diagnostic log for the
Novocontrol Alpha-AN on the BDS PC (32-bit NI-488.2 gpib-32.dll, no VISA).

Close WinALPHA / WinDETA first, then run in 32-bit Python:

    py -3.10-32 Alpha_GPIB_Diagnostic_32bit.py              # safe checks only
    py -3.10-32 Alpha_GPIB_Diagnostic_32bit.py --reset-test # + time *RST
    py -3.10-32 Alpha_GPIB_Diagnostic_32bit.py --measure    # + one 1 kHz point
    py -3.10-32 Alpha_GPIB_Diagnostic_32bit.py --all        # all of the above

Options: --address 10  --board 0  --acv 0.1  --freq 1000  --mtm 0.5
         --probe-unverified  (MBK, ZTSTAT?, ZCONSPL=0 while idle)

Default checks change nothing on the analyzer: Python and DLL details,
running GPIB programs, board state (Interface Clear only if the board reports
ECIC), bus census, *IDN? / INTTYP? / GFR? on the Alpha. --reset-test sends
*RST (parks the generator, keeps calibration). --measure applies ACV to the
sample for one point and asks you to type YES first; it always ends with
ACV=0 and ZCONSPL=0.

Everything is printed and written to AlphaDiag_<date>_<time>.log next to
this file. Send that log back.
'''
import argparse
import ctypes
import os
import platform
import struct
import subprocess
import sys
import time
from datetime import datetime

# --- NI-488.2 constants ---
ERR, TIMO, END, RQS, CMPL = 1 << 15, 1 << 14, 1 << 13, 1 << 11, 1 << 8
T10S, T30S, T300S = 13, 14, 16
IBA_PAD, IBA_SC = 0x0001, 0x000A          # ibask options
IBERR = {0: "EDVR system error / board not present", 1: "ECIC not "
         "Controller-In-Charge", 2: "ENOL no listener", 3: "EADR not "
         "addressed", 4: "EARG bad argument", 5: "ESAC not System "
         "Controller", 6: "EABO timeout / I/O aborted", 7: "ENEB no such "
         "board", 8: "EDMA", 10: "EOIP async I/O in progress", 11: "ECAP "
         "not capable", 12: "EFSO file system", 14: "EBUS bus error",
         15: "ESTB status byte lost", 16: "ESRQ SRQ stuck on",
         20: "ETAB table", 28: "EPWR lost power"}
ZRE_STATUS = {0: "invalid (buffer empty)", 1: "still in progress",
              2: "RESULT VALID", 3: "V1 out of range (sample)",
              4: "current out of range (sample)", 5: "V1 out of range "
              "(reference)", 6: "signal source disconnected"}

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(
    HERE, f"AlphaDiag_{datetime.now():%Y%m%d_%H%M%S}.log")
_log_file = open(LOG_PATH, "w", encoding="utf-8")
T0 = time.monotonic()


def log(text=""):
    line = f"[{time.monotonic() - T0:8.3f}s] {text}" if text else ""
    print(line)
    _log_file.write(line + "\n")
    _log_file.flush()


def section(title):
    log()
    log("=" * 70)
    log(title)
    log("=" * 70)


def sta_text(sta):
    bits = [n for m, n in ((ERR, "ERR"), (TIMO, "TIMO"), (END, "END"),
                           (RQS, "RQS"), (CMPL, "CMPL")) if sta & m]
    return f"ibsta=0x{sta & 0xFFFF:04X}[{' '.join(bits) or '-'}]"


class Gpib:
    def __init__(self, path):
        self.lib = lib = ctypes.WinDLL(path)
        i, sz = ctypes.c_int, ctypes.c_long
        sig = {"ibdev": [i] * 6, "ibonl": [i, i], "ibtmo": [i, i],
               "ibwrt": [i, ctypes.c_char_p, sz],
               "ibrd": [i, ctypes.c_char_p, sz], "ibwait": [i, i],
               "ibrsp": [i, ctypes.POINTER(ctypes.c_char)],
               "ibln": [i, i, i, ctypes.POINTER(ctypes.c_short)],
               "ibsic": [i], "ibask": [i, i, ctypes.POINTER(i)],
               "ibclr": [i]}
        self.missing = []
        for name, args in sig.items():
            try:
                fn = getattr(lib, name)
                fn.argtypes, fn.restype = args, i
                setattr(self, name, fn)
            except AttributeError:
                self.missing.append(name)
                setattr(self, name, None)

    def err(self):
        try:
            return self.lib.ThreadIberr()
        except AttributeError:
            return ctypes.c_int.in_dll(self.lib, "iberr").value

    def cnt(self):
        try:
            f = self.lib.ThreadIbcntl
            f.restype = ctypes.c_long
            return f()
        except AttributeError:
            return ctypes.c_long.in_dll(self.lib, "ibcntl").value

    def why(self, sta):
        if not sta & ERR:
            return sta_text(sta)
        e = self.err()
        return f"{sta_text(sta)} iberr={e} ({IBERR.get(e, 'unknown')})"


class Alpha:
    """Device session with full per-transfer logging."""

    def __init__(self, g, board, pad):
        self.g = g
        self.ud = g.ibdev(board, pad, 0, T10S, 1, 0)   # EOI on, no EOS
        log(f"ibdev(board={board}, pad={pad}, T10s, eot=1, eos=0) -> "
            f"ud={self.ud}" + ("" if self.ud >= 0 else
                               f"  FAILED iberr={g.err()}"))

    def write(self, cmd):
        data = cmd.encode("ascii")
        t = time.monotonic()
        sta = self.g.ibwrt(self.ud, data, len(data))
        log(f"  WRITE {cmd!r:28} {self.g.why(sta)} "
            f"({(time.monotonic() - t) * 1000:.0f} ms)")
        return not sta & ERR, (self.g.err() if sta & ERR else None)

    def read(self):
        buf = ctypes.create_string_buffer(65536)
        t = time.monotonic()
        sta = self.g.ibrd(self.ud, buf, len(buf))
        dt = (time.monotonic() - t) * 1000
        if sta & ERR:
            log(f"  READ  {'':28} {self.g.why(sta)} ({dt:.0f} ms)")
            return None
        raw = buf.raw[:self.g.cnt()]
        log(f"  READ  {raw!r} {sta_text(sta)} ({dt:.0f} ms)")
        return raw.strip(b" \t\r\n\x00\x10").decode("ascii", "replace")

    def query(self, cmd):
        ok, _ = self.write(cmd)
        return self.read() if ok else None

    def poll(self):
        b = ctypes.c_char()
        sta = self.g.ibrsp(self.ud, ctypes.byref(b))
        v = ord(b.value) if b.value else 0
        log(f"  SERIAL POLL -> STB=0x{v:02X} {self.g.why(sta)}")
        return v

    def wait_srq(self, timeout_code):
        self.g.ibtmo(self.ud, timeout_code)
        t = time.monotonic()
        sta = self.g.ibwait(self.ud, RQS | TIMO)
        self.g.ibtmo(self.ud, T10S)
        log(f"  WAIT SRQ -> {self.g.why(sta)} after "
            f"{time.monotonic() - t:.2f} s")
        return bool(sta & RQS)

    def close(self):
        if self.ud >= 0:
            self.g.ibonl(self.ud, 0)
            log("ibonl(ud, 0): device handle released")


# ---------------------------------------------------------------- checks
def check_environment():
    section("1. ENVIRONMENT")
    log(f"Python {sys.version.split()[0]} {struct.calcsize('P') * 8}-bit  "
        f"exe={sys.executable}")
    log(f"OS {platform.platform()}  machine={platform.machine()}")
    log(f"Script {os.path.abspath(__file__)}")
    for mod in ("matplotlib", "PIL", "numpy"):
        try:
            m = __import__(mod)
            log(f"  {mod} {getattr(m, '__version__', '?')}")
        except Exception as e:
            log(f"  {mod} NOT importable: {e}")
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


def load_driver():
    section("2. GPIB DRIVER")
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    for p in (os.path.join(windir, "System32", "gpib-32.dll"),
              os.path.join(windir, "System32", "ni4882.dll"),
              os.path.join(windir, "SysWOW64", "gpib-32.dll")):
        if os.path.isfile(p):
            st = os.stat(p)
            log(f"  present: {p}  {st.st_size} bytes  modified "
                f"{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d}")
        else:
            log(f"  absent:  {p}")
    if struct.calcsize("P") * 8 != 32:
        log("STOP: this is 64-bit Python. Run with: py -3.10-32 "
            + os.path.basename(__file__))
        return None
    path = os.path.join(windir, "System32", "gpib-32.dll")
    try:
        g = Gpib(path)
    except OSError as e:
        log(f"STOP: cannot load {path}: {e} "
            f"(winerror={getattr(e, 'winerror', None)})")
        return None
    log(f"Loaded {path} (32-bit view; Windows maps it to SysWOW64)")
    log("Missing exports: " + (", ".join(g.missing) or "none"))
    return g


def check_board(g, board, pad):
    section(f"3. BOARD {board} STATE")
    for name, opt in (("PAD (board's own address)", IBA_PAD),
                      ("System Controller", IBA_SC)):
        if g.ibask is None:
            break
        v = ctypes.c_int(-1)
        sta = g.ibask(board, opt, ctypes.byref(v))
        log(f"  ibask {name}: {v.value}  {g.why(sta)}")
    flag = ctypes.c_short(0)
    sta = g.ibln(board, pad, 0, ctypes.byref(flag))
    log(f"  ibln probe pad {pad}: listener={bool(flag.value)} {g.why(sta)}")
    if sta & ERR and g.err() == 1 and g.ibsic is None:
        log("  Board is NOT Controller-In-Charge and the DLL has no ibsic.")
    elif sta & ERR and g.err() == 1:
        log("  Board is NOT Controller-In-Charge -> sending Interface "
            "Clear (ibsic)")
        sta = g.ibsic(board)
        log(f"  ibsic({board}) -> {g.why(sta)}")
        sta = g.ibln(board, pad, 0, ctypes.byref(flag))
        log(f"  ibln re-probe: listener={bool(flag.value)} {g.why(sta)}")
    return not sta & ERR


def census(g, board):
    section(f"4. BUS CENSUS, board {board} (listen handshake only)")
    found = []
    for pad in range(1, 31):
        flag = ctypes.c_short(0)
        sta = g.ibln(board, pad, 0, ctypes.byref(flag))
        if sta & ERR:
            log(f"  pad {pad}: {g.why(sta)}")
            break
        if flag.value:
            found.append(pad)
    log(f"Listeners: {found or 'NONE'}")
    return found


def identify(alpha):
    section("5. ALPHA IDENTITY (read-only queries)")
    alpha.poll()
    idn = alpha.query("*IDN?")
    inttyp = alpha.query("INTTYP?")
    alpha.query("GFR?")
    log(f"IDN: {idn!r}")
    log(f"INTTYP: {inttyp!r}  (ZG4 = code 5)")
    alpha.poll()
    return idn is not None


def reset_test(alpha, timeout_s=60):
    section("6. *RST RECOVERY TIMING")
    alpha.write("*RST")
    t = time.monotonic()
    tries = 0
    while time.monotonic() - t < timeout_s:
        time.sleep(0.5)
        tries += 1
        ok, err = alpha.write("*IDN?")
        if ok:
            alpha.read()
            log(f"RESULT: Alpha answered {time.monotonic() - t:.1f} s "
                f"after *RST ({tries} attempts)")
            return True
        if err != 2:
            log(f"RESULT: unexpected error iberr={err} while waiting")
            return False
    log(f"RESULT: no answer within {timeout_s} s after *RST")
    return False


def probe_unverified(alpha):
    section("7. COMMANDS NOT VERIFIED AGAINST THE MANUAL (idle analyzer)")
    for cmd in ("ZTSTAT?", "MBK", "ZCONSPL=0"):
        r = alpha.query(cmd)
        log(f"  {cmd:10} -> {r!r}")


def measure(alpha, a):
    section(f"8. ONE POINT: {a.freq:g} Hz, ACV={a.acv:g} V, MTM={a.mtm:g} s")
    if a.acv > 3.0 or not 3e-5 <= a.freq <= 4e6:
        log("STOP: ACV must be <= 3 V and freq 30 uHz..4 MHz here.")
        return
    answer = input(f"Apply {a.acv:g} Vrms to the connected sample? "
                   "Type YES: ").strip()
    log(f"User answered {answer!r}")
    if answer != "YES":
        log("Skipped.")
        return
    try:
        if not reset_test(alpha):
            return
        time.sleep(1.0)
        for cmd in ("MODE=IMP", "INTTYP?", "ZREFMODE=-3", "ZLLCOR=1",
                    "ZSLCAL=1", f"FRS={a.wires}", "DRS=0 0",
                    f"ACV={a.acv:.6g}", f"MTM={a.mtm:.6g}",
                    f"GFR={a.freq:.6e}"):
            r = alpha.query(cmd)
            if r is None or (cmd[-1] != "?" and r != "OK"):
                log(f"STOP: {cmd} answered {r!r}")
                return
        alpha.poll()
        alpha.write("MST")
        if not alpha.wait_srq(T300S):
            log("No SRQ: aborting with MBK, then ZTSTAT?")
            alpha.query("MBK")
            alpha.query("ZTSTAT?")
            return
        alpha.poll()
        z = alpha.query("ZRE?")
        try:
            f = z.split("=", 1)[-1].replace(",", " ").split()
            log(f"RESULT: Z'={f[0]}  Z''={f[1]}  f={f[2]} Hz  "
                f"status={f[3]} ({ZRE_STATUS.get(int(float(f[3])), '?')})"
                f"  ref={f[4]}")
        except Exception as e:
            log(f"Could not parse ZRE? reply {z!r}: {e}")
    finally:
        log("Safe state:")
        alpha.query("ACV=0")
        alpha.query("ZCONSPL=0")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--address", type=int, default=10)
    ap.add_argument("--board", type=int, default=0)
    ap.add_argument("--reset-test", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--probe-unverified", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--acv", type=float, default=0.1)
    ap.add_argument("--freq", type=float, default=1000.0)
    ap.add_argument("--mtm", type=float, default=0.5)
    ap.add_argument("--wires", choices=("2", "4"), default="2")
    a = ap.parse_args()
    if a.all:
        a.reset_test = a.measure = a.probe_unverified = True

    log(f"Alpha GPIB diagnostic  {datetime.now():%Y-%m-%d %H:%M:%S}  "
        f"args={vars(a)}")
    check_environment()
    g = load_driver()
    if g is None:
        return
    if not check_board(g, a.board, a.address):
        log("STOP: board unusable (see above).")
        return
    found = census(g, a.board)
    if a.address not in found:
        log(f"STOP: nothing listening at address {a.address}.")
        return
    alpha = Alpha(g, a.board, a.address)
    if alpha.ud < 0:
        return
    try:
        if not identify(alpha):
            return
        if a.probe_unverified:
            probe_unverified(alpha)
        if a.reset_test and not a.measure:
            reset_test(alpha)
        if a.measure:
            measure(alpha, a)
    finally:
        alpha.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("UNEXPECTED ERROR:\n" + traceback.format_exc())
    finally:
        log()
        log(f"Log written to {LOG_PATH}")
        _log_file.close()
        if os.name == "nt" and sys.stdin and sys.stdin.isatty():
            input("Press Enter to close...")
