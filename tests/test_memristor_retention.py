"""Memristor_Retention_K2400_GUI.py: schedules, polarity, averaging and the
worker run paths against the fake 2400: two files per state with the
write_file link, time since the write, constant-bias and intermittent
schemes, Stop, write-leg turnaround, bus-error retry keeping the clock,
and GUI validation.
"""

import os
import queue
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memristor_test_support import (  # noqa: E402
    FakeK2400, analysis, assert_data_only, comment_lines, end_reason, log_events, make_link, read_file, retention, retention_params,
    run_module_tests, run_retention, run_worker, _gui_shell)

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
        assert end_reason(wpath) == "max_time" and end_reason(rpath) == "max_time"
        assert_data_only(wpath)
        assert_data_only(rpath)
        events = log_events(rpath)
        assert analysis.header_blocks(wh)["protocol"]["log_file"].endswith("_retention_LRS_log.txt")
        assert analysis.header_blocks(wh)["protocol"]["log_file"] == analysis.header_blocks(rh)["protocol"]["log_file"]
        assert events[0] == f"FILES data_file={os.path.basename(wpath)} | data_file={os.path.basename(rpath)}"
        assert len(comment_lines(rpath, "WRITE_END t_s=")) == 1
        assert [e.split(" | ")[0] for e in events if e.startswith("END ")] == ["END reason=max_time"] * 2
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
        assert any(r["event_code"] == 4 for r in wrows)      # 1e5 ohm trips at 1 V, confirmed + turnaround
        assert max(r["V_set_V"] for r in wrows) < 5.0
        assert wrows[-1]["V_set_V"] == 0.0
        assert end_reason(rpath) == "stop_button" and end_reason(wpath) == "stop_button"
        assert_data_only(wpath)
        assert_data_only(rpath)


def test_retention_bus_error_keeps_the_clock_since_the_write():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeK2400(1e6, fail_reads={20})
        worker, _, wpath, rpath = run_retention(fake, d, t_stop=1.0)
        assert worker.reason == "max_time"
        assert comment_lines(rpath, "RETRY") and comment_lines(rpath, "RECONNECT")
        assert comment_lines(wpath, "RETRY") == comment_lines(rpath, "RETRY")     # one log per run
        assert_data_only(wpath)
        assert_data_only(rpath)
        _, _, rrows = read_file(rpath)
        t = [r["t_since_write_s"] for r in rrows if r["phase"] == "read"]
        assert all(b > a for a, b in zip(t, t[1:]))


def test_average_readings_or_flags_and_averages_values():
    R = retention.Reading
    v, i, flag, trip, stat, n = retention.average_readings([R(1.0, 1e-6, 0, 0, 0), R(3.0, 3e-6, 0, 8, 1)])
    assert v == 2.0 and abs(i - 2e-6) < 1e-15 and flag == 1 and trip == 1 and stat == 8 and n == 2
    v, i, flag, trip, stat, n = retention.average_readings([R(float("nan"), 1e-6, 0, 0, None)])
    assert v is None and trip is None




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


# -- UI round 1 (K's ruling of 12 Oct 2026) ------------------------------

GUI_BASE = dict(sample_name="VCO01", visa_address="GPIB1::24::INSTR", protection="20", state="LRS", swap=False,
                vmax_write="5", i_cc_write_uA="10", step_v="0.1", dwell_s="0.1", n_confirm="3", v_read="0.1",
                t_rec="60", t_soak="60", t_stop="10000", scheme=retention.SCHEME_CONSTANT,
                spacing=retention.SPACING_LOG, points_per_decade="10", dt="10", n_avg="3")


def _collect(folder, **values):
    return _gui_shell(retention.RetentionGUI, retention, dict(GUI_BASE, **values), folder).collect_params()


def test_write_polarity_table():
    expected = {("LRS", False): 1, ("LRS", True): -1, ("HRS", False): -1, ("HRS", True): 1,
                ("control", False): 1, ("control", True): -1}
    for (state, swap), sign in expected.items():
        assert retention.write_polarity(state, swap) == sign, (state, swap)


def test_negative_vmax_write_is_refused_with_the_swap_pointer():
    with tempfile.TemporaryDirectory() as d:
        try:
            _collect(d, vmax_write="-5")
            assert False, "negative V max accepted"
        except ValueError as exc:
            assert str(exc) == "Type V max as a positive number; tick Swap polarity for a negative leg."
        try:
            _collect(d, vmax_write="0")
            assert False, "zero V max accepted"
        except ValueError as exc:
            assert "at least 1e-06" in str(exc)


def test_v_read_above_1_v_warns_and_above_never_exceed_refuses():
    assert not hasattr(retention, "V_READ_MAX_V")
    with tempfile.TemporaryDirectory() as d:
        p = _collect(d, v_read="1.5", protection="20")
        assert p["v_read"] == 1.5
        assert "V read above 1 V: the read bias stresses the state for the whole run." in p["_warning"]
        assert "at least half" not in p["_warning"]
        p = _collect(d, v_read="2.5", protection="20")
        assert ("V read is at least half of V max write: the read itself may switch the pellet."
                in p["_warning"])
        try:
            _collect(d, v_read="25", protection="20")
            assert False, "V read above never-exceed accepted"
        except ValueError as exc:
            assert "V read 25 V is above the never-exceed value 20 V" in str(exc)
        try:
            _collect(d, v_read="0", protection="20")
            assert False, "V read 0 accepted"
        except ValueError:
            pass
    # the source range still covers V read when it is above V max write
    assert retention.source_range_for(max(5.0, 25.0)) == 200.0


def test_live_write_line_text():
    tail = "; stops early at confirmed compliance. Reads at +0.1 V."
    assert retention.write_line_text("LRS", False, 5, 10, 0.1) == \
        "This run: 0 -> +5 V -> 0 at I cc 10 uA (SET, LRS)" + tail
    assert retention.write_line_text("HRS", False, 5, 10, 0.1) == \
        "This run: 0 -> -5 V -> 0 at I cc 10 uA (RESET, HRS)" + tail
    assert retention.write_line_text("control", False, 0.5, 1, 0.1) == \
        "This run: 0 -> +0.5 V -> 0 at I cc 1 uA (no switching intended, control)" + tail
    assert retention.write_line_text("LRS", True, 5, 10, 0.1) == \
        "This run: 0 -> -5 V -> 0 at I cc 10 uA (SET, LRS) (swapped)" + tail
    assert retention.write_line_text("HRS", True, 5, 10, 0.1) == \
        "This run: 0 -> +5 V -> 0 at I cc 10 uA (RESET, HRS) (swapped)" + tail


class _FakeWidget:
    def __init__(self):
        self.state = "normal"

    def configure(self, **kw):
        self.state = kw.get("state", self.state)


def test_spacing_greys_delta_t_or_points_per_decade_and_keeps_the_values():
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(retention.RetentionGUI, retention, dict(GUI_BASE), d)
        gui.widgets = {"points_per_decade": _FakeWidget(), "dt": _FakeWidget()}
        gui.refresh_estimate()
        assert gui.widgets["dt"].state == "disabled" and gui.widgets["points_per_decade"].state == "normal"
        gui.vars["spacing"].v = retention.SPACING_LINEAR
        gui.refresh_estimate()
        assert gui.widgets["dt"].state == "normal" and gui.widgets["points_per_decade"].state == "disabled"
        assert gui.vars["points_per_decade"].get() == "10" and gui.vars["dt"].get() == "10"
        gui.vars["spacing"].v = retention.SPACING_LOG
        gui.refresh_estimate()
        assert gui.widgets["dt"].state == "disabled" and gui.widgets["points_per_decade"].state == "normal"


def test_planned_reads_defaults_match_the_estimate_example():
    p = dict(t_rec=60, t_soak=60, t_stop=10000, scheme=retention.SCHEME_CONSTANT, spacing=retention.SPACING_LOG,
             points_per_decade=10, dt=10)
    old = retention.LOG_INTERVAL_S
    retention.LOG_INTERVAL_S = 1.0
    try:
        plan = retention.planned_reads(p)
        assert plan["scheduled"] == 20 and round(plan["first_s"]) == 126 and plan["last_s"] == 10000
        assert plan["soak_rows"] == 60 and plan["recover_rows"] == 60
        assert retention.planned_reads_text(plan, 3, p["scheme"]) == (
            "reads: 20 scheduled (x3 averaged), first 126 s, last 10000 s, + 60 soak and 60 recover rows")
        p["scheme"] = retention.SCHEME_INTERMITTENT
        plan = retention.planned_reads(p)
        text = retention.planned_reads_text(plan, 3, p["scheme"])
        assert plan["skipped"] > 0
        assert text.endswith(f"{plan['skipped']} of {plan['grid']} grid instants skipped (closer than t soak)")
    finally:
        retention.LOG_INTERVAL_S = old


class _VirtualClock:
    """Stands in for the module's time: perf_counter is virtual and sleep
    advances it, so the run is deterministic and independent of the PC's
    file-sync speed. Each clock read costs 1 us (as real time moves on);
    without it a wait could spin on a remainder below the clock's
    resolution."""

    def __init__(self):
        self.t = 1000.0

    def perf_counter(self):
        self.t += 1e-6
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 1e-9)


def test_planned_reads_count_matches_a_fake_run():
    """5.4: for both schemes and both spacings the scheduled-read count of
    planned_reads() equals the read rows with n_avg == n avg of a fake run."""
    times = dict(t_rec=0.1, t_soak=0.2, t_stop=1.5, points_per_decade=10, dt=0.4, n_avg=3)
    real_time = retention.time
    try:
        for scheme in retention.SCHEMES:
            for spacing in retention.SPACINGS:
                clock = _VirtualClock()
                retention.time = clock
                with tempfile.TemporaryDirectory() as d:
                    p = retention_params(d, scheme=scheme, spacing=spacing, **times)
                    stop = threading.Event()
                    worker = retention.RetentionWorker(p, make_link(retention, FakeK2400(1e6)), stop,
                                                       queue.Queue(), sleep=clock.sleep)
                    worker.gui_version = "test"
                    run_worker(worker, None, stop)
                    assert worker.reason == "max_time", (scheme, spacing, worker.reason)
                    rpath = [os.path.join(d, f) for f in os.listdir(d) if "_reads" in f][0]
                    _, _, rrows = read_file(rpath)
                    measured = [r for r in rrows if r["phase"] == "read" and r["n_avg"] == 3]
                    plan = retention.planned_reads(p)
                    assert plan["scheduled"] == len(measured), (scheme, spacing, plan, len(measured))
                    assert plan["scheduled"] >= 3, (scheme, spacing, plan)
                    assert abs(measured[0]["t_since_write_s"] - plan["first_s"]) < 1e-3
                    assert abs(measured[-1]["t_since_write_s"] - plan["last_s"]) < 1e-3
    finally:
        retention.time = real_time


if __name__ == "__main__":
    run_module_tests(globals())
