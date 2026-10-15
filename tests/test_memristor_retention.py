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
        # one read per 0.4 s schedule instant; a slow runner can finish a read late,
        # so check the instant each read belongs to, not the gap between reads
        slots = [int(r["t_since_write_s"] / 0.4 + 1e-6) for r in reads]
        assert all(s >= 1 for s in slots) and all(b > a for a, b in zip(slots, slots[1:]))
        soaks = [r for r in rrows if r["phase"] == "soak"]
        assert all(r["V_set_V"] == 0.1 for r in soaks)
        recov = [r for r in rrows if r["phase"] == "recover"]
        assert all(r["V_set_V"] == 0.0 for r in recov)


def test_retention_stop_button_and_write_leg_turnaround():
    with tempfile.TemporaryDirectory() as d:
        # Stop is pressed after a fixed number of readings (not a timer), so
        # it lands in the same place on a slow CI runner as on the lab PC.
        worker, _, wpath, rpath = run_retention(FakeK2400(1e5), d, stop_at_read=40, t_stop=30, vmax_write=5, step_v=0.5)
        assert worker.reason == "stop_button"
        _, _, wrows = read_file(wpath)
        assert any(r["event_code"] == 4 for r in wrows)      # 1e5 ohm trips at 1 V, confirmed + turnaround
        assert max(r["V_set_V"] for r in wrows) < 5.0
        assert wrows[-1]["V_set_V"] == 0.0                   # return leg always finishes at 0 V
        _, _, rrows = read_file(rpath)
        assert any(r["phase"] == "recover" for r in rrows)   # Stop landed after the write
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
    base = dict(sample_name="Demo01", visa_address="GPIB1::24::INSTR", protection="20", state="HRS", swap=False, set_vmax="5",
                set_icc_uA="10", reset_vmax="5", reset_icc_uA="10", step_v="0.1", dwell_s="0.1", n_confirm="3", v_read="0.1", t_rec="60",
                t_soak="60", t_stop_h="2.78", scheme=retention.SCHEME_INTERMITTENT, spacing=retention.SPACING_LINEAR,
                points_per_decade="10", dt="10", n_avg="3")
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(retention.RetentionGUI, retention, base, d)
        p = gui.collect_params()
        assert p["polarity"] == -1 and "skipped" in p["_warning"]
        gui = _gui_shell(retention.RetentionGUI, retention, dict(base, reset_sign="+", scheme=retention.SCHEME_CONSTANT), d)
        p = gui.collect_params()
        assert p["polarity"] == 1 and p["_warning"] == ""


# -- UI round 1 (K's ruling of 12 Oct 2026) ------------------------------

GUI_BASE = dict(sample_name="Demo01", visa_address="GPIB1::24::INSTR", protection="20", state="LRS", swap=False,
                set_vmax="5", set_icc_uA="10", reset_vmax="5", reset_icc_uA="10", step_v="0.1", dwell_s="0.1", n_confirm="3", v_read="0.1",
                t_rec="60", t_soak="60", t_stop_h="2.78", scheme=retention.SCHEME_CONSTANT,
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
            _collect(d, set_vmax="-5")
            assert False, "negative V max accepted"
        except ValueError as exc:
            assert str(exc) == "Type V max as a positive number; choose the sign with SET sign / RESET sign."
        try:
            _collect(d, set_vmax="0")
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
        "This run: no write (control); t = 0 is the start of t rec. Reads at +0.1 V, I cc 1 uA."
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



def test_retention_compliance_order_follows_the_direction_so_no_error():
    """15 Oct 2026 fix (common block): PROT first when the compliance range
    goes up, RANG first otherwise (A6). The fake refuses both mistakes, as
    the 2400 does; retention never reads the queue, so it must stay empty."""
    from memristor_test_support import assert_compliance_order
    for extra in (dict(), dict(i_cc_write=1.0), dict(i_cc_write=1e-3, state="HRS"), dict(i_cc_write=5e-8),
                  dict(scheme=retention.SCHEME_INTERMITTENT, i_cc_write=0.5)):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeK2400(1e6)
            worker, _, wpath, rpath = run_retention(fake, d, **extra)
            assert fake.err_queue == [], (extra, fake.err_queue)
            assert worker.outcome == "finished" and end_reason(rpath) == worker.reason, (extra, worker.reason)
            assert_compliance_order(retention, fake.writes)


# -- control fix and master mode (K's rulings of 15 Oct 2026) -------------

SHORT = dict(t_rec=0.1, t_soak=0.2, t_stop=1.5)
REST_S = 0.01 * 3600        # 0.01 h


def _virtual_run(cls, p, fake, on_sleep=None):
    """Run a worker on the virtual clock (deterministic, rests take no real
    time). on_sleep(worker, stop) is called on every sleep."""
    clock = _VirtualClock()
    stop = threading.Event()
    worker = None

    def sleep(s):
        clock.sleep(s)
        if on_sleep is not None and worker is not None:
            on_sleep(worker, stop)
    real_time = retention.time
    retention.time = clock
    try:
        worker = cls(p, make_link(retention, fake), stop, queue.Queue(), sleep=sleep)
        worker.gui_version = "test"
        run_worker(worker, None, stop)
    finally:
        retention.time = real_time
    return worker


def _files(folder, part):
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if part in f)


def _timed_reads(rpath):
    _, _, rows = read_file(rpath)
    return [r["t_since_write_s"] for r in rows if r["phase"] == "read" and r["n_avg"] == 3]


def master_params(folder, **extra):
    p = retention_params(folder, master=True, state="master", polarity=0, set_vmax=5.0, set_icc=1e-5,
                         reset_vmax=4.0, reset_icc=2e-5, rest_s=REST_S, set_swap=False,
                         reset_swap=False, **SHORT)
    p.update(extra)
    return p


def _master_log(folder):
    """(t_s, text) of every master log line."""
    path = _files(folder, "_retention_master_log")
    assert len(path) == 1, path
    out = []
    with open(path[0], encoding="utf-8") as fh:
        for ln in fh:
            iso, t_s, text = ln.rstrip("\n").split(" | ", 2)
            assert "T" in iso and t_s.startswith("t_s="), ln
            out.append((float(t_s[4:]), text))
    return out


def test_control_run_has_no_write_and_the_lrs_read_schedule():
    """Section 5 test 1."""
    with tempfile.TemporaryDirectory() as d:
        p = retention_params(d, state="control", polarity=0, vmax_write=0.0, step_v=0.1, n_confirm=0, **SHORT)
        fake = FakeK2400(1e6)
        worker = _virtual_run(retention.RetentionWorker, p, fake)
        assert worker.reason == "max_time"
        assert _files(d, "_write") == []
        rpath = _files(d, "_reads")[0]
        assert "_retention_control_reads" in rpath
        rh, _, rrows = read_file(rpath)
        proto = analysis.header_blocks(rh)["protocol"]
        assert proto["write"] == "none (control)" and proto["write_file"] == "none"
        assert proto["Vmax_write_V"] == "none" and proto["I_cc_write_A"] == "none" and proto["polarity"] == "none"
        assert "t_soak_s" in proto and "n_confirm" in proto and "log_file" in proto
        assert max(abs(v) for v in fake_sourced(fake)) <= 0.1 + 1e-12      # nothing above V read was sourced
        assert log_events(rpath)[0] == f"FILES data_file={os.path.basename(rpath)}"
        assert comment_lines(rpath, "WRITE_END t_s=")[0].endswith("| write=none (control)")
        assert [r["phase"] for r in rrows][:4] == ["settle"] * 3 + ["recover"]
        assert abs(rrows[3]["t_since_write_s"]) < 1e-3         # t = 0 is the start of t rec
        control_reads = _timed_reads(rpath)
    with tempfile.TemporaryDirectory() as d:
        _virtual_run(retention.RetentionWorker, retention_params(d, **SHORT), FakeK2400(1e6))
        lrs_reads = _timed_reads(_files(d, "_reads")[0])
    assert len(control_reads) == len(lrs_reads) >= 3
    assert all(abs(a - b) < 1e-3 for a, b in zip(control_reads, lrs_reads))


def fake_sourced(fake):
    return [float(w.split()[1]) for w in fake.writes if w.startswith(":SOUR:VOLT ")]


def _check_master_sequence(d, fake, worker, set_swap=False, reset_swap=False):
    assert worker.outcome == "finished" and worker.reason == "completed", worker.reason
    names = [os.path.basename(f) for f in _files(d, ".csv")]
    assert sorted(n.split("_retention_")[1] for n in names) == sorted(
        ["control_reads.csv", "LRS_write.csv", "LRS_reads.csv", "HRS_write.csv", "HRS_reads.csv"])
    events = _master_log(d)
    kinds = [text.split(" ")[0] for _, text in events if not text.startswith(("RETRY", "RECONNECT"))]
    assert kinds == ["MASTER_START", "RUN_START", "RUN_END", "REST_START", "REST_END", "RUN_START", "RUN_END",
                     "REST_START", "REST_END", "RUN_START", "RUN_END", "MASTER_END"], kinds
    starts = [text for _, text in events if text.startswith("RUN_START")]
    assert starts == ["RUN_START run=1/3 | state=control", "RUN_START run=2/3 | state=LRS",
                      "RUN_START run=3/3 | state=HRS"]
    assert all("reason=max_time" in text for _, text in events if text.startswith("RUN_END"))
    assert "file=" in events[2][1] and "_control_reads" in events[2][1]
    assert events[-1][1].startswith("MASTER_END reason=completed")
    s_set, s_reset = (-1 if set_swap else 1), (-1 if reset_swap else 1)
    _, _, lrs_w = read_file(_files(d, "_LRS_write")[0])
    _, _, hrs_w = read_file(_files(d, "_HRS_write")[0])
    assert max(s_set * r["V_set_V"] for r in lrs_w) == 5.0 and min(s_set * r["V_set_V"] for r in lrs_w) == 0.0
    assert min(s_reset * r["V_set_V"] for r in hrs_w) == -4.0 and max(s_reset * r["V_set_V"] for r in hrs_w) == 0.0
    for path, swapped in ((_files(d, "_LRS_write")[0], set_swap), (_files(d, "_HRS_write")[0], reset_swap)):
        assert analysis.header_blocks(read_file(path)[0])["protocol"]["swap_polarity"] == ("1" if swapped else "0")
    # nothing measured during the rests: no row of any file falls inside one
    rest_marks = [(t, text) for t, text in events if text.startswith(("REST_START", "REST_END"))]
    rests = [(t0, t1) for (t0, _), (t1, _) in zip(rest_marks[::2], rest_marks[1::2])]
    assert len(rests) == 2 and all(abs((t1 - t0) - REST_S) < 1.0 for t0, t1 in rests)
    ids = set()
    for k, state in enumerate(retention.MASTER_ORDER):
        for path in _files(d, f"_retention_{state}_"):
            if not path.endswith(".csv"):
                continue
            h, _, rows = read_file(path)
            proto = analysis.header_blocks(h)["protocol"]
            ids.add(proto["master_id"])
            assert proto["master_run"] == f"{k + 1}/3" and float(proto["rest_before_s"]) == (REST_S if k else 0)
            t = [r["t_wall_s" if "_reads" in path else "t_s"] for r in rows]
            assert not any(t0 < x < t1 for x in t for t0, t1 in rests), path
            assert end_reason(path) == "max_time"
            assert_data_only(path)
    assert len(ids) == 1
    _, _, lrs_r = read_file(_files(d, "_LRS_reads")[0])
    assert analysis.header_blocks(read_file(_files(d, "_LRS_reads")[0])[0])["instrument"]["I_cc_A"] == "1e-05"
    assert analysis.header_blocks(read_file(_files(d, "_HRS_reads")[0])[0])["instrument"]["I_cc_A"] == "2e-05"
    assert not fake.output_on and fake.writes[-1] == ":OUTP OFF"
    return events


def test_master_sequence_control_lrs_hrs_with_rests():
    """Section 5 test 2, with each write's sign flipped on its own (SET -
    alone, RESET + alone as for a unipolar pellet, both)."""
    for set_swap, reset_swap in ((False, False), (True, False), (False, True), (True, True)):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeK2400(1e6)
            p = master_params(d, set_swap=set_swap, reset_swap=reset_swap)
            worker = _virtual_run(retention.MasterRetentionWorker, p, fake)
            _check_master_sequence(d, fake, worker, set_swap, reset_swap)


def test_master_stop_during_a_rest_ends_the_sequence():
    """Section 5 test 3."""
    def press_stop(worker, stop):
        if worker.state.startswith("resting"):
            stop.set()
    with tempfile.TemporaryDirectory() as d:
        fake = FakeK2400(1e6)
        worker = _virtual_run(retention.MasterRetentionWorker, master_params(d), fake, press_stop)
        assert worker.reason == "stop_button" and worker.outcome == "stopped"
        assert not fake.output_on and fake.writes[-1] == ":OUTP OFF"
        events = _master_log(d)
        assert events[-1][1].startswith("MASTER_END reason=stop_button")
        assert [t for _, t in events if t.startswith("RUN_START")] == ["RUN_START run=1/3 | state=control"]
        assert events[-2][1].startswith("REST_START")
        csvs = _files(d, ".csv")
        assert len(csvs) == 1 and "_control_reads" in csvs[0] and end_reason(csvs[0]) == "max_time"


def test_master_bus_error_during_a_rest_is_retried_and_the_rest_keeps_its_time():
    """Section 5 test 4: the first rest check fails once."""
    old = retention.REST_CHECK_INTERVAL_S
    retention.REST_CHECK_INTERVAL_S = 5.0
    try:
        with tempfile.TemporaryDirectory() as d:
            fake = FakeK2400(1e6, fail_outp={0})
            worker = _virtual_run(retention.MasterRetentionWorker, master_params(d), fake)
            events = _check_master_sequence(d, fake, worker)
            texts = [t for _, t in events]
            k = texts.index(next(t for t in texts if t.startswith("RETRY")))
            assert texts[k - 1].startswith("REST_START") and "what=rest check" in texts[k]
            assert texts[k + 1].startswith("RECONNECT") and texts[k + 2].startswith("REST_END")
            assert fake.outp_queries > 3
    finally:
        retention.REST_CHECK_INTERVAL_S = old


def test_master_estimate_matches_the_run_within_5_percent():
    """Section 5 test 5."""
    with tempfile.TemporaryDirectory() as d:
        p = master_params(d)
        _virtual_run(retention.MasterRetentionWorker, p, FakeK2400(1e6))
        duration = _master_log(d)[-1][0]
        est = retention.master_estimate_s(p)
        assert abs(est - duration) <= 0.05 * duration, (est, duration)


def test_master_gui_fields_and_grey_out():
    assert retention.disabled_write_fields(True, "LRS") == {"state"}
    assert retention.disabled_write_fields(False, "LRS") == {"rest_h", "reset_sign", "reset_vmax", "reset_icc_uA"}
    assert retention.disabled_write_fields(False, "HRS") == {"rest_h", "set_sign", "set_vmax", "set_icc_uA"}
    assert retention.disabled_write_fields(False, "control") == {
        "rest_h", "set_sign", "set_vmax", "reset_sign", "reset_vmax", "reset_icc_uA", "step_v", "dwell_s", "n_confirm"}
    with tempfile.TemporaryDirectory() as d:
        master = dict(master=True, set_vmax="5", set_icc_uA="10", reset_vmax="3", reset_icc_uA="20", rest_h="2")
        p = _collect(d, **master)
        assert p["master"] and p["rest_s"] == 7200 and p["set_icc"] == 1e-5 and p["reset_icc"] == 2e-5
        assert p["vmax_min"] == 3 and p["vmax_write"] == 5
        p = _collect(d, **dict(master, v_read="2"))
        assert "at least half" in p["_warning"]             # 2 V >= half of the 3 V RESET
        try:
            _collect(d, **dict(master, reset_vmax="-3"))
            assert False, "negative RESET V max accepted"
        except ValueError as exc:
            assert str(exc) == retention.VMAX_NEGATIVE_ERROR
        # control: greyed write fields are not read, and no 'half of V max' warning
        p = _collect(d, state="control", set_vmax="junk", reset_vmax="", reset_icc_uA="x", step_v="", v_read="2.5")
        assert p["polarity"] == 0 and p["vmax_write"] == 0 and "at least half" not in p["_warning"]
        assert p["i_cc_write"] == 1e-5                       # control reads use SET I cc
        # single mode: LRS writes with the SET pair, HRS with the RESET pair
        p = _collect(d, state="LRS", set_vmax="4", set_icc_uA="10", reset_vmax="3", reset_icc_uA="20")
        assert (p["vmax_write"], p["i_cc_write"], p["polarity"]) == (4, 1e-5, 1)
        p = _collect(d, state="HRS", set_vmax="4", set_icc_uA="10", reset_vmax="3", reset_icc_uA="20")
        assert (p["vmax_write"], p["i_cc_write"], p["polarity"]) == (3, 2e-5, -1)
        p = _collect(d, state="HRS", set_vmax="junk", set_icc_uA="")      # greyed SET pair not read
        assert p["vmax_write"] == 5
        # each sign on its own: a unipolar pellet writes both legs negative
        p = _collect(d, **dict(master, set_sign="-", reset_sign="-"))
        assert p["set_swap"] and not p["reset_swap"]
        assert retention.master_line_text(True, False, 5, 10, 3, 20, 0.1, 0) == (
            "Master: control (no write) -> LRS: 0 -> -5 V -> 0 at 10 uA -> HRS: 0 -> -3 V -> 0 at 20 uA. "
            "Reads at +0.1 V.")
        p = _collect(d, state="HRS", reset_sign="+")
        assert p["polarity"] == 1 and p["swap"]
        p = _collect(d, state="LRS", reset_sign="+")
        assert p["polarity"] == 1 and not p["swap"]
        line = retention.master_line_text(False, False, 5, 10, 3, 20, 0.1, 2)
        assert line == ("Master: control (no write) -> rest 2 h -> LRS: 0 -> +5 V -> 0 at 10 uA -> rest 2 h -> "
                        "HRS: 0 -> -3 V -> 0 at 20 uA. Reads at +0.1 V.")


def test_t_stop_is_typed_in_hours_and_kept_in_seconds():
    with tempfile.TemporaryDirectory() as d:
        assert _collect(d, t_stop_h="1")["t_stop"] == 3600.0
        assert _collect(d)["t_stop"] == 2.78 * 3600
        try:
            _collect(d, t_stop_h="0")
            assert False, "t stop 0 h accepted"
        except ValueError as exc:
            assert "t stop" in str(exc)


if __name__ == "__main__":
    run_module_tests(globals())
