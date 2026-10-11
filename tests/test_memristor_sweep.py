"""Memristor_Sweep_K2400_GUI.py: the common block (pure functions, RunFile,
K2400Link) that every memristor module carries, and the sweep worker run
paths against the fake 2400: compliance confirmation and turnaround
(acceptance test 3), separate negative-half compliance, the ladder end
rule, Stop, OVP, configuration refusal, bus-error retry with RETRY and
RECONNECT lines, Stop during a retry gap, GUI validation, source hygiene
(ASCII only, common block byte-identical) and the launcher registry.
"""

import math
import os
import queue
import re
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memristor_test_support import (  # noqa: E402
    FakeK2400, MODULES, K2400_DIR, UTILS_DIR, analysis, comment_lines, end_reason, make_link, read_file,
    run_module_tests, run_worker, sweep, _gui_shell, sweep_params, run_sweep)

def test_half_leg_and_return_leg_hit_vmax_exactly_and_avoid_duplicates():
    assert sweep.half_leg(1.0, 0.3) == [0.0, 0.3, 0.6, 0.8999999999999999, 1.0]
    assert sweep.half_leg(0.5, 0.1)[-1] == 0.5
    assert len(sweep.half_leg(5, 0.1)) == 51
    assert sweep.return_leg(0.5, 0.1) == [0.4, 0.30000000000000004, 0.2, 0.1, 0.0] or \
        [round(v, 9) for v in sweep.return_leg(0.5, 0.1)] == [0.4, 0.3, 0.2, 0.1, 0.0]
    assert sweep.return_leg(-0.3, 0.1)[0] < 0 and sweep.return_leg(-0.3, 0.1)[-1] == 0.0
    assert sweep.return_leg(1.0, 0.3)[0] < 1.0


def test_build_loop_shapes_start_and_end_at_zero_without_duplicate_turns():
    for shape in sweep.SHAPES:
        segs = sweep.build_loop(shape, 5, 3, 1)
        pts = [v for s in segs for v in s["points"]]
        assert pts[0] == 0.0 and pts[-1] == 0.0
        for a, b in zip(pts, pts[1:]):
            assert a != b, f"duplicate point {a} in {shape}"
    bip = sweep.build_loop(sweep.SHAPE_BIPOLAR_PLUS, 5, 3, 1)
    assert [s["segment"] for s in bip] == [1, 2, 3, 4]
    assert bip[0]["points"][-1] == 5 and bip[2]["points"][-1] == -3
    assert bip[1]["points"][-1] == 0.0 and bip[2]["points"][0] == -1.0
    assert sweep.loop_point_count(sweep.SHAPE_BIPOLAR_PLUS, 5, 5, 0.1) == 201
    uni = sweep.build_loop(sweep.SHAPE_UNIPOLAR_MINUS, 5, 3, 1)
    assert len(uni) == 2 and uni[0]["points"][-1] == -3
    minus_first = sweep.build_loop(sweep.SHAPE_BIPOLAR_MINUS, 5, 3, 1)
    assert minus_first[0]["sign"] == -1 and minus_first[2]["sign"] == 1


def test_parse_ladder_and_run_plan():
    assert sweep.parse_ladder("5, 10; 20\n40") == [5.0, 10.0, 20.0, 40.0]
    for bad in ("", "a", "0", "-5"):
        try:
            sweep.parse_ladder(bad)
            assert False, bad
        except ValueError:
            pass
    plan = sweep.run_plan(sweep.SHAPE_LADDER, 1, 1, 0.1, 3, [5, 10])
    assert plan == [(1, 5, 5), (2, 10, 10)]
    assert sweep.run_plan(sweep.SHAPE_UNIPOLAR_PLUS, 2, 3, 0.1, 2, []) == [(1, 2, 3), (2, 2, 3)]


def test_trip_tracker_confirms_after_n_points_and_rearms_on_false_alarm():
    t = sweep.TripTracker(3)
    assert t.observe(0) == ("", False)
    assert t.observe(1) == ("trip_first", False)
    assert t.observe(1) == ("", False)
    assert t.observe(1) == ("", False)
    assert t.observe(1) == ("trip_confirmed;turnaround", True)
    assert t.observe(1) == ("", False)
    t = sweep.TripTracker(2)
    assert t.observe(1) == ("trip_first", False)
    assert t.observe(0) == ("trip_unconfirmed", False)
    assert t.observe(1) == ("trip_first", False)
    assert t.observe(1) == ("", False)
    assert t.observe(1) == ("trip_confirmed;turnaround", True)
    t = sweep.TripTracker(0)
    assert t.observe(1) == ("trip_confirmed;turnaround", True)
    t = sweep.TripTracker(3)
    t.observe(1)
    t.observe(1)
    assert t.close_segment() == "trip_confirmed"
    t = sweep.TripTracker(3)
    t.observe(0)
    assert t.close_segment() == ""


def test_status_decoding_and_read_reply_parsing():
    assert sweep.decode_status(8) == (1, 0)
    assert sweep.decode_status(65536) == (1, 0)
    assert sweep.decode_status(16) == (0, 1)
    assert sweep.decode_status(0) == (0, 0)
    assert sweep.decode_status("garbage") == (0, 0)
    v, i, t, stat = sweep.parse_read_reply("+1.000000E+00,+9.910000E+37,+1.2E+00,+8.0000E+00")
    assert v == 1.0 and math.isnan(i) and stat == 8
    for bad in ("1,2,3", "a,b,c,d"):
        try:
            sweep.parse_read_reply(bad)
            assert False
        except sweep.BusError:
            pass
    assert sweep.resistance_ohm(1.0, 0.0) is None
    assert sweep.resistance_ohm(float("nan"), 1e-6) is None
    assert sweep.resistance_ohm(1.0, 1e-6) == 1e6


def test_protection_checks_follow_the_manual_list():
    assert sweep.parse_protection("20") == 20.0
    assert sweep.parse_protection("210") == 210.0
    assert sweep.parse_protection("NONE") == 210.0   # legacy alias
    try:
        sweep.parse_protection("25")
        assert False
    except ValueError:
        pass
    assert sweep.protection_command_value(None) == 210.0
    assert sweep.protection_command_value(210.0) == 210.0
    ok, msg = sweep.check_vmax_against_protection(25, 20)
    assert not ok and "never-exceed" in msg
    ok, msg = sweep.check_vmax_against_protection(19.5, 20)
    assert ok and "clamp" in msg
    ok, msg = sweep.check_vmax_against_protection(5, 20)
    assert ok and msg == ""
    ok, msg = sweep.check_vmax_against_protection(150, None)
    assert ok and msg == ""
    ok, _ = sweep.check_vmax_against_protection(250, None)
    assert not ok
    assert sweep.source_range_for(5) == 20 and sweep.source_range_for(2.05) == 2 and sweep.source_range_for(100) == 200
    assert sweep.measure_range_for(1e-5) == 1e-5 and sweep.measure_range_for(1e-9) == 1e-6
    for bad in (5e-10, 2.0):
        try:
            sweep.validate_i_cc(bad)
            assert False
        except ValueError:
            pass


def test_formatting_never_fabricates_values_and_stays_ascii():
    assert sweep.fmt_num(None) == ""
    assert sweep.fmt_num(float("nan")) == ""
    assert sweep.fmt_num(3) == "3"
    assert sweep.fmt_num(1e-5).startswith("1.0000")
    assert sweep.fmt_cell("a|b\nc") == "a/b c"
    assert sweep.header_value("x \u2192 y \u00b5A") == "x -> y uA"
    assert sweep.ascii_text("\u03a9") == "Ohm"
    assert sweep.safe_stem("VCO 01/a") == "VCO_01_a"
    assert sweep.format_duration(3725) == "1 h 02 min"
    assert sweep.format_duration(65) == "1 min 05 s"
    assert sweep.format_duration(float("nan")) == "--"


def test_time_estimate_matches_the_protocol_formula():
    per = sweep.point_time_s(0.1)
    assert abs(per - (0.1 + 0.02 + 0.01 + 0.01)) < 1e-12
    assert abs(sweep.sweep_time_estimate_s(100, 0.1) - (100 * per + 10 * 0.1)) < 1e-9


def test_runfile_writes_header_blocks_rows_comments_and_end_line():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.csv")
        f = sweep.RunFile(path, "sweep", [("sample", [("name", "VCO01"), ("area_cm2", "")]),
                                           ("protocol", [("module", "sweep"), ("notes", "a|b")])], ("a", "b", "c"))
        f.write_row([1.0, None, "txt"])
        f.comment("RETRY n=1 | error=boom")
        f.write_row([float("nan"), 2, 3])
        f.end("completed")
        f.end("twice")  # ignored
        text = open(path, encoding="utf-8").read()
        lines = text.splitlines()
        assert lines[0].startswith("# PICA memristor sweep v")
        assert "# [sample] name=VCO01 | area_cm2=" in lines[1]
        assert "notes=a/b" in lines[2]
        assert lines[3] == "# columns: a,b,c"
        assert lines[4] == "a,b,c"
        assert lines[5].startswith("1.00000000e+00,,txt")
        assert lines[6].startswith("# RETRY n=1")
        assert lines[7] == ",2,3"
        assert lines[8].startswith("# END reason=completed | points=2 | end_time=")
        assert text.count("# END") == 1
        header, columns, rows = analysis.parse_pica_csv(path)
        assert columns == ["a", "b", "c"] and rows[0]["b"] == "" and rows[1]["a"] == ""


def test_link_startup_order_follows_a6_and_remembers_config():
    fake = FakeK2400()
    link = make_link(sweep, fake)
    link.open()
    assert "MODEL 2400" in link.reset_and_identify()
    link.configure(20, 20.0, 1e-5, 0.1)
    w = fake.writes
    assert w[:2] == ["*RST", "*CLS"]
    assert ":SENS:FUNC:CONC ON" in w and ':SENS:FUNC "VOLT","CURR"' in w
    assert ":FORM:ELEM VOLT,CURR,TIME,STAT" in w
    assert ":SOUR:VOLT:RANG 20" in w and ":SOUR:VOLT:PROT 20" in w
    i_rang, i_prot, i_auto = (w.index(":SENS:CURR:RANG 1e-05"), w.index(":SENS:CURR:PROT 1e-05"),
                              w.index(":SENS:CURR:RANG:AUTO ON"))
    assert i_rang < i_prot < i_auto
    assert ":SENS:CURR:NPLC 1" in w and ":SOUR:DEL 0.1" in w
    assert ":OUTP:SMOD NORM" in w and ":SOUR:CLE:AUTO OFF" in w
    assert ":ROUT:TERM FRON" in w and ":SYST:RSEN OFF" in w
    link.configure(20, None, 1e-5, 0.1)
    assert ":SOUR:VOLT:PROT 210" in fake.writes
    rd = link.read_point(True)
    assert rd.compliance_flag == 0 and rd.trip_query == 0 and rd.v == 0.0
    fake.v = 20.0
    fake.icc = 1e-5
    rd = link.read_point(True)
    assert rd.compliance_flag == 1 and rd.trip_query == 1 and abs(rd.i - 1e-5) < 1e-12
    rd = link.read_point(False)
    assert rd.trip_query is None


def test_link_refuses_a_compliance_readback_mismatch():
    fake = FakeK2400(prot_readback=2e-5)
    link = make_link(sweep, fake)
    link.open()
    try:
        link.set_compliance(1e-5)
        assert False
    except sweep.ConfigMismatch:
        pass
    # within the 5 % band (instrument rounding) is accepted and remembered
    fake = FakeK2400(prot_readback=1.03e-5)
    link = make_link(sweep, fake)
    link.open()
    link.set_compliance(1e-5)
    assert link.i_cc == 1e-5 and link.i_cc_readback == 1.03e-5
    assert sweep.COMPLIANCE_READBACK_TOLERANCE == 0.05


def test_point_line_is_compact_and_skips_empty_fields():
    row = {"t_s": 1.23456, "loop": 2, "segment": 3, "vmax_V": 20.0, "V_set_V": 1.2, "V_meas_V": 1.19876,
           "I_A": 3.2e-6, "R_ohm": 374612.5, "compliance_flag": 0, "trip_query": None, "status_bits": 0,
           "event": "", "phase": "sweep"}
    assert sweep.point_line("sweep", row) == "sweep L=2 S=3 ph=sweep t=1.235 Vset=1.2 Vmeas=1.199 I=3.2e-06 R=3.746e+05 cc=0"
    raw = {"cycle": 7, "leg": "set", "loop": 7, "segment": 1, "V_set_V": 5.0, "V_meas_V": 4.99, "I_A": 1e-5,
           "R_ohm": 4.99e5, "compliance_flag": 1, "event": "trip_confirmed", "phase": "sweep"}
    line = sweep.point_line("raw", raw)
    assert line.startswith("raw C=7 leg=set S=1") and " L=" not in line and line.endswith("cc=1 ev=trip_confirmed")
    reads = {"t_since_write_s": 12.5, "t_wall_s": 40.0, "phase": "read", "V_set_V": 0.2, "V_meas_V": 0.2,
             "I_A": float("nan"), "R_ohm": None, "n_avg": 5, "compliance_flag": 0}
    assert sweep.point_line("reads", reads) == "reads ph=read tsw=12.5 Vset=0.2 Vmeas=0.2 I=nan n=5 cc=0"


def test_link_wraps_transport_errors_as_bus_errors_and_ramps_to_zero():
    fake = FakeK2400(fail_forever=True)
    link = make_link(sweep, fake)
    link.open()
    try:
        link.read_point()
        assert False
    except sweep.BusError:
        pass
    link.source(0.5)
    link.ramp_to_zero(0.1)
    volts = [float(c.split()[1]) for c in fake.writes if c.startswith(":SOUR:VOLT ")]
    assert volts[-1] == 0.0 and all(a > b for a, b in zip(volts[1:], volts[2:]) if a != 0.0)


def test_sweep_on_1_megohm_trips_at_10v_confirms_turns_around_and_measures_the_rest():
    """Acceptance test 3 of the protocol, on the fake."""
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, path = run_sweep(FakeK2400(1e6), d)
        assert worker.reason == "completed"
        header, columns, rows = read_file(path)
        assert list(columns) == list(sweep.SWEEP_COLUMNS)
        assert end_reason(header) == "completed"
        settle = [r for r in rows if r["phase"] == "settle"]
        assert len(settle) == 3 and all(r["V_set_V"] == 0.0 for r in settle)
        seg1 = [r for r in rows if r["segment"] == 1]
        v1 = [r["V_set_V"] for r in seg1]
        assert v1 == [float(k) for k in range(0, 15)], v1       # 0..14: trip at 11, confirm 12, 13, 14
        events = [r["event"] for r in seg1]
        assert events[11] == "trip_first" and events[14] == "trip_confirmed;turnaround"
        assert all(r["compliance_flag"] == r["trip_query"] for r in rows if r["trip_query"] != "")
        assert [r["compliance_flag"] for r in seg1] == [0] * 11 + [1] * 4
        seg2 = [r["V_set_V"] for r in rows if r["segment"] == 2]
        assert seg2 == [float(k) for k in range(13, -1, -1)]
        seg3 = [r["V_set_V"] for r in rows if r["segment"] == 3]
        assert seg3[0] == -1.0 and seg3[-1] == -14.0
        seg4 = [r["V_set_V"] for r in rows if r["segment"] == 4]
        assert seg4[-1] == 0.0 and seg4[0] == -13.0
        for r in rows:
            if r["phase"] == "sweep" and r["I_A"] != 0:
                assert abs(r["R_ohm"] - 1e6) / 1e6 < 1e-6
        assert all(r["vmax_V"] == 20.0 for r in rows if r["phase"] == "sweep")
        writes = worker.link.fake.writes
        assert writes[-1] == ":OUTP OFF"
        assert writes[-2] == ":SOUR:VOLT 0"
        assert worker.link.fake.closed
        assert any(m[0] == "done" and m[1] == "finished" for m in msgs)


def test_sweep_without_a_trip_runs_to_vmax_with_all_flags_zero():
    with tempfile.TemporaryDirectory() as d:
        worker, _, path = run_sweep(FakeK2400(1e9), d, vmax_plus=5.0, vmax_minus=5.0, step_v=0.5)
        _, _, rows = read_file(path)
        sw = [r for r in rows if r["phase"] == "sweep"]
        assert len(sw) == 41 and all(r["compliance_flag"] == 0 for r in sw)
        assert max(r["V_set_V"] for r in sw) == 5.0 and min(r["V_set_V"] for r in sw) == -5.0
        assert all(r["event"] == "" for r in sw)


def test_sweep_negative_half_uses_its_own_compliance():
    with tempfile.TemporaryDirectory() as d:
        worker, _, path = run_sweep(FakeK2400(1e6), d, i_cc_minus=2e-6, vmax_plus=5, vmax_minus=5, step_v=0.5)
        _, _, rows = read_file(path)
        neg = [r for r in rows if r["segment"] == 3]
        assert any(r["compliance_flag"] == 1 for r in neg)
        assert all(r["compliance_flag"] == 0 for r in rows if r["segment"] == 1)
        w = worker.link.fake.writes
        assert ":SENS:CURR:PROT 2e-06" in w and w.index(":SENS:CURR:PROT 2e-06") > w.index(":SENS:CURR:PROT 1e-05")


def test_sweep_ladder_stops_after_the_entry_with_a_confirmed_trip():
    with tempfile.TemporaryDirectory() as d:
        worker, _, path = run_sweep(FakeK2400(1e6), d, shape=sweep.SHAPE_LADDER, ladder=[5.0, 15.0, 30.0],
                                    protection_v=None)
        _, _, rows = read_file(path)
        loops = sorted({int(r["loop"]) for r in rows if r["phase"] == "sweep"})
        assert loops == [1, 2]
        assert {r["vmax_V"] for r in rows if r["loop"] == 2} == {15.0}
        l2 = [r for r in rows if r["loop"] == 2]
        assert any("turnaround" in r["event"] for r in l2) and l2[-1]["segment"] == 4


def test_sweep_stop_button_finishes_the_point_and_ramps_down():
    class SlowFake(FakeK2400):
        def query(self, cmd):
            if cmd.strip().upper() == ":READ?":
                time.sleep(0.02)
            return FakeK2400.query(self, cmd)
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, path = run_sweep(SlowFake(1e9), d, stop_after=0.3, vmax_plus=20, vmax_minus=20, step_v=0.1)
        assert worker.reason == "stop_button"
        header, _, rows = read_file(path)
        assert end_reason(header) == "stop_button"
        assert 3 < len(rows) < 400
        w = worker.link.fake.writes
        assert w[-1] == ":OUTP OFF" and w[-2] == ":SOUR:VOLT 0"
        assert any(m[0] == "done" and m[1] == "stopped" for m in msgs)


def test_sweep_ovp_bit_stops_the_run():
    with tempfile.TemporaryDirectory() as d:
        worker, _, path = run_sweep(FakeK2400(1e9, ovp_at=3.0), d, vmax_plus=5, vmax_minus=5, step_v=1.0)
        assert worker.reason == "ovp_trip"
        header, _, rows = read_file(path)
        assert end_reason(header) == "ovp_trip"
        assert rows[-1]["event"] == "ovp" and rows[-1]["V_set_V"] == 3.0
        assert worker.link.fake.writes[-1] == ":OUTP OFF"


def test_sweep_config_mismatch_is_an_exception_end_with_output_off():
    with tempfile.TemporaryDirectory() as d:
        p = sweep_params(d)
        fake = FakeK2400(prot_readback=5e-5)
        link = make_link(sweep, fake)
        worker = sweep.SweepWorker(p, link, threading.Event(), queue.Queue(), sleep=lambda s: None)
        worker.gui_version = "test"
        run_worker(worker)
        assert worker.reason.startswith("exception:") and "compliance read-back" in worker.reason
        assert fake.writes[-1] == ":OUTP OFF"
        assert not [f for f in os.listdir(d) if f.endswith(".csv")]   # refused before the file was opened


def test_sweep_bus_error_is_retried_and_the_point_repeated():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeK2400(1e9, fail_reads={5, 6})
        worker, _, path = run_sweep(fake, d, vmax_plus=3, vmax_minus=3, step_v=1.0)
        assert worker.reason == "completed"
        retries = comment_lines(path, "RETRY")
        reconnects = comment_lines(path, "RECONNECT")
        assert len(retries) == 2 and "n=1" in retries[0] and "n=2" in retries[1]
        assert "error=" in retries[0] and "VI_ERROR_TMO" in retries[0]
        assert len(reconnects) == 1 and "after" in reconnects[0]      # one line per recovered gap
        assert " | t_s=" in retries[0] and " | what=" in retries[0]      # fields separated by ' | ' like every header line
        assert " | " in reconnects[0] and "/" not in reconnects[0].split("after")[1].split("|")[0]
        _, _, rows = read_file(path)
        sw = [r["V_set_V"] for r in rows if r["phase"] == "sweep"]
        assert sw == [0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0, -2.0, -1.0, 0.0]
        w = fake.writes
        assert w.count("*RST") == 1          # reconnect re-asserts the configuration, it does not reset
        assert w.count(":SOUR:VOLT:PROT 20") >= 2


def test_stop_during_a_retry_gap_ends_with_its_own_reason():
    fail = FakeK2400(1e9, fail_forever=True)
    with tempfile.TemporaryDirectory() as d:
        p = sweep_params(d, vmax_plus=3, vmax_minus=3, step_v=1.0)
        stop = threading.Event()
        link = make_link(sweep, fail)
        worker = sweep.SweepWorker(p, link, stop, queue.Queue(), sleep=lambda s: None)
        worker.gui_version = "test"
        sweep.RETRY_SCHEDULE_S = (0.05, 0.05)
        try:
            msgs = run_worker(worker, stop_after=0.4, stop_event=stop)
        finally:
            sweep.RETRY_SCHEDULE_S = (0.01, 0.01, 0.02)
        assert worker.reason == "stop_button_during_retry"
        files = [f for f in os.listdir(d) if "_sweep" in f]
        header, _, _ = read_file(os.path.join(d, files[0]))
        assert end_reason(header) == "stop_button_during_retry"
        assert len(comment_lines(os.path.join(d, files[0]), "RETRY")) >= 2
        assert any("reconnecting" in str(m[1].get("state", "")) for m in msgs if m[0] == "status")
        assert fail.writes[-1] == ":OUTP OFF"


def test_sweep_file_name_is_sample_timestamp_kind_and_a_rerun_never_overwrites():
    with tempfile.TemporaryDirectory() as d:
        ts = sweep.timestamp_for_name()
        first = sweep.run_file_path(d, "VCO01", "sweep", ts)
        assert os.path.basename(first) == f"VCO01_{ts}_sweep.csv"
        assert re.fullmatch(r"VCO01_\d{8}_\d{6}_sweep\.csv", os.path.basename(first))
        open(first, "w").close()
        second = sweep.run_file_path(d, "VCO01", "sweep", ts)
        assert os.path.basename(second) == f"VCO01_{ts}_sweep_1.csv"
        open(second, "w").close()
        assert os.path.basename(sweep.run_file_path(d, "VCO01", "sweep", ts)) == f"VCO01_{ts}_sweep_2.csv"


def test_sweep_gui_refuses_vmax_above_protection_and_bad_ladder():
    base = dict(sample_name="VCO01", visa_address="GPIB1::24::INSTR", protection="20", shape=sweep.SHAPE_BIPOLAR_PLUS, vmax_plus="5",
                vmax_minus="5", step_v="0.1", dwell_s="0.1", i_cc_plus_uA="10", i_cc_minus_uA="10", loops="1",
                ladder="", n_confirm="3")
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(sweep.SweepGUI, sweep, base, d)
        p = gui.collect_params()
        assert p["i_cc_plus"] == 1e-5 and p["protection_v"] == 20.0 and p["_warning"] == ""
        gui = _gui_shell(sweep.SweepGUI, sweep, dict(base, vmax_plus="25"), d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "never-exceed" in str(exc)
        gui = _gui_shell(sweep.SweepGUI, sweep, dict(base, vmax_plus="19.5"), d)
        assert "clamp" in gui.collect_params()["_warning"]
        gui = _gui_shell(sweep.SweepGUI, sweep, dict(base, protection="210", vmax_plus="150"), d)
        assert gui.collect_params()["protection_v"] == 210.0
        gui = _gui_shell(sweep.SweepGUI, sweep, dict(base, shape=sweep.SHAPE_LADDER, ladder=""), d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "ladder" in str(exc).lower()
        gui = _gui_shell(sweep.SweepGUI, sweep, dict(base, sample_name=""), d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "mandatory" in str(exc)
        gui = _gui_shell(sweep.SweepGUI, sweep, dict(base, i_cc_plus_uA="0.0001"), d)
        try:
            gui.collect_params()
            assert False
        except ValueError:
            pass


def test_modules_are_ascii_only_and_share_the_common_block_verbatim():
    paths = [os.path.join(K2400_DIR, n) for n in ("Memristor_Sweep_K2400_GUI.py", "Memristor_Retention_K2400_GUI.py",
                                                  "Memristor_Endurance_K2400_GUI.py")]
    paths.append(os.path.join(UTILS_DIR, "Memristor_Analysis_GUI.py"))
    blocks = []
    for p in paths:
        text = open(p, encoding="utf-8").read()
        text.encode("ascii")
        assert "\u2014" not in text
        if "COMMON BLOCK" in text:
            start = text.index("# COMMON BLOCK")
            end = text.index("# END OF COMMON BLOCK")
            blocks.append(text[start:end])
    assert len(blocks) == 3 and blocks[0] == blocks[1] == blocks[2]
    for mod in MODULES:
        assert mod.DEFAULT_GPIB_ADDRESS == "GPIB1::24::INSTR"
        assert mod.PROTECTION_CHOICES == ("20", "40", "60", "80", "100", "120", "160", "210")


def test_registry_entries_exist():
    from pica import cli, main, main_v2
    for name in ("pica.keithley.k2400.Memristor_Sweep_K2400_GUI", "pica.keithley.k2400.Memristor_Retention_K2400_GUI",
                 "pica.keithley.k2400.Memristor_Endurance_K2400_GUI", "pica.utils.Memristor_Analysis_GUI"):
        assert name in cli.ALL_GUI_MODULES
    paths = main.PICALauncherApp.SCRIPT_PATHS
    for key in ("K2400 Memristor Sweep", "K2400 Memristor Retention", "K2400 Memristor Endurance", "Memristor Analysis"):
        assert key in paths and os.path.exists(paths[key]), key
    keys = {key for cat in main_v2.CATALOG for _l, key, _f in cat["modules"]}
    assert {"K2400 Memristor Sweep", "K2400 Memristor Retention", "K2400 Memristor Endurance"} <= keys
    from pica import module_info
    assert "Memristor Analysis" in {k for _l, k, _g in module_info.UTILITY_TOOLS}
    for key in ("K2400 Memristor Sweep", "K2400 Memristor Retention", "K2400 Memristor Endurance", "Memristor Analysis"):
        assert key in module_info.MODULE_INFO, key
    quick = {p["key"] for cat in main_v2.QUICK_CATALOG for m in cat["modules"] for p in m["protocols"]}
    assert "K2400 Memristor Retention" in quick

if __name__ == "__main__":
    run_module_tests(globals())
