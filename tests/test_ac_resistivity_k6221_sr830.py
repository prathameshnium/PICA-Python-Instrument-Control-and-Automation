"""Tests for the AC resistivity module (Keithley 6221 current + SR830 lock-in).

The thing this module can get wrong without raising anything is the number it
writes into the file. The 6221 is programmed in PEAK amps and the SR830
answers in RMS volts, so a resistance built by dividing one by the other
without converting is wrong by exactly sqrt(2) = 1.414 -- large enough to
matter and small enough to look plausible. That conversion, the in-phase
convention R = X / I_rms, the geometry formulas, and the checks that catch a
missing trigger link cable are pinned here.

The instrument limits are transcribed from the Model 6220/6221 Reference
Manual Table 7-4 (page 7-27) and the SR830 manual chapter 5; a transcription
error there silently lets a value through that the instrument then clamps.

Runnable as plain Python as well as under pytest.
"""

import ast
import importlib.util
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

GUI_PATH = os.path.join(REPO_ROOT, "pica", "lockin", "sr830",
                        "AC_Resistivity_K6221_SR830_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gui = _load("ac_resistivity_under_test", GUI_PATH)
GUI_SOURCE = open(GUI_PATH, encoding="utf-8").read()

SR830_IDN = "Stanford_Research_Systems,SR830,s/n12345,ver1.07"
K6221_IDN = "KEITHLEY INSTRUMENTS INC.,MODEL 6221,1234567,A05"


class FakeInstrument:
    def __init__(self, idn, answers=None):
        self.answers = {"*IDN?": idn}
        self.answers.update(answers or {})
        self.writes = []
        self.queries = []
        self.closed = False
        self.read_termination = None
        self.write_termination = None
        self.timeout = None

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        if command in self.answers:
            return self.answers[command]
        raise IOError(f"unexpected query {command!r}")

    def close(self):
        self.closed = True


class _PatchVisa:
    def __init__(self, instrument):
        self.instrument = instrument

    def __enter__(self):
        self._old = gui.pyvisa
        self._old_flag = gui.PYVISA_AVAILABLE
        instrument = self.instrument

        class _FakeRM:
            def open_resource(self, address):
                return instrument

        class _FakeVisa:
            ResourceManager = staticmethod(_FakeRM)

        gui.pyvisa = _FakeVisa
        gui.PYVISA_AVAILABLE = True
        return instrument

    def __exit__(self, *exc):
        gui.pyvisa = self._old
        gui.PYVISA_AVAILABLE = self._old_flag
        return False


# ------------------------------------------- the peak / RMS conversion

def test_the_6221_peak_amplitude_becomes_an_rms_current():
    # 6221 manual 7-5 and note 3, page 7-29: SOUR:WAVE:AMPL is peak.
    assert gui.rms_from_peak(1.0) == 1.0 / math.sqrt(2.0)
    assert abs(gui.rms_from_peak(10e-3) - 7.0710678e-3) < 1e-10


def test_peak_and_rms_conversions_are_inverses():
    for value in (2e-12, 1e-6, 1e-3, 0.105):
        assert abs(gui.peak_from_rms(gui.rms_from_peak(value)) - value) \
            < value * 1e-12


def test_a_10ma_peak_drive_across_100_ohms_reads_707mv_rms():
    # The whole point of the module in one number: 10 mA peak is 7.071 mA rms,
    # so a 100 ohm sample develops 0.7071 V rms in phase with the current.
    current_rms = gui.rms_from_peak(10e-3)
    resistance, magnitude, theta = gui.resistance_from_lockin(
        0.70710678, 0.0, current_rms)
    assert abs(resistance - 100.0) < 1e-6
    assert abs(magnitude - 100.0) < 1e-6
    assert abs(theta) < 1e-9


def test_dividing_by_the_peak_current_would_be_wrong_by_root_two():
    # Guards the failure this module exists to avoid: the naive division gives
    # a resistance 1.414x too small, which reads like a plausible sample.
    current_peak = 10e-3
    naive = 0.70710678 / current_peak
    correct = 0.70710678 / gui.rms_from_peak(current_peak)
    assert abs(correct / naive - math.sqrt(2.0)) < 1e-9


# ------------------------------------------- the in-phase convention

def test_resistance_uses_x_and_the_magnitude_is_reported_beside_it():
    resistance, magnitude, theta = gui.resistance_from_lockin(3e-3, 4e-3, 1e-3)
    assert abs(resistance - 3.0) < 1e-12       # X / I
    assert abs(magnitude - 5.0) < 1e-12        # sqrt(X^2+Y^2) / I
    assert abs(theta - math.degrees(math.atan2(4, 3))) < 1e-9


def test_the_magnitude_is_never_smaller_than_the_in_phase_resistance():
    for x, y in ((1e-3, 0.0), (1e-3, 1e-4), (-1e-3, 5e-4), (0.0, 1e-3)):
        resistance, magnitude, _theta = gui.resistance_from_lockin(x, y, 1e-3)
        assert magnitude >= abs(resistance) - 1e-15


def test_a_zero_or_negative_current_is_refused_not_divided_by():
    for bad in (0.0, -1e-6):
        try:
            gui.resistance_from_lockin(1e-3, 0.0, bad)
        except ValueError:
            continue
        raise AssertionError("a %r current was divided by" % bad)


# ------------------------------------------- geometry

def test_no_geometry_gives_no_resistivity():
    assert gui.resistivity_from_resistance(100.0, 0, 1e-3, 1e-4, 1e-3) is None


def test_bar_geometry_is_resistance_times_area_over_length():
    # 100 ohm across a 1 mm x 100 um cross section, probes 1 mm apart:
    # rho = 100 x (1e-3 x 100e-6) / 1e-3 = 1e-2 ohm m.
    rho = gui.resistivity_from_resistance(100.0, 1, 1e-3, 100e-6, 1e-3)
    assert abs(rho - 100.0 * 1e-3 * 100e-6 / 1e-3) < 1e-18
    assert abs(rho - 1e-2) < 1e-15


def test_van_der_pauw_carries_the_pi_over_ln2_factor():
    rho = gui.resistivity_from_resistance(100.0, 2, 0.0, 100e-6, 0.0)
    assert abs(rho - (math.pi / math.log(2.0)) * 100e-6 * 100.0) < 1e-18


def test_a_missing_dimension_is_refused_rather_than_dividing_by_zero():
    for code, args in ((1, (0.0, 1e-4, 1e-3)), (1, (1e-3, 1e-4, 0.0)),
                       (2, (0.0, 0.0, 0.0))):
        try:
            gui.resistivity_from_resistance(100.0, code, *args)
        except ValueError:
            continue
        raise AssertionError("geometry %d accepted %r" % (code, args))


def test_sheet_resistance_is_resistivity_over_thickness():
    assert gui.sheet_resistance(1e-5, 100e-6) == 1e-5 / 100e-6
    assert gui.sheet_resistance(None, 100e-6) is None
    assert gui.sheet_resistance(1e-5, 0.0) is None


# ------------------------------------------- instrument limits

def test_the_6221_wave_limits_match_table_7_4():
    # SOUR:WAVE:AMPLitude = 2e-12 to 0.105 A peak; FREQuency 0 to 1e5 Hz.
    assert (gui.WAVE_AMPL_MIN, gui.WAVE_AMPL_MAX) == (2e-12, 0.105)
    assert gui.WAVE_FREQ_MAX == 100000.0
    assert (gui.COMPLIANCE_MIN, gui.COMPLIANCE_MAX) == (0.1, 105.0)
    assert (gui.PMARK_LINE_MIN, gui.PMARK_LINE_MAX) == (1, 6)
    assert gui.PMARK_LINE_DEFAULT == 3


def test_out_of_range_drive_is_refused_before_it_is_sent():
    bad = [
        (0.0005, 1e-3, 10.0),          # below 1 mHz
        (200000.0, 1e-3, 10.0),        # above the 6221 wave generator
        (110000.0, 1e-3, 10.0),        # above the SR830 external ref limit
        (100.0, 0.2, 10.0),            # above 105 mA peak
        (100.0, 1e-13, 10.0),          # below 2 pA peak
        (100.0, 1e-3, 0.05),           # compliance below 0.1 V
        (100.0, 1e-3, 200.0),          # compliance above 105 V
    ]
    for frequency, current, compliance in bad:
        try:
            gui.validate_drive(frequency, current, compliance)
        except ValueError:
            continue
        raise AssertionError(
            "accepted %g Hz, %g A peak, %g V" % (frequency, current, compliance))


def test_boundary_drive_values_are_accepted():
    gui.validate_drive(gui.WAVE_FREQ_MIN, gui.WAVE_AMPL_MIN,
                       gui.COMPLIANCE_MIN)
    # The pair's ceiling is the 6221's 100 kHz, not the SR830's 102 kHz
    # external reference limit: the narrower of the two wins.
    assert gui.WAVE_FREQ_MAX < gui.SR830_EXT_FREQ_MAX
    gui.validate_drive(gui.WAVE_FREQ_MAX, gui.WAVE_AMPL_MAX,
                       gui.COMPLIANCE_MAX)


def test_the_sr830_tables_have_their_full_code_range():
    assert len(gui.SENS_LABELS) == len(gui.SENS_VOLTS) == 27
    assert gui.SENS_VOLTS[0] == 2e-9 and gui.SENS_VOLTS[-1] == 1.0
    assert len(gui.OFLT_LABELS) == len(gui.OFLT_SECONDS) == 20
    assert gui.OFLT_SECONDS[0] == 10e-6 and gui.OFLT_SECONDS[-1] == 30e3
    assert gui.OFSL_DB == [6, 12, 18, 24]
    assert len(gui.LIA_STATUS_BITS) == 8


def test_the_time_constant_table_is_monotonic():
    for earlier, later in zip(gui.OFLT_SECONDS, gui.OFLT_SECONDS[1:]):
        assert later > earlier
    for earlier, later in zip(gui.SENS_VOLTS, gui.SENS_VOLTS[1:]):
        assert later > earlier


# ------------------------------------------- settling

def test_the_settle_wait_grows_with_the_time_constant_and_the_slope():
    # 300 ms code 9, 24 dB/oct code 3 -> 10 time constants -> 3 s.
    assert abs(gui.settle_seconds(9, 3) - 3.0) < 1e-12
    # The same time constant on the shallowest filter settles sooner.
    assert gui.settle_seconds(9, 0) < gui.settle_seconds(9, 3)
    assert gui.settle_seconds(9, 3, 2.0) == gui.settle_seconds(9, 3) + 2.0
    # A negative extra wait cannot shorten the settle.
    assert gui.settle_seconds(9, 3, -5.0) == gui.settle_seconds(9, 3)


def test_settle_multipliers_never_decrease_with_slope():
    for earlier, later in zip(gui.SETTLE_TIME_CONSTANTS,
                              gui.SETTLE_TIME_CONSTANTS[1:]):
        assert later >= earlier


# ------------------------------------------- the health checks

def _health(**kwargs):
    args = dict(lia_status=0, programmed_hz=133.0, measured_hz=133.0,
                x_volts=1e-6, sens_code=20)
    args.update(kwargs)
    return gui.lockin_health(**args)


def test_a_healthy_point_reports_nothing():
    assert _health() == []


def test_an_unplugged_reference_cable_is_caught_by_the_unlock_bit():
    problems = _health(lia_status=1 << gui.LIA_UNLOCK_BIT)
    assert len(problems) == 1
    assert "trigger link" in problems[0].lower()


def test_a_reference_frequency_that_disagrees_with_the_6221_is_caught():
    # The lock-in free-running at its own last frequency while the 6221 is at
    # 133 Hz would otherwise produce a perfectly plausible looking number.
    problems = _health(measured_hz=1000.0)
    assert any("mismatch" in problem for problem in problems)
    # A 0.5% difference is measurement jitter, not a fault.
    assert _health(measured_hz=133.0 * 1.005) == []


def test_input_filter_and_output_overloads_are_all_reported():
    for bit in (0, 1, 2):
        problems = _health(lia_status=1 << bit)
        assert problems == [gui.LIA_STATUS_BITS[bit]], bit


def test_a_reading_near_full_scale_is_flagged_before_it_clips():
    full_scale = gui.SENS_VOLTS[20]      # 10 mV/nA
    assert _health(x_volts=0.5 * full_scale) == []
    problems = _health(x_volts=0.95 * full_scale)
    assert any("sensitivity" in problem for problem in problems)
    # And a negative X of the same size is just as close to the rail.
    assert gui.lockin_health(0, 133.0, 133.0, -0.95 * full_scale, 20)


# ------------------------------------------- setpoint lists

def test_continuous_mode_is_one_setpoint_the_loop_repeats():
    assert gui.build_setpoints(0, 133.0, 1e-3, 0, 0, 1, False) \
        == [(133.0, 1e-3)]


def test_a_current_sweep_holds_the_frequency_and_a_frequency_sweep_the_current():
    currents = gui.build_setpoints(1, 133.0, 1e-3, 1e-6, 1e-4, 3, True)
    assert [frequency for frequency, _current in currents] == [133.0] * 3
    assert [round(current, 12) for _f, current in currents] \
        == [1e-6, 1e-5, 1e-4]

    frequencies = gui.build_setpoints(2, 133.0, 1e-3, 10.0, 30.0, 3, False)
    assert [current for _f, current in frequencies] == [1e-3] * 3
    assert [frequency for frequency, _c in frequencies] == [10.0, 20.0, 30.0]


def test_sweep_endpoints_are_both_included():
    for points in (linear for linear in (2, 3, 5, 11)):
        values = gui.linear_points(1.0, 2.0, points)
        assert len(values) == points
        assert abs(values[0] - 1.0) < 1e-12
        assert abs(values[-1] - 2.0) < 1e-12
        values = gui.log_points(1.0, 100.0, points)
        assert abs(values[0] - 1.0) < 1e-12
        assert abs(values[-1] - 100.0) < 1e-9


def test_a_one_point_sweep_is_the_start_value():
    assert gui.linear_points(5.0, 9.0, 1) == [5.0]
    assert gui.log_points(5.0, 9.0, 1) == [5.0]


def test_a_logarithmic_sweep_through_zero_is_refused():
    for start, stop in ((0.0, 10.0), (-1.0, 10.0), (1.0, 0.0)):
        try:
            gui.log_points(start, stop, 5)
        except ValueError:
            continue
        raise AssertionError("log sweep accepted %g to %g" % (start, stop))


def test_an_empty_sweep_is_refused():
    for spacing in (gui.linear_points, gui.log_points):
        try:
            spacing(1.0, 2.0, 0)
        except ValueError:
            continue
        raise AssertionError("%s accepted zero points" % spacing.__name__)


# ------------------------------------------- identity guards

def test_the_lockin_backend_refuses_anything_that_is_not_an_sr830():
    for idn in (K6221_IDN, "KEITHLEY INSTRUMENTS INC.,MODEL 2182A,x,y", ""):
        instrument = FakeInstrument(idn)
        with _PatchVisa(instrument):
            try:
                gui.SR830Lockin("GPIB0::8::INSTR")
            except ConnectionError:
                assert instrument.closed
                # Nothing but OUTX 1 may have been written to a stranger.
                assert instrument.writes == ['OUTX 1'], instrument.writes
                continue
        raise AssertionError("a %r was accepted as an SR830" % idn)


def test_the_source_backend_refuses_anything_that_is_not_a_6221():
    for idn in (SR830_IDN, "KEITHLEY INSTRUMENTS INC.,MODEL 2400,x,y", ""):
        instrument = FakeInstrument(idn)
        with _PatchVisa(instrument):
            try:
                gui.K6221WaveSource("GPIB0::13::INSTR")
            except ConnectionError:
                assert instrument.closed
                # No current source command may reach a stranger at all.
                assert instrument.writes == [], instrument.writes
                continue
        raise AssertionError("a %r was accepted as a 6221" % idn)


def test_a_real_pair_is_accepted():
    lockin = FakeInstrument(SR830_IDN)
    with _PatchVisa(lockin):
        backend = gui.SR830Lockin("GPIB0::8::INSTR")
    assert backend.idn == SR830_IDN
    source = FakeInstrument(K6221_IDN)
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    assert backend.idn == K6221_IDN


def test_the_idn_markers_are_case_insensitive_and_safe_on_junk():
    assert gui.is_sr830_idn("stanford_research_systems,sr830,0,1")
    assert not gui.is_sr830_idn(None)
    assert gui.is_k6221_idn("keithley instruments inc.,model 6221,0,1")
    assert not gui.is_k6221_idn(12345)


# ------------------------------------------- the command sequences

def test_outx_1_goes_out_before_the_first_lockin_query():
    lockin = FakeInstrument(SR830_IDN)
    with _PatchVisa(lockin):
        gui.SR830Lockin("GPIB0::8::INSTR")
    assert lockin.writes[0] == 'OUTX 1'
    assert lockin.queries[0] == '*IDN?'


def test_the_lockin_is_put_on_the_external_reference_and_the_ttl_edge():
    lockin = FakeInstrument(SR830_IDN)
    with _PatchVisa(lockin):
        backend = gui.SR830Lockin("GPIB0::8::INSTR")
    lockin.writes.clear()
    backend.configure_for_external_reference(
        harmonic=1, phase=0.0, isrc=1, icpl=0, ignd=0, ilin=3, sync=1,
        sens=20, oflt=9, ofsl=3, rmod=1)
    # FMOD 0 is external, RSLP 1 is the TTL rising edge the 1 us 6221 phase
    # marker pulse presents. Either one wrong and the lock-in measures at the
    # wrong frequency.
    assert lockin.writes[0] == 'FMOD 0'
    assert lockin.writes[1] == 'RSLP 1'
    assert 'HARM 1' in lockin.writes
    assert 'ISRC 1' in lockin.writes
    assert 'SENS 20' in lockin.writes
    assert 'OFLT 9' in lockin.writes
    # The time constant is set before the sensitivity so the last write is the
    # one whose settling the run then waits for.
    assert lockin.writes.index('OFSL 3') < lockin.writes.index('OFLT 9')


def test_the_lockin_refuses_a_harmonic_or_phase_outside_the_manual_limits():
    lockin = FakeInstrument(SR830_IDN)
    with _PatchVisa(lockin):
        backend = gui.SR830Lockin("GPIB0::8::INSTR")
    for harmonic, phase in ((0, 0.0), (20000, 0.0), (1, -400.0), (1, 800.0)):
        lockin.writes.clear()
        try:
            backend.configure_for_external_reference(
                harmonic, phase, 1, 0, 0, 3, 1, 20, 9, 3, 1)
        except ValueError:
            assert lockin.writes == []
            continue
        raise AssertionError("accepted harmonic %r phase %r" % (harmonic, phase))


def test_snap_takes_x_and_y_in_one_query():
    lockin = FakeInstrument(
        SR830_IDN, {'SNAP? 1,2,3,4': "1.0E-3,2.0E-4,1.02E-3,11.3"})
    with _PatchVisa(lockin):
        backend = gui.SR830Lockin("GPIB0::8::INSTR")
    x, y, r, theta = backend.snap()
    assert (x, y) == (1.0e-3, 2.0e-4)
    assert (r, theta) == (1.02e-3, 11.3)
    # One query, not four: X and Y must come from the same instant or the
    # magnitude and the phase built from them are of two different moments.
    assert lockin.queries.count('SNAP? 1,2,3,4') == 1


def test_the_source_is_prepared_as_an_infinite_sine_with_a_phase_marker():
    source = FakeInstrument(K6221_IDN)
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    source.writes.clear()
    backend.prepare(compliance_v=10.0, pmark_line=3, pmark_phase=0.0)
    assert 'SOUR:WAVE:FUNC SIN' in source.writes
    assert 'SOUR:CURR:COMP 10.0000' in source.writes
    assert 'SOUR:WAVE:OFFS 0' in source.writes
    assert 'SOUR:WAVE:RANG BEST' in source.writes
    # INFinity, so a long settle cannot outlast the waveform.
    assert 'SOUR:WAVE:DUR:TIME INF' in source.writes
    assert 'SOUR:WAVE:PMAR:STAT ON' in source.writes
    assert 'SOUR:WAVE:PMAR:OLIN 3' in source.writes
    assert 'SOUR:WAVE:PMAR 0.0' in source.writes
    # The reset must come first or it undoes everything set after it.
    assert source.writes[0] == '*RST'


def test_setting_a_drive_aborts_then_arms_then_initiates():
    source = FakeInstrument(K6221_IDN, {'OUTP?': '1'})
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    source.writes.clear()
    backend.set_drive(133.0, 10e-3)
    order = [command.split()[0] for command in source.writes]
    assert order == ['SOUR:WAVE:ABOR', 'SOUR:WAVE:FREQ', 'SOUR:WAVE:AMPL',
                     'SOUR:WAVE:ARM', 'SOUR:WAVE:INIT'], source.writes
    # The amplitude goes out in peak amps, exactly as given.
    amplitude = [c for c in source.writes if c.startswith('SOUR:WAVE:AMPL')][0]
    assert abs(float(amplitude.split()[1]) - 10e-3) < 1e-15


def test_the_output_is_switched_on_when_init_did_not_do_it():
    source = FakeInstrument(K6221_IDN, {'OUTP?': '0'})
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    source.writes.clear()
    backend.set_drive(133.0, 1e-3)
    assert source.writes[-1] == 'OUTP ON'


def test_switching_the_output_off_aborts_the_waveform_first():
    source = FakeInstrument(K6221_IDN)
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    source.writes.clear()
    backend.output_off()
    assert source.writes == ['SOUR:WAVE:ABOR', 'OUTP OFF']
    assert backend.output_on is False


def test_closing_the_source_drops_the_current_first():
    source = FakeInstrument(K6221_IDN)
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    source.writes.clear()
    backend.close()
    assert 'OUTP OFF' in source.writes
    assert source.closed


def test_the_error_queue_is_read_as_a_code_and_a_message():
    source = FakeInstrument(
        K6221_IDN, {'SYST:ERR?': '-221,"Settings conflict"'})
    with _PatchVisa(source):
        backend = gui.K6221WaveSource("GPIB0::13::INSTR")
    code, text = backend.read_error()
    assert code == -221
    assert text == "Settings conflict"


# ------------------------------------------- header and columns

def test_the_header_records_the_convention_the_numbers_were_built_with():
    settings = {"fmod": 0, "harm": 1, "phas": 0.0, "rslp": 1, "isrc": 1,
                "icpl": 0, "ignd": 0, "ilin": 3, "sync": 1, "sens": 20,
                "oflt": 9, "ofsl": 3, "rmod": 1}
    drive = {"mode": 0, "frequency": 133.0, "current_peak": 10e-3,
             "compliance": 10.0, "pmark_phase": 0.0, "pmark_line": 3,
             "settle": 3.0}
    geometry = {"code": 1, "width": 1e-3, "thickness": 100e-6, "length": 1e-3}
    header = gui.build_log_header(
        "AC_Resistivity_K6221_SR830_GUI.py", "1.0", "Sample-A", "PD",
        SR830_IDN, "GPIB0::8::INSTR", K6221_IDN, "GPIB0::13::INSTR",
        settings, drive, geometry)
    assert "R = X / I_rms" in header
    assert "I_rms = I_peak / sqrt(2)" in header
    assert "7.071068E-03 A rms" in header
    assert "trigger link line 3" in header
    assert header.rstrip().endswith(gui.DATA_COLUMNS)
    for line in header.splitlines():
        if line and line != gui.DATA_COLUMNS:
            assert line.startswith("#"), line


def test_every_data_column_has_a_unit_or_is_a_label():
    columns = gui.DATA_COLUMNS.split(",")
    assert columns[0] == "Timestamp"
    assert "R in-phase (Ohm)" in columns
    assert "R magnitude (Ohm)" in columns
    assert "I rms (A)" in columns
    assert "I peak (A)" in columns
    assert "Frequency locked (Hz)" in columns
    # One row of the writer must produce exactly this many fields.
    assert len(columns) == 15


# ------------------------------------------- no stray commands

def test_no_command_outside_the_documented_mnemonic_sets_is_ever_sent():
    """Every literal write()/query() is a command from one of the two manuals.

    A typo in a mnemonic is not an error on the bus, it is a command that does
    not happen, and the run carries on producing numbers regardless.
    """
    known = {
        # SR830 manual ch.5
        'OUTX', '*IDN?', 'FMOD', 'RSLP', 'HARM', 'PHAS', 'ISRC', 'ICPL',
        'IGND', 'ILIN', 'SYNC', 'RMOD', 'OFSL', 'OFLT', 'SENS', 'FREQ?',
        'SNAP?', 'LIAS?', 'SENS?', 'AGAN', 'APHS', 'FMOD?', 'HARM?', 'PHAS?',
        'RSLP?', 'ISRC?', 'ICPL?', 'IGND?', 'ILIN?', 'SYNC?', 'OFLT?',
        'OFSL?', 'RMOD?',
        # 6221 manual sections 7 and 10
        '*RST', '*CLS', 'SOUR:CURR:COMP', 'SOUR:WAVE:FUNC', 'SOUR:WAVE:OFFS',
        'SOUR:WAVE:RANG', 'SOUR:WAVE:DUR:TIME', 'SOUR:WAVE:PMAR:STAT',
        'SOUR:WAVE:PMAR:OLIN', 'SOUR:WAVE:PMAR', 'SOUR:WAVE:FREQ',
        'SOUR:WAVE:AMPL', 'SOUR:WAVE:ARM', 'SOUR:WAVE:INIT',
        'SOUR:WAVE:ABOR', 'OUTP', 'OUTP?', 'SYST:ERR?',
    }
    for node in ast.walk(ast.parse(GUI_SOURCE)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") not in ("write", "query"):
            continue
        # Only the VISA session, not the open() handle the data file is
        # written through, which also has a .write.
        receiver = getattr(node.func, "value", None)
        if getattr(receiver, "attr", "") != "instrument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                head = arg.value.split()[0] if arg.value.split() else ""
                assert head in known, arg.value
            elif isinstance(arg, ast.BinOp) and \
                    isinstance(arg.left, ast.Constant) and \
                    isinstance(arg.left.value, str):
                head = arg.left.value.split()[0]
                assert head in known, arg.left.value


def test_the_module_never_divides_a_voltage_by_a_peak_current():
    """The peak-to-RMS conversion has exactly one home, and it is used.

    If a second division by current appears somewhere else in the file, this
    is the test that notices before the sqrt(2) does.
    """
    assert GUI_SOURCE.count("def rms_from_peak") == 1
    assert "current_peak_a / math.sqrt(2.0)" in GUI_SOURCE \
        or "peak_amps / math.sqrt(2.0)" in GUI_SOURCE
    # resistance_from_lockin is the only place a voltage meets a current.
    assert GUI_SOURCE.count("x_volts / current_rms") == 1


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
