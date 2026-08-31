"""Tests for the Tektronix AFG 3022B function-generator control module.

No Tk window and no GPIB cable: the limit checks, the shape/load parsing and
the preview maths are module-level functions for exactly this reason, and the
backend is exercised against a fake VISA session that records every command.
The numbers used here are the AFG 3022B specification (25 MHz sine,
12.5 MHz square/pulse, 500 kHz ramp, 20 mVpp-10 Vpp into 50 ohm, doubled
into an open circuit).

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

AFG_PATH = os.path.join(REPO_ROOT, "pica", "tektronix",
                        "Function_Gen_AFG3022B_GUI.py")
V1_PATH = os.path.join(REPO_ROOT, "pica", "main.py")
V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


afg = _load("pica_afg3022b_module", AFG_PATH)
AFG_SOURCE = open(AFG_PATH, encoding="utf-8").read()


# --------------------------------------------------------------- shape names

def test_every_dropdown_label_maps_to_a_scpi_mnemonic():
    for label in afg.SHAPE_LABELS:
        assert afg.shape_code(label) in afg.SHAPE_TO_LABEL


def test_the_instrument_spelling_of_a_shape_comes_back_as_a_label():
    assert afg.shape_label("SIN") == "Sine"
    assert afg.shape_label("SINusoid") == "Sine"
    assert afg.shape_label('"PRN"') == "Noise"
    assert afg.shape_label("USER2") == "Arbitrary (USER2)"


def test_an_unrecognised_shape_is_not_silently_turned_into_a_sine():
    assert afg.shape_label("WHATEVER") == "WHATEVER"


# ---------------------------------------------------------------- load parsing

def test_a_high_impedance_load_is_recognised_however_it_is_reported():
    for reported in ("INF", "INFinity", "9.9E+37", "High Z (open)"):
        assert afg.load_key(reported) == "INF"


def test_a_fifty_ohm_load_stays_fifty_ohm():
    for reported in ("50", "50.0", "", "nonsense"):
        assert afg.load_key(reported) == "50"


def test_the_amplitude_window_doubles_into_an_open_circuit():
    assert afg.amplitude_limits("50") == (0.020, 10.0)
    assert afg.amplitude_limits("INF") == (0.040, 20.0)


# ------------------------------------------------------------------- limits

def _params(**overrides):
    base = {'shape': "SIN", 'frequency': 1000.0, 'amplitude': 1.0,
            'unit': "VPP", 'offset': 0.0, 'phase': 0.0, 'duty': 50.0,
            'symmetry': 50.0, 'load': "50"}
    base.update(overrides)
    return base


def test_an_ordinary_sine_passes():
    assert afg.validate_channel(_params()) == []


def test_a_sine_above_twenty_five_megahertz_is_refused():
    assert afg.validate_channel(_params(frequency=30e6))


def test_a_square_above_twelve_and_a_half_megahertz_is_refused():
    assert afg.validate_channel(_params(shape="SQU", frequency=20e6))
    assert afg.validate_channel(_params(shape="SQU", frequency=10e6)) == []


def test_a_ramp_above_five_hundred_kilohertz_is_refused():
    assert afg.validate_channel(_params(shape="RAMP", frequency=1e6))
    assert afg.validate_channel(_params(shape="RAMP", frequency=4e5)) == []


def test_an_amplitude_past_the_fifty_ohm_rail_is_refused_but_passes_open():
    assert afg.validate_channel(_params(amplitude=15.0))
    assert afg.validate_channel(_params(amplitude=15.0, load="INF")) == []


def test_an_amplitude_below_the_floor_is_refused():
    assert afg.validate_channel(_params(amplitude=0.001))


def test_offset_plus_half_the_amplitude_may_not_pass_the_rail():
    # 4 V offset on a 4 Vpp sine peaks at 6 V, past the 5 V the AFG 3022B
    # delivers into 50 ohm.
    assert afg.validate_channel(_params(amplitude=4.0, offset=4.0))
    assert afg.validate_channel(_params(amplitude=4.0, offset=2.0)) == []
    assert afg.validate_channel(
        _params(amplitude=4.0, offset=4.0, load="INF")) == []


def test_dc_needs_no_frequency_or_amplitude():
    assert afg.validate_channel(
        _params(shape="DC", frequency=None, amplitude=None, offset=1.0)) == []


def test_noise_needs_no_frequency():
    assert afg.validate_channel(_params(shape="PRN", frequency=None)) == []


def test_a_pulse_duty_outside_the_instrument_range_is_refused():
    assert afg.validate_channel(_params(shape="PULS", duty=0.0))
    assert afg.validate_channel(_params(shape="PULS", duty=120.0))
    assert afg.validate_channel(_params(shape="PULS", duty=30.0)) == []


def test_ramp_symmetry_is_a_percentage():
    assert afg.validate_channel(_params(shape="RAMP", frequency=1e3,
                                        symmetry=150.0))
    assert afg.validate_channel(_params(shape="RAMP", frequency=1e3,
                                        symmetry=0.0)) == []


def test_a_phase_beyond_one_turn_is_refused():
    assert afg.validate_channel(_params(phase=400.0))


# ------------------------------------------------------------------ preview

def test_a_sine_preview_spans_the_amplitude_around_the_offset():
    _t, y = afg.preview_waveform("SIN", 1000.0, 2.0, offset=1.0)
    assert abs(y.max() - 2.0) < 0.05
    assert abs(y.min() - 0.0) < 0.05


def test_a_pulse_preview_honours_its_duty_cycle():
    _t, y = afg.preview_waveform("PULS", 1000.0, 2.0, duty=25.0, points=4000)
    high = (y > 0).sum() / y.size
    assert abs(high - 0.25) < 0.02


def test_a_dc_preview_is_the_offset_and_nothing_else():
    _t, y = afg.preview_waveform("DC", 1000.0, 5.0, offset=2.5)
    assert y.min() == y.max() == 2.5


def test_an_arbitrary_record_is_not_guessed_at():
    # USERn lives in the generator's memory; drawing an invented shape for
    # it would be a lie on the screen.
    _t, y = afg.preview_waveform("USER1", 1000.0, 2.0, offset=0.5)
    assert y.min() == y.max() == 0.5


def test_the_noise_preview_is_the_same_every_redraw():
    _t, first = afg.preview_waveform("PRN", 1000.0, 1.0)
    _t, second = afg.preview_waveform("PRN", 1000.0, 1.0)
    assert (first == second).all()


def test_a_zero_frequency_does_not_divide_by_zero():
    t, y = afg.preview_waveform("SIN", 0.0, 1.0)
    assert t.size == y.size > 0


# ------------------------------------------------------------------ backend

class FakeInstrument:
    """Records every write and answers the queries the module makes."""

    def __init__(self, idn="TEKTRONIX,AFG3022B,C012345,SCPI:99.0 FV:1.02"):
        self.idn = idn
        self.writes = []
        self.timeout = None
        self.write_termination = None
        self.read_termination = None
        self.closed = False

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        if command == '*IDN?':
            return self.idn + "\n"
        if command == 'SYSTem:ERRor?':
            return '0,"No error"\n'
        answers = {
            'SOURce1:FUNCtion:SHAPe?': 'SIN',
            'SOURce1:FREQuency?': '1.0E+03',
            'SOURce1:VOLTage:UNIT?': 'VPP',
            'SOURce1:VOLTage:LEVel:IMMediate:AMPLitude?': '1.000',
            'SOURce1:VOLTage:LEVel:IMMediate:OFFSet?': '0.000',
            'SOURce1:PHASe:ADJust?': '0.000',
            'OUTPut1:IMPedance?': '5.0E+01',
            'OUTPut1:STATe?': '0',
        }
        if command in answers:
            return answers[command] + "\n"
        raise AssertionError(f"unexpected query: {command}")

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self, instrument):
        self._instrument = instrument
        self.closed = False

    def open_resource(self, address):
        return self._instrument

    def close(self):
        self.closed = True


def _backend(monkeypatched_instrument):
    """Build a backend around a fake instrument without touching pyvisa."""
    backend = afg.AFG3022B_Backend.__new__(afg.AFG3022B_Backend)
    backend.visa_address = "GPIB0::11::INSTR"
    backend.instrument = monkeypatched_instrument
    backend.identity = monkeypatched_instrument.idn
    backend._rm = FakeResourceManager(monkeypatched_instrument)
    return backend


def test_the_module_never_resets_the_generator():
    # A *RST would blank both channels of a box that is very likely already
    # driving somebody's experiment. Prose about not sending it is fine; a
    # quoted "*RST" would be a command literal on its way to the cable.
    assert "'*RST" not in AFG_SOURCE
    assert '"*RST' not in AFG_SOURCE


def test_connecting_refuses_an_address_that_is_not_an_afg():
    instrument = FakeInstrument(idn="KEITHLEY INSTRUMENTS,MODEL 2400,x,y")
    backend = afg.AFG3022B_Backend.__new__(afg.AFG3022B_Backend)
    backend.visa_address = "GPIB0::24::INSTR"
    backend._rm = FakeResourceManager(instrument)
    backend.instrument = None
    backend.identity = ""

    afg.pyvisa_saved = getattr(afg, 'pyvisa', None)
    afg.pyvisa = type("RM", (), {
        "ResourceManager": staticmethod(lambda: FakeResourceManager(instrument))})
    try:
        raised = False
        try:
            backend.connect()
        except IOError:
            raised = True
        assert raised, "a non-AFG identity must stop the connection"
        # Nothing beyond *IDN? may have been sent to the stranger.
        assert instrument.writes == []
    finally:
        afg.pyvisa = afg.pyvisa_saved


def test_apply_sets_the_load_before_the_levels():
    instrument = FakeInstrument()
    backend = _backend(instrument)
    backend.apply_channel(1, {
        'shape': "SIN", 'frequency': 1000.0, 'amplitude': 1.0, 'unit': "VPP",
        'offset': 0.0, 'phase': 0.0, 'duty': 50.0, 'symmetry': 50.0,
        'load': "INF"})
    writes = instrument.writes
    assert writes[0] == "OUTPut1:IMPedance INFinity"
    amplitude_index = next(
        i for i, w in enumerate(writes) if "AMPLitude" in w)
    assert amplitude_index > 0


def test_apply_writes_only_the_channel_it_was_given():
    instrument = FakeInstrument()
    backend = _backend(instrument)
    backend.apply_channel(1, {
        'shape': "SQU", 'frequency': 1000.0, 'amplitude': 1.0, 'unit': "VPP",
        'offset': 0.0, 'phase': 0.0, 'duty': 50.0, 'symmetry': 50.0,
        'load': "50"})
    assert not any("SOURce2" in w or "OUTPut2" in w for w in instrument.writes)


def test_duty_and_symmetry_go_only_to_the_shape_that_owns_them():
    instrument = FakeInstrument()
    backend = _backend(instrument)
    backend.apply_channel(1, {
        'shape': "SQU", 'frequency': 1000.0, 'amplitude': 1.0, 'unit': "VPP",
        'offset': 0.0, 'phase': 0.0, 'duty': 30.0, 'symmetry': 40.0,
        'load': "50"})
    # The AFG 3022B square wave is fixed at 50 %: neither control applies.
    assert not any("DCYCle" in w or "SYMMetry" in w for w in instrument.writes)


def test_a_rejected_command_is_raised_not_swallowed():
    class ComplainingInstrument(FakeInstrument):
        def query(self, command):
            if command == 'SYSTem:ERRor?':
                if not getattr(self, '_complained', False):
                    self._complained = True
                    return '-222,"Data out of range"\n'
                return '0,"No error"\n'
            return FakeInstrument.query(self, command)

    backend = _backend(ComplainingInstrument())
    raised = False
    try:
        backend.set_output(1, True)
    except IOError:
        raised = True
    assert raised, "an error in the AFG queue must reach the operator"


def test_reading_a_channel_reports_phase_in_degrees():
    # PHASe:ADJust? answers in radians whatever unit it was set in.
    class RadianInstrument(FakeInstrument):
        def query(self, command):
            if command == 'SOURce1:PHASe:ADJust?':
                return '1.5708\n'
            return FakeInstrument.query(self, command)

    state = _backend(RadianInstrument()).read_channel(1)
    assert abs(state['phase'] - 90.0) < 0.01


def test_closing_the_panel_does_not_switch_the_outputs_off():
    # Disconnect must leave a running experiment running; ALL OUTPUTS OFF is
    # the deliberate way to stop it.
    marker = "def disconnect_instrument"
    body = AFG_SOURCE.split(marker, 1)[1].split("def _set_controls_enabled", 1)[0]
    assert "all_outputs_off" not in body


# --------------------------------------------------------------- the window

def _headless_root():
    try:
        import tkinter as tk
    except ImportError:
        return None
    try:
        root = tk.Tk()
    except Exception:
        return None
    root.withdraw()
    return root


def _skip(reason):
    try:
        import pytest
        pytest.skip(reason)
    except ImportError:
        print(f"SKIP: {reason}")
    return None


def test_the_window_builds_all_the_way_through():
    """Build the real panel -- the path that crashed on the first launch.

    create_channel_frame(1) ends by greying the fields the shape does not
    use, which asks for a preview redraw; channel 2's widgets and the
    figure do not exist yet at that moment, and the console does not exist
    when the Information panel reports a missing logo. Both were fatal in
    the constructor, so the window never appeared and the launcher only
    said it had started a process.
    """
    root = _headless_root()
    if root is None:
        return _skip("no display for a Tk root")
    try:
        app = afg.AFG3022BControlGUI(root)
        assert app.widgets[1]['shape'].get() == "Sine"
        assert app.widgets[2]['shape'].get() == "Sine"
        # The console must have taken over whatever was logged before it.
        assert app.console_widget is not None
        assert app._pending_log == []
    finally:
        root.destroy()


def test_logging_before_the_console_exists_does_not_raise():
    panel = afg.AFG3022BControlGUI.__new__(afg.AFG3022BControlGUI)
    panel.console_widget = None
    panel._pending_log = []
    panel.log("logo missing")
    assert panel._pending_log and "logo missing" in panel._pending_log[0]


# ------------------------------------------------------------ launcher wiring

def test_the_module_is_registered_in_both_launchers():
    v1 = open(V1_PATH, encoding="utf-8").read()
    v2 = open(V2_PATH, encoding="utf-8").read()
    assert "tektronix/Function_Gen_AFG3022B_GUI.py" in v1
    assert "AFG3022B Function Generator" in v1
    assert "AFG3022B Function Generator" in v2


def test_the_registered_path_exists():
    assert os.path.exists(AFG_PATH)


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
