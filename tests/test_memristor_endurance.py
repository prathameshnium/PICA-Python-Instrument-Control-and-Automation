"""Memristor_Endurance_K2400_GUI.py: cycle list parsing, time estimate and
the worker run paths against the fake 2400: five cycles on 1 Mohm
(acceptance test 5), trip bookkeeping per leg, Stop, the _log file (data
files hold data only), no max time, bus-error retry marking retried=1, pulse mode (ZERO + Auto-Off command order, one row
per pulse) and the GUI locks on pulse mode.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memristor_test_support import (  # noqa: E402
    FakeK2400, analysis, assert_data_only, comment_lines, end_reason, endurance, log_events, log_path_of,
    read_file, run_module_tests, _gui_shell, run_endurance)

def test_endurance_cycle_list_and_failure_rule():
    assert endurance.parse_cycle_list("1,5,10") == {1, 5, 10}
    assert endurance.parse_cycle_list(" 1, 2 ,5 ") == {1, 2, 5}
    assert endurance.parse_cycle_list(endurance.RAW_CYCLES_DEFAULT) == set(range(1, 11))
    assert endurance.parse_cycle_list("") == set()
    for bad in ("0", "a", "1;2"):
        try:
            endurance.parse_cycle_list(bad)
            assert False, bad
        except ValueError as exc:
            assert "ranges" not in str(exc), bad
    for bad in ("1-10", "3-1", "1-b", "1, 2-5"):
        try:
            endurance.parse_cycle_list(bad)
            assert False, bad
        except ValueError as exc:
            assert str(exc) == ("Raw cycles: type cycle numbers separated by commas, e.g. 1,2,5,10 "
                                "(ranges like 1-10 are not accepted)."), bad
    assert endurance.raw_cycles_header(" 1, 2 ,5 ") == "1,2,5"
    assert analysis.first_failure_cycle([1, 2, 3, 4, 5], [100, 5, 5, 5, 100], 10, 3) == 2
    assert analysis.first_failure_cycle([1, 2, 3], [100, 5, 100], 10, 2) is None
    assert analysis.first_failure_cycle([1, 2], [float("nan"), float("nan")], 10, 2) == 1
    est = endurance.cycle_time_estimate_s(102, 0.1, 60)
    assert est > 2 * 60 and est < 2 * 60 + 60


def test_endurance_five_cycles_on_1_megohm_matches_acceptance_test_5():
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, spath, rpath = run_endurance(FakeK2400(1e6), d)
        assert worker.reason == "max_cycles"
        sh, scols, srows = read_file(spath)
        rh, rcols, rrows = read_file(rpath)
        assert list(scols) == list(endurance.SUMMARY_COLUMNS) and list(rcols) == list(endurance.RAW_COLUMNS)
        assert end_reason(spath) == "max_cycles" and end_reason(rpath) == "max_cycles"
        assert_data_only(spath)
        assert_data_only(rpath)
        assert [r["cycle"] for r in srows] == [1, 2, 3, 4, 5]
        for r in srows:
            assert abs(r["R_LRS_ohm"] - 1e6) < 1 and abs(r["R_HRS_ohm"] - 1e6) < 1
            assert abs(r["window"] - 1.0) < 1e-9
            assert r["V_set_trip_V"] == "" and r["V_reset_trip_V"] == ""
            assert r["n_compliance_set"] == 0 and r["retried"] == 0
            assert r["read_cc_LRS"] == 0 and r["read_cc_HRS"] == 0
        assert [r["raw_saved"] for r in srows] == [1, 1, 0, 0, 1]
        raw_cycles = sorted({int(r["cycle"]) for r in rrows if r["phase"] == "sweep"})
        assert raw_cycles == [1, 2, 5]
        legs = [r for r in rrows if r["cycle"] == 1]
        assert [r["leg"] for r in legs][:11] == ["set"] * 11 and legs[11]["leg"] == "reset"
        assert legs[0]["V_set_V"] == 0.0 and legs[5]["V_set_V"] == 5.0 and legs[16]["V_set_V"] == -5.0
        settle = [r for r in rrows if r["phase"] == "settle"]
        assert len(settle) == 3
        blocks = analysis.header_blocks(sh)
        assert blocks["protocol"]["mode"] == "sweep" and blocks["protocol"]["raw_cycles"] == "1,2,5"
        assert blocks["protocol"]["LO_earthed"] == ""
        assert "max_time_s" not in blocks["protocol"] and "max_time_s" not in analysis.header_blocks(rh)["protocol"]
        log = log_path_of(spath)
        assert log == log_path_of(rpath) and os.path.basename(log) == spath.split(os.sep)[-1].replace(
            "_endurance_summary.csv", "_endurance_log.txt")
        events = log_events(spath)
        assert events[0] == (f"FILES data_file={os.path.basename(spath)} | data_file={os.path.basename(rpath)}")
        ends = [e for e in events if e.startswith("END ")]
        assert len(ends) == 2 and events[-2:] == ends
        assert ends[0].startswith("END reason=max_cycles | points=5 | end_time=")
        assert ends[0].endswith(f"| file={os.path.basename(spath)}")
        assert worker.link.fake.writes[-1] == ":OUTP OFF"


def test_endurance_records_trips_and_compliance_counts_per_leg():
    with tempfile.TemporaryDirectory() as d:
        worker, _, spath, rpath = run_endurance(FakeK2400(2e5), d, n_cycles=2, i_cc_reset=1e-4)
        _, _, srows = read_file(spath)
        for r in srows:
            assert r["V_set_trip_V"] == 3.0          # 2e5 ohm: 10 uA at 2 V, first flagged at 3 V
            assert r["n_compliance_set"] > 0
            assert r["V_reset_trip_V"] == "" and r["n_compliance_reset"] == 0
        w = worker.link.fake.writes
        assert ":SENS:CURR:PROT 0.0001" in w and ":SENS:CURR:PROT 1e-05" in w


def test_endurance_saves_the_compliance_flag_of_a_clamped_read():
    """Review finding: soaked_read() computed the compliance flag of the
    read and dropped it. 5 kohm at 0.1 V wants 20 uA, above the 10 uA I cc,
    so every read is clamped: read_cc_LRS / read_cc_HRS = 1, a WARNING
    line in the console, and the R values come from the clamped point."""
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, spath, _ = run_endurance(FakeK2400(5e3), d, n_cycles=2)
        assert worker.reason == "max_cycles"
        _, cols, srows = read_file(spath)
        assert "read_cc_LRS" in cols and "read_cc_HRS" in cols
        assert cols.index("read_cc_LRS") < cols.index("raw_saved")
        assert [(r["read_cc_LRS"], r["read_cc_HRS"]) for r in srows] == [(1, 1), (1, 1)]
        assert all(abs(r["R_LRS_ohm"] - 5e3) < 1 for r in srows)
        logs = [m[1] for m in msgs if m[0] == "log" and "WARNING" in m[1]]
        assert any("read after SET hit I cc" in ln and "read_cc=1" in ln for ln in logs)
        assert any("read after RESET hit I cc" in ln for ln in logs)
        # a 1 Mohm read draws 0.1 uA: no flag, no warning
    with tempfile.TemporaryDirectory() as d:
        _, msgs, spath, _ = run_endurance(FakeK2400(1e6), d, n_cycles=1)
        _, _, srows = read_file(spath)
        assert [(r["read_cc_LRS"], r["read_cc_HRS"]) for r in srows] == [(0, 0)]
        assert not [m for m in msgs if m[0] == "log" and "hit I cc" in m[1]]


def test_endurance_reconnect_after_a_power_cycle_ramps_up_from_zero():
    """Review finding: reconnect() turned the output straight on at the
    remembered voltage. A fake whose bus fault is a power cycle (output off,
    source 0 V) must see :SOUR:VOLT 0, :OUTP ON and then a ramp in steps no
    larger than Step V up to the remembered value."""
    class PowerCycled(FakeK2400):
        def query(self, cmd):
            if cmd.strip().upper() == ":READ?" and self.reads in self.fail_reads:
                self.reads += 1
                self.v, self.output_on = 0.0, False      # the 2400 came back from a power cycle
                raise IOError("VI_ERROR_TMO: timeout")
            return FakeK2400.query(self, cmd)
    with tempfile.TemporaryDirectory() as d:
        fake = PowerCycled(1e6, fail_reads={8})
        worker, _, spath, _ = run_endurance(fake, d, n_cycles=1, step_v=0.5)
        assert worker.reason == "max_cycles"
        _, _, srows = read_file(spath)
        assert [r["retried"] for r in srows] == [1]
    w = fake.writes
    on = [k for k, c in enumerate(w) if c == ":OUTP ON"]
    assert len(on) == 2                                   # startup and the reconnect
    k = on[1]
    assert w[k - 1] == ":SOUR:VOLT 0"                      # on at 0 V ...
    volts = []
    for c in w[k + 1:]:
        if not c.startswith(":SOUR:VOLT "):
            break
        volts.append(float(c.split()[1]))
    assert volts and volts[0] > 0 and all(b - a > 0 for a, b in zip(volts, volts[1:]))
    assert all(abs(b - a) <= 0.5 + 1e-9 for a, b in zip([0.0] + volts, volts))   # ... then Step V steps up
    assert fake.writes.count("*RST") == 1


def test_endurance_closing_the_window_waits_for_the_ramp_down():
    """Review finding: _on_closing() joined the worker for 20 s and then
    destroyed the window; the worker is a daemon thread, so a longer
    ramp-down died with the process and the output could stay on. The
    window must stay until the worker thread has ended."""
    import threading
    gui = object.__new__(endurance.EnduranceGUI)
    done = threading.Event()
    gui.is_running = True
    gui.stop_event = threading.Event()
    gui.worker_thread = threading.Thread(target=done.wait, daemon=True)
    gui.worker_thread.start()
    gui.data_queue = __import__("queue").Queue()
    gui.data_queue.put(("log", "ramping"))
    logged = []
    gui.log = logged.append
    gui._pump_after_id = "pump1"
    gui._close_after_id = None
    banners = []
    gui._set_banner = banners.append

    class _Btn:
        def config(self, **kw):
            self.state = kw.get("state")
    gui.stop_button = _Btn()

    class _Root:
        def __init__(self):
            self.scheduled, self.cancelled, self.destroyed = [], [], False

        def after(self, ms, fn):
            self.scheduled.append(fn)
            return f"id{len(self.scheduled)}"

        def after_cancel(self, ident):
            self.cancelled.append(ident)

        def destroy(self):
            self.destroyed = True
    gui.root = _Root()
    old_ask = endurance.messagebox.askyesno
    endurance.messagebox.askyesno = lambda *a, **k: True
    try:
        gui._on_closing()
        assert gui.stop_event.is_set() and not gui.root.destroyed
        assert gui.root.cancelled == ["pump1"] and gui.stop_button.state == "disabled"
        assert banners and "ramping to 0 V" in banners[0]
        for _ in range(5):                              # the worker is still busy: keep polling, never destroy
            gui.root.scheduled.pop(0)()
            assert not gui.root.destroyed
        assert logged == ["ramping"]                    # worker log lines still reach the console
        done.set()
        gui.worker_thread.join(5)
        gui.root.scheduled.pop(0)()
        assert gui.root.destroyed and not gui.root.scheduled
        # not running: closes at once
        gui2 = object.__new__(endurance.EnduranceGUI)
        gui2.is_running = False
        gui2.root = _Root()
        gui2._on_closing()
        assert gui2.root.destroyed
    finally:
        endurance.messagebox.askyesno = old_ask


def test_endurance_stop_button_during_a_cycle_writes_no_half_row():
    class SlowFake(FakeK2400):
        def query(self, cmd):
            if cmd.strip().upper() == ":READ?":
                time.sleep(0.01)
            return FakeK2400.query(self, cmd)
    with tempfile.TemporaryDirectory() as d:
        worker, _, spath, _ = run_endurance(SlowFake(1e6), d, stop_after=0.35, n_cycles=100, t_soak=0.05)
        assert worker.reason == "stop_button"
        sh, _, srows = read_file(spath)
        assert end_reason(spath) == "stop_button"
        assert_data_only(spath)
        assert 0 <= len(srows) < 100
        assert all(r["R_LRS_ohm"] != "" and r["R_HRS_ohm"] != "" for r in srows)
        assert worker.link.fake.writes[-1] == ":OUTP OFF"


def test_endurance_bus_error_repeats_the_leg_and_marks_the_cycle_retried():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeK2400(1e6, fail_reads={8})
        worker, _, spath, rpath = run_endurance(fake, d, n_cycles=2)
        assert worker.reason == "max_cycles"
        _, _, srows = read_file(spath)
        assert [r["retried"] for r in srows] == [1, 0]
        retries, reconnects = comment_lines(spath, "RETRY"), comment_lines(rpath, "RECONNECT")
        assert retries and reconnects and "what=set leg cycle 1" in retries[0]
        assert_data_only(spath)
        assert_data_only(rpath)
        events = log_events(spath)
        assert events.index(retries[0]) < events.index(reconnects[0]) < events.index(
            [e for e in events if e.startswith("END ")][0])
        _, _, rrows = read_file(rpath)
        c1 = [r for r in rrows if r["cycle"] == 1 and r["leg"] == "set"]
        assert c1[0]["V_set_V"] == 0.0 and c1[-1]["V_set_V"] == 0.0 and c1[-6]["V_set_V"] == 5.0
        # the leg restarted from 0 V: 0 V appears at least twice as the start of a set leg
        assert sum(1 for r in c1 if r["V_set_V"] == 0.0 and r["segment"] == 1) >= 2


def test_endurance_pulse_mode_uses_zero_autooff_and_writes_one_row_per_pulse():
    with tempfile.TemporaryDirectory() as d:
        worker, _, spath, rpath = run_endurance(FakeK2400(1e6), d, mode=endurance.MODE_PULSE, n_cycles=2,
                                                pulses_per_leg=3, lo_earthed=True, pulse_width_s=2.5e-3)
        assert worker.reason == "max_cycles"
        sh, _, srows = read_file(spath)
        blocks = analysis.header_blocks(sh)
        assert blocks["protocol"]["mode"] == "pulse" and blocks["protocol"]["pulse_width_measured_s"] == "0.0025"
        assert blocks["protocol"]["LO_earthed"] == "yes" and blocks["protocol"]["pulses_per_leg"] == "3"
        _, _, rrows = read_file(rpath)
        assert_data_only(spath)
        assert_data_only(rpath)
        pulses = [r for r in rrows if r["phase"] == "pulse"]
        assert len(pulses) == 2 * 2 * 3
        assert all(r["trip_query"] == "" for r in pulses)
        assert {r["V_set_V"] for r in pulses} == {5.0, -5.0}
        assert all(r["segment"] == "" for r in pulses)
        w = worker.link.fake.writes
        assert ":OUTP:SMOD ZERO" in w and ":SOUR:CLE:AUTO ON" in w
        i_zero = w.index(":OUTP:SMOD ZERO")
        assert ":OUTP OFF" in w[i_zero:i_zero + 3]
        assert ":SENS:CURR:NPLC 0.01" in w and ":SOUR:DEL 0.001" in w
        after = w[w.index(":SOUR:CLE:AUTO ON"):]
        assert ":SOUR:CLE:AUTO OFF" in after and ":OUTP:SMOD NORM" in after and ":SENS:CURR:NPLC 1" in after
        assert w[-1] == ":OUTP OFF"
        assert all(abs(r["R_LRS_ohm"] - 1e6) < 1 for r in srows)




def test_endurance_gui_locks_pulse_mode_behind_lo_earth_and_measured_width():
    base = dict(sample_name="Demo01", visa_address="GPIB1::24::INSTR", protection="20", mode=endurance.MODE_PULSE, v_set="5", v_reset="5",
                step_v="0.1", dwell_s="0.1", i_cc_set_uA="10", i_cc_reset_uA="10", v_read="0.1", t_soak="60",
                n_cycles="100", raw_cycles=" 1, 2 ,3,4,5,6,7,8,9,10", n_confirm="3", lo_earthed=False, pulse_set_v="5",
                pulse_reset_v="5", pulses_per_leg="1", pulse_width_s="", pulse_delay_s="0.001", pulse_nplc="0.01")
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(endurance.EnduranceGUI, endurance, base, d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "LO terminal" in str(exc)
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(base, lo_earthed=True), d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "pulse width" in str(exc)
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(base, lo_earthed=True, pulse_width_s="0.0025"), d)
        p = gui.collect_params()
        assert p["pulse_width_s"] == 0.0025 and p["raw_cycles"] == set(range(1, 11)) and "max_time_s" not in p
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(base, lo_earthed=True, pulse_width_s="0.0025",
                                                                 raw_cycles="1-10"), d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "ranges like 1-10 are not accepted" in str(exc)
        # V read: no 1 V cap any more (K, 12 Oct 2026); warn above 1 V, refuse above never-exceed
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(base, mode=endurance.MODE_SWEEP, v_read="1.5"), d)
        p = gui.collect_params()
        assert p["v_read"] == 1.5 and "V read above 1 V" in p["_warning"] and "at least half" not in p["_warning"]
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(base, mode=endurance.MODE_SWEEP, v_read="2.5"), d)
        assert "at least half of the smaller write voltage" in gui.collect_params()["_warning"]
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(base, mode=endurance.MODE_SWEEP, v_read="25"), d)
        try:
            gui.collect_params()
            assert False
        except ValueError as exc:
            assert "V read 25 V is above the never-exceed value 20 V" in str(exc)


class _FakeWidget:
    def __init__(self):
        self.state = "normal"

    def configure(self, **kw):
        self.state = kw.get("state", self.state)


def test_endurance_pulse_mode_greys_the_sweep_leg_fields():
    assert endurance.disabled_cycle_fields(endurance.MODE_SWEEP) == set()
    assert endurance.disabled_cycle_fields(endurance.MODE_PULSE) == {"v_set", "v_reset", "dwell_s", "n_confirm"}
    with tempfile.TemporaryDirectory() as d:
        gui = _gui_shell(endurance.EnduranceGUI, endurance, dict(mode=endurance.MODE_PULSE), d)
        gui.widgets = {k: _FakeWidget() for k in endurance.PULSE_IGNORED_FIELDS + ("step_v",)}
        gui._on_mode()
        assert all(gui.widgets[k].state == "disabled" for k in endurance.PULSE_IGNORED_FIELDS)
        assert gui.widgets["step_v"].state == "normal"      # step still sets the shutdown ramp
        gui.vars["mode"].v = endurance.MODE_SWEEP
        gui._on_mode()
        assert all(w.state == "normal" for w in gui.widgets.values())



def test_endurance_compliance_order_follows_the_direction_so_no_error():
    """15 Oct 2026 fix (common block): SET and RESET compliances differ, so
    the compliance goes up and down every cycle; neither direction may draw
    an error from the fake 2400 (+824 going up, the A6 rule going down)."""
    from memristor_test_support import assert_compliance_order
    for extra in (dict(n_cycles=2, i_cc_reset=1e-4), dict(n_cycles=2, i_cc_set=1e-3, i_cc_reset=1.0),
                  dict(n_cycles=2, i_cc_set=1.0, i_cc_reset=5e-8),
                  dict(mode=endurance.MODE_PULSE, n_cycles=2, lo_earthed=True, pulse_width_s=2.5e-3,
                       i_cc_set=1e-3, i_cc_reset=0.5)):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeK2400(1e6)
            worker, _, _, _ = run_endurance(fake, d, **extra)
            assert worker.outcome == "finished", (extra, worker.reason)
            assert fake.err_queue == [], (extra, fake.err_queue)
            assert assert_compliance_order(endurance, fake.writes) >= 3, extra


if __name__ == "__main__":
    run_module_tests(globals())
