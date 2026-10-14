"""Memristor_Sweep_K2400_GUI.py v1.5: the 2400 error queue is logged, and
nothing else changes. Pure helpers (envelope_warning, parse_error_reply,
drain_error_queue); the SCPI writes and the data rows are identical to v1.4
(5af8452) for bipolar +, bipolar -, unipolar and ladder; the run log gets
the 'after configure' and 'end of run, output off' drains; a +826 at
configure is logged and the run completes; a timed-out query is one log line
with no RETRY, no reconnect and retry_total unchanged; a drain after each
compliance change happens at 0 V; no query while sourcing; no drain when
:OUTP OFF fails.
"""

import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memristor_test_support import (  # noqa: E402
    FakeK2400, end_reason, log_events, read_file, run_module_tests, run_sweep, sweep)

ERR_826 = '+826,"Attempt to exceed power limit"'

# Same inputs as v1.4; (number of writes, sha256[:16] of the writes,
# number of rows, sha256[:16] of the columns and rows without t_s), all
# recorded with the v1.4 module at 5af8452.
V14_CASES = {
    "bipolar_plus": (dict(), (82, "0c9cbc190ebaa656", 60, "1ab0a069f1fc939b")),
    "bipolar_minus": (dict(shape=sweep.SHAPE_BIPOLAR_MINUS, i_cc_minus=2e-6, vmax_plus=5, vmax_minus=5, step_v=0.5),
                      (65, "b15a0c76d5c42341", 40, "5db3524e4bfae255")),
    "unipolar": (dict(shape=sweep.SHAPE_UNIPOLAR_PLUS, vmax_plus=5, step_v=0.5, loops=2),
                 (64, "52bd7b9c0940f500", 45, "c364e736ed7ad986")),
    "ladder": (dict(shape=sweep.SHAPE_LADDER, ladder=[5.0, 15.0, 30.0], protection_v=None),
               (109, "59bbe1b7f88438f2", 81, "8cb938f295449bbb")),
}


def _fingerprint(worker, path):
    _, cols, rows = read_file(path)
    writes = worker.link.fake.writes
    wr = "\n".join(writes)
    rr = "\n".join(repr([r[c] for c in cols if c != "t_s"]) for r in rows)
    return (len(writes), hashlib.sha256(wr.encode()).hexdigest()[:16], len(rows),
            hashlib.sha256((",".join(cols) + "\n" + rr).encode()).hexdigest()[:16])


def _instr_events(path):
    return [t for t in log_events(path) if t.startswith("INSTR_")]


def _console(msgs):
    return [m[1] for m in msgs if m[0] == "log"]


def test_envelope_warning_only_for_200v_range_above_105ma():
    assert sweep.envelope_warning(200, 1.0, 1.0)
    assert sweep.envelope_warning(200, 10e-6, 0.2)
    text = sweep.envelope_warning(200.0, 1.0, 1.0)
    assert "6-10" in text and "18-62" in text and "+826" in text and "Run continues" in text
    assert text.isascii()
    for args in ((20, 1.0, 1.0), (2, 1.0, 1.0), (200, 10e-6, 10e-6), (200, 0.105, 0.105)):  # 12 Oct settings
        assert sweep.envelope_warning(*args) is None, args


def test_parse_error_reply():
    assert sweep.parse_error_reply(ERR_826) == (826, "Attempt to exceed power limit")
    assert sweep.parse_error_reply('0,"No error"')[0] == 0
    assert sweep.parse_error_reply('+000,"No error"\n')[0] == 0
    assert sweep.parse_error_reply('-113,"Undefined header"')[0] == -113
    for bad in ("", "garbage", '"No error"', "1.5,x"):
        assert sweep.parse_error_reply(bad)[0] is None, bad


def test_drain_error_queue_stops_at_zero_garbage_or_12_reads():
    class L:
        def __init__(self, replies):
            self.replies, self.n = list(replies), 0

        def query(self, cmd):
            assert cmd == ":SYST:ERR?"
            self.n += 1
            return self.replies.pop(0) if self.replies else ERR_826
    link = L([ERR_826, '-113,"Undefined header"', '0,"No error"', ERR_826])
    assert sweep.drain_error_queue(link) == [ERR_826, '-113,"Undefined header"'] and link.n == 3
    link = L(["junk"])
    assert sweep.drain_error_queue(link) == [] and link.n == 1
    link = L([])                                        # never empties: capped
    assert len(sweep.drain_error_queue(link)) == 12 and link.n == 12


def test_writes_and_rows_are_identical_to_v14():
    for name, (extra, expected) in V14_CASES.items():
        with tempfile.TemporaryDirectory() as d:
            worker, _, path = run_sweep(FakeK2400(1e6), d, **extra)
            assert worker.reason == "completed", name
            assert _fingerprint(worker, path) == expected, name


def test_normal_run_logs_empty_drains():
    """Unipolar: no sign change, so only the two drains of the run ends.
    Bipolar adds one per sign change (the compliance is re-set there)."""
    with tempfile.TemporaryDirectory() as d:
        _, _, path = run_sweep(FakeK2400(1e6), d, shape=sweep.SHAPE_UNIPOLAR_PLUS, vmax_plus=5, step_v=0.5)
        assert _instr_events(path) == ["INSTR_ERROR_QUEUE stage=after configure | empty",
                                       "INSTR_ERROR_QUEUE stage=end of run, output off | empty"]
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, path = run_sweep(FakeK2400(1e6), d)
        events = log_events(path)
        assert events[0].startswith("FILES ")
        assert _instr_events(path) == ["INSTR_ERROR_QUEUE stage=after configure | empty",
                                       "INSTR_ERROR_QUEUE stage=after compliance change to 1e-05 A | empty",
                                       "INSTR_ERROR_QUEUE stage=end of run, output off | empty"]
        assert events[1] == "INSTR_ERROR_QUEUE stage=after configure | empty"     # right after FILES
        end_idx = next(k for k, t in enumerate(events) if t.startswith("END "))
        assert events[end_idx - 1] == "INSTR_ERROR_QUEUE stage=end of run, output off | empty"
        assert end_reason(path) == "completed"
        assert not any("INSTRUMENT ERROR" in t for t in _console(msgs))


def test_826_at_configure_is_logged_and_the_run_completes():
    with tempfile.TemporaryDirectory() as d:
        ref_worker, _, ref_path = run_sweep(FakeK2400(1e6), d)
        ref = _fingerprint(ref_worker, ref_path)
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, path = run_sweep(FakeK2400(1e6, err_queue=[ERR_826]), d)
        assert _instr_events(path)[0] == f"INSTR_ERROR stage=after configure | reply={ERR_826}"
        assert _instr_events(path)[1:] == ["INSTR_ERROR_QUEUE stage=after compliance change to 1e-05 A | empty",
                                           "INSTR_ERROR_QUEUE stage=end of run, output off | empty"]
        assert f"INSTRUMENT ERROR [after configure]: {ERR_826}" in _console(msgs)
        assert _fingerprint(worker, path) == ref                  # every point still runs
        assert end_reason(path) == "completed"


def test_query_timeout_is_one_line_without_retry_or_reconnect():
    with tempfile.TemporaryDirectory() as d:
        worker, msgs, path = run_sweep(FakeK2400(1e6, fail_err_query=True), d,
                                       i_cc_minus=2e-6, vmax_plus=5, vmax_minus=5, step_v=0.5)
        events = log_events(path)
        failed = [t for t in events if t.startswith("INSTR_ERROR_QUERY_FAILED")]
        assert len(failed) == 3, failed       # after configure, the compliance change, end of run
        assert failed[0].startswith("INSTR_ERROR_QUERY_FAILED stage=after configure | error=")
        assert not any(t.startswith(("RETRY", "RECONNECT")) for t in events)
        assert worker.retry_total == 0
        assert end_reason(path) == "completed"
        got = _fingerprint(worker, path)
    with tempfile.TemporaryDirectory() as d:
        ref_worker, _, ref_path = run_sweep(FakeK2400(1e6), d, i_cc_minus=2e-6, vmax_plus=5, vmax_minus=5, step_v=0.5)
        assert got == _fingerprint(ref_worker, ref_path)          # same writes, every point still runs


def test_compliance_change_drains_once_per_sign_change_at_zero_volts():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeK2400(1e6)
        worker, _, path = run_sweep(fake, d, loops=2, i_cc_minus=2e-6, vmax_plus=5, vmax_minus=5, step_v=0.5)
        stages = [t for t in _instr_events(path) if "after compliance change" in t]
        assert stages == ["INSTR_ERROR_QUEUE stage=after compliance change to 2e-06 A | empty",
                          "INSTR_ERROR_QUEUE stage=after compliance change to 1e-05 A | empty",
                          "INSTR_ERROR_QUEUE stage=after compliance change to 2e-06 A | empty"]
        last_v = None
        n = 0
        for kind, cmd in fake.log:
            if kind == "w" and cmd.startswith(":SOUR:VOLT "):
                last_v = float(cmd.split()[1])
            if kind == "q" and cmd == ":SYST:ERR?":
                n += 1
                assert last_v in (None, 0.0), last_v
        assert n == 5


def test_no_error_query_while_sourcing_except_at_sign_changes():
    """Between the first and the last :READ? the only :SYST:ERR? queries are
    the ones right after a compliance read-back, sent at 0 V."""
    for extra in (dict(shape=sweep.SHAPE_LADDER, ladder=[5.0, 15.0, 30.0], protection_v=None),
                  dict(i_cc_minus=2e-6, vmax_plus=5, vmax_minus=5, step_v=0.5, loops=2),
                  dict(shape=sweep.SHAPE_UNIPOLAR_MINUS, vmax_minus=5, step_v=0.5, loops=3)):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeK2400(1e6)
            run_sweep(fake, d, **extra)
            log = fake.log
            reads = [k for k, (kind, cmd) in enumerate(log) if kind == "q" and cmd == ":READ?"]
            last_v = 0.0
            for k, (kind, cmd) in enumerate(log):
                if kind == "w" and cmd.startswith(":SOUR:VOLT "):
                    last_v = float(cmd.split()[1])
                if kind == "q" and cmd == ":SYST:ERR?" and reads[0] < k < reads[-1]:
                    assert log[k - 1] == ("q", ":SENS:CURR:PROT?"), (extra, log[k - 3:k + 1])
                    assert last_v == 0.0, extra
            if extra.get("shape") == sweep.SHAPE_UNIPOLAR_MINUS:     # one sign change, at the start
                assert sum(1 for kind, cmd in log if cmd == ":SYST:ERR?") == 3


def test_no_end_drain_when_output_off_fails():
    class Fake(FakeK2400):
        def write(self, cmd):
            if cmd.upper().startswith(":OUTP OFF"):
                self.log.append(("w", cmd))
                raise IOError("VI_ERROR_TMO: timeout")
            super().write(cmd)
    with tempfile.TemporaryDirectory() as d:
        fake = Fake(1e6)
        worker, _, path = run_sweep(fake, d)
        last_off = max(k for k, (kind, cmd) in enumerate(fake.log) if cmd == ":OUTP OFF")
        assert not any(kind == "q" and cmd == ":SYST:ERR?" for kind, cmd in fake.log[last_off:])
        assert not any("end of run" in t for t in _instr_events(path))
        assert end_reason(path) == "completed"


if __name__ == "__main__":
    run_module_tests(globals())
