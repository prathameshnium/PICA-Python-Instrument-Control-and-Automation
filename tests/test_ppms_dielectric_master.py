"""
Purpose: Verify the pure protocol logic of
PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py (no Tk, no hardware):

  - TurnaroundDetector: warming/cooling turnaround detection with the
    median-of-5 glitch filter (a single spiked reading must not fake or
    poison a transition).
  - SustainedCondition: a turnaround must HOLD continuously for the
    confirmation time before the phase actually switches.
  - build_protocol_phases: phase order and per-run cooldown waits.
  - generate_ppms_seq: exact MultiVu grammar of the reference sequences
    (TMP TEMP / FLD FIELD / WAI WAITFOR), FLD only on field runs,
    reset at 20.0 Oe/s approach 2, Fscan steps with no-overshoot mode.
  - End-to-end synthetic temperature trace: cooldown -> warming ->
    top hold -> cooling transitions fire exactly once each, in order.

Runnable as plain python too:
    python tests/test_ppms_dielectric_master.py
"""

import os
import re
import sys

import pytest

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI as m


# ------------------------------------------------------------------
# Shared fixture config (mirrors the reference Dielectric_Tscan.seq)
# ------------------------------------------------------------------
def make_cfg(runs=True, schedule=True):
    cfg = {
        "sample": "TestSample",
        "runs": [],
        "base_temp": 10.0,
        "top_temp": 310.0,
        "warm_rate": 1.0,
        "cool_rate": 3.0,
        "top_hold_s": 1800.0,
        "final_cooldown_s": 10800.0,
        "field_rate": 50.0,
        "fscan_rate": 1.0,
        "base_arm": 30.0,
        "rise_k": 2.0,
        "top_arm_off": 10.0,
        "fall_k": 2.0,
        "confirm_s": 180.0,
        "overdue_s": 1200.0,
        "tscan_freqs": [1000.0, 10000.0, 100000.0],
        "schedule": [],
        "step_wait_s": 3600.0,
        "tscan_approach": 0,     # fast settle, as Dielectric_Tscan.seq
        "fscan_approach": 1,     # no overshoot, as Dielectric_Fscan.seq
    }
    if runs:
        cfg["runs"] = [
            {"label": "Run1_0Oe", "field_oe": 0.0,
             "cooldown_wait_s": 10800.0},
            {"label": "Run2_5000Oe", "field_oe": 5000.0,
             "cooldown_wait_s": 14400.0},
        ]
    if schedule:
        cfg["schedule"] = [25.0, 30.0, 50.0]
    return cfg


# ------------------------------------------------------------------
# TurnaroundDetector
# ------------------------------------------------------------------
def test_detector_warming_needs_arming():
    d = m.TurnaroundDetector()
    # Never went below the arm threshold: rising must NOT count.
    for T in (300.0, 301.0, 303.0, 305.0):
        d.update(T)
    assert not d.warming_started(30.0, 2.0)


def test_detector_warming_after_base():
    d = m.TurnaroundDetector()
    for T in (35.0, 28.0, 24.0, 21.0, 20.5, 20.2, 20.1, 20.1):
        d.update(T)
    assert not d.warming_started(30.0, 2.0)   # still at the bottom
    # The median-of-5 filter lags ~2 samples, so the FILTERED value must
    # clear the 2 K rise — a real ramp at 2 s polling does this within
    # seconds.
    for T in (20.4, 20.9, 21.3, 21.8, 22.4, 23.0, 23.6, 24.2):
        d.update(T)
    assert d.warming_started(30.0, 2.0)       # rose >= 2 K off the min


def test_detector_single_spike_is_filtered():
    d = m.TurnaroundDetector()
    for T in (25.0, 22.0, 21.0, 20.5, 20.2):
        d.update(T)
    d.update(80.0)   # one glitched reading
    # Median-of-5 swallows the spike: no fake warming.
    assert not d.warming_started(30.0, 2.0)
    # And the spike must not have poisoned max_T enough to fake
    # a cooling event either (max tracks the MEDIAN, not the raw spike).
    assert d.max_T < 30.0


def test_detector_cooling_after_top():
    d = m.TurnaroundDetector()
    for T in (295.0, 300.0, 305.0, 308.0, 309.0, 309.5, 309.5, 309.4):
        d.update(T)
    assert not d.cooling_started(300.0, 2.0)  # holding at top
    for T in (309.0, 308.0, 307.5, 307.2, 307.0, 306.8):
        d.update(T)
    assert d.cooling_started(300.0, 2.0)      # fell >= 2 K off the max


def test_detector_cooling_not_armed_below_top():
    d = m.TurnaroundDetector()
    # A dip during the warming ramp far below the top must not end a run.
    for T in (100.0, 120.0, 150.0, 148.0, 145.0, 140.0, 130.0):
        d.update(T)
    assert not d.cooling_started(300.0, 2.0)


# ------------------------------------------------------------------
# SustainedCondition (the "definitely sure" clock)
# ------------------------------------------------------------------
def test_sustained_condition_holds_and_resets():
    c = m.SustainedCondition(180.0)
    t = 1000.0
    assert not c.update(True, t)             # just started holding
    assert not c.update(True, t + 100)       # 100 s < 180 s
    assert not c.update(False, t + 150)      # broke -> reset
    assert not c.update(True, t + 200)       # restart the clock
    assert not c.update(True, t + 350)       # 150 s since restart
    assert c.update(True, t + 380 + 1)       # >= 180 s held: confirmed
    assert c.pending_s(t + 381) >= 180.0


def test_sustained_condition_zero_hold_is_immediate():
    c = m.SustainedCondition(0.0)
    assert c.update(True, 5.0)


# ------------------------------------------------------------------
# build_protocol_phases
# ------------------------------------------------------------------
def test_phase_order_full_protocol():
    phases = m.build_protocol_phases(make_cfg())
    kinds = [p["kind"] for p in phases]
    assert kinds == ["WAIT_BASE", "TSCAN", "WAIT_BASE", "TSCAN",
                     "WAIT_BASE", "FSCAN"]
    # Per-run cooldown waits (3 h then 4 h), final cooldown 3 h.
    assert phases[0]["expected_s"] == 10800.0
    assert phases[2]["expected_s"] == 14400.0
    assert phases[4]["expected_s"] == 10800.0
    # Tscan expected: 300 K at 1 K/min + 30 min hold.
    assert abs(phases[1]["expected_s"] - (300 * 60 + 1800)) < 1e-6
    # Fscan expected: 3 setpoints x step wait.
    assert abs(phases[5]["expected_s"] - 3 * 3600.0) < 1e-6


def test_phase_order_without_fscan():
    phases = m.build_protocol_phases(make_cfg(schedule=False))
    kinds = [p["kind"] for p in phases]
    assert kinds == ["WAIT_BASE", "TSCAN", "WAIT_BASE", "TSCAN"]


def test_phase_order_fscan_only():
    phases = m.build_protocol_phases(make_cfg(runs=False))
    kinds = [p["kind"] for p in phases]
    assert kinds == ["WAIT_BASE", "FSCAN"]


def test_make_run_label():
    assert m.make_run_label(1, 0.0) == "Run1_0Oe"
    assert m.make_run_label(2, 5000) == "Run2_5000Oe"
    assert m.make_run_label(3, 2500.5) == "Run3_2500.5Oe"


def test_cooldown_only_run():
    """COOL-1: a kind='cool' run gives exactly one WAIT_BASE phase (no
    TSCAN) and exactly a TMP-to-base + timed WAITFOR pair in the .seq —
    no FLD, no warming, no top hold."""
    cfg = make_cfg(schedule=False)
    cfg["runs"].append({"label": "Cooldown3_only", "field_oe": 0.0,
                        "cooldown_wait_s": 7200.0, "kind": "cool"})
    phases = m.build_protocol_phases(cfg)
    kinds = [p["kind"] for p in phases]
    assert kinds == ["WAIT_BASE", "TSCAN", "WAIT_BASE", "TSCAN",
                     "WAIT_BASE"]
    assert phases[-1]["expected_s"] == 7200.0
    assert "cooldown only" in phases[-1]["detail"]
    assert phases[-1]["run"]["kind"] == "cool"

    text = m.generate_ppms_seq(cfg)
    assert m.validate_ppms_seq(text) == []
    lines = text.strip().splitlines()
    i3 = next(i for i, l in enumerate(lines) if "Run 3" in l)
    body_after = [l for l in lines[i3:] if not l.startswith("REM")]
    assert body_after == ["TMP TEMP 10.000000 3.000000 0",
                         "WAI WAITFOR 7200 0 0 0 0 0"]
    # Runs without 'kind' (older saved rows) still behave as full runs.
    assert "TMP TEMP 310.000000 1.000000 0" in lines


def test_final_cooldown_by_default_without_fscan():
    """COOL-2: with final_cd_no_fscan set, a Tscan-only protocol still
    ENDS with a cooldown to base (phase + .seq lines); without the flag
    (older configs) nothing changes."""
    cfg = make_cfg(schedule=False)
    cfg["final_cd_no_fscan"] = True
    phases = m.build_protocol_phases(cfg)
    kinds = [p["kind"] for p in phases]
    assert kinds == ["WAIT_BASE", "TSCAN", "WAIT_BASE", "TSCAN",
                     "WAIT_BASE"]
    last = phases[-1]
    assert last["run"]["kind"] == "cool"          # timed end + loud tell
    assert last["expected_s"] == cfg["final_cooldown_s"]

    text = m.generate_ppms_seq(cfg)
    assert m.validate_ppms_seq(text) == []
    assert "Final cooldown (after last run)" in text
    body = [l for l in text.strip().splitlines() if not l.startswith("REM")]
    assert body[-2:] == ["TMP TEMP 10.000000 3.000000 0",
                         "WAI WAITFOR 10800 0 0 0 0 0"]

    # Flag absent -> exactly the old behavior (no trailing cooldown).
    old = make_cfg(schedule=False)
    assert "Final cooldown" not in m.generate_ppms_seq(old)
    assert [p["kind"] for p in m.build_protocol_phases(old)] == \
        ["WAIT_BASE", "TSCAN", "WAIT_BASE", "TSCAN"]
    # With a schedule, the pre-Fscan final cooldown stays single (the
    # COOL-2 block must not double it).
    full = make_cfg()
    full["final_cd_no_fscan"] = True
    lines = m.generate_ppms_seq(full).splitlines()
    assert sum("Final cooldown" in l for l in lines) == 1


# ------------------------------------------------------------------
# generate_ppms_seq — must match the reference MultiVu grammar
# ------------------------------------------------------------------
TMP_RE = re.compile(r"^TMP TEMP \d+\.\d{6} \d+\.\d{6} [01]$")
FLD_RE = re.compile(r"^FLD FIELD \d+(\.\d+)? \d+(\.\d+)? [02] 0$")
WAI_RE = re.compile(r"^WAI WAITFOR \d+ [01] [01] 0 0 0$")


def test_seq_grammar_every_line_valid():
    text = m.generate_ppms_seq(make_cfg())
    for line in text.strip().splitlines():
        if line.startswith("REM"):
            continue
        assert (TMP_RE.match(line) or FLD_RE.match(line)
                or WAI_RE.match(line)), f"Bad seq line: {line!r}"


def test_seq_zero_field_run_has_no_fld():
    cfg = make_cfg()
    text = m.generate_ppms_seq(cfg)
    lines = text.strip().splitlines()
    run1_i = next(i for i, l in enumerate(lines) if "Run 1" in l)
    run2_i = next(i for i, l in enumerate(lines) if "Run 2" in l)
    assert not any(l.startswith("FLD") for l in lines[run1_i:run2_i])
    fscan_i = next(i for i, l in enumerate(lines) if "Step Fscan" in l)
    run2_lines = lines[run2_i:fscan_i]
    assert "FLD FIELD 5000.0 50.0 0 0" in run2_lines
    assert "FLD FIELD 0.0 20.0 2 0" in run2_lines      # reset as reference


def test_seq_reference_shape():
    """The 5000 Oe run must reproduce the reference sequence exactly
    (modulo the REM header lines)."""
    cfg = make_cfg()
    text = m.generate_ppms_seq(cfg)
    body = [l for l in text.strip().splitlines() if not l.startswith("REM")]
    run2 = [
        "TMP TEMP 10.000000 3.000000 0",
        "WAI WAITFOR 14400 0 0 0 0 0",
        "FLD FIELD 5000.0 50.0 0 0",
        "WAI WAITFOR 120 0 1 0 0 0",
        "TMP TEMP 310.000000 1.000000 0",
        "WAI WAITFOR 1800 1 0 0 0 0",
        "FLD FIELD 0.0 20.0 2 0",
        "WAI WAITFOR 20 0 1 0 0 0",
    ]
    joined = "\n".join(body)
    assert "\n".join(run2) in joined


def test_seq_fscan_steps_no_overshoot_and_wait():
    cfg = make_cfg()
    text = m.generate_ppms_seq(cfg)
    lines = [l for l in text.strip().splitlines() if not l.startswith("REM")]
    # Each schedule setpoint: TMP mode 1 (no overshoot) + stable WAITFOR
    # of step_wait_s seconds.
    assert "TMP TEMP 25.000000 1.000000 1" in lines
    assert "TMP TEMP 30.000000 1.000000 1" in lines
    assert "TMP TEMP 50.000000 1.000000 1" in lines
    assert lines.count("WAI WAITFOR 3600 1 0 0 0 0") == 3


def test_dwell_table_floors_fscan_waits():
    """DWELL-1: a wait-vs-T table floors each generated Fscan WAITFOR
    (hold outside the table, interpolate inside); without a table the
    flat step_wait_s stands. Also feeds the FSCAN phase's expected_s."""
    cfg = make_cfg()
    cfg["schedule"] = [25.0, 205.0, 310.0]
    cfg["step_wait_s"] = 1800.0            # 30 min computed wait
    cfg["dwell_table"] = m.parse_tol_table("200:30, 210:40, 310:45")
    assert m.fscan_step_wait_s(cfg, 25.0) == 1800.0    # table 30 = base
    assert m.fscan_step_wait_s(cfg, 205.0) == 2100.0   # interp 35 min
    assert m.fscan_step_wait_s(cfg, 310.0) == 2700.0   # hold 45 min
    text = m.generate_ppms_seq(cfg)
    assert "WAI WAITFOR 1800 1 0 0 0 0" in text
    assert "WAI WAITFOR 2100 1 0 0 0 0" in text
    assert "WAI WAITFOR 2700 1 0 0 0 0" in text
    assert not m.validate_ppms_seq(text)
    phases = m.build_protocol_phases(cfg)
    assert phases[-1]["expected_s"] == 1800.0 + 2100.0 + 2700.0
    # No table -> flat waits, exactly as before (count only the Step
    # Fscan section — the runs' top-hold WAITFORs are also 1800 s)
    cfg["dwell_table"] = None
    assert m.fscan_step_wait_s(cfg, 310.0) == 1800.0
    fscan_part = m.generate_ppms_seq(cfg).split("Step Fscan")[1]
    assert fscan_part.count("WAI WAITFOR 1800 1 0 0 0 0") == 3


def test_seq_no_fscan_section_when_schedule_empty():
    text = m.generate_ppms_seq(make_cfg(schedule=False))
    assert "Step Fscan" not in text
    assert "Final cooldown" not in text


def test_seq_approach_mode_toggles():
    """SEQ-1: the TMP approach toggles must flip the mode digit on the
    right lines — Tscan section vs Fscan steps independently."""
    cfg = make_cfg()
    cfg["tscan_approach"] = 1
    cfg["fscan_approach"] = 0
    text = m.generate_ppms_seq(cfg)
    lines = [l for l in text.strip().splitlines() if l.startswith("TMP")]
    # Tscan section: cooldowns + warmings + final cooldown, all mode 1
    assert "TMP TEMP 10.000000 3.000000 1" in lines
    assert "TMP TEMP 310.000000 1.000000 1" in lines
    # Fscan steps now fast-settle (mode 0)
    assert "TMP TEMP 25.000000 1.000000 0" in lines
    assert not any(l == "TMP TEMP 25.000000 1.000000 1" for l in lines)
    # Defaults (no keys) must reproduce the reference modes: 0 / 1.
    cfg2 = make_cfg()
    del cfg2["tscan_approach"], cfg2["fscan_approach"]
    text2 = m.generate_ppms_seq(cfg2)
    assert "TMP TEMP 10.000000 3.000000 0" in text2
    assert "TMP TEMP 25.000000 1.000000 1" in text2


# ------------------------------------------------------------------
# validate_ppms_seq (SEQ-2) — the faulty-sequence gate
# ------------------------------------------------------------------
def test_validator_accepts_generated_sequences():
    for cfg in (make_cfg(), make_cfg(schedule=False), make_cfg(runs=False)):
        text = m.generate_ppms_seq(cfg)
        assert m.validate_ppms_seq(text) == [], \
            f"generator emitted an invalid sequence:\n{text}"


def test_validator_accepts_reference_tscan_seq():
    """The user's real reference sequence must validate clean — this
    pins the validator to actual MultiVu output, commented-out (!)
    lines included."""
    ref = os.path.join(project_root, "pica", "PPMS", "data_file_for_ref",
                       "Dielectric_Tscan.seq")
    if not os.path.exists(ref):
        pytest.skip("PPMS reference sequences not present "
                    "(gitignored lab data) - skipping reference check.")
    with open(ref, encoding="utf-8") as fh:
        text = fh.read()
    assert m.validate_ppms_seq(text) == []


def test_validator_catches_faults():
    # Malformed shapes
    assert m.validate_ppms_seq("TMP TEMP 10.0 3.0\n")          # missing mode
    assert m.validate_ppms_seq("WAI WAITFOR -5 0 0 0 0 0\n")   # negative wait
    assert m.validate_ppms_seq("TEMP 10 3 0\n")                # unknown cmd
    assert m.validate_ppms_seq("FLD FIELD 5000.0 50.0 5 0\n")  # bad approach
    # Out-of-range values (grammar OK, PPMS could not execute)
    assert m.validate_ppms_seq("TMP TEMP 500.000000 3.000000 0\n")  # >400 K
    assert m.validate_ppms_seq("TMP TEMP 10.000000 50.000000 0\n")  # >20 K/min
    assert m.validate_ppms_seq("FLD FIELD 999999.0 50.0 0 0\n")     # field
    # Hand-edited decimals are fine (validator is format-agnostic)
    assert m.validate_ppms_seq("TMP TEMP 25.5 1 1\n") == []
    # Comments / disabled lines / MES remarks pass through
    ok = "REM hello\n!TMP TEMP 10.000000 3.000000 0\nMES anything\n\n"
    assert m.validate_ppms_seq(ok) == []


def test_validator_rejects_non_ascii():
    """SEQ-4: DynaCool reads .seq as ANSI — a UTF-8 em-dash renders as
    mojibake in the sequence editor, so ANY non-ASCII character (even in
    a REM comment) must be rejected. The generator itself must emit
    pure-ASCII sequences."""
    errs = m.validate_ppms_seq("REM note with an em—dash\n")
    assert errs and "non-ASCII" in errs[0]
    assert m.validate_ppms_seq("TMP TEMP 25.000000 1.000000 1 °\n")
    for cfg in (make_cfg(), make_cfg(schedule=False), make_cfg(runs=False)):
        assert m.generate_ppms_seq(cfg).isascii()


# ------------------------------------------------------------------
# End-to-end synthetic trace through the detection logic
# ------------------------------------------------------------------
def synthetic_profile():
    """(t, T) pairs, 30 s cadence: 310 K -> cool to 20 K -> sit ->
    warm to 310 K -> hold -> cool again. Compressed timescale."""
    pts = []
    t = 0.0
    T = 310.0
    while T > 20.0:                    # cooldown ~3 K/step
        pts.append((t, T)); T -= 3.0; t += 30.0
    for _ in range(40):                # sit at base
        pts.append((t, 20.0 + 0.05 * ((_ % 3) - 1))); t += 30.0
    T = 20.0
    while T < 310.0:                   # warming 1 K/step
        pts.append((t, T)); T += 1.0; t += 30.0
    for _ in range(80):                # hold at top
        pts.append((t, 310.0 - 0.1 * (_ % 2))); t += 30.0
    T = 310.0
    while T > 250.0:                   # next cooldown begins
        pts.append((t, T)); T -= 2.0; t += 30.0
    return pts


def test_synthetic_trace_transitions_fire_in_order():
    cfg = make_cfg()
    warm_det = m.TurnaroundDetector()
    warm_conf = m.SustainedCondition(cfg["confirm_s"])
    warming_at = None
    cool_det = None
    cool_conf = None
    cooling_at = None
    top_arm = cfg["top_temp"] - cfg["top_arm_off"]

    for t, T in synthetic_profile():
        if warming_at is None:
            warm_det.update(T)
            if warm_conf.update(
                    warm_det.warming_started(cfg["base_arm"],
                                             cfg["rise_k"]), t):
                warming_at = (t, T)
                cool_det = m.TurnaroundDetector()
                cool_conf = m.SustainedCondition(cfg["confirm_s"])
        elif cooling_at is None:
            cool_det.update(T)
            if cool_conf.update(
                    cool_det.cooling_started(top_arm, cfg["fall_k"]), t):
                cooling_at = (t, T)

    assert warming_at is not None, "warming never confirmed"
    assert cooling_at is not None, "cooling never confirmed"
    # Warming confirmed early in the ramp (well below 40 K)...
    assert warming_at[1] < 40.0
    # ...and cooling confirmed only after the top was actually reached
    # and left (below 310 but above the arm threshold minus the ramp).
    assert cooling_at[0] > warming_at[0]
    assert cooling_at[1] < 310.0
    assert cool_det.max_T > top_arm


# ------------------------------------------------------------------
# Durable write buffering (same pattern as the parents; covered here
# directly so the worker simulation may legitimately bypass fsync)
# ------------------------------------------------------------------
def test_write_buffer_mechanics():
    import tempfile
    from collections import deque

    class H:
        _csv_line = staticmethod(m.PPMSMasterGUI._csv_line)
        _durable_append = staticmethod(m.PPMSMasterGUI._durable_append)
        _write_or_buffer = m.PPMSMasterGUI._write_or_buffer
        _flush_pending_rows = m.PPMSMasterGUI._flush_pending_rows

        def __init__(self):
            self._pending_rows = deque()
            self._write_error_logged = False
            self.msgs = []

        def _put_gui_msg(self, t, **kw):
            self.msgs.append(kw.get("text", t))

    h = H()
    d = tempfile.mkdtemp(prefix="pica_master_wb_")
    good = os.path.join(d, "x.csv")
    bad = os.path.join(d, "no_such_dir", "y.csv")

    h._write_or_buffer(good, "a\n")              # straight to disk
    h._write_or_buffer(bad, "b\n")               # fails -> buffered
    assert h._pending_rows
    assert any("WRITE ERROR" in s for s in h.msgs)
    h._write_or_buffer(good, "c\n")              # joins buffer (ordering)
    assert len(h._pending_rows) == 2

    os.makedirs(os.path.dirname(bad))            # path recovers
    h._write_or_buffer(good, "d\n")              # flushes buffer first
    assert not h._pending_rows
    with open(good, encoding="utf-8") as fh:
        assert fh.read() == "a\nc\nd\n"          # order preserved
    with open(bad, encoding="utf-8") as fh:
        assert fh.read() == "b\n"


# ------------------------------------------------------------------
# Timing helpers
# ------------------------------------------------------------------
def test_parse_duration_and_fmt():
    assert m.parse_duration_min("3:30") == 210.0
    assert m.parse_duration_min("90") == 90.0
    assert m.fmt_hms(3661) == "1:01:01"


# ------------------------------------------------------------------
# TOL-1 (v1.3): temperature-dependent tolerance — embedded copy must
# behave identically to the Sync original
# ------------------------------------------------------------------
def test_tol_table_copy_matches_sync():
    from pica.keysight import PPMS_Sync_Freq_Scan_E4980A_GUI as sync
    assert m.TOL_TABLE_PRESETS == sync.TOL_TABLE_PRESETS
    table = m.parse_tol_table(m.TOL_TABLE_PRESETS["Safe (recommended)"])
    for T in (20.0, 30.0, 35.0, 55.0, 100.0, 300.0):
        assert m.tol_from_table(table, T, 0.4) == \
            sync.tol_from_table(table, T, 0.4)
    # The lab scenario: probe 1.15 K above a 30 K setpoint passes with
    # the table, fails on the fixed tolerance.
    import time as _t
    now = _t.time()
    window = [(now + i, 31.15) for i in range(6)]
    p = {"mode": "flat_band", "tol": 0.5, "window_min": 0.05,
         "drift": 0.05, "guard": 2.0, "tol_table": None}
    ok, _ = m.PPMSMasterGUI._window_check(None, window, 30.0, p)
    assert not ok
    p["tol_table"] = table
    ok, _ = m.PPMSMasterGUI._window_check(None, window, 30.0, p)
    assert ok


def test_estimate_scan_seconds_positive():
    est = m.estimate_scan_seconds([100.0, 1000.0, 1e6], 0.2, "MED")
    assert est > 0


if __name__ == "__main__":
    # Plain-python runnable: execute every test_* function in order.
    failures = 0
    g = dict(globals())
    for name, fn in sorted(g.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
            except BaseException as e:
                # pytest.skip raises Skipped (a BaseException subclass)
                if type(e).__name__ == "Skipped":
                    print(f"SKIP  {name}: {e}")
                else:
                    raise
    print(f"\n{failures} failure(s).")
    sys.exit(1 if failures else 0)
