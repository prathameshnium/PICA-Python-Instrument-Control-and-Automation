"""CV_KE4980A_GUI.py v2.0 and its plain-script twin against the fake E4980A:
waveform and segment labels, the time column against the delay budget, a
forced status 1 row giving NaN derived values with raw 9.9E37 kept, the
four monitor columns, END lines on completion and on Stop, the bus-error
retry with RETRY and RECONNECT lines and a re-configure, frequency order
kept, header contents, input validation (delay, step, 20 V clamp), bias
forced to 0 V and off on every exit, plotter compatibility, ASCII-only
files and the two modules sharing one byte-identical run path.
"""

import ast
import inspect
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_ke4980a_test_support import (  # noqa: E402
    FakeE4980A, MODULES, gui, script, run_cv, read_data_file, column, log_lines,
    plotter_reads, run_module_tests)


def _tmp():
    return tempfile.mkdtemp(prefix="cv_test_")


# -- waveform -----------------------------------------------------------------

def test_cycle_shapes_start_at_zero_end_at_zero_without_duplicates():
    for shape in gui.SHAPES:
        pts = gui.build_waveform(True, shape, 1.0, 0, 0, 0.25, 2, ladder=[1.0, 1.0])
        vs = [v for _, _, v in pts]
        assert vs[0] == 0.0 and vs[-1] == 0.0
        for a, b in zip(vs, vs[1:]):
            assert a != b, f"duplicate point {a} in {shape}"
        assert max(abs(v) for v in vs) == 1.0
        assert {c for c, _, _ in pts} == {1, 2}


def test_bipolar_cycle_segments_and_cycle_labels():
    pts = gui.build_waveform(True, gui.SHAPE_POS_FIRST, 1.0, 0, 0, 0.5, 2)
    # leading 0 V point is cycle 1 segment 1, then 4 legs per cycle
    assert pts[0] == (1, 1, 0.0)
    cyc1 = [(s, v) for c, s, v in pts if c == 1]
    assert cyc1 == [(1, 0.0), (1, 0.5), (1, 1.0), (2, 0.5), (2, 0.0),
                    (3, -0.5), (3, -1.0), (4, -0.5), (4, 0.0)]
    cyc2 = [(s, v) for c, s, v in pts if c == 2]
    assert cyc2 == cyc1[1:]
    neg = gui.build_waveform(True, gui.SHAPE_NEG_FIRST, 1.0, 0, 0, 0.5, 1)
    assert [v for c, s, v in neg if s == 1] == [0.0, -0.5, -1.0]
    uni = gui.build_waveform(True, gui.SHAPE_UNI_NEG, 1.0, 0, 0, 0.5, 1)
    assert {s for _, s, _ in uni} == {1, 2} and min(v for _, _, v in uni) == -1.0


def test_linear_waveform_is_one_way_with_cycle_and_segment_one():
    pts = gui.build_waveform(False, "", 0.0, -1.0, 1.0, 0.5, 1)
    assert [v for _, _, v in pts] == [-1.0, -0.5, 0.0, 0.5, 1.0]
    assert {(c, s) for c, s, _ in pts} == {(1, 1)}
    down = gui.build_waveform(False, "", 0.0, 1.0, -1.0, 0.4, 1)
    assert down[0][2] == 1.0 and down[-1][2] == -1.0


def test_waveform_text_and_segment_text_read_as_sentences():
    p = gui.validate_params(dict(sample_name="Sample_A", ac_bias=1.0, v_start=-2, v_stop=2, v_step=0.05,
                                 n_cycles=3, cycle_mode=True, shape=gui.SHAPE_POS_FIRST, freq_list="1000",
                                 delay=0.2, aper="MED", alc_enabled=True, corr_enabled=True, cable_len="1",
                                 lcr_visa="x", folder="."))
    text = gui.waveform_text(p, 1000.0)
    assert text.startswith("DC staircase 0 -> +2 -> 0 -> -2 -> 0 V, step 0.05 V, hold 0.2 s + ramp 0.2 s")
    assert "x3 cycle(s); AC 1 Vrms sine at 1000 Hz" in text
    assert gui.segment_text(True, gui.SHAPE_POS_FIRST) == "1 = 0 -> +V, 2 = +V -> 0, 3 = 0 -> -V, 4 = -V -> 0"


def test_ladder_shape_runs_one_bipolar_cycle_per_listed_amplitude():
    pts = gui.build_waveform(True, gui.SHAPE_LADDER, 0, 0, 0, 0.5, 0, ladder=[0.5, 1.0])
    assert pts[0] == (1, 1, 0.0)
    assert [(s, v) for c, s, v in pts if c == 1][1:] == [(1, 0.5), (2, 0.0), (3, -0.5), (4, 0.0)]
    assert [(s, v) for c, s, v in pts if c == 2] == [(1, 0.5), (1, 1.0), (2, 0.5), (2, 0.0),
                                                     (3, -0.5), (3, -1.0), (4, -0.5), (4, 0.0)]
    assert pts[-1][2] == 0.0
    for a, b in zip(pts, pts[1:]):
        assert a[2] != b[2]


def test_parse_ladder_accepts_separators_and_rejects_bad_or_over_cap_entries():
    assert gui.parse_ladder("0.5, 1; 2" + chr(10) + "5") == [0.5, 1.0, 2.0, 5.0]
    for bad in ("", "a", "0", "-1", "25"):
        try:
            gui.parse_ladder(bad)
            assert False, bad
        except ValueError:
            pass


# -- fields a mode/shape ignores (greyed, n/a in the header) ---------------------

def test_disabled_fields_matrix_matches_the_memristor_convention():
    assert gui.disabled_fields(False, "") == {"n_cycles", "shape", "ladder"}
    for shape in (gui.SHAPE_POS_FIRST, gui.SHAPE_NEG_FIRST, gui.SHAPE_UNI_POS, gui.SHAPE_UNI_NEG):
        assert gui.disabled_fields(True, shape) == {"v_start", "ladder"}, shape
    assert gui.disabled_fields(True, gui.SHAPE_LADDER) == {"v_start", "v_stop", "n_cycles"}
    assert set(gui.SWEEP_FIELDS) == {"v_start", "v_stop", "n_cycles", "shape", "ladder"}


class _Widget:
    def __init__(self):
        self.state = "normal"

    def config(self, state):
        self.state = state

    def get(self):
        return getattr(self, "value", "")


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _window_stub(cycle_mode, shape):
    """The window's toggle without Tk: entries and combobox as stubs."""
    w = object.__new__(gui.LCR_Freq_GUI)
    w.entries = {k: _Widget() for k in ("v_start", "v_stop", "n_cycles", "ladder", "sample_name")}
    w.shape_combobox = _Widget()
    w.shape_combobox.value = shape
    w.var_cycle = _Var(cycle_mode)
    w._on_cycle_mode_toggle()
    return w


def test_window_greys_exactly_the_disjoint_entries_and_restores_them():
    w = _window_stub(False, gui.SHAPE_POS_FIRST)
    assert {k: v.state for k, v in w.entries.items() if k != "sample_name"} == {
        "v_start": "normal", "v_stop": "normal", "n_cycles": "disabled", "ladder": "disabled"}
    assert w.shape_combobox.state == "disabled"
    w = _window_stub(True, gui.SHAPE_UNI_NEG)
    assert {k: v.state for k, v in w.entries.items() if k != "sample_name"} == {
        "v_start": "disabled", "v_stop": "normal", "n_cycles": "normal", "ladder": "disabled"}
    assert w.shape_combobox.state == "readonly"
    w = _window_stub(True, gui.SHAPE_LADDER)
    assert {k: v.state for k, v in w.entries.items() if k != "sample_name"} == {
        "v_start": "disabled", "v_stop": "disabled", "n_cycles": "disabled", "ladder": "normal"}
    # switching back re-enables without touching values (greyed fields keep their text)
    w.entries["v_stop"].value = "2.0"
    w.var_cycle = _Var(False)
    w._on_cycle_mode_toggle()
    assert w.entries["v_stop"].state == "normal" and w.entries["v_stop"].get() == "2.0"


def test_blank_or_stale_greyed_entries_never_block_start():
    base = dict(sample_name="Sample_A", ac_bias=1.0, v_step=0.5, freq_list="1000", delay=0, aper="MED",
                alc_enabled=True, corr_enabled=True, cable_len="1", lcr_visa="x", folder=".")
    # linear: cycles / shape / ladder blank or nonsense
    p = gui.validate_params({**base, "cycle_mode": False, "shape": "", "v_start": "-1", "v_stop": "1",
                             "n_cycles": "", "ladder": "abc"})
    assert p["n_cycles"] == 1 and p["ladder"] == [] and p["ignored"] == ["ladder", "n_cycles", "shape"]
    # cycle: v_start blank, ladder nonsense
    p = gui.validate_params({**base, "cycle_mode": True, "shape": gui.SHAPE_POS_FIRST, "v_start": "",
                             "v_stop": "1", "n_cycles": "2", "ladder": "abc"})
    assert p["n_cycles"] == 2 and p["v_max"] == 1.0 and p["ignored"] == ["ladder", "v_start"]
    # ladder: v_start, v_stop, n_cycles blank or out of range are ignored, not clamped, not refused
    p = gui.validate_params({**base, "cycle_mode": True, "shape": gui.SHAPE_LADDER, "v_start": "",
                             "v_stop": "99", "n_cycles": "", "ladder": "0.5, 1"})
    assert p["ladder"] == [0.5, 1.0] and p["n_cycles"] == 2 and p["v_max"] == 1.0 and not p["clamped"]
    assert p["ignored"] == ["n_cycles", "v_start", "v_stop"]
    # an active field that is blank still refuses
    for mode, shape, bad in ((False, "", {"v_start": ""}), (True, gui.SHAPE_POS_FIRST, {"v_stop": ""}),
                             (True, gui.SHAPE_POS_FIRST, {"n_cycles": ""})):
        try:
            gui.validate_params({**base, "cycle_mode": mode, "shape": shape, "v_start": "-1", "v_stop": "1",
                                 "n_cycles": "1", "ladder": "", **bad})
            assert False, bad
        except ValueError:
            pass


def test_header_writes_na_for_the_fields_the_mode_ignores():
    folder = _tmp()
    worker, msgs, files, _ = run_cv(FakeE4980A(), folder, cycle_mode=False, v_start=-1.0, v_stop=1.0)
    sweep = next(h for h in read_data_file(files[0])[0] if h.startswith("[sweep]"))
    assert "v_start_v=-1 | v_stop_v=1 | v_max_v=n/a" in sweep and "ladder_list_V=n/a" in sweep
    worker, msgs, files, _ = run_cv(FakeE4980A(), _tmp(), shape=gui.SHAPE_UNI_POS, v_stop=1.0)
    sweep = next(h for h in read_data_file(files[0])[0] if h.startswith("[sweep]"))
    assert "v_start_v=n/a | v_stop_v=1 | v_max_v=1" in sweep and "ladder_list_V=n/a" in sweep
    worker, msgs, files, _ = run_cv(FakeE4980A(), _tmp(), shape=gui.SHAPE_LADDER, ladder="0.5, 1")
    sweep = next(h for h in read_data_file(files[0])[0] if h.startswith("[sweep]"))
    assert "v_start_v=n/a | v_stop_v=n/a | v_max_v=1" in sweep and "ladder_list_V=0.5 1" in sweep


# -- validation -----------------------------------------------------------------

def test_validate_params_rejects_negative_delay_zero_step_bad_ac_and_clamps_to_20_v():
    base = dict(sample_name="Sample_A", ac_bias=1.0, v_start=-1, v_stop=1, v_step=0.5, n_cycles=1,
                cycle_mode=False, shape="", freq_list="1000", delay=0.1, aper="MED", alc_enabled=True,
                corr_enabled=True, cable_len="1", lcr_visa="x", folder=".")
    for bad in (dict(delay=-0.1), dict(v_step=0), dict(ac_bias=0), dict(ac_bias=2.5),
                dict(freq_list=""), dict(freq_list="10"), dict(cycle_mode=True, n_cycles=0)):
        try:
            gui.validate_params({**base, **bad, "shape": gui.SHAPE_POS_FIRST})
            assert False, bad
        except ValueError:
            pass
    p = gui.validate_params({**base, "v_start": -25, "v_stop": 30})
    assert p["clamped"] and p["v_start"] == -20.0 and p["v_stop"] == 20.0
    assert max(abs(v) for _, _, v in p["waveform"]) <= gui.V_ABS_MAX
    assert gui.V_ABS_MAX == 20.0 and script.V_ABS_MAX == 20.0


def test_validate_params_ladder_sets_v_max_and_cycles_from_the_list_and_needs_a_list():
    base = dict(sample_name="Sample_A", ac_bias=1.0, v_start=-1, v_stop=9, v_step=0.5, n_cycles=7,
                cycle_mode=True, shape=gui.SHAPE_LADDER, freq_list="1000", delay=0, aper="MED",
                alc_enabled=True, corr_enabled=True, cable_len="1", lcr_visa="x", folder=".")
    p = gui.validate_params({**base, "ladder": "1, 2, 0.5"})
    assert p["ladder"] == [1.0, 2.0, 0.5] and p["v_max"] == 2.0 and p["n_cycles"] == 3
    assert max(abs(v) for _, _, v in p["waveform"]) == 2.0
    assert {c for c, _, _ in p["waveform"]} == {1, 2, 3}
    assert gui.waveform_text(p, 1000.0).startswith(
        "DC staircase ladder 0 -> +V -> 0 -> -V -> 0 with V = 1, 2, 0.5 V in turn (one cycle per value), step 0.5 V")
    for bad in ("", "0, 1", "21"):
        try:
            gui.validate_params({**base, "ladder": bad})
            assert False, bad
        except ValueError:
            pass


def test_frequency_list_keeps_typed_order_and_drops_duplicates():
    assert gui.parse_freq_list("100000, 1000, 1000, 10000") == [100000.0, 1000.0, 10000.0]


def test_cycle_mode_amplitude_comes_from_v_stop_not_v_start():
    p = gui.validate_params(dict(sample_name="Sample_A", ac_bias=1.0, v_start=-7, v_stop=1.5, v_step=0.5,
                                 n_cycles=1, cycle_mode=True, shape=gui.SHAPE_POS_FIRST, freq_list="1000",
                                 delay=0, aper="MED", alc_enabled=True, corr_enabled=True, cable_len="1",
                                 lcr_visa="x", folder="."))
    assert p["v_max"] == 1.5 and max(abs(v) for _, _, v in p["waveform"]) == 1.5


# -- run path -----------------------------------------------------------------

def test_complete_run_writes_labelled_rows_monitors_raw_values_and_end_line():
    folder = _tmp()
    fake = FakeE4980A()
    worker, msgs, files, log_path = run_cv(fake, folder, n_cycles=2)
    assert worker.end_reason == "complete" and len(files) == 1
    header, columns, rows, trailer = read_data_file(files[0])
    assert columns[:2] == ["Voltage(V)", "Frequency"]
    assert columns[-11:] == ["t_s", "Timestamp", "Cycle", "Segment", "Vdc_mon(V)", "Idc_mon(A)",
                             "Vac_mon(V)", "Iac_mon(A)", "R_raw", "X_raw", "Status"]
    assert len(columns) == 2 + 18 + 11 and all(len(r) == len(columns) for r in rows)
    expect = gui.build_waveform(True, gui.SHAPE_POS_FIRST, 1.0, 0, 0, 0.5, 2)
    assert column(rows, columns, "Voltage(V)") == [v for _, _, v in expect]
    assert column(rows, columns, "Cycle", int) == [c for c, _, _ in expect]
    assert column(rows, columns, "Segment", int) == [s for _, s, _ in expect]
    assert set(column(rows, columns, "Frequency")) == {1000.0}
    assert set(column(rows, columns, "Status", int)) == {0}
    # monitors follow the fake's bias and AC level
    vs = column(rows, columns, "Voltage(V)")
    vdc = column(rows, columns, "Vdc_mon(V)")
    idc = column(rows, columns, "Idc_mon(A)")
    assert all(abs(a - 0.999 * v) < 1e-6 for a, v in zip(vdc, vs))
    assert all(abs(i - v * 1e-9) < 1e-15 for i, v in zip(idc, vs))
    assert all(abs(x - 0.98) < 1e-6 for x in column(rows, columns, "Vac_mon(V)"))
    assert all(abs(x - 1e-3) < 1e-9 for x in column(rows, columns, "Iac_mon(A)"))
    # raw R, X and the derived Cp agree with the fake capacitor
    assert all(abs(r - 100.0) < 1e-6 for r in column(rows, columns, "R_raw"))
    cp = column(rows, columns, "Cp")
    assert all(abs(c - 1e-9) / 1e-9 < 1e-4 for c in cp)
    assert column(rows, columns, "R(Rs)") == column(rows, columns, "R_raw")
    # one END line after the data, in the file and in the log
    assert len(trailer) == 1 and trailer[0].startswith("END reason=complete | points=%d" % len(rows))
    events = log_lines(log_path)
    assert events[0].startswith("START module=CV_KE4980A_GUI")
    assert any(e.startswith("FILE data_file=") for e in events)
    assert any(e.startswith("PRE_RAMP from=0 V") for e in events)
    assert events[-1].startswith("END reason=complete") and "retries=0" in events[-1]
    # bias forced to 0 V and off, link closed
    assert fake.v == 0.0 and not fake.bias_on and fake.closed
    assert fake.writes[-1] == ":BIAS:STAT OFF"


def test_ladder_run_labels_cycles_by_entry_and_records_the_list_in_the_header():
    folder = _tmp()
    fake = FakeE4980A()
    worker, msgs, files, _ = run_cv(fake, folder, shape=gui.SHAPE_LADDER, ladder="0.5, 1", v_step=0.5)
    assert worker.end_reason == "complete"
    header, columns, rows, trailer = read_data_file(files[0])
    vs = column(rows, columns, "Voltage(V)")
    cyc = column(rows, columns, "Cycle", int)
    assert max(abs(v) for v, c in zip(vs, cyc) if c == 1) == 0.5
    assert max(abs(v) for v, c in zip(vs, cyc) if c == 2) == 1.0
    sweep = next(h for h in header if h.startswith("[sweep]"))
    assert "shape=ladder:" in sweep and "ladder_list_V=0.5 1" in sweep and "n_cycles=2" in sweep and "v_max_v=1" in sweep
    assert any(h.startswith("waveform: DC staircase ladder") for h in header)
    assert fake.v == 0.0 and not fake.bias_on


def test_time_column_respects_the_delay_budget_and_is_monotonic():
    folder = _tmp()
    fake = FakeE4980A()
    delay = 0.03
    worker, msgs, files, _ = run_cv(fake, folder, delay=delay, worker_sleep=time.sleep)
    _, columns, rows, _ = read_data_file(files[0])
    t = column(rows, columns, "t_s")
    assert all(b > a for a, b in zip(t, t[1:]))
    gaps = [b - a for a, b in zip(t, t[1:])]
    assert all(g >= delay * 0.9 for g in gaps), gaps
    stamps = column(rows, columns, "Timestamp", str)
    assert all("T" in s and " " not in s for s in stamps)
    assert stamps == sorted(stamps)


def test_status_one_row_has_nan_derived_values_and_keeps_raw_overload_values():
    folder = _tmp()
    fake = FakeE4980A(status_at={2: 1, 3: 4})
    worker, msgs, files, _ = run_cv(fake, folder)
    _, columns, rows, _ = read_data_file(files[0])
    status = column(rows, columns, "Status", int)
    assert status[2] == 1 and status[3] == 4 and status[0] == 0
    derived_names = columns[2:20]
    for name in derived_names:
        vals = column(rows, columns, name)
        assert math.isnan(vals[2]), name            # status 1 -> NaN
        assert not math.isnan(vals[3]), name        # status 4 keeps real data (manual p. 265)
        assert not math.isnan(vals[0]), name
    assert column(rows, columns, "R_raw")[2] == 9.9e37
    assert column(rows, columns, "X_raw")[2] == 9.9e37
    assert any("status 1" in m[1] and "NaN" in m[1] for m in msgs if m[0] == "log")
    assert any("status 4" in m[1] for m in msgs if m[0] == "log")
    # the row is still complete and the file still parses in the plotter
    headers, data = plotter_reads(files[0])
    assert data.shape == (len(rows), len(columns))


def test_stop_ends_the_file_with_an_end_line_and_zeroes_the_bias():
    folder = _tmp()
    fake = FakeE4980A()
    # Stop pressed during the 4th fetch: that point is finished and kept,
    # the 5th never starts.
    worker, msgs, files, log_path = run_cv(fake, folder, n_cycles=5, stop_at_fetch=3)
    assert worker.end_reason == "stop_button"
    _, columns, rows, trailer = read_data_file(files[0])
    assert len(rows) == 4
    assert trailer == [t for t in trailer if t.startswith("END reason=stop_button | points=4")]
    assert log_lines(log_path)[-1].startswith("END reason=stop_button")
    assert fake.v == 0.0 and not fake.bias_on and fake.closed
    assert msgs[-1] == ("done", "stop_button")


def test_bus_error_is_retried_with_retry_and_reconnect_lines_and_a_reconfigure():
    folder = _tmp()
    fake = FakeE4980A(fail_reads={2})
    at_fetch = []                                            # (instrument freq, bias) per :FETC?
    fake.on_fetch = lambda idx: at_fetch.append((fake.freq, fake.v))
    worker, msgs, files, log_path = run_cv(fake, folder, freq_list="10000")
    assert worker.end_reason == "complete" and worker.retry_total == 1
    _, columns, rows, _ = read_data_file(files[0])
    assert len(rows) == len(gui.build_waveform(True, gui.SHAPE_POS_FIRST, 1.0, 0, 0, 0.5, 1))
    # *RST in the re-configure must not leave the unit at 1 kHz / 0 V: every
    # measurement, the retried one included, is at the file's frequency and
    # at the bias its row claims (the fake's Vdc monitor reads 0.999 x V).
    assert all(f == 10000.0 for f, _ in at_fetch), at_fetch
    volts = column(rows, columns, "Voltage(V)")
    vdc = column(rows, columns, "Vdc_mon(V)")
    assert all(abs(m - 0.999 * v) < 1e-6 for v, m in zip(volts, vdc)), list(zip(volts, vdc))
    cp = column(rows, columns, "Cp")
    assert all(abs(c - 1e-9) < 1e-11 for c in cp), cp
    events = log_lines(log_path)
    retries = [e for e in events if e.startswith("RETRY ")]
    reconnects = [e for e in events if e.startswith("RECONNECT ")]
    assert len(retries) == 1 and "what=measure" in retries[0] and "VI_ERROR_TMO" in retries[0]
    assert len(reconnects) == 1 and "measure succeeded on attempt 2" in reconnects[0]
    assert fake.opens == 2                                   # reopened once
    assert fake.writes.count("*RST; *CLS") == 2              # re-configured
    assert "retries=1" in events[-1]
    # after the reconnect the bias came back on at 0 V and was ramped to the point
    i = len(fake.writes) - 1 - fake.writes[::-1].index("*RST; *CLS")
    after = fake.writes[i:]
    assert ":BIAS:STAT ON" in after
    assert fake.v == 0.0 and not fake.bias_on


def test_stop_during_a_retry_gap_ends_with_its_own_reason():
    folder = _tmp()
    fake = FakeE4980A(fail_forever=True)
    worker, msgs, files, log_path = run_cv(fake, folder, stop_after=0.3)
    assert worker.end_reason == "stop_button_during_retry"
    assert worker.retry_total >= 1
    assert log_lines(log_path)[-1].startswith("END reason=stop_button_during_retry")
    _, columns, rows, trailer = read_data_file(files[0])
    assert rows == [] and trailer[0].startswith("END reason=stop_button_during_retry | points=0")
    assert fake.v == 0.0 and not fake.bias_on


def test_missing_option_001_ends_with_error_no_retry_and_bias_off():
    folder = _tmp()
    fake = FakeE4980A(opt="710")
    worker, msgs, files, log_path = run_cv(fake, folder)
    assert worker.end_reason.startswith("error: CV sweeps require Option 001")
    assert worker.retry_total == 0 and files == []
    assert any(m[0] == "error" for m in msgs)
    assert log_lines(log_path)[-1].startswith("END reason=error: CV sweeps require Option 001")
    assert not fake.bias_on and fake.closed


def test_frequencies_run_in_typed_order_one_file_each_with_k_of_n_header():
    folder = _tmp()
    fake = FakeE4980A()
    worker, msgs, files, _ = run_cv(fake, folder, freq_list="100000, 1000, 10000", cycle_mode=False)
    assert [os.path.basename(f).split("_CV_")[1] for f in files] == ["100000Hz.txt", "1000Hz.txt", "10000Hz.txt"]
    for k, path in enumerate(files):
        header, columns, rows, _ = read_data_file(path)
        assert header[0].endswith(f"file {k + 1} of 3")
        freq_line = next(h for h in header if h.startswith("[frequency]"))
        assert "order_hz=100000,1000,10000" in freq_line
        earlier = "none" if k == 0 else ",".join(["100000", "1000"][:k])
        assert f"earlier_hz={earlier}" in freq_line
        assert set(column(rows, columns, "Frequency")) == {[100000.0, 1000.0, 10000.0][k]}
    freq_writes = [w for w in fake.writes if w.startswith(":FREQ ")]
    assert freq_writes == [":FREQ 100000.0", ":FREQ 1000.0", ":FREQ 10000.0"]


def test_header_states_everything_a_reader_needs():
    folder = _tmp()
    fake = FakeE4980A()
    worker, msgs, files, log_path = run_cv(fake, folder, n_cycles=2, delay=0.0)
    header, columns, rows, _ = read_data_file(files[0])
    joined = "\n".join(header)
    for needle in ("[instrument] idn=Keysight Technologies,E4980A", "opt=001,710", "visa=FAKE::INSTR",
                   "[sample] name=Sample_A", "mode=cycle", "shape=positive first", "v_max_v=1", "v_step_v=0.5",
                   "n_cycles=2", "points_per_file=%d" % len(rows), "delay_s=0", "ramp_step_v=0.5",
                   "ramp_dwell_s=0.1", "v_abs_max_v=20", "pre_ramp=", "this_hz=1000", "ac_level_vrms_set=1",
                   "ac_level_vrms_read=", "aperture=MED,1", "alc=1", "open_corr=1", "short_corr=1", "cable_m=1",
                   "bias_range_auto=1", "monitors=VDC,IDC,VAC,IAC", "trigger=BUS", "run_start=", "file_start=",
                   "waveform: DC staircase 0 -> +1 -> 0 -> -1 -> 0 V", "segments: 1 = 0 -> +V",
                   "no current compliance", "status: 0 normal", "NaN when status is -1 or 1",
                   "log_file=" + os.path.basename(log_path)):
        assert needle in joined, needle


def test_linear_mode_rows_carry_cycle_one_segment_one_and_the_one_way_header():
    folder = _tmp()
    fake = FakeE4980A()
    worker, msgs, files, _ = run_cv(fake, folder, cycle_mode=False, v_start=-1.0, v_stop=1.0, v_step=0.5)
    header, columns, rows, _ = read_data_file(files[0])
    assert column(rows, columns, "Voltage(V)") == [-1.0, -0.5, 0.0, 0.5, 1.0]
    assert set(column(rows, columns, "Cycle", int)) == {1} and set(column(rows, columns, "Segment", int)) == {1}
    assert any("mode=linear" in h and "shape=one-way" in h and "n_cycles=1" in h for h in header)
    assert any(h.startswith("waveform: DC staircase -1 -> 1 V one way") for h in header)


def test_bias_is_ramped_in_half_volt_steps_never_jumped():
    folder = _tmp()
    fake = FakeE4980A()
    run_cv(fake, folder, cycle_mode=False, v_start=-2.0, v_stop=2.0, v_step=2.0)
    bias = [float(w.split()[1]) for w in fake.writes if w.startswith(":BIAS:VOLT ")]
    jumps = [abs(b - a) for a, b in zip(bias, bias[1:])]
    assert max(jumps) <= 1.0 + 1e-9          # 0.5 V sweep steps; 1.0 V in force_zero_bias
    assert max(abs(b) for b in bias) <= gui.V_ABS_MAX


def test_files_are_ascii_and_plotter_compatible():
    folder = _tmp()
    fake = FakeE4980A(status_at={1: 1})
    worker, msgs, files, log_path = run_cv(fake, folder, n_cycles=2)
    for path in files + [log_path]:
        with open(path, "rb") as fh:
            raw = fh.read()
        raw.decode("ascii")
    headers, data = plotter_reads(files[0])
    _, columns, rows, _ = read_data_file(files[0])
    assert headers == columns
    assert data.shape == (len(rows), len(columns))
    v = data[:, headers.index("Voltage(V)")]
    assert list(v) == column(rows, columns, "Voltage(V)")
    cp = data[:, headers.index("Cp")]
    assert math.isnan(cp[1]) and not math.isnan(cp[0])


# -- the two modules share one run path -------------------------------------------

def test_script_twin_shares_byte_identical_link_worker_and_pure_functions():
    names = ["ascii_text", "header_value", "fmt_cell", "iso_now", "timestamp_for_name", "wait_with_stop",
             "_ramp", "half_leg", "return_leg", "build_cycle", "build_waveform", "segment_text",
             "ramp_time_s", "waveform_text", "parse_freq_list", "parse_ladder", "derived_columns",
             "nan_derived", "validate_params", "disabled_fields", "E4980ALink", "CVWorker"]
    def shape(obj):
        """The code without docstrings and comments (ast dump)."""
        tree = ast.parse(inspect.getsource(obj).strip())
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
        return ast.dump(tree)

    for name in names:
        assert shape(getattr(gui, name)) == shape(getattr(script, name)), name
    assert gui.COLUMN_ROW == script.COLUMN_ROW
    assert gui.SHAPES == script.SHAPES and gui.RETRY_SCHEDULE_S == script.RETRY_SCHEDULE_S
    for f, R, X in ((1000.0, 100.0, -159154.9), (1e6, 5.0, 2.0)):
        assert gui.derived_columns(f, R, X) == script.derived_columns(f, R, X)


def test_script_twin_runs_the_same_fake_and_writes_the_same_columns():
    folder = _tmp()
    fake = FakeE4980A()
    worker, msgs, files, log_path = run_cv(fake, folder, mod=script, n_cycles=1)
    assert worker.end_reason == "complete"
    header, columns, rows, trailer = read_data_file(files[0])
    assert columns == gui.COLUMN_ROW.split("\t")
    assert header[0].startswith("PICA CV_KE4980A_Simple_Instrument_Control v2.0")
    assert trailer[0].startswith("END reason=complete")
    assert fake.v == 0.0 and not fake.bias_on
    p = script.params_from_user_input()
    assert p["sample_name"] == "Sample_A" and p["cycle_mode"] is True
    assert ":TRIG:SOUR BUS" in fake.writes and ":INIT:CONT ON" in fake.writes
    assert all(not w.startswith(":TRIG:SOUR EXT") for w in fake.writes)


def test_source_hygiene_no_sample_names_no_pymeasure_ascii_sources():
    for path in (gui.__file__, script.__file__):
        with open(path, "rb") as fh:
            src = fh.read()
        text = src.decode("utf-8")
        assert "pymeasure" not in text
        assert "Swastika" not in text
        assert "V_ABS_MAX = 20.0" in text
    for m in MODULES:
        for cmd in (":FUNC:SMON:VDC ON", ":FUNC:SMON:IDC ON", ":FETC:SMON:VDC?", ":FETC:SMON:IDC?"):
            assert cmd in inspect.getsource(m.E4980ALink), cmd


if __name__ == "__main__":
    run_module_tests(dict(globals()))
