"""Live polling in the direct-control module: refused query vs dead bus.

A Cryo-con answers a query it does not take with SILENCE, so at the call
site a refused query and a dead bus are the same thing: a full VISA
timeout (10 s here - CRYOCON_TIMEOUT_MS, which stays where it is).

Before T_Control v1.5 each status field wrapped its own query in
'except Exception', so no timeout ever reached _poll_loop and its "stop
polling on a bus error" branch could not run. On the lab unit that meant:

  * CONTROL? - refused in all five diagnostic runs of 25 Sep 2026 - cost
    a 10 s timeout on the Tk main thread in EVERY 2.8 s poll cycle;
  * a genuinely dead bus was polled for ever, eight timeouts a tick.

The fake below answers exactly what the lab unit answered in those runs
(Untracked_Stuff/Diagnostics/log_25_09_26/) and times out on everything
else, with the exception pyvisa really raises. A permissive fake would
agree with the code by construction and prove nothing.

No Tk root is needed: the GUI object is built bare and given stub labels.
Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                     "T_Control_CC34_DirectControl_GUI.py")
_spec = importlib.util.spec_from_file_location("cc34_poll_refusals", _PATH)
control = importlib.util.module_from_spec(_spec)
sys.modules["cc34_poll_refusals"] = control
_spec.loader.exec_module(control)


def _timeout():
    """What pyvisa raises when nothing comes back."""
    if control.pyvisa is None:
        return IOError("VI_ERROR_TMO")
    from pyvisa.errors import VisaIOError
    return VisaIOError(-1073807339)


def _skip_without_pyvisa():
    if control.pyvisa is not None:
        return False
    try:
        import pytest
        pytest.skip("pyvisa is not installed")
    except ImportError:
        print("SKIP: pyvisa is not installed")
    return True


class LabUnitLink:
    """The lab Model 34 as the 25 Sep 2026 runs recorded it.

    Channel A read dots in that run, B-D dashes; every ALARM? answered
    '--'. Loop 2 (AOUT) answered only SETPT?, TYPE? and MODE? - the rest
    were never asked, so here they time out, which is the honest default.
    """

    ANSWERS = {
        "INPUT? A": "77.350", "INPUT? B": "-------",
        "INPUT? C": "-------", "INPUT? D": ".......",
        "INPUT A:ALARM?": "--", "INPUT B:ALARM?": "--",
        "INPUT C:ALARM?": "--", "INPUT D:ALARM?": "--",
        "HEATER:OUTPWR?": "0", "HEATER:SETPT?": "330.000000K",
        "AOUT:SETPT?": "100.000000K", "AOUT:TYPE?": "RAMPP",
        "HEATER:TYPE?": "RAMPT", "HEATER:SOURCE?": "CHA",
        "HEATER:PGAIN?": "0.500000", "HEATER:IGAIN?": "1.000000",
        "HEATER:DGAIN?": "1.000000", "HEATER:RAMP?": "NO",
        "HEATER:RATE?": "1.000000", "HEATER:RANGE?": "5.0W",
        "HEATER:LOAD?": "50", "OVERTEMP:ENABLE?": "ON",
        "OVERTEMP:SOURCE?": "CHA", "OVERTEMP:TEMP?": "310.000000",
        "SYSTEM:LOCKOUT?": "OFF",
    }

    def __init__(self):
        self.answers = dict(self.ANSWERS)
        self.queries = []
        self.timeouts = []
        self.dead = False
        self.is_connected = True

    def query(self, command):
        self.queries.append(command)
        if not self.dead and command in self.answers:
            return self.answers[command]
        self.timeouts.append(command)
        raise _timeout()

    def write(self, command):
        raise AssertionError(f"polling wrote {command!r}")


class Label:
    def __init__(self):
        self.text = ""

    def config(self, text=None, **_kw):
        if text is not None:
            self.text = text

    configure = config


class Root:
    def __init__(self):
        self.scheduled = []

    def after(self, ms, fn):
        self.scheduled.append((ms, fn))
        return f"after#{len(self.scheduled)}"

    def after_cancel(self, _after_id):
        pass


LABEL_KEYS = (
    [f"{kind}_{ch}" for kind in ("temp", "alarm") for ch in "ABCD"]
    + [f"{kind}_{loop}" for kind in ("outpwr", "htrread", "setpoint", "pid",
                                     "type", "ramp") for loop in "12"]
    + ["control_status", "range_1", "overtemp", "lockout"])


def _panel(link=None):
    """A DirectControlGUI with no window, polling the given link."""
    link = link or LabUnitLink()
    backend = object.__new__(control.Cryocon34Backend)
    backend.link = link
    backend.log = lambda msg: None
    backend.loop_prefix_mode = "name"
    backend.heater_query = "OUTPWR?"

    gui = object.__new__(control.DirectControlGUI)
    gui.backend = backend
    gui.root = Root()
    gui.poll_btn = Label()
    gui.lines = []
    gui.log = gui.lines.append
    gui.status_labels = {key: Label() for key in LABEL_KEYS}
    gui.channel_units = {ch: "K" for ch in "ABCD"}
    gui.is_connected = True
    gui.polling_active = False
    gui._poll_after_id = None
    gui._poll_stage = 0
    gui._poll_notes = {}
    gui._require_connection = lambda: True
    return gui, link


def _cycles(gui, count):
    """Run whole poll cycles, as the after() chain would."""
    for _ in range(count * gui.POLL_STAGE_COUNT):
        if not gui.polling_active:
            return
        gui._poll_loop()


# ------------------------------------------------------------------ tests

def test_the_lab_unit_keeps_polling_and_is_asked_control_once():
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 4)
    assert gui.polling_active, gui.lines
    assert link.queries.count("CONTROL?") == 1, link.queries.count("CONTROL?")
    assert gui.status_labels["control_status"].text == "no answer"
    # Everything the unit does answer is still on the panel.
    assert gui.status_labels["temp_A"].text == "77.350 K"
    assert gui.status_labels["setpoint_1"].text == "330.000"
    assert gui.status_labels["overtemp"].text == "ON, CHA, 310.000000"
    assert gui.status_labels["lockout"].text == "OFF"


def test_every_refused_query_costs_one_timeout_per_polling_session():
    """The whole point: a refused query is paid for once, not per cycle."""
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 1)
    first_cycle = list(link.timeouts)
    assert "CONTROL?" in first_cycle, first_cycle
    _cycles(gui, 5)
    # Every refusal was paid for in the first cycle; none since.
    assert link.timeouts == first_cycle, link.timeouts[len(first_cycle):]
    # Once per FIELD: loop 2's output power and heater read-back are two
    # fields that both send AOUT:OUTPWR?, so that command appears twice.
    assert first_cycle.count("CONTROL?") == 1
    assert first_cycle.count("AOUT:OUTPWR?") <= 2
    # And each refused field is logged once, not once a tick.
    notes = [line for line in gui.lines if "no answer" in line]
    assert len(notes) == len(gui._poll_refused), (notes, gui._poll_refused)


def test_a_refused_field_is_reported_in_plain_words_once():
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 3)
    control_notes = [line for line in gui.lines if "control_status" in line]
    assert len(control_notes) == 1, control_notes
    assert "not asked again" in control_notes[0], control_notes[0]


def test_a_bus_that_dies_mid_session_stops_polling_on_one_timeout():
    """A field that has answered and now times out is the bus, not the
    firmware. One timeout, then polling stops - not eight a tick."""
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 2)
    assert gui.polling_active
    before = len(link.timeouts)
    link.dead = True
    _cycles(gui, 2)
    assert not gui.polling_active
    assert len(link.timeouts) - before == 1, link.timeouts[before:]
    assert any("polling stopped" in line for line in gui.lines), gui.lines
    assert gui.poll_btn.text == "Start Polling"


def test_a_bus_that_is_dead_from_the_start_stops_on_the_first_input_query():
    """INPUT? is the one query every Cryo-con answers - an empty input
    still says '-------' - so its timeout is never mistaken for a
    refusal, even before anything has answered."""
    if _skip_without_pyvisa():
        return
    link = LabUnitLink()
    link.dead = True
    gui, link = _panel(link)
    gui._start_polling()
    _cycles(gui, 1)
    assert not gui.polling_active
    assert link.queries == ["INPUT? A"], link.queries


def test_whichever_stage_the_bus_dies_in_costs_exactly_one_timeout():
    if _skip_without_pyvisa():
        return
    for stage in range(control.DirectControlGUI.POLL_STAGE_COUNT):
        gui, link = _panel()
        gui._start_polling()
        _cycles(gui, 2)
        before = len(link.timeouts)
        gui._poll_stage = stage
        link.dead = True
        for _ in range(2 * gui.POLL_STAGE_COUNT):
            if not gui.polling_active:
                break
            gui._poll_loop()
        assert not gui.polling_active, stage
        assert len(link.timeouts) - before == 1, (stage,
                                                  link.timeouts[before:])


def test_a_sensor_fault_is_named_and_is_not_a_bus_error():
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 3)
    assert gui.polling_active
    for ch in "BCD":
        assert gui.status_labels[f"temp_{ch}"].text == "no sensor", ch
    # A faulted channel is asked every cycle, like a healthy one: it can
    # come back.
    assert link.queries.count("INPUT? B") == link.queries.count("INPUT? A")
    assert link.queries.count("INPUT? B") >= 3
    assert "temp_B" not in gui._poll_refused


def test_a_reply_that_does_not_parse_is_not_a_refusal():
    """Junk is the field's problem, not the firmware's: keep asking."""
    if _skip_without_pyvisa():
        return
    link = LabUnitLink()
    link.answers["HEATER:SETPT?"] = "garbage"
    gui, link = _panel(link)
    gui._start_polling()
    _cycles(gui, 3)
    assert gui.polling_active
    assert link.queries.count("HEATER:SETPT?") == 3
    assert "setpoint_1" not in gui._poll_refused


def test_restarting_polling_asks_the_refused_queries_afresh():
    """What a unit refuses is re-learned on every Start, so a restart
    after a firmware change or a bus fix is not stuck with old answers."""
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 2)
    assert "control_status" in gui._poll_refused
    gui._stop_polling()
    link.answers["CONTROL?"] = "OFF"
    gui._start_polling()
    _cycles(gui, 1)
    assert gui.polling_active
    assert gui.status_labels["control_status"].text == "OFF"
    assert link.queries.count("CONTROL?") == 2


def test_a_dropped_session_stops_polling_too():
    """ConnectionError is what the backend raises with no link at all."""
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 1)
    gui.backend.link = None
    _cycles(gui, 1)
    assert not gui.polling_active


def test_polling_never_writes():
    """LabUnitLink.write raises: any write during polling fails here."""
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    _cycles(gui, 3)
    assert gui.polling_active


def test_the_bus_error_check_itself_can_never_raise():
    """It runs inside every field's except clause. With a pyvisa that has
    no VisaIOError (the lab-bus regression harness installs exactly such
    a stub) the first version raised AttributeError there, and a sensor
    fault on channel B took the whole poll loop down."""
    class StubVisa:
        pass

    real = control.pyvisa
    control.pyvisa = StubVisa
    try:
        link = LabUnitLink()
        link.timeouts = []
        link.query = lambda command: (
            link.answers[command] if command in link.answers
            else (_ for _ in ()).throw(IOError("VI_ERROR_TMO")))
        gui, _ = _panel(link)
        gui._start_polling()
        _cycles(gui, 2)
        assert gui.status_labels["temp_B"].text == "no sensor"
        assert not control.DirectControlGUI._is_bus_error(IOError("x"))
        assert control.DirectControlGUI._is_bus_error(ConnectionError("x"))
    finally:
        control.pyvisa = real


def test_start_is_still_idempotent():
    """v1.2's fix must survive: a second Start adds no second chain."""
    if _skip_without_pyvisa():
        return
    gui, link = _panel()
    gui._start_polling()
    scheduled = len(gui.root.scheduled)
    queries = len(link.queries)
    gui._start_polling()
    assert len(gui.root.scheduled) == scheduled
    assert len(link.queries) == queries


# ------------------------------------------- numbers typed by the operator
#
# Found by the same audit: float() accepts 'nan' and 'inf'. Every setter
# but set_alarm has a range check that happens to reject both, so typing
# 'nan' into an alarm field sent 'INPUT A:ALARM:HIGHEST nan' to the unit.

class Entry:
    def __init__(self, text):
        self.text = text

    def get(self):
        return self.text


class RecordingLink:
    def __init__(self):
        self.writes = []

    def write(self, command):
        self.writes.append(command)


def _alarm_backend():
    backend = object.__new__(control.Cryocon34Backend)
    backend.link = RecordingLink()
    backend.log = lambda msg: None
    return backend


def test_no_numeric_field_accepts_nan_or_infinity():
    gui = object.__new__(control.DirectControlGUI)
    for text in ("nan", "NaN", "inf", "-inf", "Infinity", "1e999"):
        try:
            gui._read_float_entry(Entry(text), "Setpoint")
        except ValueError as exc:
            assert "Setpoint" in str(exc), str(exc)
            continue
        raise AssertionError(f"{text!r} was accepted as a number")


def test_ordinary_numbers_still_parse():
    gui = object.__new__(control.DirectControlGUI)
    for text, value in (("300", 300.0), (" 4.2 ", 4.2), ("-1.5", -1.5),
                        ("1e-3", 0.001), ("0", 0.0)):
        assert gui._read_float_entry(Entry(text), "x") == value, text
    for text in ("", "abc", "3 K"):
        try:
            gui._read_float_entry(Entry(text), "x")
        except ValueError:
            continue
        raise AssertionError(f"{text!r} was accepted")


def test_set_alarm_refuses_to_send_a_non_number():
    nan, inf = float("nan"), float("inf")
    for high, low in ((nan, 1.0), (300.0, nan), (inf, 1.0), (300.0, -inf)):
        backend = _alarm_backend()
        try:
            backend.set_alarm("A", high, low, True, True)
        except ValueError:
            assert backend.link.writes == [], backend.link.writes
            continue
        raise AssertionError(f"sent {backend.link.writes!r}")


def test_set_alarm_refuses_a_crossed_window_and_a_bad_channel():
    for args in (("A", 10.0, 20.0), ("E", 300.0, 1.0), ("a", 300.0, 1.0)):
        backend = _alarm_backend()
        try:
            backend.set_alarm(*args, True, False)
        except ValueError:
            assert backend.link.writes == []
            continue
        raise AssertionError(f"{args!r} was sent")


def test_a_valid_alarm_is_sent_in_one_compound_command():
    backend = _alarm_backend()
    cmd = backend.set_alarm("B", 310.0, 2.0, True, False)
    assert backend.link.writes == [cmd]
    assert cmd == ("INPUT B:ALARM:HIGHEST 310.0;LOWEST 2.0;"
                   "HIENA YES;LOENA NO"), cmd


def _run_all():
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
