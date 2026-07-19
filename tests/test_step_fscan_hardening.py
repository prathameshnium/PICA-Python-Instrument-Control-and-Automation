"""
Purpose: Verify the v1.7 unattended-run hardening of
Step_Frequency_Scan_E4980A_GUI.py (HARD-1..4, the Passive v1.4
pattern) without Tk or hardware — plumbing presence plus source-level
policy checks that would catch a regression re-introducing the modal
completion dialog or dropping fsync/reconnect.

Runnable as plain python too:
    python tests/test_step_fscan_hardening.py
"""

import inspect
import os
import sys

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import Step_Frequency_Scan_E4980A_GUI as m


def test_hardening_plumbing_present():
    # HARD-1: keep-awake; HARD-2: reconnect + retry loop
    assert callable(m.CombinedGUI._set_keep_awake)
    assert callable(m.CombinedGUI._comm_recover)
    assert callable(m.CombinedGUI._get_status_hardened)
    assert callable(m.Lakeshore_Backend.reconnect)
    assert callable(m.LCR_Backend.reconnect)
    assert float(m.CombinedGUI.PROGRAM_VERSION.split("-")[0]) >= 1.7


def test_beep_supports_alarm_counts():
    # HARD-4: multi-beep helper (Passive v1.4 pattern)
    sig = inspect.signature(m.CombinedGUI._beep)
    assert "times" in sig.parameters
    assert sig.parameters["times"].default == 1


def test_no_modal_dialog_on_completion():
    """HARD-4: the gui_queue pump must never open a messagebox — the
    old 'Sequence Complete' showinfo would linger over an unattended
    bench until someone clicked it."""
    src = inspect.getsource(m.CombinedGUI._process_gui_queue)
    assert "messagebox" not in src
    assert "_beep" in src            # completion is announced audibly


def test_worker_paths_fsync():
    """HARD-3: every run-time data write forces the row to disk."""
    for fn in (m.CombinedGUI._log_temperature_point,
               m.CombinedGUI._run_frequency_sweep,
               m.CombinedGUI._write_summary_row):
        assert "fsync" in inspect.getsource(fn), fn.__name__


def test_sweep_retries_after_comm_recovery():
    """HARD-2: a measurement comm error must recover + retry the same
    frequency point, never silently abandon the rest of the sweep."""
    src = inspect.getsource(m.CombinedGUI._run_frequency_sweep)
    assert "_comm_recover" in src
    # while-loop with explicit index so `continue` retries the point
    assert "idx" in src and "while idx" in src


def test_lakeshore_reconnect_is_session_only():
    """HARD-2 safety: reconnect must never re-send heater/ramp/setpoint
    commands — the 350 keeps executing its program while the link is
    down, and recovery must not disturb it."""
    src = inspect.getsource(m.Lakeshore_Backend.reconnect)
    for forbidden in ("RANGE", "RAMP", "SETP", "set_heater_range",
                      "configure_ramp", "set_setpoint"):
        assert forbidden not in src, forbidden


def test_worker_start_engages_keep_awake():
    src = inspect.getsource(m.CombinedGUI.start_sequence)
    assert "_set_keep_awake(True)" in src
    src = inspect.getsource(m.CombinedGUI._process_gui_queue)
    assert "_set_keep_awake(False)" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
    print("\nALL TESTS PASSED")
