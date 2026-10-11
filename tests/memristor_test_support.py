"""Shared support for the memristor module tests (one test file per module:
test_memristor_sweep.py, test_memristor_retention.py,
test_memristor_endurance.py, test_memristor_analysis.py).

Loads the four modules from disk, makes their waits negligible, and provides
a fake Keithley 2400: a resistor behind the 2400's current compliance that
answers the SCPI subset the modules use, records every write in order, and
can fail :READ? on demand (one index, several, or forever) to exercise the
bus-error retry. No hardware, no Tk root. Not a test file itself.
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

K2400_DIR = os.path.join(REPO_ROOT, "pica", "keithley", "k2400")
UTILS_DIR = os.path.join(REPO_ROOT, "pica", "utils")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sweep = _load("memristor_sweep_under_test", os.path.join(K2400_DIR, "Memristor_Sweep_K2400_GUI.py"))
retention = _load("memristor_retention_under_test", os.path.join(K2400_DIR, "Memristor_Retention_K2400_GUI.py"))
endurance = _load("memristor_endurance_under_test", os.path.join(K2400_DIR, "Memristor_Endurance_K2400_GUI.py"))
analysis = _load("memristor_analysis_under_test", os.path.join(UTILS_DIR, "Memristor_Analysis_GUI.py"))

MODULES = (sweep, retention, endurance)


def _fast(mod):
    """Make the waits of a module negligible for the tests."""
    mod.RST_SLEEP_S = 0.0
    mod.SETTLE_S = 0.0
    mod.PACING_S = 0.0
    mod.RAMP_STEP_S = 0.0
    mod.RECOVERY_BETWEEN_LEGS_S = 0.0
    mod.LOG_INTERVAL_S = 0.05
    mod.RETRY_SCHEDULE_S = (0.01, 0.01, 0.02)


for _m in MODULES:
    _fast(_m)


# -- fake instrument ----------------------------------------------------

class FakeK2400:
    """Resistor of `resistance` ohm behind the 2400's current compliance.
    Records every write in order. fail_reads: set of :READ? indices (0-based)
    that raise; fail_forever: every :READ? raises. ovp_at: |V| at or above
    which status bit 4 is set."""

    def __init__(self, resistance=1e6, fail_reads=(), fail_forever=False, ovp_at=None, prot_readback=None):
        self.r = float(resistance)
        self.writes = []
        self.v = 0.0
        self.icc = 1.05
        self.output_on = False
        self.timeout = 0
        self.reads = 0
        self.fail_reads = set(fail_reads)
        self.fail_forever = fail_forever
        self.ovp_at = ovp_at
        self.prot_readback = prot_readback
        self.clear_auto = False
        self.closed = False
        self.t0 = time.perf_counter()

    def write(self, cmd):
        self.writes.append(cmd)
        parts = cmd.split()
        key = parts[0].upper()
        if key == ":SOUR:VOLT":
            self.v = float(parts[1])
        elif key == ":SENS:CURR:PROT":
            self.icc = float(parts[1])
        elif key == ":OUTP":
            self.output_on = parts[1].upper() == "ON"
        elif key == ":SOUR:CLE:AUTO":
            self.clear_auto = parts[1].upper() == "ON"
        elif key == "*RST":
            self.v, self.icc, self.output_on, self.clear_auto = 0.0, 1.05e-4, False, False

    def _reading(self):
        i = self.v / self.r
        stat = (1 << 14) | (1 << 11) | (1 << 12) | (1 << 2)
        if abs(i) > self.icc:
            i = math.copysign(self.icc, i)
            v = i * self.r
            stat |= 1 << 3
        else:
            v = self.v
        if self.ovp_at is not None and abs(self.v) >= self.ovp_at:
            stat |= 1 << 4
        return v, i, stat

    def query(self, cmd):
        c = cmd.strip().upper()
        if c == "*IDN?":
            return "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234567,C30   Mar 17 2006 09:29:29/A02  /K/J\n"
        if c == ":READ?":
            idx = self.reads
            self.reads += 1
            if self.fail_forever or idx in self.fail_reads:
                raise IOError("VI_ERROR_TMO: timeout")
            v, i, stat = self._reading()
            return f"{v:+.6E},{i:+.6E},{time.perf_counter() - self.t0:+.3E},{stat:+.4E}\n"
        if c == ":SENS:CURR:PROT:TRIP?":
            _, _, stat = self._reading()
            return "1\n" if stat & (1 << 3) else "0\n"
        if c == ":SENS:CURR:PROT?":
            return f"{self.prot_readback if self.prot_readback is not None else self.icc:+.6E}\n"
        if c == ":OUTP?":
            return "1\n" if self.output_on else "0\n"
        raise AssertionError(f"unexpected query {cmd!r}")

    def close(self):
        self.closed = True


def make_link(mod, fake):
    link = mod.K2400Link("FAKE::INSTR", timeout_s=1.0, sleep=lambda s: None)

    def _open():
        link.inst = fake
    link.open = _open
    link.fake = fake
    return link


def base_params(folder, **extra):
    p = dict(sample_name="VCO01", notes="dummy load", electrode="Ag paste", area_cm2=None, thickness_mm=None,
             hi_face="face A", visa_address="FAKE::INSTR", protection_v=20.0, folder=folder, stem="VCO01")
    p["_sample_block"] = ("sample", [("name", "VCO01"), ("notes", "dummy load"), ("electrode", "Ag paste"),
                                     ("area_cm2", ""), ("thickness_mm", ""), ("HI_face", "face A")])
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


def read_file(path):
    header, columns, rows = analysis.parse_pica_csv(path)
    return header, columns, rows


def end_reason(header):
    for line in header:
        if line.startswith("# END"):
            return analysis.header_blocks([line])["end"]["reason"]
    return None


def comment_lines(path, prefix):
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.startswith("# " + prefix)]


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _gui_shell(cls, mod, values, folder):
    gui = object.__new__(cls)
    gui.vars = {k: _Var(v) for k, v in values.items()}
    gui.file_location_path = folder
    gui.notes_text = None
    gui.notes = lambda: values.get("_notes", "dummy")
    gui.companion_path = ""
    gui.warn_label = None
    return gui


# -- worker run helpers (one per measurement module) ---------------------

def sweep_params(folder, **extra):
    p = base_params(folder, shape=sweep.SHAPE_BIPOLAR_PLUS, step_v=1.0, vmax_plus=20.0, vmax_minus=20.0, loops=1,
                    ladder=[], dwell_s=0.0, i_cc_plus=1e-5, i_cc_minus=1e-5, n_confirm=3)
    p.update(extra)
    return p


def run_sweep(fake, folder, stop_after=None, **extra):
    p = sweep_params(folder, **extra)
    stop = threading.Event()
    q = queue.Queue()
    link = make_link(sweep, fake)
    worker = sweep.SweepWorker(p, link, stop, q, sleep=lambda s: None)
    worker.gui_version = "test"
    msgs = run_worker(worker, stop_after, stop, timeout=60)
    files = [f for f in os.listdir(folder) if "_sweep_" in f]
    assert len(files) == 1
    return worker, msgs, os.path.join(folder, files[0])


def retention_params(folder, **extra):
    p = base_params(folder, state="LRS", swap=False, polarity=1, vmax_write=5.0, i_cc_write=1e-5, step_v=1.0,
                    dwell_s=0.0, n_confirm=3, v_read=0.1, t_rec=0.15, t_soak=0.15, t_stop=1.2,
                    scheme=retention.SCHEME_CONSTANT, spacing=retention.SPACING_LOG, points_per_decade=10,
                    dt=0.2, n_avg=3, companion="")
    p.update(extra)
    return p


def run_retention(fake, folder, stop_after=None, **extra):
    p = retention_params(folder, **extra)
    stop = threading.Event()
    link = make_link(retention, fake)
    worker = retention.RetentionWorker(p, link, stop, queue.Queue(), sleep=lambda s: None)
    worker.gui_version = "test"
    msgs = run_worker(worker, stop_after, stop, timeout=60)
    files = sorted(os.listdir(folder))
    writes = [f for f in files if "_write_" in f]
    reads = [f for f in files if "_reads_" in f]
    assert len(writes) == 1 and len(reads) == 1
    return worker, msgs, os.path.join(folder, writes[0]), os.path.join(folder, reads[0])


def endurance_params(folder, **extra):
    p = base_params(folder, mode=endurance.MODE_SWEEP, v_set=5.0, v_reset=5.0, step_v=1.0, dwell_s=0.0,
                    i_cc_set=1e-5, i_cc_reset=1e-5, v_read=0.1, t_soak=0.02, n_cycles=5, max_time_s=3600,
                    raw_cycles_text="1-2, 5", raw_cycles={1, 2, 5}, n_confirm=3, lo_earthed=False,
                    pulse_set_v=5.0, pulse_reset_v=5.0, pulses_per_leg=1, pulse_width_s=None,
                    pulse_delay_s=0.001, pulse_nplc=0.01)
    p.update(extra)
    return p


def run_endurance(fake, folder, stop_after=None, **extra):
    p = endurance_params(folder, **extra)
    stop = threading.Event()
    link = make_link(endurance, fake)
    worker = endurance.EnduranceWorker(p, link, stop, queue.Queue(), sleep=lambda s: None)
    worker.gui_version = "test"
    msgs = run_worker(worker, stop_after, stop, timeout=60)
    files = sorted(os.listdir(folder))
    summ = [f for f in files if "_summary_" in f]
    raw = [f for f in files if "_raw_" in f]
    assert len(summ) == 1 and len(raw) == 1
    return worker, msgs, os.path.join(folder, summ[0]), os.path.join(folder, raw[0])


def run_module_tests(namespace):
    """Plain-python runner: python tests/test_memristor_<module>.py"""
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
