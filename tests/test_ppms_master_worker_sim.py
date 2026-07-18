"""
Purpose: End-to-end simulation of the PPMS Dielectric Master worker
state machine (PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py) with
mocked instruments — no Tk, no hardware, compressed timescale.

A scripted probe-temperature profile mimics one full protocol:
    310 K -> cooldown -> ~20 K (sit) -> warming Tscan -> 310 K hold ->
    cooldown -> ~20 K -> rise to the 25 K Fscan setpoint -> stable.
The REAL worker methods run against it and must:
    - confirm warming / cooling turnarounds (never on a blip),
    - execute WAIT_BASE -> TSCAN -> WAIT_BASE -> FSCAN in order,
    - write the Tscan per-frequency files, Fscan sweep file and all
      master logs to disk,
    - finish with a 'sequence_complete' message.

Runnable as plain python too:
    python tests/test_ppms_master_worker_sim.py
"""

import glob
import math
import os
import queue
import sys
import tempfile
import threading
import time
from collections import deque

import numpy as np

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI as m


# ------------------------------------------------------------------
# Fakes
# ------------------------------------------------------------------
class FakeThermometer:
    """Time-based fake PPMS probe: temperature is a piecewise-linear
    function of WALL TIME (compressed timescale), like the real world.
    This makes the simulation independent of how fast the worker polls
    — a per-read script would let a fast machine (or a slow CI runner)
    consume the trajectory at the wrong rate and starve later phases.
    All the worker's turnaround conditions are latched state (min/max
    history), so arbitrarily sparse polling still detects every
    transition."""

    def __init__(self, segments):
        # segments: [(duration_s, T_start, T_end), ...]; after the last
        # segment the final temperature holds forever (with a tiny
        # deterministic wobble so stability windows see realistic noise).
        self.segments = list(segments)
        self.t0 = None
        self.rm = None

    def connect(self, visa_address):
        return "FAKE,MODEL350,SIM,1.0"

    def reconnect(self):
        return self.connect(None)

    def get_temperature(self, channel, retries=2):
        now = time.time()
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        for dur, T_a, T_b in self.segments:
            if t <= dur:
                return T_a + (T_b - T_a) * (t / dur)
            t -= dur
        last_T = self.segments[-1][2]
        return last_T + 0.02 * math.sin(3.0 * t)

    def shutdown(self):
        pass


class FakeLCR:
    def __init__(self):
        self.rm = None
        self.params = {}
        self.n_measurements = 0

    def initialize_instrument(self, p):
        self.params = p

    def perform_measurement(self, freq, delay):
        self.n_measurements += 1
        return 100.0, -50.0, 0     # R, X, status

    def reconnect(self):
        pass

    def close_instrument(self):
        pass


def build_profile():
    """The synthetic PPMS run, compressed to ~25 s of wall time:
    (duration_s, T_start, T_end) segments. After the last segment the
    probe sits at 25 K (the Fscan setpoint) forever."""
    return [
        (4.0, 310.0, 20.0),    # cooldown 1
        (2.0, 20.0, 20.0),     # sit at base
        (10.0, 20.0, 310.0),   # warming (the Tscan run)
        (2.0, 310.0, 310.0),   # hold at top
        (4.0, 310.0, 20.0),    # final cooldown
        (1.0, 20.0, 20.0),     # sit at base
        (2.0, 20.0, 25.0),     # PPMS moves to the 25 K setpoint
    ]


def _plain_append(path, text):
    """fsync-free replacement for _durable_append: the sim writes
    thousands of rows and per-row fsync can exceed the test timeout on
    slow CI disks. Durability mechanics are covered by the dedicated
    write-buffer tests, not this simulation."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def make_worker(tmpdir):
    """Assemble a worker harness around the real class methods."""
    gui = object.__new__(m.PPMSMasterGUI)
    gui._durable_append = _plain_append   # instance attr beats staticmethod
    gui.thermo_backend = FakeThermometer(build_profile())
    gui.lcr_backend = FakeLCR()
    gui.cmd_queue = queue.Queue()
    gui.gui_queue = queue.Queue()
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    gui._worker_phase = None
    gui._phase_index = -1
    gui._pending_rows = deque()
    gui._write_error_logged = False
    gui._measured_point_s = None
    gui.save_dir = tmpdir
    gui.sweep_frequencies = np.array([100.0, 1000.0, 10000.0,
                                      100000.0, 1000000.0])
    gui.params = {
        "mode": "flat_band",
        "tol": 0.5,
        "window_min": 0.03,       # ~2 s rolling window (compressed)
        "drift": 60.0,            # generous on the compressed timescale
        "guard": 2.0,
        "stab_timeout": 2.0,      # min; proceeds anyway if missed
        "delay": 0.004,           # poll delay (s)
        "margin_min": 1.0,
        "thermo_visa": "FAKE::LS350",
        "channel": "A",
    }
    gui.lcr_params = {
        "sample_name": "SimSample",
        "ac_bias": 1.0, "dc_bias": 0.0, "delay": 0.0,
        "aper": "SHOR", "alc_enabled": True, "corr_enabled": False,
        "cable_len": "1", "lcr_visa": "FAKE::E4980A",
    }
    cfg = {
        "sample": "SimSample",
        "runs": [{"label": "Run1_0Oe", "field_oe": 0.0,
                  "cooldown_wait_s": 5.0}],
        "base_temp": 10.0,
        "top_temp": 310.0,
        "warm_rate": 1000.0,      # only used for the ETA display
        "cool_rate": 3.0,
        "top_hold_s": 0.0,
        "final_cooldown_s": 5.0,
        "field_rate": 50.0,
        "fscan_rate": 1.0,
        "base_arm": 30.0,
        "rise_k": 2.0,
        "top_arm_off": 10.0,
        "fall_k": 2.0,
        "confirm_s": 0.15,        # compressed 'definitely sure' hold
        "overdue_s": 3600.0,
        "tscan_freqs": [1000.0, 10000.0, 100000.0],
        "schedule": [25.0],
        "step_wait_s": 60.0,
    }
    gui.cfg = cfg
    gui.schedule = cfg["schedule"]
    gui.protocol_phases = m.build_protocol_phases(cfg)
    return gui


def drain(gui):
    msgs = []
    while True:
        try:
            msgs.append(gui.gui_queue.get_nowait())
        except queue.Empty:
            return msgs


def test_full_protocol_simulation():
    tmpdir = tempfile.mkdtemp(prefix="pica_master_sim_")
    gui = make_worker(tmpdir)

    kinds = [p["kind"] for p in gui.protocol_phases]
    assert kinds == ["WAIT_BASE", "TSCAN", "WAIT_BASE", "FSCAN"]

    t = threading.Thread(target=gui._hardware_worker_loop, daemon=True)
    t.start()
    t.join(timeout=300)   # generous: shared CI runners can be very slow
    if t.is_alive():
        gui.is_running = False
        t.join(timeout=10)
        raise AssertionError("worker did not finish the simulated "
                             "protocol within 300 s")

    msgs = drain(gui)
    types = [x["type"] for x in msgs]
    assert "sequence_complete" in types, "protocol did not complete"
    assert "worker_done" in types

    # Phase outcomes, in order, from the phase_status messages
    done = [x["status"] for x in msgs
            if x["type"] == "phase_status" and x["status"] != "ACTIVE"]
    assert len(done) == 4, f"expected 4 finished phases, got {done}"
    assert "warming confirmed" in done[0]
    assert "cooling confirmed" in done[1]
    assert "warming confirmed" in done[2]
    assert done[3].endswith("complete")

    # --- Files on disk ---
    # Master logs
    assert glob.glob(os.path.join(tmpdir, "*_Master_TempLog.csv"))
    proto_logs = glob.glob(os.path.join(tmpdir, "*_Master_ProtocolLog.csv"))
    assert proto_logs
    with open(proto_logs[0], encoding="utf-8") as fh:
        proto_rows = fh.read().strip().splitlines()
    assert len(proto_rows) == 1 + 4        # header + 4 phases

    # Tscan run folder: one file per frequency + run T-log
    run_dir = os.path.join(tmpdir, "SimSample_Run1_0Oe")
    assert os.path.isdir(run_dir)
    for f in (1000, 10000, 100000):
        path = os.path.join(run_dir, f"SimSample_Run1_0Oe-{f}Hz.txt")
        assert os.path.exists(path), f"missing Tscan file {path}"
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
        assert lines[0].startswith("Temperature\tQ\tD")
        assert len(lines) > 10, "Tscan file has too few data rows"
        # 19 columns: temperature + 18 derived parameters
        assert len(lines[1].split("\t")) == 19
    assert os.path.exists(os.path.join(
        run_dir, "SimSample_Run1_0Oe_T-log.txt"))

    # Fscan folder: one sweep file at 25 K with T_set as column 20
    fscan_dir = os.path.join(tmpdir, "SimSample_Fscan")
    assert os.path.isdir(fscan_dir)
    sweeps = glob.glob(os.path.join(fscan_dir, "*_FreqScan.txt"))
    assert len(sweeps) == 1
    with open(sweeps[0], encoding="utf-8") as fh:
        lines = fh.read().strip().splitlines()
    assert lines[1].startswith("Frequency\tQ\tD")
    data = [l for l in lines if not l.startswith(("#", "Frequency"))]
    assert len(data) == 5                  # 5 sweep frequencies
    cols = data[0].split("\t")
    assert len(cols) == 20
    assert abs(float(cols[-1]) - 25.0) < 1e-6   # T_set column

    # Timing log has the one Fscan step row
    timing = glob.glob(os.path.join(tmpdir, "*_Master_TimingLog.csv"))
    assert timing
    with open(timing[0], encoding="utf-8") as fh:
        rows = fh.read().strip().splitlines()
    assert len(rows) == 2                  # header + 1 step

    # AID-1: RUNSTATE crash aid ends the run saying COMPLETE
    runstates = glob.glob(os.path.join(tmpdir, "*_Master_RUNSTATE.txt"))
    assert runstates, "RUNSTATE file was not written"
    with open(runstates[0], encoding="utf-8") as fh:
        state = fh.read()
    assert "status: COMPLETE" in state, state
    assert "phase: 4/4" in state, state


# ------------------------------------------------------------------
# FALL-1: WAIT_BASE fallback ceiling (v1.2)
# ------------------------------------------------------------------
def make_wait_base_harness(tmpdir, fallback_x, segments,
                           expected_s, confirm_s=0.05):
    """Minimal harness around the REAL _phase_wait_base."""
    gui = object.__new__(m.PPMSMasterGUI)
    gui._durable_append = _plain_append
    gui.thermo_backend = FakeThermometer(segments)
    gui.cmd_queue = queue.Queue()
    gui.gui_queue = queue.Queue()
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    gui._worker_phase = None
    gui._phase_index = 0
    gui._pending_rows = deque()
    gui._write_error_logged = False
    gui.save_dir = tmpdir
    gui.start_time = time.time()
    gui.tlog_path = os.path.join(tmpdir, "TempLog.csv")
    gui.params = {"delay": 0.01, "channel": "A", "tol": 0.5,
                  "window_min": 0.05, "drift": 0.05, "guard": 2.0}
    gui.cfg = {"base_arm": 30.0, "rise_k": 2.0, "confirm_s": confirm_s,
               "overdue_s": 3600.0, "fallback_x": fallback_x}
    phase = {"kind": "WAIT_BASE", "label": "Cooldown 1",
             "detail": "test", "expected_s": expected_s, "run": None}
    gui.protocol_phases = [phase]
    return gui, phase


def test_wait_base_fallback_ceiling_fires():
    """Probe never dips below Base arm (detection can never fire): the
    fallback ceiling must end the phase at fallback_x * expected."""
    tmpdir = tempfile.mkdtemp(prefix="pica_fallback_")
    gui, ph = make_wait_base_harness(
        tmpdir, fallback_x=2.0, segments=[(9999.0, 300.0, 300.0)],
        expected_s=0.4)
    t0 = time.time()
    outcome = gui._phase_wait_base(0, ph)
    took = time.time() - t0
    assert outcome == "fallback (time ceiling)", outcome
    assert took >= 0.8, f"fired before the ceiling ({took:.2f} s)"
    logs = [x.get("text", "") for x in drain(gui) if x["type"] == "log"]
    assert any("FALLBACK" in t for t in logs), logs


def test_wait_base_fallback_off_never_fires():
    """fallback_x = 0 disables the ceiling: only detection or Stop can
    end the phase (unattended never-abort policy unchanged)."""
    tmpdir = tempfile.mkdtemp(prefix="pica_fallback_")
    gui, ph = make_wait_base_harness(
        tmpdir, fallback_x=0.0, segments=[(9999.0, 300.0, 300.0)],
        expected_s=0.2)
    threading.Timer(1.2, lambda: setattr(gui, "is_running", False)).start()
    outcome = gui._phase_wait_base(0, ph)
    assert outcome == "stopped", outcome   # waited well past 2x expected


def test_wait_base_detection_wins_over_fallback():
    """Warming detection stays primary: with a generous ceiling armed,
    a real base-dip-then-rise must still confirm warming."""
    tmpdir = tempfile.mkdtemp(prefix="pica_fallback_")
    gui, ph = make_wait_base_harness(
        tmpdir, fallback_x=2.0,
        segments=[(0.3, 25.0, 20.0), (3.0, 20.0, 60.0)],
        expected_s=100.0)
    outcome = gui._phase_wait_base(0, ph)
    assert outcome == "warming confirmed", outcome


# ------------------------------------------------------------------
# SKIP-1: Skip Freq Step (v1.2)
# ------------------------------------------------------------------
def test_skip_freq_only_arms_inside_fscan_step():
    """The skip_freq command must be ignored outside WAIT_STABLE/SCAN —
    a stale flag would silently skip the FIRST Fscan step hours later."""
    tmpdir = tempfile.mkdtemp(prefix="pica_skipfreq_")
    gui, _ = make_wait_base_harness(
        tmpdir, 0.0, [(9999.0, 300.0, 300.0)], 1.0)

    gui._worker_phase = "WAIT_BASE"          # a cooldown, not an Fscan step
    gui.cmd_queue.put(("skip_freq",))
    assert gui._process_cmd_queue() is False
    assert gui._skip_step_requested is False
    logs = [x.get("text", "") for x in drain(gui)]
    assert any("ignored" in t for t in logs), logs

    gui._worker_phase = "WAIT_STABLE"        # inside an Fscan step
    gui.cmd_queue.put(("skip_freq",))
    gui._process_cmd_queue()
    assert gui._skip_step_requested is True


def test_skip_freq_abandons_stability_wait():
    """During the stability wait, Skip Freq Step returns 'step_skipped'
    so the Fscan loop moves to the NEXT setpoint without sweeping."""
    tmpdir = tempfile.mkdtemp(prefix="pica_skipfreq_")
    gui, _ = make_wait_base_harness(
        tmpdir, 0.0, [(9999.0, 25.0, 25.0)], 1.0)
    gui._skip_step_requested = True
    outcome, wait_s = gui._wait_for_stability(25.0)
    assert outcome == "step_skipped", outcome
    assert gui._skip_step_requested is False   # flag consumed


if __name__ == "__main__":
    for _name in ("test_full_protocol_simulation",
                  "test_wait_base_fallback_ceiling_fires",
                  "test_wait_base_fallback_off_never_fires",
                  "test_wait_base_detection_wins_over_fallback",
                  "test_skip_freq_only_arms_inside_fscan_step",
                  "test_skip_freq_abandons_stability_wait"):
        globals()[_name]()
        print(f"PASS  {_name}")
