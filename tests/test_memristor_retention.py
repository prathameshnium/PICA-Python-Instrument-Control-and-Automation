"""Memristor_Retention_K2400_GUI.py: schedules, polarity, averaging and the
worker run paths against the fake 2400: two files per state with the
write_file link, time since the write, constant-bias and intermittent
schemes, Stop, write-leg turnaround, bus-error retry keeping the clock,
and GUI validation.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memristor_test_support import (  # noqa: E402
    FakeK2400, analysis, comment_lines, end_reason, read_file, retention, run_module_tests, _gui_shell,
    run_retention)

def test_retention_schedules():
    s = retention.log_schedule(3, 0.5, 100)
    assert [round(x, 6) for x in s] == [round(10 ** (k / 3), 6) for k in range(0, 7)]
    s10 = retention.log_schedule(10, 100, 1000)
    assert len(s10) == 10 and s10[0] > 100 and abs(s10[-1] - 1000) < 1e-6
    assert retention.log_schedule(10, 50, 10) == []
    assert retention.linear_schedule(10, 25, 60) == [30, 40, 50, 60]
    assert retention.linear_schedule(10, 0, 30) == [10, 20, 30]
    assert retention.next_instant([1, 2, 5], 2) == 5 and retention.next_instant([1, 2], 5) is None
    assert retention.write_polarity("LRS") == 1 and retention.write_polarity("HRS") == -1
    assert retention.write_polarity("control") == 1 and retention.write_polarity("HRS", swap=True) == 1
    try:
        retention.log_schedule(0, 1, 10)
        assert False
    except ValueError:
        pass


def test_retention_constant_bias_writes_two_files_and_logs_since_the_write():
    """Acceptance test 4 (constant bias, log spacing) on the fake."""
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, wpath, rpath = run_retention(FakeK2400(1e6), d)
        assert worker.reason == "max_time"
        wh, wcols, wrows = read_file(wpath)
        rh, rcols, rrows = read_file(rpath)
        assert list(wcols) == list(retention.WRITE_COLUMNS) and list(rcols) == list(retention.READS_COLUMNS)
        assert end_reason(wh) == "max_time" and end_reason(rh) == "max_time"
        blocks = analysis.header_blocks(rh)
        assert blocks["protocol"]["write_file"] == os.path.basename(wpath)
        assert blocks["protocol"]["state"] == "LRS" and blocks["protocol"]["module"] == "retention_reads"
        assert analysis.header_blocks(wh)["protocol"]["module"] == "retention_write"
        assert [r["V_set_V"] for r in wrows] == [0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0]
        assert [r["segment"] for r in wrows] == [1] * 6 + [2] * 5
        phases = [r["phase"] for r in rrows]
        assert phases[:3] == ["settle"] * 3 and "recover" in phases and "read" in phases and "soak" not in phases
        assert all(r["t_since_write_s"] == "" for r in rrows if r["phase"] == "settle")
        rec = [r for r in rrows if r["phase"] == "recover"]
        assert all(r["V_set_V"] == 0.0 for r in rec)
        reads = [r for r in rrows if r["phase"] == "read"]
        assert all(r["V_set_V"] == 0.1 for r in reads)
        assert all(abs(r["R_ohm"] - 1e6) < 1 for r in reads)
        t = [r["t_since_write_s"] for r in reads]
        assert all(b > a for a, b in zip(t, t[1:]))
        assert any(r["n_avg"] == 3 for r in reads) and any(r["n_avg"] == 1 for r in reads)
        assert t[-1] <= 1.2 + 0.3
        w = worker.link.fake.writes
        assert ":SOUR:DEL 0" in w and w[-1] == ":OUTP OFF"
        assert any(m[0] == "status" and "left_s" in m[1] for m in msgs)


def test_retention_intermittent_linear_sources_zero_between_instants():
    with tempfile.TemporaryDirectory() as d:
        worker, _, wpath, rpath = run_retention(FakeK2400(1e6), d, scheme=retention.SCHEME_INTERMITTENT,
                                                spacing=retention.SPACING_LINEAR, dt=0.4, t_stop=1.5, t_rec=0.1,
                                                t_soak=0.1, state="HRS", polarity=-1)
        assert worker.reason == "max_time"
        _, _, wrows = read_file(wpath)
        assert wrows[5]["V_set_V"] == -5.0          # HRS = RESET = negative write
        _, _, rrows = read_file(rpath)
        phases = [r["phase"] for r in rrows]
        assert "soak" in phases and "read" in phases and "recover" in phases
        reads = [r for r in rrows if r["phase"] == "read"]
        assert len(reads) >= 2 and all(r["n_avg"] == 3 for r in reads)
        for a, b in zip(reads, reads[1:]):
            assert b["t_since_write_s"] - a["t_since_write_s"] >= 0.3
        soaks = [r for r in rrows if r["phase"] == "soak"]
        assert all(r["V_set_V"] == 0.1 for r in soaks)
        recov = [r for r in rrows if r["phase"] == "recover"]
        assert all(r["V_set_V"] == 0.0 for r in recov)


def test_retention_stop_button_and_write_leg_turnaround():
    with tempfile.TemporaryDirectory() as d:
        worker, _, wpath, rpath = run_retention(FakeK2400(1e5), d, stop_after=0.5, t_stop=30, vmax_write=5, step_v=0.5)
        assert worker.reason == "stop_button"
        _, _, wrows = read_file(wpath)
        assert any("turnaround" in r["event"] for r in wrows)      # 1e5 ohm trips at 1 V
        assert max(r["V_set_V"] for r in wrows) < 5.0
        assert wrows[-1]["V_set_V"] == 0.0
        rh, _, _ = read_file(rpath)
        assert end_reason(rh) == "stop_button"


def test_retention_bus_error_keeps_the_clock_since_the_write():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeK2400(1e6, fail_reads={20})
        worker, _, wpath, rpath = run_retention(fake, d, t_stop=1.0)
        assert worker.reason == "max_time"
        assert comment_lines(rpath, "RETRY") and comment_lines(wpath, "RETRY")
        _, _, rrows = read_file(rpath)
        t = [r["t_since_write_s"] for r in rrows if r["phase"] == "read"]
        assert all(b > a for a, b in zip(t, t[1:]))


def test_average_readings_or_flags_and_averages_values():
    R = retention.Reading
    v, i, flag, trip, stat, n = retention.average_readings([R(1.0, 1e-6, 0, 0, 0), R(3.0, 3e-6, 0, 8, 1)])
    assert v == 2.0 and abs(i - 2e-6) < 1e-15 and flag == 1 and trip == 1 and stat == 8 and n == 2
    v, i, flag, trip, stat, n = retention.average_readings([R(float("nan"), 1e-6, 0, 0, None)])
    assert v is None and trip is None


def test_retention_open_analysis_preloads_the_reads_file_of_the_v11_name():
    base = dict(sample_name="VCO01")
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(retention.RetentionGUI, retention, base, d)
        ts = retention.timestamp_for_name()
        gui.current_files = [retention.run_file_path(d, "VCO01", "retention_LRS_write", ts),
                             retention.run_file_path(d, "VCO01", "retention_LRS_reads", ts)]
        assert [os.path.basename(f) for f in gui.analysis_files()] == [f"VCO01_{ts}_retention_LRS_reads.csv"]
        gui.companion_path = "other.csv"
        assert gui.analysis_files()[-1] == "other.csv"


def test_retention_gui_warns_when_intermittent_spacing_is_finer_than_the_soak():
    base = dict(sample_name="VCO01", visa_address="GPIB1::24::INSTR", protection="20", state="HRS", swap=False, vmax_write="5",
                i_cc_write_uA="10", step_v="0.1", dwell_s="0.1", n_confirm="3", v_read="0.1", t_rec="60",
                t_soak="60", t_stop="10000", scheme=retention.SCHEME_INTERMITTENT, spacing=retention.SPACING_LINEAR,
                points_per_decade="10", dt="10", n_avg="3")
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(retention.RetentionGUI, retention, base, d)
        p = gui.collect_params()
        assert p["polarity"] == -1 and "skipped" in p["_warning"]
        gui = _gui_shell(retention.RetentionGUI, retention, dict(base, swap=True, scheme=retention.SCHEME_CONSTANT), d)
        p = gui.collect_params()
        assert p["polarity"] == 1 and p["_warning"] == ""

if __name__ == "__main__":
    run_module_tests(globals())
