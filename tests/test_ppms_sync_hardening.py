"""
Purpose: Verify the v1.3 unattended-run hardening of
PPMS_Sync_Freq_Scan_E4980A_GUI.py (HARD-1..3).

Covers, using the real class methods on a minimal harness (no Tk, no
hardware):
  - HARD-1: durable open/append/fsync writes; disk failure buffers rows
    (single error log, nothing raised) and recovery flushes them in order.
  - HARD-2: reconnect() exists on both backends; _reconnect_with_backoff
    escalates 5 -> 10 -> 30 -> 60 s, retries until success, and aborts
    only when Stop is requested.
  - HARD-3: keep-awake plumbing present.

Runnable as plain python too:  python tests/test_ppms_sync_hardening.py
"""

import os
import shutil
import sys
import tempfile
from collections import deque
from unittest.mock import patch

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import PPMS_Sync_Freq_Scan_E4980A_GUI as m


class WriteHarness:
    """Minimal stand-in carrying only the state the HARD-1 helpers touch."""
    _csv_line = staticmethod(m.PPMSSyncGUI._csv_line)
    _durable_append = staticmethod(m.PPMSSyncGUI._durable_append)
    _write_or_buffer = m.PPMSSyncGUI._write_or_buffer
    _flush_pending_rows = m.PPMSSyncGUI._flush_pending_rows

    def __init__(self):
        self._pending_rows = deque()
        self._write_error_logged = False
        self.msgs = []

    def _put_gui_msg(self, msg_type, **kw):
        self.msgs.append(kw.get("text", msg_type))


class BackoffHarness:
    """Minimal stand-in for the HARD-2 reconnect loop."""
    CLR_ACCENT_RED = "#BA6B5E"
    _reconnect_with_backoff = m.PPMSSyncGUI._reconnect_with_backoff

    def __init__(self, stop_after_polls=None):
        self.is_running = True
        self.msgs = []
        self._polls = 0
        self._stop_after_polls = stop_after_polls

    def _put_gui_msg(self, msg_type, **kw):
        self.msgs.append(kw.get("text", msg_type))

    def _process_cmd_queue(self):
        self._polls += 1
        if (self._stop_after_polls is not None
                and self._polls >= self._stop_after_polls):
            self.is_running = False
            return True
        return False


class FakeClock:
    """time.time/time.sleep replacement so backoff tests run instantly."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def time(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_csv_line_and_durable_write():
    work = tempfile.mkdtemp(prefix="pica_hard_")
    try:
        h = WriteHarness()
        path = os.path.join(work, "TempLog.csv")
        h._write_or_buffer(path, h._csv_line(["Timestamp", "T_K", 1]))
        h._write_or_buffer(path, h._csv_line(["2026-07-16 12:00:00",
                                              300.1234, 0]))
        with open(path) as fh:
            lines = fh.read().splitlines()
        assert lines == ["Timestamp,T_K,1",
                         "2026-07-16 12:00:00,300.1234,0"], lines
        assert not h._pending_rows
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_write_failure_buffers_then_recovery_flushes_in_order():
    work = tempfile.mkdtemp(prefix="pica_hard_")
    try:
        h = WriteHarness()
        good = os.path.join(work, "TempLog.csv")
        baddir = os.path.join(work, "vanished")
        bad = os.path.join(baddir, "Scan.txt")

        # Failing path: rows buffer, error logged ONCE, nothing raised
        h._write_or_buffer(bad, "row1\n")
        h._write_or_buffer(bad, "row2\n")
        assert len(h._pending_rows) == 2
        errs = [t for t in h.msgs if "WRITE ERROR" in t]
        assert len(errs) == 1, h.msgs

        # While rows pend, even good-path rows join the buffer (ordering)
        h._write_or_buffer(good, "late\n")
        assert len(h._pending_rows) == 3

        # Disk 'recovers' -> next write flushes everything in order
        os.makedirs(baddir)
        h._write_or_buffer(bad, "row3\n")
        assert not h._pending_rows
        with open(bad) as fh:
            assert fh.read().splitlines() == ["row1", "row2", "row3"]
        with open(good) as fh:
            assert fh.read().splitlines() == ["late"]
        assert any("recovered" in t for t in h.msgs), h.msgs
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_reconnect_backoff_escalates_and_returns_true_on_success():
    h = BackoffHarness()
    calls = []

    def flaky_reconnect():
        calls.append(1)
        if len(calls) < 3:          # fail twice, succeed on the third try
            raise ConnectionError("bus still down")

    clock = FakeClock()
    with patch("time.time", clock.time), patch("time.sleep", clock.sleep):
        ok = h._reconnect_with_backoff("test-instr", flaky_reconnect, 1)
    assert ok is True
    assert len(calls) == 3
    # Waits escalate 5 (attempt 1) -> 10 (attempt 2) -> 30 (attempt 3)
    assert clock.t == 5 + 10 + 30, clock.t
    assert any("reconnected" in t for t in h.msgs), h.msgs


def test_reconnect_backoff_aborts_on_stop():
    h = BackoffHarness(stop_after_polls=2)  # Stop pressed during backoff

    def never_called():
        raise AssertionError("must not reconnect after Stop")

    clock = FakeClock()
    with patch("time.time", clock.time), patch("time.sleep", clock.sleep):
        ok = h._reconnect_with_backoff("test-instr", never_called, 1)
    assert ok is False
    assert h.is_running is False


def test_hardening_plumbing_present():
    assert callable(m.Probe_Thermometer_Backend.reconnect)
    assert callable(m.LCR_Backend.reconnect)
    assert callable(m.PPMSSyncGUI._set_keep_awake)
    assert m.PPMSSyncGUI.PROGRAM_VERSION.startswith("1.6")
    # Keep-awake flags match SetThreadExecutionState documentation
    assert m.PPMSSyncGUI.ES_CONTINUOUS == 0x80000000
    assert m.PPMSSyncGUI.ES_SYSTEM_REQUIRED == 0x00000001


# ------------------------------------------------------------------
# v1.4 backports from the Master module
# ------------------------------------------------------------------
def test_v14_no_modal_dialog_in_queue_pump():
    """UNAT-1: a messagebox inside _process_gui_queue would block all
    further queue processing until someone clicks OK — and unattended
    runs have nobody in the lab."""
    import inspect
    src = inspect.getsource(m.PPMSSyncGUI._process_gui_queue)
    assert "messagebox" not in src


def test_v14_render_fscan_seq_exact_and_valid():
    text = m.render_fscan_seq(
        "S", [(25.0, 1.0, 3600.0), (30.0, 2.0, 1800.0)], 1,
        initial_note="note")
    assert "TMP TEMP 25.000000 1.000000 1" in text
    assert "WAI WAITFOR 3600 1 0 0 0 0" in text
    assert "TMP TEMP 30.000000 2.000000 1" in text
    assert "WAI WAITFOR 1800 1 0 0 0 0" in text
    assert "REM note" in text
    assert m.validate_ppms_seq(text) == []
    # Fast-settle toggle flips the mode digit
    text0 = m.render_fscan_seq("S", [(25.0, 1.0, 60.0)], 0)
    assert "TMP TEMP 25.000000 1.000000 0" in text0


def test_v14_validator_catches_faults():
    assert m.validate_ppms_seq("TMP TEMP 500.000000 3.000000 0\n")  # >400 K
    assert m.validate_ppms_seq("TMP TEMP 10.000000 50.000000 0\n")  # >20 K/min
    assert m.validate_ppms_seq("BOGUS 1 2 3\n")                     # unknown
    assert m.validate_ppms_seq("TMP TEMP 25.5 1 1\n") == []         # loose fmt
    assert m.validate_ppms_seq("REM x\n!TMP TEMP 10 3 0\nMES hi\n") == []
    # The real reference sequence must validate clean when present
    # (gitignored lab data — quietly nothing to check on CI).
    ref = os.path.join(project_root, "pica", "PPMS", "data_file_for_ref",
                       "Dielectric_Tscan.seq")
    if os.path.exists(ref):
        with open(ref, encoding="utf-8") as fh:
            assert m.validate_ppms_seq(fh.read()) == []


def test_v14_smart_sleep_detector_and_flat_rate():
    # SMART-1 plumbing: the copied detector behaves
    d = m.TurnaroundDetector()
    for T in (35.0, 25.0, 21.0, 20.3, 20.1, 20.1, 20.2):
        d.update(T)
    assert not d.warming_started(30.0, 2.0)
    for T in (20.6, 21.2, 21.9, 22.6, 23.3, 24.0, 24.7):
        d.update(T)
    assert d.warming_started(30.0, 2.0)
    c = m.SustainedCondition(180.0)
    assert not c.update(True, 0.0)
    assert not c.update(False, 100.0)      # break resets the clock
    assert not c.update(True, 120.0)
    assert c.update(True, 301.0)

    # RATE-1: flat 1 K/min default (reference sequences), no max_rate set
    class H:
        _seq_default_rate = m.PPMSSyncGUI._seq_default_rate
        entries = {}
    h = H()
    assert h._seq_default_rate(25.0, None) is None
    assert h._seq_default_rate(25.0, 310.0) == 1.0
    assert h._seq_default_rate(250.0, 200.0) == 1.0


# ------------------------------------------------------------------
# v1.5 — double-start race fix + ASCII-safe .seq
# ------------------------------------------------------------------
def test_v15_start_sequence_reentry_guard():
    """HARD-5: with a run active, Start must be a no-op. The harness has
    NO widgets — without the guard the method would crash on the first
    widget access instead of returning quietly."""
    gui = object.__new__(m.PPMSSyncGUI)
    gui.is_running = True
    assert gui.start_sequence() is None   # returns before touching any UI


def test_v15_sequence_complete_does_not_reenable_ui():
    """HARD-5: only worker_done may call set_ui_state(False). The worker
    is still closing instruments when sequence_complete arrives; an
    early re-enable lets a click launch a second worker on the same
    VISA sessions."""
    import inspect
    src = inspect.getsource(m.PPMSSyncGUI._process_gui_queue)
    seq_i = src.index('"sequence_complete"')
    done_i = src.index('"worker_done"')
    assert "set_ui_state" not in src[seq_i:done_i], \
        "sequence_complete branch must not re-enable the UI"
    assert "set_ui_state" in src[done_i:]


def test_v15_validator_rejects_non_ascii():
    """SEQ-3: DynaCool reads .seq as ANSI — a UTF-8 em-dash renders as
    mojibake, so the validator must reject ANY non-ASCII character,
    comments included."""
    errs = m.validate_ppms_seq("REM note with an em—dash\n")
    assert errs and "non-ASCII" in errs[0]
    assert m.validate_ppms_seq("TMP TEMP 25.000000 1.000000 1 °\n")
    assert m.validate_ppms_seq("REM plain ascii note\n") == []


def test_v15_export_note_is_ascii_and_valid():
    """The initial-wait REM note must survive the ASCII validator (the
    old wording used an em-dash)."""
    note = ("Initial wait 3:30 is a PC-side sleep in the measurement "
            "program. Each WAITFOR below starts only after the PPMS "
            "reports stable.")
    text = m.render_fscan_seq("S", [(25.0, 1.0, 3600.0)], 1,
                              initial_note=note)
    assert text.isascii()
    assert m.validate_ppms_seq(text) == []


# ------------------------------------------------------------------
# v1.6 — TOL-1: temperature-dependent tolerance (low-T probe offset)
# ------------------------------------------------------------------
def test_tol_table_parse_and_validation():
    t = m.parse_tol_table("40:1.2, 30:1.5; 100:0.4")
    assert t == [(30.0, 1.5), (40.0, 1.2), (100.0, 0.4)]   # sorted
    for bad in ("", "30", "30:0", "30:-1", "30:1.5, 30:1.2", "abc:1"):
        try:
            m.parse_tol_table(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} must raise")


def test_tol_from_table_interp_hold_extrapolate():
    table = m.parse_tol_table(
        m.TOL_TABLE_PRESETS["Safe (recommended)"])
    f = m.tol_from_table
    assert f(None, 30.0, 0.5) == 0.5                 # table off
    assert f(table, 30.0, 0.4) == 1.5                # exact entry
    assert abs(f(table, 35.0, 0.4) - 1.35) < 1e-9    # linear between
    assert f(table, 300.0, 0.4) == 0.4               # hold above top
    assert f(table, 200.0, 0.5) == 0.5               # max(base, table)
    # Below the lowest entry: extrapolate the 30->40 slope upward
    v20 = f(table, 20.0, 0.4)
    assert abs(v20 - 1.8) < 1e-9, v20                # 1.5 + 0.03*10
    assert f(table, -500.0, 0.4) == m.TOL_EXTRAP_CAP_K   # capped


def test_window_check_low_t_offset_scenario():
    """The measured lab case: PPMS at 30 K, probe steady at ~31.15 K.
    flat_band with the fixed 0.5 K tolerance fails the band; with the
    Safe table it passes — and the drift guard still applies."""
    import time as _t
    now = _t.time()
    window = [(now + i, 31.15 + 0.03 * ((i % 3) - 1)) for i in range(6)]
    p = {"mode": "flat_band", "tol": 0.5, "window_min": 0.05,
         "drift": 0.5, "guard": 2.0, "tol_table": None}
    ok, metrics = m.PPMSSyncGUI._window_check(None, window, 30.0, p)
    assert not ok and metrics["max_dev"] > 1.0       # fixed tol: fails
    p["tol_table"] = m.parse_tol_table(
        m.TOL_TABLE_PRESETS["Safe (recommended)"])
    ok, _ = m.PPMSSyncGUI._window_check(None, window, 30.0, p)
    assert ok                                        # table: passes
    # Same offset at 300 K must still FAIL — the table narrows back
    ok, _ = m.PPMSSyncGUI._window_check(
        None, [(now + i, 301.15) for i in range(6)], 300.0, p)
    assert not ok


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
    print("\nALL TESTS PASSED")
