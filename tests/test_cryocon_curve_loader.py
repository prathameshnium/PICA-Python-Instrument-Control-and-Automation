"""Tests for pica/cryocon/Sensor_Curve_Loader_CC34_GUI.py.

The module turns a Lake Shore calibrated-sensor file into a Cryo-con .crv
curve and sends it to a Model 34 with CALCUR. A wrong curve does not announce
itself: the instrument accepts it, reads a plausible-looking temperature, and
every measurement made against that sensor afterwards is quietly wrong. So the
tests here are mostly about the ways it could be wrong rather than the way it
should be right.

The three that matter most:

  * test_columns_are_not_swapped and test_crv_body_is_reading_then_temperature
    A Lake Shore .dat is temperature-first; a Cryo-con curve is reading-first.
    Swapping them is the single easiest catastrophic mistake, and once the
    numbers are in a file with no labels the two orders are indistinguishable.

  * test_dat_and_340_describe_the_same_sensor
    The .dat (raw calibration, ohms, temperature-first) and the .340 (Lake
    Shore's own breakpoint curve, log-ohms, reading-first) are two independent
    files for the same physical sensor, written by Lake Shore in opposite
    column orders. Converting the .dat the way the module does and comparing
    it against the .340 checks the whole chain against a source this
    repository did not write.

  * test_a_headerless_two_column_file_is_refused
    The module must refuse to guess a column order rather than pick one.

Everything is run against the real files on the Lake Shore CD copy in
Untracked_Stuff/, which is where the lab Cernox calibration actually lives.
Those files are untracked, so every test that needs them skips cleanly when
they are absent.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import math
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                           "Sensor_Curve_Loader_CC34_GUI.py")

CAL_DIR = os.path.join(REPO_ROOT, "Untracked_Stuff", "Curv_for_Cernox",
                       "Cernox", "Calibration Data", "1068")
DAT_17680 = os.path.join(CAL_DIR, "X17680.dat")
DAT_17681 = os.path.join(CAL_DIR, "X17681.dat")
TBL_17680 = os.path.join(CAL_DIR, "X17680.tbl")
LS340_17680 = os.path.join(CAL_DIR, "X17680.340")

HAVE_CAL_FILES = all(os.path.exists(path) for path in
                     (DAT_17680, DAT_17681, TBL_17680, LS340_17680))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LOADER = _load("cc34_curve_loader", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()


class SkipTest(Exception):
    """Raised when a test needs the untracked Lake Shore CD files."""


def _need_cal_files():
    if not HAVE_CAL_FILES:
        raise SkipTest(f"Lake Shore calibration files not present in {CAL_DIR}")


# ---------------------------------------------------------------------------
# Reading the Lake Shore files
# ---------------------------------------------------------------------------

def test_dat_file_reads_as_the_raw_calibration():
    """X17680.dat is 71 points of temperature-and-ohms, 3.59 K to 330 K.

    The numbers are the file's own first and last rows, so a reader that
    silently dropped a row, mis-parsed the E-notation or skipped the header
    differently would move them.
    """
    _need_cal_files()
    source = LOADER.load_sensor_file(DAT_17680)
    assert source["kind"] == "lakeshore-dat"
    assert source["units"] == "OHMS"
    assert len(source["points"]) == 71

    temperatures = [t for t, _ in source["points"]]
    ohms = [r for _, r in source["points"]]
    assert math.isclose(min(temperatures), 3.59132424209341, rel_tol=1e-12)
    assert math.isclose(max(temperatures), 330.027223722539, rel_tol=1e-12)
    assert math.isclose(max(ohms), 977.251462533428, rel_tol=1e-12)
    assert math.isclose(min(ohms), 43.7606182230672, rel_tol=1e-12)


def test_columns_are_not_swapped():
    """Temperature goes in the temperature slot and ohms in the reading slot.

    In a Lake Shore .dat the temperature column comes first. If the reader
    took the columns positionally in the Cryo-con order instead, every
    'temperature' here would be a resistance between 43 and 977, and every
    'reading' a temperature between 3 and 330. The two ranges are used to
    tell them apart, because that is the only thing that distinguishes them
    once the labels are gone.
    """
    _need_cal_files()
    source = LOADER.load_sensor_file(DAT_17680)
    for temperature, reading in source["points"]:
        assert 3.0 < temperature < 340.0, (
            f"temperature {temperature} is outside the range the .dat covers; "
            "the columns look swapped")
    # And the sensor really is an NTC: the coldest point is the largest
    # resistance. A swap would invert this.
    coldest = min(source["points"], key=lambda pair: pair[0])
    warmest = max(source["points"], key=lambda pair: pair[0])
    assert coldest[1] > warmest[1]


def test_both_lab_cernox_sensors_read():
    """X17680 is the one in use; X17681 is its pair on the same sales order."""
    _need_cal_files()
    for path in (DAT_17680, DAT_17681):
        source = LOADER.load_sensor_file(path)
        assert source["units"] == "OHMS"
        assert 50 <= len(source["points"]) <= LOADER.MAX_CURVE_POINTS
        temperatures = [t for t, _ in source["points"]]
        assert min(temperatures) < 5.0
        assert max(temperatures) > 300.0


def test_tbl_file_reads_past_the_sensitivity_columns():
    """X17680.tbl has four columns; only temperature and resistance are used."""
    _need_cal_files()
    source = LOADER.load_sensor_file(TBL_17680)
    assert source["kind"] == "lakeshore-tbl"
    assert source["units"] == "OHMS"
    for temperature, reading in source["points"]:
        assert 3.0 < temperature < 340.0
        assert 20.0 < reading < 1200.0


def test_340_file_reads_as_log_ohms_in_cryocon_column_order():
    """The .340 is already reading-first and already log-ohm.

    Its header says 'Data Format: 4 (Log Ohms/Kelvin)' and 'Number of
    Breakpoints: 129'; both are checked, because a reader that took the wrong
    format code would be out by a factor of 10 to the power of everything.
    """
    _need_cal_files()
    source = LOADER.load_sensor_file(LS340_17680)
    assert source["kind"] == "lakeshore-340"
    assert source["units"] == "LOGOHM"
    assert len(source["points"]) == 129
    assert source["meta"]["serial"] == "X17680"
    assert source["meta"]["stated_multiplier"] == -1.0

    readings = [r for _, r in source["points"]]
    temperatures = [t for t, _ in source["points"]]
    assert math.isclose(min(readings), 1.64523, abs_tol=1e-6)
    assert math.isclose(max(readings), 2.94699, abs_tol=1e-6)
    assert math.isclose(min(temperatures), 4.0, abs_tol=1e-9)
    assert math.isclose(max(temperatures), 325.0, abs_tol=1e-9)


def test_dat_and_340_describe_the_same_sensor():
    """Cross-check the whole conversion against a file Lake Shore wrote.

    The .dat is temperature-first in ohms; the .340 is reading-first in
    log-ohms. They are the same calibration written two different ways. Taking
    the .dat through convert_units() and comparing it with the .340 at the
    .340's own temperatures exercises the column mapping, the unit conversion
    and the format code all at once, against a source outside this repository.

    A column swap or a wrong format code would put this out by orders of
    magnitude, so the tolerance only has to be tight enough to catch a real
    error; 0.01 in log10(R) is 2.3% in resistance, and the two files agree far
    better than that wherever the .dat has points nearby.
    """
    _need_cal_files()
    dat = LOADER.load_sensor_file(DAT_17680)
    ref = LOADER.load_sensor_file(LS340_17680)

    converted = LOADER.convert_units(dat["points"], "OHMS", "LOGOHM")
    converted.sort(key=lambda pair: pair[0])
    dat_temperatures = [t for t, _ in converted]
    dat_logohms = [r for _, r in converted]

    worst = 0.0
    compared = 0
    for temperature, reference_logohm in ref["points"]:
        if not (dat_temperatures[0] <= temperature <= dat_temperatures[-1]):
            continue
        # Linear interpolation in log10(R) against log10(T): both files are
        # smooth in those coordinates, so the interpolation error stays well
        # below the difference a real fault would produce.
        index = 1
        while dat_temperatures[index] < temperature:
            index += 1
        t_low, t_high = dat_temperatures[index - 1], dat_temperatures[index]
        r_low, r_high = dat_logohms[index - 1], dat_logohms[index]
        span = math.log10(t_high) - math.log10(t_low)
        fraction = 0.0 if span == 0 else (
            math.log10(temperature) - math.log10(t_low)) / span
        interpolated = r_low + fraction * (r_high - r_low)
        worst = max(worst, abs(interpolated - reference_logohm))
        compared += 1

    assert compared > 100, f"only {compared} points overlapped"
    assert worst < 0.01, (
        f"the .dat and the .340 disagree by up to {worst:.4f} in log10(R); "
        "the column mapping or the unit conversion is wrong")


def test_a_headerless_two_column_file_is_refused():
    """Guessing the column order is the one thing the module must never do."""
    path = os.path.join(_scratch(), "headerless.dat")
    with open(path, "w", encoding="ascii") as handle:
        handle.write("3.5913  977.251\n300.0   46.49\n")
    try:
        LOADER.load_sensor_file(path)
    except LOADER.CurveFileError as exc:
        assert "column" in str(exc).lower()
    else:
        raise AssertionError(
            "a file with no column headings was accepted; its column order "
            "was guessed")


def test_an_unsupported_lakeshore_format_is_refused_by_name():
    """Format 5 is log-ohm against log-Kelvin, which is not a Cryo-con unit."""
    path = os.path.join(_scratch(), "format5.340")
    with open(path, "w", encoding="ascii") as handle:
        handle.write("Sensor Model:   TEST\n"
                     "Data Format:    5      (Log Ohms/Log Kelvin)\n"
                     "Number of Breakpoints:   2\n\n"
                     "No.   Units      Temperature (K)\n"
                     "  1  1.64523       325.000\n"
                     "  2  2.94699         4.000\n")
    try:
        LOADER.load_sensor_file(path)
    except LOADER.CurveFileError as exc:
        assert "format 5" in str(exc).lower()
    else:
        raise AssertionError("an unconvertible Lake Shore format was accepted")


def test_a_truncated_340_is_refused():
    """A breakpoint count that disagrees with the rows means a partial file."""
    path = os.path.join(_scratch(), "short.340")
    with open(path, "w", encoding="ascii") as handle:
        handle.write("Data Format:    4      (Log Ohms/Kelvin)\n"
                     "Number of Breakpoints:   129\n\n"
                     "No.   Units      Temperature (K)\n"
                     "  1  1.64523       325.000\n"
                     "  2  2.94699         4.000\n")
    try:
        LOADER.load_sensor_file(path)
    except LOADER.CurveFileError as exc:
        assert "129" in str(exc)
    else:
        raise AssertionError("a truncated .340 was accepted")


# ---------------------------------------------------------------------------
# Building the .crv
# ---------------------------------------------------------------------------

def _cernox_curve():
    """The lab Cernox, converted the way the GUI's defaults convert it."""
    _need_cal_files()
    source = LOADER.load_sensor_file(DAT_17680)
    points = LOADER.convert_units(source["points"], "OHMS", "LOGOHM")
    return points


def test_crv_body_is_reading_then_temperature():
    """Every data line is <log-ohm> <Kelvin>, in that order.

    Sorted ascending by sensor reading, as the instrument stores them, so the
    first data line is the smallest log-ohm, which is the WARMEST point.
    """
    points = _cernox_curve()
    lines = LOADER.build_crv_lines(
        "CX1030 X17680", "R8K10UA", -1.0, "LOGOHM", points)

    assert lines[0] == "CX1030 X17680"
    assert lines[1] == "R8K10UA"
    assert lines[2] == "-1.0"
    assert lines[3] == "LOGOHM"
    assert lines[-1] == ";"
    assert len(lines) == 4 + len(points) + 1

    first_reading, first_temperature = lines[4].split()
    last_reading, last_temperature = lines[-2].split()
    # log10(43.76) = 1.641 at 330 K, log10(977.25) = 2.990 at 3.59 K.
    assert 1.6 < float(first_reading) < 1.7
    assert 320.0 < float(first_temperature) < 340.0
    assert 2.9 < float(last_reading) < 3.1
    assert 3.0 < float(last_temperature) < 4.0


def test_crv_file_is_plain_ascii_and_ends_with_the_semicolon_line():
    points = _cernox_curve()
    lines = LOADER.build_crv_lines(
        "CX1030 X17680", "R8K10UA", -1.0, "LOGOHM", points)
    text = LOADER.crv_file_text(lines)
    text.encode("ascii")                     # raises if anything crept in
    assert text.endswith(";\n")
    assert "\r" not in text


def test_a_crv_round_trips_through_its_own_reader():
    """What is written is what parse_crv_text reads back, to the last digit.

    parse_crv_text is also what reads the instrument's CALCUR? reply, so this
    is the same code path the verification step depends on.
    """
    points = _cernox_curve()
    lines = LOADER.build_crv_lines(
        "CX1030 X17680", "R8K10UA", -1.0, "LOGOHM", points)
    header, read_back = LOADER.parse_crv_text(LOADER.crv_file_text(lines))

    assert header == {"name": "CX1030 X17680", "sensor_type": "R8K10UA",
                      "multiplier": -1.0, "units": "LOGOHM"}
    comparison = LOADER.compare_curves(points, read_back)
    assert comparison["matched"], comparison["problems"]
    # Six significant digits is what the instrument keeps, so that is the
    # most the writer should throw away.
    assert comparison["worst_reading_error"] < 1e-5
    assert comparison["worst_temperature_error"] < 1e-5


def test_an_unterminated_curve_is_refused():
    """No semicolon means the reply or the file was cut short."""
    text = "NAME\nR8K10UA\n-1\nLOGOHM\n1.64 330\n2.99 3.6\n"
    try:
        LOADER.parse_crv_text(text)
    except LOADER.CurveFileError as exc:
        assert "semicolon" in str(exc).lower()
    else:
        raise AssertionError("a curve with no terminator was accepted")


def test_fmt6_never_writes_exponent_notation():
    """The firmware's number parser is undocumented; '1e-05' is not risked."""
    for value in (1e-5, 1.23456789e-7, 977.251462533428, -1.0, 0.0,
                  1.6452312345, 3.5e12, 325.0, 4.0):
        text = LOADER.fmt6(value)
        assert "e" not in text.lower(), f"{value!r} formatted as {text!r}"
        assert math.isclose(float(text), value, rel_tol=2e-6, abs_tol=1e-12)
        # Every field carries a decimal point: the manual's own examples do,
        # and a header field the instrument cannot parse is replaced with a
        # default rather than reported.
        assert "." in text, f"{value!r} formatted as the bare integer {text!r}"


def test_units_convert_only_where_that_means_something():
    points = [(300.0, 100.0), (4.0, 1000.0)]
    as_log = LOADER.convert_units(points, "OHMS", "LOGOHM")
    assert math.isclose(as_log[0][1], 2.0, abs_tol=1e-12)
    assert math.isclose(as_log[1][1], 3.0, abs_tol=1e-12)
    back = LOADER.convert_units(as_log, "LOGOHM", "OHMS")
    for (t1, r1), (t2, r2) in zip(points, back):
        assert math.isclose(r1, r2, rel_tol=1e-12)
    for pair in (("OHMS", "VOLTS"), ("VOLTS", "LOGOHM")):
        try:
            LOADER.convert_units(points, *pair)
        except LOADER.CurveFileError:
            pass
        else:
            raise AssertionError(f"{pair[0]} was converted to {pair[1]}")


def test_a_zero_resistance_has_no_logarithm():
    try:
        LOADER.convert_units([(300.0, 0.0)], "OHMS", "LOGOHM")
    except LOADER.CurveFileError as exc:
        assert "logarithm" in str(exc).lower()
    else:
        raise AssertionError("log10(0) was accepted")


# ---------------------------------------------------------------------------
# The checks that stop a wrong curve being sent
# ---------------------------------------------------------------------------

def test_the_lab_cernox_passes_every_check_with_the_manuals_settings():
    """Table 4 of the manual: Cernox is R8K10UA, multiplier -1, LogOhms."""
    points = _cernox_curve()
    errors, warnings, stats = LOADER.analyse_curve(
        points, "LOGOHM", "R8K10UA", -1.0, "CX1030 X17680")
    assert errors == [], errors
    assert stats["count"] == 71
    assert stats["coefficient"] == "negative"
    # 977.25 ohm at the cold end against the 8 kohm full scale of R8K10UA.
    assert math.isclose(stats["peak_ohms"], 977.251462533428, rel_tol=1e-9)
    assert stats["full_scale"] == 8.0e3


def test_a_positive_multiplier_on_an_ntc_sensor_is_an_error():
    """The sign of the multiplier is the sensor's temperature coefficient."""
    points = _cernox_curve()
    errors, _, _ = LOADER.analyse_curve(
        points, "LOGOHM", "R8K10UA", +1.0, "CX1030 X17680")
    assert any("negative temperature coefficient" in message
               for message in errors), errors


def test_a_zero_multiplier_is_an_error():
    points = _cernox_curve()
    errors, _, _ = LOADER.analyse_curve(
        points, "LOGOHM", "R8K10UA", 0.0, "CX1030 X17680")
    assert any("cannot be zero" in message for message in errors), errors


def test_a_sensor_type_whose_full_scale_is_too_small_is_an_error():
    """R312R1MA is a 312 ohm Platinum range; the Cernox reaches 977 ohm."""
    points = _cernox_curve()
    errors, _, _ = LOADER.analyse_curve(
        points, "LOGOHM", "R312R1MA", -1.0, "CX1030 X17680")
    assert any("full scale" in message for message in errors), errors


def test_a_voltage_sensor_type_on_a_resistance_curve_is_an_error():
    points = _cernox_curve()
    errors, _, _ = LOADER.analyse_curve(
        points, "LOGOHM", "Diode", -1.0, "CX1030 X17680")
    assert any("voltage input" in message for message in errors), errors


def test_the_name_rules_from_the_calcur_page_are_enforced():
    points = _cernox_curve()
    for name, fragment in (("ab", "shorter"),
                           ("0123456789ABCDEF", "characters"),
                           ("Cernox™ 17680", "ASCII")):
        errors, _, _ = LOADER.analyse_curve(
            points, "LOGOHM", "R8K10UA", -1.0, name)
        assert any(fragment in message for message in errors), (name, errors)


def test_too_few_and_too_many_points_are_errors():
    errors, _, _ = LOADER.analyse_curve(
        [(300.0, 2.0)], "LOGOHM", "R8K10UA", -1.0, "TESTCURVE")
    assert any("at least" in message for message in errors), errors

    many = [(300.0 - 0.5 * i, 1.5 + 0.005 * i) for i in range(201)]
    errors, _, _ = LOADER.analyse_curve(
        many, "LOGOHM", "R8K10UA", -1.0, "TESTCURVE")
    assert any("at most" in message for message in errors), errors


def test_a_repeated_sensor_reading_is_an_error():
    """The instrument interpolates on the reading, so a repeat is ambiguous."""
    points = [(300.0, 1.60), (200.0, 1.80), (100.0, 1.80), (10.0, 2.50)]
    errors, _, _ = LOADER.analyse_curve(
        points, "LOGOHM", "R8K10UA", -1.0, "TESTCURVE")
    assert any("repeat" in message for message in errors), errors


def test_thinning_keeps_both_ends_and_invents_nothing():
    original = [(float(i), float(i) / 1000.0) for i in range(1, 501)]
    thinned, dropped = LOADER.thin_points(original, LOADER.MAX_CURVE_POINTS)
    assert len(thinned) <= LOADER.MAX_CURVE_POINTS
    assert dropped == len(original) - len(thinned)
    assert thinned[0] == original[0]
    assert thinned[-1] == original[-1]
    assert all(point in original for point in thinned)
    # A curve already inside the limit is left exactly as it is.
    unchanged, none_dropped = LOADER.thin_points(original[:10], 200)
    assert unchanged == original[:10] and none_dropped == 0


def test_the_lab_cernox_needs_no_thinning():
    """71 points against a 200-point limit: the whole calibration fits."""
    points = _cernox_curve()
    thinned, dropped = LOADER.thin_points(points, LOADER.MAX_CURVE_POINTS)
    assert dropped == 0 and len(thinned) == 71


# ---------------------------------------------------------------------------
# Comparing what came back
# ---------------------------------------------------------------------------

def test_compare_curves_notices_a_missing_point():
    points = _cernox_curve()
    comparison = LOADER.compare_curves(points, points[:-1])
    assert not comparison["matched"]
    assert "70" in comparison["problems"][0]


def test_compare_curves_notices_a_corrupted_value():
    points = _cernox_curve()
    damaged = list(points)
    temperature, reading = damaged[10]
    damaged[10] = (temperature, reading * 1.01)     # 1% out
    comparison = LOADER.compare_curves(points, damaged)
    assert not comparison["matched"]
    assert comparison["worst_reading_error"] > 1e-3


def test_compare_curves_ignores_the_order_points_arrive_in():
    """The instrument sorts the curve itself before storing it."""
    points = _cernox_curve()
    comparison = LOADER.compare_curves(points, list(reversed(points)))
    assert comparison["matched"], comparison["problems"]


# ---------------------------------------------------------------------------
# Talking to the instrument
# ---------------------------------------------------------------------------

class FakeInstrument:
    """A Cryo-con that records exactly what bytes it was sent."""

    def __init__(self, idn="Cryocon,34,204683,3.03A"):
        self.idn = idn
        self.timeout = None
        self.raw_writes = []
        self.writes = []
        self.queries = []
        self.closed = False
        self._pending = []

    def write(self, command):
        self.writes.append(command)
        if command.upper().startswith("CALCUR?"):
            index = int(command.split()[-1])
            self._pending = list(self.stored.get(index, []))

    def write_raw(self, payload):
        self.raw_writes.append(payload)

    def query(self, command):
        self.queries.append(command)
        if command.strip() == "*IDN?":
            return self.idn + "\n"
        return "0\n"

    def read(self):
        if not self._pending:
            raise IOError("VI_ERROR_TMO: Timeout expired")
        return self._pending.pop(0) + "\n"

    def close(self):
        self.closed = True

    stored = {}


class FakeResourceManager:
    def __init__(self, instrument):
        self.instrument = instrument

    def list_resources(self):
        return ("GPIB0::12::INSTR",)

    def open_resource(self, address):
        self.instrument.address = address
        return self.instrument


def _connected_backend(instrument=None, address="GPIB0::12::INSTR"):
    """A backend wired to a fake instrument, with no real VISA anywhere."""
    instrument = instrument or FakeInstrument()
    backend = LOADER.CurveLoaderBackend(log=lambda message: None)
    backend.rm = FakeResourceManager(instrument)

    link = LOADER.CryoconLink.__new__(LOADER.CryoconLink)
    link.address = address
    link.timeout_ms = LOADER.CRYOCON_TIMEOUT_MS
    link.instrument = instrument
    link.idn = instrument.idn
    link._log = lambda message: None
    link._last_io = 0.0
    link.rm = backend.rm
    backend.link = link
    return backend, instrument


def test_send_curve_writes_the_command_the_header_the_points_and_the_semicolon():
    """205 lines for a 200-point curve: CALCUR, four header lines, then data."""
    points = _cernox_curve()
    lines = LOADER.build_crv_lines(
        "CX1030 X17680", "R8K10UA", -1.0, "LOGOHM", points)
    backend, instrument = _connected_backend()

    LOADER.CURVE_SETTLE_S_BACKUP = LOADER.CURVE_SETTLE_S
    LOADER.CURVE_SETTLE_S = 0.0
    LOADER.CURVE_LINE_GAP_S_BACKUP = LOADER.CURVE_LINE_GAP_S
    LOADER.CURVE_LINE_GAP_S = 0.0
    try:
        sent = backend.send_curve(1, lines, b"")
    finally:
        LOADER.CURVE_SETTLE_S = LOADER.CURVE_SETTLE_S_BACKUP
        LOADER.CURVE_LINE_GAP_S = LOADER.CURVE_LINE_GAP_S_BACKUP

    written = [payload.decode("ascii") for payload in instrument.raw_writes]
    assert sent == len(written) == len(lines) + 1
    assert written[0] == "CALCUR 1"
    assert written[1] == "CX1030 X17680"
    assert written[2] == "R8K10UA"
    assert written[3] == "-1.0"
    assert written[4] == "LOGOHM"
    assert written[-1] == ";"
    assert len(written) == 5 + 71 + 1
    # Nothing was sent through PyVISA's write(), which would have appended a
    # terminator of its own choosing behind the module's back.
    assert instrument.writes == []


def test_send_curve_refuses_a_slot_outside_1_to_12():
    """The Model 34 has twelve user curves; the Model 32 has four."""
    lines = LOADER.build_crv_lines(
        "TESTCURVE", "R8K10UA", -1.0, "LOGOHM",
        [(300.0, 1.6), (4.0, 2.9)])
    backend, _ = _connected_backend()
    for index in (0, -1, 13, 61):
        try:
            backend.send_curve(index, lines, b"")
        except ValueError as exc:
            assert "1 to 12" in str(exc)
        else:
            raise AssertionError(f"slot {index} was accepted")


def test_line_endings_follow_the_manuals_rule_per_interface():
    """No terminator on GPIB and USB; a line feed on RS-232 and LAN."""
    for address, expected in (("GPIB0::12::INSTR", b""),
                              ("USB0::0x1234::0x5678::SN::INSTR", b""),
                              ("ASRL3::INSTR", b"\n"),
                              ("TCPIP0::192.168.0.5::5000::SOCKET", b"\n")):
        backend, _ = _connected_backend(address=address)
        assert backend.resolve_line_ending(None) == expected, address
    # An explicit choice always wins over the automatic rule.
    backend, _ = _connected_backend(address="GPIB0::12::INSTR")
    assert backend.resolve_line_ending(b"\r\n") == b"\r\n"


def test_the_chosen_line_ending_is_the_bytes_that_go_on_the_wire():
    lines = LOADER.build_crv_lines(
        "TESTCURVE", "R8K10UA", -1.0, "LOGOHM",
        [(300.0, 1.6), (4.0, 2.9)])
    backend, instrument = _connected_backend()
    gap, settle = LOADER.CURVE_LINE_GAP_S, LOADER.CURVE_SETTLE_S
    LOADER.CURVE_LINE_GAP_S = LOADER.CURVE_SETTLE_S = 0.0
    try:
        backend.send_curve(2, lines, b"\n")
    finally:
        LOADER.CURVE_LINE_GAP_S, LOADER.CURVE_SETTLE_S = gap, settle
    assert instrument.raw_writes[0] == b"CALCUR 2\n"
    assert instrument.raw_writes[-1] == b";\n"


def test_read_curve_collects_lines_until_the_semicolon():
    """On GPIB each line is its own message, so the reply is read line by
    line and stops at the terminator rather than at a timeout."""
    instrument = FakeInstrument()
    instrument.stored = {3: ["CX1030 X17680", "R8K10UA", "-1", "LOGOHM",
                             "1.64110   330.027", "2.99001   3.59132", ";"]}
    backend, _ = _connected_backend(instrument)
    text = backend.read_curve(3)
    header, points = LOADER.parse_crv_text(text)
    assert header["name"] == "CX1030 X17680"
    assert header["sensor_type"] == "R8K10UA"
    assert header["units"] == "LOGOHM"
    assert len(points) == 2
    assert math.isclose(points[0][0], 330.027, abs_tol=1e-3)


def test_read_curve_survives_an_empty_slot():
    """An unused slot times out rather than answering; that is not a crash."""
    instrument = FakeInstrument()
    instrument.stored = {}
    backend, _ = _connected_backend(instrument)
    assert backend.read_curve(7).strip() == ""


def test_user_curve_to_sensor_index_matches_appendix_a():
    """Manual p.220: user curve 1 is senix 10, user curve 12 is senix 21."""
    assert LOADER.CurveLoaderBackend.senix_for_user_curve(1) == 10
    assert LOADER.CurveLoaderBackend.senix_for_user_curve(2) == 11
    assert LOADER.CurveLoaderBackend.senix_for_user_curve(11) == 20
    assert LOADER.CurveLoaderBackend.senix_for_user_curve(12) == 21


class FakePyvisa:
    """Stands in for the pyvisa module so CryoconLink opens the fake bus.

    CryoconLink builds its own ResourceManager, which is the right thing for
    it to do and the reason it has to be intercepted at the module level here
    rather than by handing the backend a manager.
    """

    def __init__(self, instrument):
        self._instrument = instrument

    def ResourceManager(self):            # noqa: N802 - mirrors pyvisa's name
        return FakeResourceManager(self._instrument)


def test_connecting_to_something_that_is_not_a_cryocon_is_refused():
    """The Lakeshore 350 sits on the same bus; CALCUR must never reach it.

    The lab's Cryocon is at GPIB0::12 and the Lakeshore 350 answers on
    GPIB1::12, which is the Cryo-con's own factory address. Picking by
    address alone would send a calibration curve to the Lakeshore.
    """
    instrument = FakeInstrument(idn="LSCI,MODEL350,LSA2FKB/#######,1.7")
    real_pyvisa = LOADER.pyvisa
    LOADER.pyvisa = FakePyvisa(instrument)
    try:
        backend = LOADER.CurveLoaderBackend(log=lambda message: None)
        backend.rm = FakeResourceManager(instrument)
        try:
            backend.connect("GPIB1::12::INSTR")
        except ConnectionError as exc:
            assert "not a Cryo-con" in str(exc), exc
            assert backend.link is None
        else:
            raise AssertionError("a non-Cryocon instrument was accepted")
        # It was identified and then dropped without a single command beyond
        # the '*IDN?' that identified it.
        assert instrument.queries == ["*IDN?"]
        assert instrument.writes == [] and instrument.raw_writes == []
        assert instrument.closed
    finally:
        LOADER.pyvisa = real_pyvisa


def test_connecting_to_a_real_cryocon_is_accepted():
    instrument = FakeInstrument()
    real_pyvisa = LOADER.pyvisa
    LOADER.pyvisa = FakePyvisa(instrument)
    try:
        backend = LOADER.CurveLoaderBackend(log=lambda message: None)
        backend.rm = FakeResourceManager(instrument)
        idn = backend.connect("GPIB0::12::INSTR")
        assert "Cryocon" in idn
        assert backend.is_connected
    finally:
        LOADER.pyvisa = real_pyvisa


def test_disconnect_is_non_destructive():
    """Closing the session must not stop a running control loop."""
    backend, instrument = _connected_backend()
    backend.disconnect()
    assert instrument.closed
    assert instrument.writes == []
    assert instrument.raw_writes == []


# ---------------------------------------------------------------------------
# What the module must never do
# ---------------------------------------------------------------------------

def _commands_in_source():
    """Every literal the module hands to link.write / write_line / query.

    Only these reach the instrument, so only these are worth policing. A
    blanket scan of all string literals would flag the manual's own file name
    ("The User Interface ...") and every sentence containing the word
    'control', which says nothing about what is sent.
    """
    import re
    pattern = re.compile(
        r'\.(?:write|write_line|query)\(\s*f?["\']([^"\']*)["\']')
    return pattern.findall(SOURCE)


def test_the_module_sends_no_control_or_reset_command():
    """This is a curve loader. It has no business touching a heater.

    On a Cryo-con, *RST is a fifteen-second hardware reset that takes the
    instrument off the bus, and STOP or CONTROL would drop or engage the
    control loops in the middle of somebody else's run.
    """
    forbidden = ("*RST", "*CLS", "STOP", "CONTROL", "LOOP", "SETPT",
                 "PGAIN", "IGAIN", "DGAIN", "RANGE", "PMANUAL", "MAXPWR",
                 "MAXSET", "OVERTEMP", "AUTOTUNE", "PIDTABLE", "RELAYS")
    commands = _commands_in_source()
    assert commands, "no commands were found; the scan is not looking at the "\
                     "right thing"
    for command in commands:
        upper = command.upper()
        for banned in forbidden:
            assert banned not in upper, (
                f"the module sends {command!r}, which contains the control "
                f"command {banned!r}")


def test_the_only_writes_are_the_curve_the_name_and_the_channel():
    """Four write paths, each behind its own button, and nothing else.

    write_line() carries the curve; write() carries the CALCUR? query (which
    is read back by hand, line by line), the sensor name and the channel
    assignment. Everything else the module says is a query.
    """
    assert SOURCE.count("self.link.write_line(") == 1
    assert SOURCE.count("self.link.write(") == 3
    for expected in ('self.link.write(f"CALCUR? {index}")',
                     'self.link.write(f\'SENTYPE {senix}:NAME "{name}"\')',
                     'self.link.write(f"INPUT {channel}:SENIX {senix}")'):
        assert expected in SOURCE, expected
    # The curve itself goes out as 'CALCUR <n>' followed by the block.
    assert 'f"CALCUR {index}"' in SOURCE


def test_the_cernox_defaults_are_the_ones_in_table_4():
    """Cernox: R8K10UA, 10 uA, negative coefficient, LogOhms."""
    assert LOADER.CERNOX_DEFAULTS == {
        "sensor_type": "R8K10UA",
        "multiplier": "-1.0",
        "units": "LOGOHM",
    }
    full_scale, unit, description = LOADER.SENSOR_TYPES["R8K10UA"]
    assert full_scale == 8.0e3 and unit == "ohm"
    assert "Cernox" in description


def test_the_model_34_limits_are_the_ones_in_the_manual():
    assert (LOADER.MIN_USER_CURVE, LOADER.MAX_USER_CURVE) == (1, 12)
    assert (LOADER.MIN_CURVE_POINTS, LOADER.MAX_CURVE_POINTS) == (2, 200)
    assert (LOADER.MIN_NAME_CHARS, LOADER.MAX_NAME_CHARS) == (4, 15)
    assert LOADER.SENIX_OFFSET == 9
    assert LOADER.CURVE_UNITS == ("LOGOHM", "OHMS", "VOLTS")


def test_the_direct_control_module_can_reach_the_loader():
    """Direct Control offers the loader, and the path it uses really exists.

    The Sensor Index field in Direct Control can only point at a user curve
    that has already been installed, so the two modules belong together. The
    link is resolved through find_pica_root() rather than a counted number of
    '..', because a module that moves must not lose its buttons.
    """
    direct_control_path = os.path.join(REPO_ROOT, "pica", "cryocon",
                                       "T_Control_CC34_DirectControl_GUI.py")
    direct_control = _load("cc34_direct_control_for_link_test",
                           direct_control_path)
    resolved = direct_control.pica_sibling(
        "cryocon", "Sensor_Curve_Loader_CC34_GUI.py")
    assert resolved, "Direct Control cannot find the curve loader"
    assert os.path.isfile(resolved)
    assert os.path.samefile(resolved, MODULE_PATH)

    source = open(direct_control_path, encoding="utf-8").read()
    assert "launch_curve_loader" in source
    # A missing sibling must disable the button, not crash the window.
    assert "Sensor Curve Loader Not Available" in source


def test_both_launchers_list_the_loader():
    """A module nobody can find is a module nobody uses."""
    key = "Cryocon Sensor Curve Loader"
    main_source = open(os.path.join(REPO_ROOT, "pica", "main.py"),
                       encoding="utf-8").read()
    v2_source = open(os.path.join(REPO_ROOT, "pica", "main_v2.py"),
                     encoding="utf-8").read()
    assert "cryocon/Sensor_Curve_Loader_CC34_GUI.py" in main_source
    assert main_source.count(f'"{key}"') >= 2      # path entry and menu entry
    # main_v2 shares main.py's SCRIPT_PATHS, so it only needs the menu entry.
    assert f'"{key}"' in v2_source


def test_the_module_is_hardened_the_way_its_siblings_are():
    """The Rev 3.03A pattern: settle, retry the first *IDN?, pace every
    operation. Nothing here should have to relearn that."""
    assert LOADER.CRYOCON_CONNECT_ATTEMPTS >= 3
    assert LOADER.CRYOCON_OPEN_SETTLE_S > 0
    assert LOADER.CRYOCON_MIN_GAP_S > 0
    assert LOADER.CURVE_LINE_GAP_S > 0
    assert LOADER.CURVE_SETTLE_S >= 0.25   # the manual's flash write time


# ---------------------------------------------------------------------------

def _scratch():
    """A throwaway directory for the deliberately malformed test files.

    Kept out of the repository: these files exist only to be rejected, and a
    stray X.340 in tests/ that is not a real curve would be a trap for the
    next person who goes looking for one.
    """
    global _SCRATCH_DIR
    if _SCRATCH_DIR is None:
        _SCRATCH_DIR = tempfile.mkdtemp(prefix="cc34_curve_loader_")
    return _SCRATCH_DIR


_SCRATCH_DIR = None


if __name__ == "__main__":
    failures = skipped = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS  {name}")
            except SkipTest as exc:
                skipped += 1
                print(f"SKIP  {name}: {exc}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s), {skipped} skipped.")
    sys.exit(1 if failures else 0)
