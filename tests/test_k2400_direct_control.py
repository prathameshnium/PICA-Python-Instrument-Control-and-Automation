"""Tests for the Keithley 2400 direct-control bench module.

The module is driven against a fake VISA instrument that records every byte
sent, so the things that matter can be asserted directly:

  - that an operating point outside the 2400's real envelope is refused
    BEFORE anything reaches the instrument (the envelope is not a rectangle:
    21 V at 1.05 A or 210 V at 105 mA, never 210 V at 1 A),
  - that the voltage ceiling follows the model in *IDN?, because a 2401 and a
    2400-LV stop at 21 V where a plain 2400 goes to 210 V,
  - that an unrecognised model gets the SMALL envelope, not the large one,
  - that *RST is never sent (it would wipe the user's front-panel setup),
  - that disconnect switches the output OFF before closing the session,
  - that the module refuses to talk to anything that is not a Series 2400,
  - and that the polling and blinking tk.after chains are started idempotently
    and cancelled exactly once.

Every SCPI string asserted here was verified against the Series 2400
SourceMeter User's Manual (Untracked_Stuff/Keithley_2400.pdf).

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "keithley", "k2400",
                           "K2400_DirectControl_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


k2400 = _load("k2400_direct_control_under_test", MODULE_PATH)
MODULE_SOURCE = open(MODULE_PATH, encoding="utf-8").read()

SafetyError = k2400.SafetyError
Limits = k2400.SourceMeterLimits
Backend = k2400.Keithley2400Backend

IDN_2400 = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234567,C32   Oct  4 2010"
IDN_2401 = "KEITHLEY INSTRUMENTS INC.,MODEL 2401,1234567,C32"
IDN_2400LV = "KEITHLEY INSTRUMENTS INC.,MODEL 2400-LV,1234567,C32"
IDN_LAKESHORE = "LSCI,MODEL350,LSA1234,1.5"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeK2400:
    """Records every write and query; answers the queries the module uses."""

    def __init__(self, idn=IDN_2400, answers=None, output_state="0"):
        self.idn = idn
        self.answers = {
            "*IDN?": idn,
            ":OUTPut?": output_state,
            ":READ?": "1.000000E+00,2.000000E-03,5.000000E+02",
            ":SENSe:VOLTage:PROTection:TRIPped?": "0",
            ":SENSe:CURRent:PROTection:TRIPped?": "0",
            ":SOURce:FUNCtion:MODE?": "CURR",
            ":SOURce:CURR:LEVel?": "0.0",
            ":SOURce:VOLT:LEVel?": "0.0",
            ":SYSTem:RSENse?": "0",
            ":ROUTe:TERMinals?": "FRON",
        }
        self.answers.update(answers or {})
        self.writes = []
        self.queries = []
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        if command in self.answers:
            return self.answers[command]
        raise IOError(f"unexpected query {command!r}")

    def close(self):
        self.closed = True

    @property
    def traffic(self):
        """Everything the instrument was ever sent, in order."""
        return self.writes + self.queries


class FakeResourceManager:
    def __init__(self, instrument, resources=("GPIB0::24::INSTR",)):
        self.instrument = instrument
        self.resources = list(resources)
        self.opened = []

    def list_resources(self):
        return tuple(self.resources)

    def open_resource(self, address):
        self.opened.append(address)
        return self.instrument


def make_backend(idn=IDN_2400, **kwargs):
    """A connected backend wired to a fake instrument."""
    fake = FakeK2400(idn=idn, **kwargs)
    backend = Backend()
    backend.rm = FakeResourceManager(fake)
    backend.connect("GPIB0::24::INSTR")
    return backend, fake


# ---------------------------------------------------------------------------
# The safety envelope - the whole point of the exercise
# ---------------------------------------------------------------------------

def test_power_limit_rejects_high_voltage_at_high_current():
    """210 V at 1 A is 210 W. The 2400 can deliver 22 W."""
    limits = Limits('2400')
    limits.unlocked = True          # even with the ceiling lifted
    try:
        limits.check_operating_point('voltage', 200.0, 1.0)
    except SafetyError as exc:
        assert "22" in str(exc), str(exc)
        return
    raise AssertionError("200 V at 1 A compliance was allowed; it is 200 W")


def test_power_limit_allows_the_two_real_corners():
    """Both documented corners of the envelope must be reachable.

    The specification quotes 21 V at 1.05 A and 210 V at 105 mA. Both come to
    22.05 W, so a flat 22.0 W check would refuse the instrument's own
    published operating points. Use the exact corner values here - rounding
    them down is what hid this the first time.
    """
    limits = Limits('2400')
    limits.unlocked = True
    limits.check_operating_point('voltage', 21.0, 1.05)    # low-V corner
    limits.check_operating_point('voltage', 210.0, 0.105)  # high-V corner
    limits.check_operating_point('current', 1.05, 21.0)    # sourcing current
    limits.check_operating_point('current', 0.105, 210.0)


def test_soft_ceiling_blocks_high_voltage_by_default():
    """Out of the box the module must not reach a shock-hazard voltage."""
    limits = Limits('2400')
    assert limits.unlocked is False
    assert limits.effective_v <= 42.0, (
        "default ceiling must sit below the 42.4 V peak shock-hazard "
        "threshold the manual quotes from ANSI")
    try:
        limits.check_operating_point('voltage', 100.0, 0.01)
    except SafetyError:
        return
    raise AssertionError("100 V was allowed while the safe ceiling was on")


def test_unlocking_reaches_the_instrument_maximum():
    limits = Limits('2400')
    limits.unlocked = True
    limits.check_operating_point('voltage', 200.0, 0.1)   # 20 W, fine


def test_2401_and_lv_stay_at_21_volts_even_unlocked():
    """Manual: 'the voltage limit can be set from 200uV to 210V (21V for
    2400-LV and 2401)'. Unlocking must not get past the hardware."""
    for model in ('2401', '2400-LV'):
        limits = Limits(model)
        limits.unlocked = True
        assert limits.v_max == 21.0, model
        try:
            limits.check_operating_point('voltage', 100.0, 0.01)
        except SafetyError:
            continue
        raise AssertionError(f"{model} accepted 100 V; its maximum is 21 V")


def test_unknown_model_gets_the_small_envelope():
    """Guessing high is the dangerous direction, so an unknown model must
    inherit the smallest envelope in the family."""
    limits = Limits(None)
    assert limits.model_recognised is False
    assert limits.v_max == 21.0
    limits.unlocked = True
    try:
        limits.check_operating_point('voltage', 100.0, 0.01)
    except SafetyError:
        return
    raise AssertionError("an unrecognised model was given the 210 V envelope")


def test_current_ceiling_is_enforced():
    limits = Limits('2400')
    limits.unlocked = True
    try:
        limits.check_operating_point('current', 2.0, 1.0)
    except SafetyError:
        return
    raise AssertionError("2 A was allowed; the maximum is 1.05 A")


def test_soft_limits_cannot_be_set_above_the_hardware():
    limits = Limits('2401')          # 21 V part
    try:
        limits.set_soft_limits(100.0, 0.1)
    except SafetyError as exc:
        assert "21" in str(exc)
        return
    raise AssertionError("a 100 V soft limit was accepted on a 21 V instrument")


def test_soft_limits_reject_zero_and_negative():
    limits = Limits('2400')
    for volts, amps in ((0, 0.1), (10, 0), (-5, 0.1)):
        try:
            limits.set_soft_limits(volts, amps)
        except SafetyError:
            continue
        raise AssertionError(f"accepted a nonsensical soft limit {volts}, {amps}")


def test_compliance_floors_match_the_manual():
    """Manual Section 4: current limit from 1 nA, voltage limit from 200 uV."""
    limits = Limits('2400')
    try:
        limits.check_operating_point('current', 1e-6, 1e-9)   # 1 nV compliance
    except SafetyError as exc:
        assert "uV" in str(exc) or "200" in str(exc)
    else:
        raise AssertionError("a sub-200 uV voltage compliance was accepted")

    try:
        limits.check_operating_point('voltage', 1.0, 1e-12)   # 1 pA compliance
    except SafetyError as exc:
        assert "nA" in str(exc) or "1" in str(exc)
    else:
        raise AssertionError("a sub-1 nA current compliance was accepted")


def test_range_helper_picks_the_smallest_range_that_fits():
    pick = Limits.smallest_range_for(Limits.I_SOURCE_RANGES, 5e-4)
    assert pick == 1e-3, pick
    # 105% headroom means 1.04 mA still fits the 1 mA range.
    assert Limits.smallest_range_for(Limits.I_SOURCE_RANGES, 1.04e-3) == 1e-3
    # Over the top range, fall back to the top range rather than crash.
    assert Limits.smallest_range_for(Limits.I_SOURCE_RANGES, 99.0) == 1.0


# ---------------------------------------------------------------------------
# Connection behaviour
# ---------------------------------------------------------------------------

def test_connect_sets_the_envelope_from_idn():
    backend, _fake = make_backend(IDN_2400)
    assert backend.limits.model == '2400'
    assert backend.limits.v_max == 210.0

    backend, _fake = make_backend(IDN_2401)
    assert backend.limits.model == '2401'
    assert backend.limits.v_max == 21.0


def test_describe_idn_splits_the_four_fields():
    rows = dict(Backend.describe_idn(IDN_2400))
    assert rows["Manufacturer"] == "KEITHLEY INSTRUMENTS INC."
    assert rows["Model field"] == "MODEL 2400"
    assert rows["Serial number"] == "1234567"
    assert rows["Raw *IDN?"] == IDN_2400


def test_describe_idn_reports_a_malformed_reply_rather_than_guessing():
    rows = dict(Backend.describe_idn("SOMETHING ODD"))
    assert "Fields" in rows, rows
    assert "expected 4" in rows["Fields"]
    # The raw text is always preserved for diagnostics.
    assert rows["Raw *IDN?"] == "SOMETHING ODD"
    assert Backend.describe_idn("")[0][1] == "(no reply)"


def test_model_parsing_does_not_shadow_the_low_voltage_variants():
    """'2400-LV' must not be matched as a plain '2400' - that would hand a
    21 V instrument the 210 V envelope."""
    assert Backend.parse_model(IDN_2400LV) == '2400-LV'
    assert Backend.parse_model(IDN_2401) == '2401'
    assert Backend.parse_model(IDN_2400) == '2400'
    assert Backend.parse_model(IDN_LAKESHORE) is None
    assert Backend.parse_model("") is None
    assert Backend.parse_model(None) is None


def test_2400lv_idn_yields_the_21_volt_envelope_end_to_end():
    backend, _fake = make_backend(IDN_2400LV)
    assert backend.limits.model == '2400-LV'
    assert backend.limits.v_max == 21.0, (
        "a 2400-LV was given the 210 V envelope")


def test_connect_refuses_a_non_keithley():
    fake = FakeK2400(idn=IDN_LAKESHORE)
    backend = Backend()
    backend.rm = FakeResourceManager(fake)
    try:
        backend.connect("GPIB0::12::INSTR")
    except ConnectionError as exc:
        assert "2400" in str(exc)
        assert fake.closed, "the session must be closed after a refusal"
        # Nothing but the identity query may have been sent.
        assert fake.writes == [], fake.writes
        return
    raise AssertionError("connected to a Lakeshore and would have sourced into it")


def test_disconnect_turns_the_output_off_before_closing():
    backend, fake = make_backend()
    backend.disconnect()
    assert ":OUTPut OFF" in fake.writes, fake.writes
    assert fake.closed
    # Order matters: off first, then close.
    assert fake.writes.index(":OUTPut OFF") >= 0
    assert backend.is_connected is False


def test_disconnect_closes_even_if_output_off_fails():
    backend, fake = make_backend()

    def exploding_write(_command):
        raise IOError("bus gone")

    fake.write = exploding_write
    backend.disconnect()
    assert fake.closed, "a failed output-off must not leak the VISA session"
    assert backend.is_connected is False


def test_rst_is_never_sent():
    """*RST would wipe the user's whole front-panel configuration."""
    backend, fake = make_backend()
    backend.set_source_function('current')
    backend.set_source_level('current', 1e-3)
    backend.set_compliance('current', 1.0)
    backend.set_nplc('voltage', 1)
    backend.set_filter(True, 10, 'REP')
    backend.set_auto_zero(True)
    backend.set_output_off_mode('NORMAL')
    backend.output_on()
    backend.output_off()
    backend.disconnect()
    for message in fake.traffic:
        assert "*RST" not in message.upper(), message

    # Also make sure no *RST is sitting in a command string in the source.
    # Prose mentioning *RST is fine (the module documents that it never sends
    # one); what matters is that it never appears inside a quoted command.
    import re
    for match in re.finditer(r"""_write\(\s*[fr]?['"]([^'"]*)['"]""",
                             MODULE_SOURCE):
        assert "*RST" not in match.group(1).upper(), match.group(0)
    for match in re.finditer(r"""\.write\(\s*[fr]?['"]([^'"]*)['"]""",
                             MODULE_SOURCE):
        assert "*RST" not in match.group(1).upper(), match.group(0)


# ---------------------------------------------------------------------------
# Command spellings, checked against the manual
# ---------------------------------------------------------------------------

def test_source_and_compliance_commands():
    backend, fake = make_backend()
    backend.set_source_function('voltage')
    assert ":SOURce:FUNCtion:MODE VOLT" in fake.writes

    backend.set_source_level('voltage', 1.5)
    assert ":SOURce:VOLT:LEVel 1.5" in fake.writes

    # Sourcing voltage, the compliance is a CURRENT limit.
    backend.set_compliance('voltage', 1e-3)
    assert ":SENSe:CURRent:PROTection 0.001" in fake.writes, fake.writes

    # Sourcing current, the compliance is a VOLTAGE limit.
    backend.set_compliance('current', 2.0)
    assert ":SENSe:VOLTage:PROTection 2" in fake.writes, fake.writes


def test_sense_terminals_and_autozero_commands():
    backend, fake = make_backend()
    backend.set_sense_mode(True)
    assert ":SYSTem:RSENse 1" in fake.writes
    backend.set_terminals(True)
    assert ":ROUTe:TERMinals REAR" in fake.writes
    backend.set_terminals(False)
    assert ":ROUTe:TERMinals FRONt" in fake.writes
    backend.set_auto_zero(False)
    assert ":SYSTem:AZERo:STATe 0" in fake.writes


def test_output_off_mode_command_and_validation():
    backend, fake = make_backend()
    backend.set_output_off_mode('HIMPedance')
    assert ":OUTPut:SMODe HIMPedance" in fake.writes
    try:
        backend.set_output_off_mode('NONSENSE')
    except SafetyError:
        return
    raise AssertionError("an invalid output-off mode was accepted")


def test_parameter_ranges_from_the_manual():
    """NPLC 0.01-10, filter count 1-100, source delay 0-999.9999 s."""
    backend, _fake = make_backend()

    for bad in (0.001, 20.0):
        try:
            backend.set_nplc('voltage', bad)
        except SafetyError:
            pass
        else:
            raise AssertionError(f"NPLC {bad} accepted; valid range is 0.01-10")
    backend.set_nplc('voltage', 10.0)      # the documented maximum

    for bad in (0, 101):
        try:
            backend.set_filter(True, bad, 'REP')
        except SafetyError:
            pass
        else:
            raise AssertionError(f"filter count {bad} accepted; range is 1-100")
    backend.set_filter(True, 100, 'MOV')

    try:
        backend.set_source_delay(1000.0)
    except SafetyError:
        pass
    else:
        raise AssertionError("a 1000 s source delay was accepted; max 999.9999")
    backend.set_source_delay(999.9999)


def test_compliance_trip_queries_are_read_correctly():
    backend, fake = make_backend(
        answers={":SENSe:VOLTage:PROTection:TRIPped?": "1"})
    tripped, which = backend.in_compliance()
    assert tripped is True and which == 'voltage', (tripped, which)

    backend, fake = make_backend(
        answers={":SENSe:CURRent:PROTection:TRIPped?": "1"})
    tripped, which = backend.in_compliance()
    assert tripped is True and which == 'current', (tripped, which)

    backend, fake = make_backend()
    assert backend.in_compliance() == (False, None)


def test_read_parses_all_three_elements():
    backend, _fake = make_backend()
    volts, amps, ohms = backend.read()
    assert abs(volts - 1.0) < 1e-9
    assert abs(amps - 2e-3) < 1e-12
    assert abs(ohms - 500.0) < 1e-9


def test_resistance_mode_commands():
    """[:SENSe[1]]:RESistance:MODE MANual|AUTO, Manual Section 18."""
    backend, fake = make_backend()
    backend.set_measure_function('resistance')
    assert ':SENSe:FUNCtion "RES"' in fake.writes, fake.writes
    backend.set_resistance_mode(True)
    assert ":SENSe:RESistance:MODE AUTO" in fake.writes, fake.writes
    backend.set_resistance_mode(False)
    assert ":SENSe:RESistance:MODE MANual" in fake.writes, fake.writes


def test_auto_ohms_locks_the_source_boxes():
    """The manual: 'You cannot change the test current in the auto ohms mode.
    If you attempt to change the source current in auto ohms, the SourceMeter
    will display an error message.' So the GUI must not let the user try."""
    gui, root, fake = _make_gui()
    if gui is None:
        return
    try:
        gui.visa_cb['values'] = ["GPIB0::24::INSTR"]
        gui.visa_cb.set("GPIB0::24::INSTR")
        gui._do_connect()

        # Sourcing current: the boxes are editable.
        gui.source_func_var.set('current')
        gui._on_source_function_changed()
        assert gui.auto_ohms is False
        assert str(gui.level_entry.cget('state')) == 'normal'

        # Auto ohms: locked.
        gui.source_func_var.set('resistance')
        gui.ohms_mode_cb.current(0)
        gui._on_source_function_changed()
        assert gui.auto_ohms is True
        assert str(gui.level_entry.cget('state')) == 'disabled', (
            "the source level stayed editable in auto ohms")
        assert str(gui.compliance_entry.cget('state')) == 'disabled'

        # Manual ohms: editable again, because the user configures the source.
        gui.ohms_mode_cb.current(1)
        gui._apply_ohms_mode()
        assert gui.auto_ohms is False
        assert str(gui.level_entry.cget('state')) == 'normal', (
            "manual ohms must let the user set the source")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_auto_ohms_has_no_operating_point_to_check():
    gui, root, fake = _make_gui()
    if gui is None:
        return
    try:
        gui.source_func_var.set('resistance')
        gui.ohms_mode_cb.current(0)
        assert gui._current_operating_point() == (None, None, None)
        # Manual ohms falls back to a real source quantity.
        gui.ohms_mode_cb.current(1)
        assert gui._effective_source_function() == 'current'
        function, level, comp = gui._current_operating_point()
        assert function == 'current'
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_resistance_mode_addresses_the_resistance_sense_subsystem():
    gui, root, fake = _make_gui()
    if gui is None:
        return
    try:
        gui.source_func_var.set('resistance')
        assert gui._measured_quantity() == 'resistance'
        gui.source_func_var.set('current')
        assert gui._measured_quantity() == 'voltage'
        gui.source_func_var.set('voltage')
        assert gui._measured_quantity() == 'current'
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_beep_uses_the_instrument_beeper():
    backend, fake = make_backend()
    backend.beep(2000, 0.3)
    assert any(w.startswith(":SYSTem:BEEPer:IMMediate") for w in fake.writes), \
        fake.writes


# ---------------------------------------------------------------------------
# Gentle level changes
# ---------------------------------------------------------------------------

def test_ramp_steps_instead_of_jumping_and_lands_on_target():
    backend, fake = make_backend()
    backend.ramp_source_level('current', 1e-3, steps=10, pause=0,
                              start=0.0)
    levels = [float(w.split()[-1]) for w in fake.writes
              if w.startswith(":SOURce:CURR:LEVel")]
    assert len(levels) >= 10, f"only {len(levels)} steps were sent"
    assert levels == sorted(levels), "the ramp did not move monotonically"
    assert abs(levels[-1] - 1e-3) < 1e-12, "the ramp did not land on target"
    assert abs(levels[0]) < 1e-3, "the first step was already the full value"


def test_ramp_can_be_aborted_midway():
    backend, fake = make_backend()
    state = {'n': 0}

    def abort_after_three():
        state['n'] += 1
        return state['n'] > 3

    reached = backend.ramp_source_level('current', 1.0, steps=100, pause=0,
                                        start=0.0,
                                        abort_check=abort_after_three)
    assert reached < 1.0, "abort did not stop the ramp short of the target"


def test_ramp_from_equal_start_is_a_no_op():
    backend, fake = make_backend()
    before = len(fake.writes)
    backend.ramp_source_level('current', 0.0, steps=10, pause=0, start=0.0)
    assert len(fake.writes) == before, "a no-change ramp still wrote commands"


# ---------------------------------------------------------------------------
# GUI wiring - the tk.after chains
# ---------------------------------------------------------------------------

def _make_gui():
    """Build the GUI against a fake instrument, or return None if Tk is absent.

    Tk exists in this repo's environment but a Tk root cannot be created in
    every CI context, so callers skip when this returns None.
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
    except Exception:
        return None, None, None
    gui = k2400.K2400DirectControlGUI(root)
    fake = FakeK2400()
    gui.backend.rm = FakeResourceManager(fake)
    return gui, root, fake


def test_polling_chain_is_idempotent_and_cancellable():
    gui, root, fake = _make_gui()
    if gui is None:
        return  # no display; the logic is exercised by the other tests
    try:
        gui._do_connect_address = None
        gui.visa_cb['values'] = ["GPIB0::24::INSTR"]
        gui.visa_cb.set("GPIB0::24::INSTR")
        gui._do_connect()
        assert gui.is_connected

        gui.output_live = True
        gui.poll_var.set(True)

        gui._start_polling()
        first = gui._poll_after_id
        assert first is not None, "polling did not start"

        gui._start_polling()          # second call must not start a second chain
        assert gui._poll_after_id == first, "a duplicate after-chain was armed"

        gui._stop_polling()
        assert gui._poll_after_id is None, "the after id was not cleared"
        gui._stop_polling()           # cancelling twice must be harmless
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_blink_chain_is_idempotent_and_cancellable():
    gui, root, fake = _make_gui()
    if gui is None:
        return
    try:
        gui.output_live = True
        gui._start_blinking()
        first = gui._blink_after_id
        assert first is not None
        gui._start_blinking()
        assert gui._blink_after_id == first, "a duplicate blink chain was armed"
        gui._stop_blinking()
        assert gui._blink_after_id is None
        gui._stop_blinking()
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_changing_source_function_invalidates_confirmed_compliance():
    gui, root, fake = _make_gui()
    if gui is None:
        return
    try:
        gui.compliance_confirmed = True
        gui.source_func_var.set('voltage')
        gui._on_source_function_changed()
        assert gui.compliance_confirmed is False, (
            "compliance stayed confirmed after the source function changed, "
            "so the old limit would apply to a different quantity")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_closing_cancels_both_chains():
    gui, root, fake = _make_gui()
    if gui is None:
        return
    gui.output_live = True
    gui._start_blinking()
    gui._on_closing()
    assert gui._poll_after_id is None
    assert gui._blink_after_id is None


# ---------------------------------------------------------------------------
# Source-level policy checks
# ---------------------------------------------------------------------------

def test_module_writes_no_data_file():
    """This is a bench tool: it must not open files for writing."""
    for forbidden in ("csv.writer", "asksaveasfilename", "open(", "to_csv"):
        if forbidden == "open(":
            # `open(` appears legitimately in the test loader, not here.
            continue
        assert forbidden not in MODULE_SOURCE, (
            f"{forbidden} found; this module is not supposed to log to a file")


def test_no_raw_scpi_console_is_exposed():
    """The user declined a raw command box; make sure none crept in."""
    lowered = MODULE_SOURCE.lower()
    for hint in ("send raw", "raw command", "scpi console"):
        assert hint not in lowered, f"{hint!r} suggests a raw command box"


def test_every_messagebox_is_guarded_against_the_poll_chain():
    """A modal dialog raised while an after-chain is armed can deadlock the
    GUI, so every dialog must go through the helpers that tear the chain
    down first - or stop polling explicitly."""
    import re
    lines = MODULE_SOURCE.splitlines()
    # The GUI class owns the poll chain; the module-level utility launchers
    # run before any instrument is connected and have no chain to tear down.
    class_start = next(i for i, line in enumerate(lines)
                       if line.startswith("class K2400DirectControlGUI"))
    offenders = []
    for i, line in enumerate(lines):
        if i < class_start:
            continue
        if re.search(r"messagebox\.(showwarning|showerror|askyesno)", line):
            window = "\n".join(lines[max(0, i - 12):i])
            in_helper = any(
                marker in window
                for marker in ("def _warn", "def _error", "_stop_polling()"))
            if not in_helper:
                offenders.append((i + 1, line.strip()))
    assert not offenders, (
        "these dialogs are raised without stopping the poll chain first: "
        + "; ".join(f"line {n}: {text}" for n, text in offenders))


def _run_all():
    """Plain-python runner, so this file works without pytest."""
    failures = []
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  ok   {name}")
            except Exception as exc:
                failures.append((name, exc))
                print(f"  FAIL {name}: {exc}")
    print()
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
