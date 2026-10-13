"""Shared support for the C-V module tests (test_cv_ke4980a.py).

Loads CV_KE4980A_GUI.py and the plain-script twin from disk and provides a
fake Keysight E4980A: a capacitor with a series resistance that answers the
SCPI subset the modules use (BUS trigger path, :FETC?, the four monitors,
the header snapshot queries), records every write in order, can return a
chosen status on chosen points and can fail :FETC? on demand to exercise
the bus-error retry. No hardware, no Tk root. Not a test file itself.
"""

import importlib.util
import math
import os
import queue
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

KEYSIGHT_DIR = os.path.join(REPO_ROOT, "pica", "keysight")
UTILS_DIR = os.path.join(REPO_ROOT, "pica", "utils")
GUI_PATH = os.path.join(KEYSIGHT_DIR, "CV_KE4980A_GUI.py")
SCRIPT_PATH = os.path.join(KEYSIGHT_DIR, "Instrument_Control",
                           "CV_KE4980A_Simple_Instrument_Control.py")
PLOTTER_PATH = os.path.join(UTILS_DIR, "PlotterUtil_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gui = _load("cv_gui_under_test", GUI_PATH)
script = _load("cv_script_under_test", SCRIPT_PATH)
MODULES = (gui, script)

for _m in MODULES:
    _m.RETRY_SCHEDULE_S = (0.01, 0.01, 0.02)


class FakeE4980A:
    """Capacitor `c_f` with series resistance `r_ohm` behind an E4980A with
    Option 001. status_at: {point_index: status} for :FETC? replies;
    fail_reads: set of :FETC? indices that raise; fail_forever: every
    :FETC? raises. Records every write."""

    def __init__(self, c_f=1e-9, r_ohm=100.0, status_at=None, fail_reads=(),
                 fail_forever=False, opt="001,710", idn="Keysight Technologies,E4980A,MY46000000,A.06.17"):
        self.c = c_f
        self.r = r_ohm
        self.status_at = dict(status_at or {})
        self.fail_reads = set(fail_reads)
        self.fail_forever = fail_forever
        self.opt = opt
        self.idn = idn
        self.writes = []
        self.v = 0.0
        self.freq = 1000.0
        self.bias_on = False
        self.ac = 1.0
        self.timeout = 0
        self.read_termination = ""
        self.write_termination = ""
        self.fetches = 0
        self.triggered = False
        self.closed = False
        self.on_fetch = None       # callable(idx) run before every :FETC? (deterministic Stop)
        self.opens = 0

    def write(self, cmd):
        self.writes.append(cmd)
        for part in cmd.split(";"):
            part = part.strip()
            toks = part.split()
            key = toks[0].upper() if toks else ""
            if key == ":BIAS:VOLT":
                self.v = float(toks[1])
            elif key == ":BIAS:STAT":
                self.bias_on = toks[1].upper() in ("ON", "1")
            elif key == ":FREQ":
                self.freq = float(toks[1])
            elif key == ":VOLT":
                self.ac = float(toks[1])
            elif key == ":TRIG:IMM":
                self.triggered = True
            elif key == "*RST":
                self.v, self.bias_on, self.freq, self.ac = 0.0, False, 1000.0, 1.0

    def _rx(self):
        omega = 2 * math.pi * self.freq
        return self.r, -1.0 / (omega * self.c)

    def query(self, cmd):
        c = cmd.strip().upper()
        if c == "*IDN?":
            return self.idn + "\n"
        if c == "*OPT?":
            return self.opt + "\n"
        if c == "*OPC?":
            return "1\n"
        if c == ":SYST:ERR?":
            return '+0,"No error"\n'
        if c == ":BIAS:VOLT?":
            return f"{self.v:+.6E}\n"
        if c == ":FETC?":
            idx = self.fetches
            self.fetches += 1
            if self.on_fetch is not None:
                self.on_fetch(idx)
            if self.fail_forever or idx in self.fail_reads:
                raise IOError("VI_ERROR_TMO: timeout")
            if not self.triggered:
                return f"{9.9e37:+.6E},{9.9e37:+.6E},-1\n"
            self.triggered = False
            status = self.status_at.get(idx, 0)
            if status in (-1, 1):
                return f"{9.9e37:+.6E},{9.9e37:+.6E},{status:+d}\n"
            r, x = self._rx()
            return f"{r:+.6E},{x:+.6E},{status:+d}\n"
        if c == ":FETC:SMON:VDC?":
            return f"{self.v * 0.999:+.6E}\n"
        if c == ":FETC:SMON:IDC?":
            return f"{self.v * 1e-9:+.6E}\n"
        if c == ":FETC:SMON:VAC?":
            return f"{self.ac * 0.98:+.6E}\n"
        if c == ":FETC:SMON:IAC?":
            return f"{self.ac / 1000.0:+.6E}\n"
        if c == ":APER?":
            return "MED,1\n"
        if c == ":VOLT?":
            return f"{self.ac:+.6E}\n"
        if c in (":AMPL:ALC?", ":CORR:OPEN:STAT?", ":CORR:SHOR:STAT?",
                 ":BIAS:RANG:AUTO?", ":FUNC:IMP:RANG:AUTO?"):
            return "1\n"
        if c == ":CORR:LENG?":
            return "1\n"
        if c == ":BIAS:STAT?":
            return "1\n" if self.bias_on else "0\n"
        raise AssertionError(f"unexpected query {cmd!r}")

    def close(self):
        self.closed = True


def make_link(mod, fake):
    link = mod.E4980ALink("FAKE::INSTR", timeout_s=1.0, sleep=lambda s: None)

    def _open():
        fake.opens += 1
        fake.closed = False
        link.inst = fake
    link.open = _open
    link.fake = fake
    return link


def base_params(folder, **extra):
    p = dict(sample_name="Sample_A", ac_bias=1.0, v_start=-1.0, v_stop=1.0, v_step=0.5,
             n_cycles=1, cycle_mode=True, shape=gui.SHAPE_POS_FIRST, freq_list="1000",
             delay=0.0, aper="MED", alc_enabled=True, corr_enabled=True, cable_len="1",
             lcr_visa="FAKE::INSTR", folder=folder)
    p.update(extra)
    return p


def run_worker(worker, stop_after=None, stop_event=None, timeout=30):
    """Run the worker in a thread (so Stop can be pressed) and drain the queue."""
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    if stop_after is not None:
        time.sleep(stop_after)
        stop_event.set()
    t.join(timeout)
    assert not t.is_alive(), "worker did not finish"
    msgs = []
    while True:
        try:
            msgs.append(worker.q.get_nowait())
        except queue.Empty:
            break
    return msgs


def run_cv(fake, folder, mod=gui, stop_after=None, stop_at_fetch=None, worker_sleep=None, **extra):
    """Validate params, run the worker against the fake, return
    (worker, messages, data file paths in order, log path)."""
    p = mod.validate_params(base_params(folder, **extra))
    stop = threading.Event()
    q = queue.Queue()
    link = make_link(mod, fake)
    if stop_at_fetch is not None:
        fake.on_fetch = lambda idx: stop.set() if idx >= stop_at_fetch else None
    worker = mod.CVWorker(p, link, stop, q, sleep=worker_sleep or (lambda s: None))
    msgs = run_worker(worker, stop_after, stop, timeout=60)
    _ = sorted(f for f in os.listdir(folder) if "_CV_" in f and f.endswith("Hz.txt") or
                   ("_CV_" in f and "Hz_" in f))
    return worker, msgs, [os.path.join(folder, f) for f in worker.files], worker.log_path


def read_data_file(path):
    """(header lines without '#', column names, rows as lists of str, trailing comment lines)."""
    header, columns, rows, trailer = [], None, [], []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if not ln:
                continue
            if ln.startswith("#"):
                (trailer if columns is not None else header).append(ln[1:].strip())
            elif columns is None:
                columns = ln.split("\t")
            else:
                rows.append(ln.split("\t"))
    return header, columns, rows, trailer


def column(rows, columns, name, cast=float):
    i = columns.index(name)
    return [cast(r[i]) for r in rows]


def log_lines(log_path):
    with open(log_path, encoding="utf-8") as fh:
        out = []
        for ln in fh:
            iso, t_s, text = ln.rstrip("\n").split(" | ", 2)
            assert "T" in iso and t_s.startswith("t_s="), ln
            out.append(text)
        return out


def plotter_reads(path):
    """Parse `path` exactly as PlotterUtil_GUI does; returns (headers, array)."""
    plotter = _load("plotter_under_test", PLOTTER_PATH)
    cls = None
    for obj in vars(plotter).values():
        if isinstance(obj, type) and hasattr(obj, "_read_data_from_file"):
            cls = obj
            break
    assert cls is not None, "PlotterUtil_GUI has no class with _read_data_from_file"
    inst = object.__new__(cls)
    headers, _delim, data = inst._read_data_from_file(path)
    return headers, data


def run_module_tests(namespace):
    """Plain-python runner: python tests/test_cv_ke4980a.py"""
    failures = 0
    for name, fn in sorted(namespace.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL ", name, "->", type(exc).__name__, exc)
    sys.exit(1 if failures else 0)
