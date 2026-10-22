'''
Alpha_GPIB_MiniTalk_32bit.py -- smallest possible talk test for the
Novocontrol Alpha-AN over the 32-bit NI-488.2 gpib-32.dll (no VISA).

Run in 32-bit Python, with WinALPHA / WinDETA CLOSED (they hold the bus):

    py -3.10-32 Alpha_GPIB_MiniTalk_32bit.py          # address 10
    py -3.10-32 Alpha_GPIB_MiniTalk_32bit.py 10 0     # address, board

Sends *IDN?, prints the reply, then lets you type commands like WinALPHA's
"Send GPIB Commands to Analyzer" box. Queries end in '?'; set commands
(e.g. ACV=0.1) answer OK or an error code, which is read back too.
Empty line quits.
'''
import ctypes
import os
import struct
import sys

ADDRESS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
BOARD = int(sys.argv[2]) if len(sys.argv) > 2 else 0
T10S = 13                     # NI-488.2 timeout code for 10 s
ERR = 0x8000

if struct.calcsize("P") * 8 != 32:
    sys.exit("This is 64-bit Python. Run:  py -3.10-32 " + sys.argv[0])

# From 32-bit Python, System32 is redirected to SysWOW64 (the 32-bit DLL).
dll = ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                 "System32", "gpib-32.dll"))
for name, args in (("ibdev", [ctypes.c_int] * 6),
                   ("ibwrt", [ctypes.c_int, ctypes.c_char_p, ctypes.c_long]),
                   ("ibrd", [ctypes.c_int, ctypes.c_char_p, ctypes.c_long]),
                   ("ibonl", [ctypes.c_int, ctypes.c_int]),
                   ("ibsic", [ctypes.c_int])):
    getattr(dll, name).argtypes = args
    getattr(dll, name).restype = ctypes.c_int


def iberr():
    try:
        return dll.ThreadIberr()
    except AttributeError:
        return ctypes.c_int.in_dll(dll, "iberr").value


def count():
    try:
        dll.ThreadIbcntl.restype = ctypes.c_long
        return dll.ThreadIbcntl()
    except AttributeError:
        return ctypes.c_long.in_dll(dll, "ibcntl").value


# Interface Clear: make our board Controller-In-Charge again in case another
# GPIB program (WinALPHA) left it otherwise -- else every call fails ECIC.
if dll.ibsic(BOARD) & ERR:
    sys.exit(f"ibsic failed (iberr={iberr()}; 1=ECIC, 5=not system "
             f"controller). Close WinALPHA/WinDETA and retry.")

# ibdev(board, pad, sad, timeout, eot=1 -> EOI on last byte, eos=0 -> none)
ud = dll.ibdev(BOARD, ADDRESS, 0, T10S, 1, 0)
if ud < 0:
    sys.exit(f"ibdev failed (iberr={iberr()}). Is the board index right?")


def query(cmd):
    data = cmd.encode("ascii")
    if dll.ibwrt(ud, data, len(data)) & ERR:
        return f"<write failed, iberr={iberr()}; 2=no listener, 6=timeout>"
    buf = ctypes.create_string_buffer(4096)
    if dll.ibrd(ud, buf, len(buf)) & ERR:
        return f"<read failed, iberr={iberr()}; 6=timeout>"
    return buf.raw[:count()].strip(b" \t\r\n\x00").decode("ascii", "replace")


try:
    print(f"GPIB{BOARD}::{ADDRESS}  *IDN? ->", query("*IDN?"))
    while True:
        cmd = input("cmd> ").strip()
        if not cmd:
            break
        print("   ", query(cmd))
finally:
    dll.ibonl(ud, 0)          # release the device handle
