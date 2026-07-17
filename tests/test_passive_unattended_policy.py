"""
Purpose: Verify the v1.4 unattended policy of
Temprature_Scan_Passive_E4980A_GUI.py — the safety-kill and
runtime-error handlers must NEVER open a modal dialog (runs execute
overnight with nobody at the PC; a messagebox would linger for days),
only log + beep, and must still stop the run.

Runnable as plain python too:
    python tests/test_passive_unattended_policy.py
"""

import inspect
import os
import sys
from unittest.mock import patch

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import Temprature_Scan_Passive_E4980A_GUI as m


class Harness:
    """Minimal stand-in carrying only what the two handlers touch."""
    _handle_kill_event = m.Integrated_CT_GUI._handle_kill_event
    _handle_runtime_error = m.Integrated_CT_GUI._handle_runtime_error

    def __init__(self):
        self.logs = []
        self.beeps = []
        self.stops = []

        class B:
            SAFETY_KILL_TEMP_K = 400.0
        self.backend = B()

    def log(self, text):
        self.logs.append(text)

    def _beep(self, times=1):
        self.beeps.append(times)

    def _update_live_plots(self, force=False):
        pass

    def stop_measurement(self, from_user=True):
        self.stops.append(from_user)


def test_kill_event_no_modal_stops_and_beeps():
    h = Harness()
    with patch.object(m, "messagebox") as mb:
        h._handle_kill_event()
    assert not mb.mock_calls, "kill handler must not open any dialog"
    assert h.stops == [False], "run must be stopped (not as user-stop)"
    assert h.beeps, "audible alert expected"
    assert any("SAFETY KILL" in t for t in h.logs), h.logs


def test_runtime_error_no_modal_stops_and_beeps():
    h = Harness()
    with patch.object(m, "messagebox") as mb:
        h._handle_runtime_error(RuntimeError("boom"))
    assert not mb.mock_calls, "error handler must not open any dialog"
    assert h.stops == [False]
    assert h.beeps
    assert any("boom" in t for t in h.logs), h.logs


def test_handlers_source_has_no_messagebox():
    for fn in (m.Integrated_CT_GUI._handle_kill_event,
               m.Integrated_CT_GUI._handle_runtime_error):
        assert "messagebox" not in inspect.getsource(fn), fn.__name__


def test_beep_helper_and_version():
    assert callable(m.Integrated_CT_GUI._beep)
    assert hasattr(m, "HAS_WINSOUND")   # optional winsound import block
    assert m.Integrated_CT_GUI.PROGRAM_VERSION.startswith("1.4")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
    print("\nALL TESTS PASSED")
