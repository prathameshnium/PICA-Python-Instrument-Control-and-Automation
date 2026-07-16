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
    assert m.PPMSSyncGUI.PROGRAM_VERSION.startswith("1.3")
    # Keep-awake flags match SetThreadExecutionState documentation
    assert m.PPMSSyncGUI.ES_CONTINUOUS == 0x80000000
    assert m.PPMSSyncGUI.ES_SYSTEM_REQUIRED == 0x00000001


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
    print("\nALL TESTS PASSED")
