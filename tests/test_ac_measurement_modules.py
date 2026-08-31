"""Tests for the ten AC measurement modules built around the Keithley 6221.

Five modules pair the 6221 with an SR830 lock-in and five pair it with a
Keithley 197A DMM; each set covers I-V, frequency scan, R-T under Lakeshore
350 control, and passive R-T against the Lakeshore 350 and the Cryo-con 34.
They are standalone files by design, which means the same conversions are
written out ten times, which in turn means a fix applied to nine of them is
exactly the failure this file exists to catch.

What is pinned here:

  - I_rms = I_peak / sqrt(2). The 6221 is programmed in PEAK amps and every
    voltage in these modules is RMS, so a resistance built without the
    conversion is wrong by exactly 1.414 -- large enough to matter and small
    enough to look plausible.
  - The instrument limits, transcribed from the Model 6220/6221 Reference
    Manual Table 7-4 (page 7-27). A transcription error there silently lets a
    value through that the instrument then clamps.
  - The health checks: for the SR830, the ones that catch a missing trigger
    link cable; for the 197A, the ones that say the reading is outside the
    meter's AC band or is buried in noise.
  - That the data row and the column header are the same length in every
    module, which no amount of running the GUI tells you until the file is
    opened afterwards.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

SR830_DIR = os.path.join(REPO_ROOT, "pica", "lockin", "sr830")
K197A_DIR = os.path.join(REPO_ROOT, "pica", "keithley", "k6221_k197a")

# (short name, folder, filename, thermometry kind)
MODULE_FILES = [
    ("sr830_iv", SR830_DIR, "IV_AC_K6221_SR830_GUI.py", None),
    ("sr830_fscan", SR830_DIR, "Frequency_Scan_K6221_SR830_GUI.py", None),
    ("sr830_rt_control", SR830_DIR,
     "RT_AC_K6221_SR830_L350_T_Control_GUI.py", "control"),
    ("sr830_rt_sensing", SR830_DIR,
     "RT_AC_K6221_SR830_L350_T_Sensing_GUI.py", "sensing"),
    ("sr830_rt_cc34", SR830_DIR,
     "RT_AC_K6221_SR830_CC34_T_Sensing_GUI.py", "sensing"),
    ("k197a_iv", K197A_DIR, "IV_AC_K6221_K197A_GUI.py", None),
    ("k197a_fscan", K197A_DIR, "Frequency_Scan_K6221_K197A_GUI.py", None),
    ("k197a_rt_control", K197A_DIR,
     "RT_AC_K6221_K197A_L350_T_Control_GUI.py", "control"),
    ("k197a_rt_sensing", K197A_DIR,
     "RT_AC_K6221_K197A_L350_T_Sensing_GUI.py", "sensing"),
    ("k197a_rt_cc34", K197A_DIR,
     "RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py", "sensing"),
]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULES = {}
for _name, _folder, _filename, _thermo in MODULE_FILES:
    _path = os.path.join(_folder, _filename)
    assert os.path.exists(_path), "missing module file: %s" % _path
    MODULES[_name] = _load("ac_module_%s" % _name, _path)

SR830_MODULES = [name for name in MODULES if name.startswith("sr830")]
K197A_MODULES = [name for name in MODULES if name.startswith("k197a")]
THERMO_KIND = {name: kind for name, _f, _n, kind in MODULE_FILES}


# -----------------------------------------------------------------------------
# The conversion every one of these modules can get wrong without raising
# -----------------------------------------------------------------------------

def test_peak_and_rms_round_trip_in_every_module():
    for name, module in MODULES.items():
        assert abs(module.rms_from_peak(module.peak_from_rms(1.0)) - 1.0) < 1e-12, name
        assert abs(module.rms_from_peak(1.0) - 1.0 / math.sqrt(2.0)) < 1e-15, name
        assert abs(module.peak_from_rms(1.0) - math.sqrt(2.0)) < 1e-15, name


def test_resistance_uses_rms_current_not_peak():
    """A 1 V rms reading on a 1 A PEAK drive is sqrt(2) ohms, not 1 ohm."""
    for name in SR830_MODULES:
        module = MODULES[name]
        current_rms = module.rms_from_peak(1.0)
        resistance, magnitude, theta = module.resistance_from_lockin(
            1.0, 0.0, current_rms)
        assert abs(resistance - math.sqrt(2.0)) < 1e-12, name
        assert abs(magnitude - math.sqrt(2.0)) < 1e-12, name
        assert abs(theta) < 1e-12, name

    for name in K197A_MODULES:
        module = MODULES[name]
        current_rms = module.rms_from_peak(1.0)
        assert abs(1.0 / current_rms - math.sqrt(2.0)) < 1e-12, name


def test_lockin_magnitude_is_never_below_the_in_phase_part():
    module = MODULES["sr830_iv"]
    resistance, magnitude, theta = module.resistance_from_lockin(
        3e-6, 4e-6, 1e-6)
    assert abs(resistance - 3.0) < 1e-9
    assert abs(magnitude - 5.0) < 1e-9
    assert magnitude >= resistance
    assert abs(theta - math.degrees(math.atan2(4.0, 3.0))) < 1e-9


# -----------------------------------------------------------------------------
# 6221 limits and sweep spacing
# -----------------------------------------------------------------------------

def test_6221_limits_match_the_manual():
    for name, module in MODULES.items():
        assert module.WAVE_FREQ_MIN == 0.001, name
        assert module.WAVE_FREQ_MAX == 100000.0, name
        assert module.WAVE_AMPL_MIN == 2e-12, name
        assert module.WAVE_AMPL_MAX == 0.105, name
        assert module.COMPLIANCE_MIN == 0.1, name
        assert module.COMPLIANCE_MAX == 105.0, name
        assert module.PMARK_LINE_DEFAULT == 3, name


def test_validate_drive_refuses_what_the_6221_would_clamp():
    for name, module in MODULES.items():
        module.validate_drive(133.0, 1e-5, 10.0)          # a good setpoint

        for frequency, current, compliance in (
                (0.0, 1e-5, 10.0),          # below the wave generator minimum
                (200000.0, 1e-5, 10.0),     # above its maximum
                (133.0, 1.0, 10.0),         # above 105 mA peak
                (133.0, 1e-15, 10.0),       # below 2 pA peak
                (133.0, 1e-5, 0.0),         # compliance below 0.1 V
                (133.0, 1e-5, 200.0)):      # compliance above 105 V
            try:
                module.validate_drive(frequency, current, compliance)
            except ValueError:
                continue
            raise AssertionError(
                "%s accepted %g Hz, %g A peak, %g V compliance"
                % (name, frequency, current, compliance))


def test_detector_frequency_ceiling_is_the_right_one():
    """The SR830 external reference reaches 102 kHz; the 197A ceiling is the
    6221's own, because the band check does the talking there."""
    for name in SR830_MODULES:
        assert MODULES[name].DETECTOR_FREQ_MAX == 102000.0, name
        assert MODULES[name].USE_PHASE_MARKER is True, name
    for name in K197A_MODULES:
        assert MODULES[name].DETECTOR_FREQ_MAX == 100000.0, name
        # Nothing is locked to the marker in this pairing, so it stays off.
        assert MODULES[name].USE_PHASE_MARKER is False, name


def test_sweep_spacing():
    module = MODULES["sr830_fscan"]
    assert module.linear_points(0.0, 10.0, 1) == [0.0]
    assert module.linear_points(0.0, 10.0, 3) == [0.0, 5.0, 10.0]
    decade = module.log_points(1.0, 1000.0, 4)
    for got, want in zip(decade, [1.0, 10.0, 100.0, 1000.0]):
        assert abs(got - want) < 1e-9
    for bad in ((0.0, 10.0, 3), (1.0, 0.0, 3), (-1.0, 10.0, 3)):
        try:
            module.log_points(*bad)
        except ValueError:
            continue
        raise AssertionError("log_points accepted %r" % (bad,))


# -----------------------------------------------------------------------------
# Geometry
# -----------------------------------------------------------------------------

def test_geometry_formulas():
    module = MODULES["sr830_iv"]
    # Bar: rho = R w t / L. 10 ohm through 1 mm x 100 um over 1 mm.
    assert abs(module.resistivity_from_resistance(
        10.0, 1, 1e-3, 100e-6, 1e-3) - 1e-3) < 1e-15
    # van der Pauw, symmetric: rho = (pi/ln2) t R.
    expected = (math.pi / math.log(2.0)) * 100e-6 * 10.0
    assert abs(module.resistivity_from_resistance(
        10.0, 2, 0.0, 100e-6, 0.0) - expected) < 1e-15
    # No geometry asked for means no resistivity, not a zero.
    assert module.resistivity_from_resistance(10.0, 0, 0.0, 0.0, 0.0) is None
    assert module.sheet_resistance(None, 100e-6) is None
    assert abs(module.sheet_resistance(1e-3, 100e-6) - 10.0) < 1e-12

    for bad in ((10.0, 1, 0.0, 100e-6, 1e-3),
                (10.0, 1, 1e-3, 0.0, 1e-3),
                (10.0, 1, 1e-3, 100e-6, 0.0),
                (10.0, 2, 0.0, 0.0, 0.0)):
        try:
            module.resistivity_from_resistance(*bad)
        except ValueError:
            continue
        raise AssertionError("resistivity accepted %r" % (bad,))


# -----------------------------------------------------------------------------
# SR830: the checks that catch a missing trigger link cable
# -----------------------------------------------------------------------------

def test_lockin_health_is_quiet_when_everything_is_right():
    for name in SR830_MODULES:
        module = MODULES[name]
        assert module.lockin_health(0, 133.0, 133.0, 1e-4, 20) == []


def test_lockin_health_catches_an_unplugged_reference():
    for name in SR830_MODULES:
        module = MODULES[name]
        unlocked = module.lockin_health(
            1 << module.LIA_UNLOCK_BIT, 133.0, 133.0, 1e-4, 20)
        assert any("Reference unlock" in problem for problem in unlocked), name

        # Free-running: the lock-in is not at the frequency the 6221 was told
        # to generate.
        mismatched = module.lockin_health(0, 133.0, 400.0, 1e-4, 20)
        assert any("frequency mismatch" in problem.lower()
                   for problem in mismatched), name


def test_lockin_health_catches_overload_and_a_full_scale_reading():
    module = MODULES["sr830_rt_control"]
    for bit in range(3):
        problems = module.lockin_health(1 << bit, 133.0, 133.0, 1e-4, 20)
        assert problems, "overload bit %d went unreported" % bit
    # SENS code 20 is 10 mV full scale; 9.5 mV is above the 90% headroom.
    assert module.SENS_VOLTS[20] == 10e-3
    assert module.lockin_health(0, 133.0, 133.0, 9.5e-3, 20)


def test_sr830_tables_line_up():
    module = MODULES["sr830_iv"]
    assert len(module.SENS_LABELS) == len(module.SENS_VOLTS) == 27
    assert len(module.OFLT_LABELS) == len(module.OFLT_SECONDS) == 20
    assert len(module.OFSL_LABELS) == len(module.SETTLE_TIME_CONSTANTS) == 4
    # 300 ms time constant, 24 dB/oct: ten time constants, plus the extra.
    assert abs(module.settle_seconds(9, 3, 0.0) - 3.0) < 1e-12
    assert abs(module.settle_seconds(9, 3, 1.5) - 4.5) < 1e-12
    # A negative extra settle must not shorten the wait.
    assert abs(module.settle_seconds(9, 3, -5.0) - 3.0) < 1e-12


# -----------------------------------------------------------------------------
# Keithley 197A: no reference, so every check is made of the numbers
# -----------------------------------------------------------------------------

def test_meter_health_is_quiet_on_a_good_point():
    for name in K197A_MODULES:
        module = MODULES[name]
        readings = [1.000e-3, 1.001e-3, 0.999e-3]
        assert module.meter_health(1.0e-3, 2e-6, 133.0, readings) == []


def test_meter_health_flags_a_drive_outside_the_ac_band():
    for name in K197A_MODULES:
        module = MODULES[name]
        low = module.meter_health(
            1e-3, 1e-6, module.K197A_ACV_BAND_MIN / 2.0, [1e-3])
        high = module.meter_health(
            1e-3, 1e-6, module.K197A_ACV_BAND_MAX * 2.0, [1e-3])
        assert any("outside the 197A AC volts band" in p for p in low), name
        assert any("outside the 197A AC volts band" in p for p in high), name


def test_meter_health_flags_noise_and_impossible_readings():
    module = MODULES["k197a_iv"]
    # A spread wider than 10% of the mean: the signal is at the noise floor.
    noisy = module.meter_health(1e-3, 5e-4, 133.0, [0.8e-3, 1.2e-3, 1e-3])
    assert any("spread over" in problem for problem in noisy)
    # An AC volts reading cannot be zero or negative.
    assert module.meter_health(0.0, 0.0, 133.0, [0.0])
    assert module.meter_health(-1e-3, 0.0, 133.0, [-1e-3])


def test_k197a_reply_parsing():
    module = MODULES["k197a_fscan"]
    value, raw = module.parse_k197a_reading("NACV+1.23456E-3")
    assert abs(value - 1.23456e-3) < 1e-12
    assert raw == "NACV+1.23456E-3"
    assert module.parse_k197a_reading("no number here")[0] is None
    assert module.parse_k197a_reading(None)[0] is None

    assert module.looks_like_k197a_reading("NACV+1.23456E-3")
    # A SCPI identity string also contains digits; the comma count is what
    # separates it from a reading.
    assert not module.looks_like_k197a_reading(
        "KEITHLEY INSTRUMENTS INC.,MODEL 2182,1234567,A05")
    assert not module.looks_like_k197a_reading("no number here")


def test_k197a_reading_rate_floor():
    for name in K197A_MODULES:
        module = MODULES[name]
        # The 197A specifications give a maximum of 3 readings per second.
        assert abs(module.K197A_MIN_READ_INTERVAL - 0.34) < 1e-12, name


# -----------------------------------------------------------------------------
# Thermometry
# -----------------------------------------------------------------------------

def test_instruments_are_identified_by_idn_and_not_by_address():
    module = MODULES["sr830_rt_cc34"]
    assert module.is_k6221_idn(
        "KEITHLEY INSTRUMENTS INC.,MODEL 6221,1234567,A05")
    assert not module.is_k6221_idn(
        "KEITHLEY INSTRUMENTS INC.,MODEL 2182,1234567,A05")
    assert module.is_sr830_idn("Stanford_Research_Systems,SR830,s/n,ver1.07")
    assert not module.is_sr830_idn("Stanford_Research_Systems,SR860,s/n,ver")
    assert module.is_cryocon_idn("Cryocon,34,204683,3.03A")
    assert not module.is_cryocon_idn("LSCI,MODEL350,1234567,1.5")

    lakeshore = MODULES["sr830_rt_control"]
    assert lakeshore.is_lakeshore_idn("LSCI,MODEL350,1234567,1.5")
    # The Lakeshore now sits on the Cryocon's old factory address, so an
    # address that "looks like the Lakeshore's" proves nothing.
    assert not lakeshore.is_lakeshore_idn("Cryocon,34,204683,3.03A")


def test_cryocon_status_strings_are_named_and_not_raised_as_float_errors():
    for name in ("sr830_rt_cc34", "k197a_rt_cc34"):
        module = MODULES[name]
        assert abs(module.parse_cryocon_number("77.350", "temperature", "A")
                   - 77.35) < 1e-12, name
        # A trailing unit character, which plain float() rejects outright.
        assert abs(module.parse_cryocon_number("77.350K", "temperature", "A")
                   - 77.35) < 1e-12, name
        # A multi-channel reply comes back as semicolon separated fields.
        assert abs(module.parse_cryocon_number(
            "77.350;80.000", "temperature", "A") - 77.35) < 1e-12, name

        for reply in ("-------", ".......", "N/A", "NACK", ""):
            try:
                module.parse_cryocon_number(reply, "temperature", "A")
            except module.CryoconStatusError:
                continue
            raise AssertionError(
                "%s turned %r into a number" % (name, reply))


def test_lakeshore_heater_range_map():
    for name in ("sr830_rt_control", "k197a_rt_control"):
        module = MODULES[name]
        assert module.HEATER_RANGE_CODES["Off"] == 0, name
        assert module.HEATER_RANGE_CODES["High"] == 5, name
        assert set(module.HEATER_RANGE_LABELS) == set(
            module.HEATER_RANGE_CODES), name


# -----------------------------------------------------------------------------
# The output file
# -----------------------------------------------------------------------------

def _row_field_count(module, thermo_kind):
    """How many fields a data row carries, counted the way the module builds it.

    Timestamp and elapsed, then the temperature where there is one, then the
    three drive columns, the detector's own, and resistivity, sheet resistance
    and flags.
    """
    detector = module.DETECTOR_COLUMNS.count(',') + 1
    return 2 + (1 if thermo_kind else 0) + 3 + detector + 3


def test_the_header_and_the_row_are_the_same_length():
    for name, module in MODULES.items():
        columns = module.DATA_COLUMNS.split(',')
        assert len(columns) == _row_field_count(module, THERMO_KIND[name]), name
        assert columns[0] == "Timestamp", name
        assert columns[-1] == "Flags", name
        if THERMO_KIND[name]:
            assert "Temperature (K)" in columns, name
        else:
            assert "Temperature (K)" not in columns, name


def test_detector_row_fields_fill_the_detector_columns():
    lockin_point = {
        'locked_hz': 133.0, 'x': 1e-4, 'y': 1e-6, 'r_volts': 1.0e-4,
        'theta': 0.57, 'resistance': 14.14, 'magnitude': 14.15,
    }
    for name in SR830_MODULES:
        module = MODULES[name]
        fields = module.detector_row_fields(lockin_point)
        assert len(fields) == module.DETECTOR_COLUMNS.count(',') + 1, name
        assert not any(',' in field for field in fields), name

    meter_point = {
        'voltage': 1e-3, 'spread': 2e-6, 'count': 5, 'resistance': 141.4,
    }
    for name in K197A_MODULES:
        module = MODULES[name]
        fields = module.detector_row_fields(meter_point)
        assert len(fields) == module.DETECTOR_COLUMNS.count(',') + 1, name
        assert not any(',' in field for field in fields), name


class FakeInstrumentIdentity:
    def __init__(self, idn, address, channel="A", output=1):
        self.idn = idn
        self.address = address
        self.channel = channel
        # Only the Lakeshore controller has an output; the passive
        # thermometers ignore it.
        self.output = output


SR830_SETTINGS = {
    "fmod": 0, "harm": 1, "phas": 0.0, "rslp": 1, "isrc": 1, "icpl": 0,
    "ignd": 0, "ilin": 3, "sync": 1, "sens": 20, "oflt": 9, "ofsl": 3,
    "rmod": 1,
}

K197A_SETTINGS = {"averages": 5, "read_interval": 0.4, "range_name": "auto"}


def _params(module, thermo_kind):
    params = {
        'compliance': 10.0,
        'pmark_line': 3,
        'pmark_phase': 0.0,
        'settle': 3.0,
        'drive_description': "133.0000 Hz, 1.0000E-05 A rms",
        'geometry': {'code': 1, 'width': 1e-3, 'thickness': 1e-4,
                     'length': 1e-3},
    }
    if thermo_kind == 'control':
        params['temperature'] = {
            'channel': "A", 'heater_range': "High", 'start_temp': 300.0,
            'end_temp': 100.0, 'rate': 1.0, 'cutoff': 90.0,
            'stabilise_window': 2.0, 'interval': 10.0,
        }
    elif thermo_kind == 'sensing':
        params['temperature'] = {
            'channel': "A", 'interval': 10.0, 'stop_low': 1.0,
            'stop_high': 400.0,
        }
    return params


def test_every_header_line_is_commented_and_ends_with_the_columns():
    source = FakeInstrumentIdentity(
        "KEITHLEY INSTRUMENTS INC.,MODEL 6221,1234567,A05", "GPIB0::13::INSTR")
    for name, module in MODULES.items():
        thermo_kind = THERMO_KIND[name]
        if name in SR830_MODULES:
            detector = FakeInstrumentIdentity(
                "Stanford_Research_Systems,SR830,s/n,ver1.07",
                "GPIB0::8::INSTR")
            settings = SR830_SETTINGS
        else:
            detector = FakeInstrumentIdentity(
                "Keithley 197A (no identify query on this interface)",
                "GPIB0::7::INSTR")
            settings = K197A_SETTINGS
        thermometer = None
        if thermo_kind:
            thermometer = FakeInstrumentIdentity(
                "LSCI,MODEL350,1234567,1.5" if "cc34" not in name
                else "Cryocon,34,204683,3.03A", "GPIB0::12::INSTR")

        header = module.build_log_header(
            "Sample-A", "PD", source, detector, thermometer, settings,
            _params(module, thermo_kind))
        lines = header.strip().split("\n")
        assert lines[-1] == module.DATA_COLUMNS, name
        for line in lines[:-1]:
            assert line.startswith("#"), "%s: uncommented header line %r" % (
                name, line)
        assert any("MODEL 6221" in line for line in lines), name
        # The geometry that was actually used has to be in the file.
        assert any("Geometry:" in line for line in lines), name
        if thermo_kind:
            assert any("channel" in line.lower() for line in lines), name


def test_passive_modules_say_so_in_the_file_header():
    """A passive run that looks like a controlled one in the file is a run
    whose temperature history cannot be reconstructed later."""
    for name in ("sr830_rt_sensing", "sr830_rt_cc34",
                 "k197a_rt_sensing", "k197a_rt_cc34"):
        module = MODULES[name]
        thermometer = FakeInstrumentIdentity(
            "Cryocon,34,204683,3.03A" if "cc34" in name
            else "LSCI,MODEL350,1234567,1.5", "GPIB0::12::INSTR")
        lines = module.thermometer_header_lines(
            thermometer, _params(module, 'sensing')['temperature'])
        assert any("NONE from this module" in line for line in lines), name


def test_the_197a_file_says_the_settings_were_not_read_back():
    for name in K197A_MODULES:
        module = MODULES[name]
        detector = FakeInstrumentIdentity(
            "Keithley 197A (no identify query on this interface)",
            "GPIB0::7::INSTR")
        lines = module.detector_header_lines(
            detector.idn, detector.address, K197A_SETTINGS)
        assert any("not a read-back" in line for line in lines), name
        assert any("upper bound" in line for line in lines), name


if __name__ == "__main__":
    failures = 0
    for test_name, test in sorted(list(globals().items())):
        if not test_name.startswith("test_") or not callable(test):
            continue
        try:
            test()
            print("PASS %s" % test_name)
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (test_name, exc))
    print("%d failure(s)." % failures)
    sys.exit(1 if failures else 0)
