"""Tests for pica/lakeshore/Sensor_Curve_Loader_L340_L350_GUI.py.

The module installs a Lake Shore calibration file as a Model 340 or Model 350
user curve. A wrong curve does not announce itself: the instrument accepts it,
reads a plausible-looking temperature, and every measurement made against that
sensor afterwards is quietly wrong. So the tests here are mostly about the ways
it could be wrong rather than the way it should be right.

The ones that matter most:

  * test_only_the_340_transfer_ends_with_crvsav
    The 340 manual, printed 9-30: "Curves are not permanently updated in the
    curve FLASH until a CRVSAV command is issued." A curve sent, read back and
    verified on a 340 without CRVSAV is still only in RAM and the next power
    cycle throws it away, silently. The 350 has no such command.

  * test_the_two_models_have_different_user_curve_ranges
    21-60 on the 340 and 21-59 on the 350. Curve 60 on a 350 is not an error,
    it is a write that goes nowhere.

  * test_columns_are_not_swapped and
    test_crvpt_sends_sensor_units_then_temperature
    A Lake Shore .dat is temperature-first; CRVPT is reading-first. Once the
    numbers are in a file with no labels the two orders are indistinguishable.

  * test_an_occupied_curve_is_refused_not_warned_about and
    test_the_pre_send_check_reads_the_instrument_not_the_listing
    The module fills empty user curves and never replaces one. A listing can
    be minutes old, so the curve is read again with CRVHDR? immediately before
    anything is written and an unreadable reply counts as not-empty.

  * test_a_leftover_tail_is_caught
    CRVHDR and CRVPT do not shorten a curve. Replacing a 200-point curve with
    a 134-point one leaves points 135-200 of the old calibration in place.

Everything is run against the real files on the Lake Shore CD copy in
Untracked_Stuff/, which is where the lab Cernox calibration actually lives.
Those files are untracked, so every test that needs them skips cleanly when
they are absent.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import math
import os
import re
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATH = os.path.join(REPO_ROOT, "pica", "lakeshore",
                           "Sensor_Curve_Loader_L340_L350_GUI.py")

CAL_DIR = os.path.join(REPO_ROOT, "Untracked_Stuff", "Curv_for_Cernox",
                       "Cernox", "Calibration Data", "1068")
DAT_17680 = os.path.join(CAL_DIR, "X17680.dat")
DAT_17681 = os.path.join(CAL_DIR, "X17681.dat")
TBL_17680 = os.path.join(CAL_DIR, "X17680.tbl")
LS340_17680 = os.path.join(CAL_DIR, "X17680.340")
LS340_17681 = os.path.join(CAL_DIR, "X17681.340")

HAVE_CAL_FILES = all(os.path.exists(path) for path in
                     (DAT_17680, DAT_17681, TBL_17680, LS340_17680,
                      LS340_17681))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LOADER = _load("ls340_350_curve_loader", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()

NEXT_METHOD = chr(92) + "n" + "    def "

M340 = LOADER.MODEL_340
M350 = LOADER.MODEL_350
NEGATIVE = LOADER.COEFFICIENT_NEGATIVE
POSITIVE = LOADER.COEFFICIENT_POSITIVE


class SkipTest(Exception):
    """Raised when a test needs something this machine does not have."""


def _skip(reason):
    """Skip under pytest; skip cleanly when run as plain Python too."""
    try:
        import pytest
    except ImportError:
        raise SkipTest(reason)
    pytest.skip(reason)


def _need_cal_files():
    if not HAVE_CAL_FILES:
        _skip(f"Lake Shore calibration files not present in {CAL_DIR}")


# One Tk root for the whole module. Creating and destroying several Tk roots in
# a single process is unreliable -- the second one can fail to find tk.tcl --
# so the tests that need a live window each get a Toplevel on this shared root
# and destroy only that.
_SHARED_ROOT = None
_SHARED_ROOT_FAILED = False


def _shared_root():
    global _SHARED_ROOT, _SHARED_ROOT_FAILED
    if _SHARED_ROOT_FAILED:
        _skip("no display for a Tk root")
    if _SHARED_ROOT is None:
        try:
            import tkinter as tk
            _SHARED_ROOT = tk.Tk()
            _SHARED_ROOT.withdraw()
        except Exception as exc:
            _SHARED_ROOT_FAILED = True
            _skip(f"no display for a Tk root: {exc}")
    return _SHARED_ROOT


_SCRATCH_DIR = None


def _scratch():
    """A throwaway directory for the deliberately malformed test files.

    Kept out of the repository: these files exist only to be rejected, and a
    stray X.340 in tests/ that is not a real curve would be a trap for the next
    person who goes looking for one.
    """
    global _SCRATCH_DIR
    if _SCRATCH_DIR is None:
        _SCRATCH_DIR = tempfile.mkdtemp(prefix="ls_curve_loader_")
    return _SCRATCH_DIR


def _write(name, text):
    path = os.path.join(_scratch(), name)
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write(text)
    return path


SAMPLE_340 = ("Sensor Model:   CX-1030-SD-4L\n"
              "Serial Number:  X17680\n"
              "Data Format:    4      (Log Ohms/Kelvin)\n"
              "SetPoint Limit: 325.      (Kelvin)\n"
              "Temperature coefficient:  1    (Negative)\n"
              "Number of Breakpoints:   3\n"
              "\n"
              "No.   Units      Temperature (K)\n"
              "\n"
              "  1   1.64523      325.000\n"
              "  2   2.00000      100.000\n"
              "  3   2.94699        4.000\n")


# ---------------------------------------------------------------------------
# Reading the Lake Shore files
# ---------------------------------------------------------------------------

def test_the_340_file_reads_with_its_whole_header():
    """X17680.340 is 129 breakpoints, 4.000 to 325.000 K, log-ohm.

    Every CRVHDR field except the curve name is in that file's header, and the
    module must take them from it rather than from anybody's memory: format 4,
    limit 325 K, coefficient 1 (negative).
    """
    _need_cal_files()
    source = LOADER.load_sensor_file(LS340_17680)
    assert source["kind"] == "lakeshore-340"
    assert source["units"] == "LOGOHM"
    assert len(source["points"]) == 129

    meta = source["meta"]
    assert meta["model"] == "CX-1030-SD-4L"
    assert meta["serial"] == "X17680"
    assert meta["format"] == 4
    assert meta["limit"] == 325.0
    assert meta["coefficient"] == NEGATIVE

    temperatures = [t for t, _ in source["points"]]
    assert math.isclose(min(temperatures), 4.000, rel_tol=1e-12)
    assert math.isclose(max(temperatures), 325.000, rel_tol=1e-12)


def test_the_dat_file_reads_as_the_raw_calibration():
    """X17680.dat is 71 points of temperature-and-ohms, 3.59 K to 330 K.

    The numbers are the file's own extremes, so a reader that silently dropped
    a row, mis-parsed the E-notation or skipped the header differently would
    move them.
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

    In a Lake Shore .dat the temperature column comes first; in a .340 it comes
    second. If either reader took its columns positionally in the other file's
    order, every 'temperature' would be a resistance between 43 and 977. The
    two ranges are what tells them apart once the labels are gone.
    """
    _need_cal_files()
    for path in (DAT_17680, LS340_17680):
        source = LOADER.load_sensor_file(path)
        for temperature, _ in source["points"]:
            assert 3.0 < temperature < 340.0, (
                f"{os.path.basename(path)}: temperature {temperature} is "
                "outside the range this sensor covers; the columns look "
                "swapped")
        # And the sensor really is an NTC: the coldest point is the largest
        # reading. A swap would invert this.
        coldest = min(source["points"], key=lambda pair: pair[0])
        warmest = max(source["points"], key=lambda pair: pair[0])
        assert coldest[1] > warmest[1]


def test_both_lab_cernox_sensors_read():
    """X17680 is the one in use; X17681 is its pair on the same sales order."""
    _need_cal_files()
    for path in (LS340_17680, LS340_17681):
        source = LOADER.load_sensor_file(path)
        assert source["units"] == "LOGOHM"
        assert source["meta"]["format"] == 4
        assert source["meta"]["coefficient"] == NEGATIVE
        assert 2 <= len(source["points"]) <= LOADER.MAX_CURVE_POINTS


def test_dat_and_340_describe_the_same_sensor():
    """Two independent Lake Shore files for one sensor, in opposite orders.

    The .dat is the raw measurement in ohms, temperature-first; the .340 is
    Lake Shore's own fit evaluated at 129 breakpoints, in log-ohm and
    reading-first. Converting the .dat the way the module does and comparing
    it against the .340 checks the whole chain against a source this
    repository did not write.
    """
    _need_cal_files()
    raw = LOADER.load_sensor_file(DAT_17680)
    fitted = LOADER.load_sensor_file(LS340_17680)
    converted = LOADER.convert_units(raw["points"], raw["units"], "LOGOHM")

    table = sorted(fitted["points"], key=lambda pair: pair[0])
    temperatures = [t for t, _ in table]
    readings = [r for _, r in table]

    residuals = []
    for temperature, log_ohm in converted:
        if not (temperatures[0] <= temperature <= temperatures[-1]):
            continue                       # outside what the .340 covers
        for index in range(1, len(temperatures)):
            if temperatures[index] >= temperature:
                break
        span = temperatures[index] - temperatures[index - 1]
        weight = 0.0 if span == 0 else (temperature -
                                        temperatures[index - 1]) / span
        interpolated = (readings[index - 1] +
                        weight * (readings[index] - readings[index - 1]))
        residuals.append(log_ohm - interpolated)

    assert len(residuals) > 50
    rms = math.sqrt(sum(value * value for value in residuals) /
                    len(residuals))
    assert rms < 5e-4, (
        f"the .dat and the .340 disagree by {rms:.2e} in log10(R); they are "
        "the same calibration and should agree to about 1e-4")


def test_a_headerless_two_column_file_is_refused():
    """Guessing a column order is the mistake this module exists to prevent."""
    path = _write("mystery.txt", "1.0 2.0\n3.0 4.0\n5.0 6.0\n")
    try:
        LOADER.load_sensor_file(path)
    except LOADER.CurveFileError as exc:
        assert "column" in str(exc).lower()
        return
    raise AssertionError("a headerless two-column file was accepted")


def test_data_format_5_is_refused_rather_than_read_as_kelvin():
    """Format 5 is log ohm versus LOG kelvin, and the 350 has no format 5.

    Its temperature column holds log10(K). Reading it as kelvin would put a
    curve covering 0.6 to 2.5 K into an instrument, from a file that describes
    4 to 325 K.
    """
    path = _write("format5.340",
                  SAMPLE_340.replace("Data Format:    4", "Data Format:    5"))
    try:
        LOADER.load_sensor_file(path)
    except LOADER.CurveFileError as exc:
        assert "log" in str(exc).lower()
        return
    raise AssertionError("a log-kelvin file was read as if it were kelvin")


def test_a_file_that_miscounts_its_own_breakpoints_is_refused():
    """A partial load is worse than no load: it would be sent as a whole
    curve."""
    path = _write("short.340",
                  SAMPLE_340.replace("Number of Breakpoints:   3",
                                     "Number of Breakpoints:   4"))
    try:
        LOADER.load_sensor_file(path)
    except LOADER.CurveFileError as exc:
        assert "4" in str(exc) and "3" in str(exc)
        return
    raise AssertionError("a file that miscounts its breakpoints was accepted")


# ---------------------------------------------------------------------------
# Extending the curve with the .dat
# ---------------------------------------------------------------------------

def test_the_dat_extends_the_340_to_134_points():
    """The settled recipe for the lab Cernox: 134 points, 3.5913 to 330.027 K.

    Only points beyond the ends of the .340 are added, so the interpolation
    inside the certified 4.000-325.000 K is still purely Lake Shore's table.
    """
    _need_cal_files()
    main = LOADER.load_sensor_file(LS340_17680)
    extra = LOADER.load_sensor_file(DAT_17680)
    merged, added, notes = LOADER.extend_curve(
        main["points"], extra["points"], main["units"], extra["units"])

    assert len(merged) == 134
    assert len(added) == 5
    temperatures = [t for t, _ in merged]
    assert math.isclose(min(temperatures), 3.59132424209341, rel_tol=1e-9)
    assert math.isclose(max(temperatures), 330.027223722539, rel_tol=1e-9)
    assert len(merged) <= LOADER.MAX_CURVE_POINTS

    # Every one of the .340's own points survives untouched.
    for point in main["points"]:
        assert point in merged
    assert any("uncertified" in note for note in notes)


def test_extending_is_off_unless_asked_for():
    """The certificate covers 4.00 to 325 K, so the extension is opt-in."""
    assert "self.use_second_var = tk.BooleanVar(value=False)" in SOURCE


def test_two_files_that_disagree_are_not_merged():
    """A second file whose out-of-range point falls back inside the first's
    reading span describes a different sensor, not a longer one."""
    primary = [(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)]
    disagreeing = [(40.0, 2.5)]        # hotter, but its reading is in range
    try:
        LOADER.extend_curve(primary, disagreeing, "LOGOHM", "LOGOHM")
    except LOADER.CurveFileError as exc:
        assert "disagree" in str(exc)
        return
    raise AssertionError("two disagreeing files were merged")


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------

def _commands(model=M350, curve=21, points=None, **kwargs):
    points = points or [(325.0, 1.64523), (100.0, 2.0), (4.0, 2.94699)]
    arguments = dict(name="CX-1030-SD-4L", serial="X17680", fmt_code=4,
                     limit=325.0, coefficient=NEGATIVE)
    arguments.update(kwargs)
    return LOADER.build_curve_commands(
        model, curve, arguments["name"], arguments["serial"],
        arguments["fmt_code"], arguments["limit"], arguments["coefficient"],
        points, erase_first=kwargs.get("erase_first",
                                       LOADER.ERASE_FIRST_DEFAULT))


def test_the_header_command_is_the_manuals_syntax():
    """CRVHDR <curve>,<name>,<SN>,<format>,<limit>,<coefficient>.

    The worked example printed in both manuals is
    'CRVHDR 21,DT-470,00011134,2,325.0,1'. The limit is written to the three
    decimals its +nnn.nnn field carries.
    """
    header = LOADER.crvhdr_command(21, "DT-470", "00011134", 2, 325.0,
                                   NEGATIVE)
    assert header == "CRVHDR 21,DT-470,00011134,2,325.000,1"
    assert len(header.split(",")) == 6


def test_crvpt_sends_sensor_units_then_temperature():
    """CRVPT <curve>,<index>,<units value>,<temp value>.

    Reading first, temperature second. This is the same order as a .340 file
    and the opposite of a .dat, and getting it backwards is the single easiest
    catastrophic mistake.
    """
    assert (LOADER.crvpt_command(21, 2, 0.10191, 470.0) ==
            "CRVPT 21,2,0.10191,470.0")

    commands = _commands()
    points = [command for command in commands
              if command.startswith("CRVPT")]
    assert len(points) == 3
    # index 1 is the LOWEST sensor reading, which on a Cernox is the hot end
    assert points[0] == "CRVPT 21,1,1.64523,325.0"
    assert points[-1] == "CRVPT 21,3,2.94699,4.0"
    for index, command in enumerate(points, start=1):
        fields = command.split(" ", 1)[1].split(",")
        assert int(fields[1]) == index
        reading, temperature = float(fields[2]), float(fields[3])
        assert 1.0 < reading < 4.0        # log-ohm
        assert 3.0 < temperature < 340.0  # kelvin


def test_the_transfer_erases_the_curve_first():
    """CRVHDR and CRVPT do not shorten a curve, so CRVDEL leads.

    Without it, a 200-point curve replaced by a 134-point one keeps points
    135-200 of the old calibration and the instrument interpolates straight
    through them past the cold end.
    """
    assert LOADER.ERASE_FIRST_DEFAULT is True
    assert _commands()[0] == "CRVDEL 21"
    assert _commands(erase_first=False)[0].startswith("CRVHDR")


def test_only_the_340_transfer_ends_with_crvsav():
    """The 340 keeps curve edits in RAM until CRVSAV; the 350 has no CRVSAV.

    A 340 curve that was sent, read back and verified without CRVSAV is still
    lost on the next power cycle, with no error at any point.
    """
    for_340 = _commands(model=M340)
    for_350 = _commands(model=M350)
    assert for_340[-1] == "CRVSAV"
    assert "CRVSAV" not in for_350
    assert len(for_340) == len(for_350) + 1
    assert LOADER.MODEL_SPECS[M340]["needs_crvsav"] is True
    assert LOADER.MODEL_SPECS[M350]["needs_crvsav"] is False
    # CRVDEL on a 340 is a curve edit too, so the erase path saves as well.
    assert "CRVSAV" in SOURCE.split("def delete_curve")[1].split("def ")[0]


def test_the_two_models_have_different_user_curve_ranges():
    """21-60 on the 340, 21-59 on the 350, and standard curves are read-only."""
    assert (LOADER.MODEL_SPECS[M340]["user_curve_min"],
            LOADER.MODEL_SPECS[M340]["user_curve_max"]) == (21, 60)
    assert (LOADER.MODEL_SPECS[M350]["user_curve_min"],
            LOADER.MODEL_SPECS[M350]["user_curve_max"]) == (21, 59)

    _commands(model=M340, curve=60)      # legal
    _commands(model=M350, curve=59)      # legal
    for model, curve in ((M350, 60), (M340, 61), (M340, 20), (M350, 1)):
        try:
            _commands(model=model, curve=curve)
        except ValueError:
            continue
        raise AssertionError(
            f"curve {curve} was accepted on a Model {model}")


def test_format_5_exists_on_the_340_and_not_on_the_350():
    assert 5 in LOADER.MODEL_SPECS[M340]["formats"]
    assert 5 not in LOADER.MODEL_SPECS[M350]["formats"]
    errors, _, _ = LOADER.analyse_curve(
        M350, [(325.0, 1.64523), (4.0, 2.94699)], "LOGOHM", 5,
        "CX-1030", "X17680", 325.0, NEGATIVE)
    assert any("format 5" in message.lower() for message in errors)


def test_the_command_file_records_exactly_what_was_sent():
    text = LOADER.command_file_text(M340, _commands(model=M340))
    body = [line for line in text.splitlines() if not line.startswith("#")]
    assert body == _commands(model=M340)
    assert "CRVSAV" in text
    text.encode("ascii")                 # never write a non-ASCII record


# ---------------------------------------------------------------------------
# What is checked before anything is sent
# ---------------------------------------------------------------------------

def test_a_coefficient_that_contradicts_the_data_is_an_error():
    """CRVHDR's coefficient is the sign of dT/d(sensor units).

    A Cernox is negative. Sending 2 would tell a 340 the sensor behaves the
    other way round; the 350 recomputes it, but the two instruments differ and
    nothing here relies on that.
    """
    points = [(325.0, 1.64523), (4.0, 2.94699)]
    assert LOADER.coefficient_from_points(points) == NEGATIVE
    errors, _, _ = LOADER.analyse_curve(M350, points, "LOGOHM", 4, "CX-1030",
                                        "X17680", 325.0, POSITIVE)
    assert any("coefficient" in message for message in errors)


def test_a_comma_in_a_name_is_refused():
    """CRVHDR is comma-separated, and so is the CRVHDR? reply.

    One comma in the name shifts every field after it by one, and the header
    that comes back would be read as a different curve entirely.
    """
    points = [(325.0, 1.64523), (4.0, 2.94699)]
    for name, serial in (("CX-1030,SD", "X17680"),
                         ("CX-1030;SD", "X17680"),
                         ("CX-1030", "X176,80")):
        errors, _, _ = LOADER.analyse_curve(M350, points, "LOGOHM", 4, name,
                                            serial, 325.0, NEGATIVE)
        assert errors, f"{name!r}/{serial!r} was accepted"


def test_the_name_and_serial_length_limits_are_the_manuals():
    points = [(325.0, 1.64523), (4.0, 2.94699)]
    assert LOADER.MAX_NAME_CHARS == 15
    assert LOADER.MAX_SERIAL_CHARS == 10
    errors, _, _ = LOADER.analyse_curve(M350, points, "LOGOHM", 4, "A" * 16,
                                        "X17680", 325.0, NEGATIVE)
    assert errors
    errors, _, _ = LOADER.analyse_curve(M350, points, "LOGOHM", 4, "A" * 15,
                                        "X" * 11, 325.0, NEGATIVE)
    assert errors
    errors, _, _ = LOADER.analyse_curve(M350, points, "LOGOHM", 4, "A" * 15,
                                        "X" * 10, 325.0, NEGATIVE)
    assert not errors


def test_the_curve_is_checked_against_the_inputs_sensor_type():
    """Both manuals: a curve that does not match the input becomes curve 0.

    That is a silent refusal, so it has to be caught before INCRV is sent
    rather than discovered by reading a temperature that never changes.
    """
    points = [(325.0, 1.64523), (4.0, 2.94699)]
    # a log-ohm curve on a 350 diode input
    errors, _, _ = LOADER.analyse_curve(M350, points, "LOGOHM", 4, "CX-1030",
                                        "X17680", 325.0, NEGATIVE,
                                        input_type=1)
    assert any("curve 0" in message for message in errors)
    # the same curve on NTC RTD is fine
    errors, _, stats = LOADER.analyse_curve(M350, points, "LOGOHM", 4,
                                            "CX-1030", "X17680", 325.0,
                                            NEGATIVE, input_type=3)
    assert not errors
    assert stats["input_type_name"] == "NTC RTD"
    # on a 340 a Cernox has its own sensor type, 8
    errors, _, stats = LOADER.analyse_curve(M340, points, "LOGOHM", 4,
                                            "CX-1030", "X17680", 325.0,
                                            NEGATIVE, input_type=8)
    assert not errors
    assert stats["input_type_name"] == "Cernox"
    assert LOADER.MODEL_SPECS[M340]["ntc_type"] == 8
    assert LOADER.MODEL_SPECS[M350]["ntc_type"] == 3


def test_the_350_input_range_table_places_the_real_sensor():
    """X17680 peaks at 885 ohm over the .340, and the 1 kohm range covers it.

    885.1 ohm is 10 ** 2.94699, the .340's coldest breakpoint at 4.000 K; the
    .dat reaches 977.25 ohm at 3.5913 K, which is still inside the same range.
    The ranges are the printed table on page 149, not a guess, and the 340 has
    no equivalent printed table so nothing is invented for it.
    """
    _need_cal_files()
    source = LOADER.load_sensor_file(LS340_17680)
    _, _, stats = LOADER.analyse_curve(M350, source["points"], "LOGOHM", 4,
                                       "CX-1030", "X17680", 325.0, NEGATIVE)
    assert math.isclose(stats["peak_ohms"], 885.095, rel_tol=1e-5)
    assert stats["suggested_range"] == 4
    assert stats["suggested_range_ohms"] == 1000.0

    extra = LOADER.load_sensor_file(DAT_17680)
    merged, _, _ = LOADER.extend_curve(source["points"], extra["points"],
                                       "LOGOHM", extra["units"])
    _, _, stats = LOADER.analyse_curve(M350, merged, "LOGOHM", 4, "CX-1030",
                                       "X17680", 325.0, NEGATIVE)
    assert math.isclose(stats["peak_ohms"], 977.251462533428, rel_tol=1e-6)
    assert stats["suggested_range"] == 4

    _, _, stats = LOADER.analyse_curve(M340, source["points"], "LOGOHM", 4,
                                       "CX-1030", "X17680", 325.0, NEGATIVE)
    assert "suggested_range" not in stats

    errors, _, _ = LOADER.analyse_curve(M350, [(325.0, 1.0), (4.0, 6.0)],
                                        "LOGOHM", 4, "BIG", "1", 325.0,
                                        NEGATIVE)
    assert any("off the top" in message for message in errors)


def test_a_curve_short_of_the_working_range_warns():
    """The certificate stops at 4.00 K and there is no data below 3.5913 K.

    Nothing can invent it, so the module says so at load time instead of
    leaving it to be found on a cold cryostat.
    """
    _need_cal_files()
    source = LOADER.load_sensor_file(LS340_17680)
    _, warnings, _ = LOADER.analyse_curve(
        M350, source["points"], "LOGOHM", 4, "CX-1030", "X17680", 325.0,
        NEGATIVE, working_range=(3.0, 320.0))
    assert any("no curve to read" in message for message in warnings)


def test_the_real_cernox_curve_passes_every_check_on_both_models():
    """The whole point: the file the lab actually has, checked end to end."""
    _need_cal_files()
    source = LOADER.load_sensor_file(LS340_17680)
    meta = source["meta"]
    for model, input_type in ((M340, 8), (M350, 3)):
        errors, _, stats = LOADER.analyse_curve(
            model, source["points"], "LOGOHM", meta["format"],
            "CX-1030-SD-4L", meta["serial"], meta["limit"],
            meta["coefficient"], working_range=(4.0, 325.0),
            input_type=input_type)
        assert not errors, (model, errors)
        assert stats["count"] == 129
        assert stats["coefficient"] == NEGATIVE
        commands = LOADER.build_curve_commands(
            model, 21, "CX-1030-SD-4L", meta["serial"], meta["format"],
            meta["limit"], meta["coefficient"], source["points"])
        expected = 129 + 2 + (1 if model == M340 else 0)
        assert len(commands) == expected


def test_more_than_200_points_is_thinned_and_said_to_be():
    """Both models hold 200 breakpoints, and nothing is interpolated to fit."""
    assert LOADER.MAX_CURVE_POINTS == 200
    original = [(float(i), float(i)) for i in range(1, 301)]
    thinned, dropped = LOADER.thin_points(original)
    assert len(thinned) == 200
    assert dropped == 100
    assert thinned[0] == original[0] and thinned[-1] == original[-1]
    assert all(point in original for point in thinned)


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------

def test_a_padded_header_reply_is_read_the_way_it_is_printed():
    """CRVHDR? pads the name to 15 and the serial to 10."""
    header = LOADER.parse_crvhdr_reply(
        "CX-1030-SD-4L  ,X17680    ,4,+325.000,1")
    assert header["name"] == "CX-1030-SD-4L"
    assert header["serial"] == "X17680"
    assert header["format"] == 4
    assert math.isclose(header["limit"], 325.0)
    assert header["coefficient"] == 1
    assert header["limit_text"] == "+325.000"

    assert LOADER.header_is_empty(LOADER.parse_crvhdr_reply(
        "               ,          ,1,+0.000,1"))
    assert not LOADER.header_is_empty(header)


def test_a_point_reply_keeps_the_reading_first_order():
    temperature, reading, texts = LOADER.parse_crvpt_reply(
        "+1.64523,+325.000")
    assert math.isclose(reading, 1.64523)
    assert math.isclose(temperature, 325.0)
    assert texts == ("+1.64523", "+325.000")
    assert LOADER.point_is_empty(
        *LOADER.parse_crvpt_reply("+0.00000,+0.000")[:2])


def test_the_readback_is_judged_at_the_precision_it_was_printed_to():
    """A tolerance chosen here would either cry wolf or hide a real error."""
    sent = [(325.0, 1.645231)]
    read = [(325.0, 1.6452)]
    loose = LOADER.compare_curves(sent, read,
                                  read_texts=[("1.6452", "325.000")])
    assert loose["matched"], loose["problems"]
    tight = LOADER.compare_curves(sent, read,
                                  read_texts=[("1.64520000", "325.000")])
    assert not tight["matched"]


def _verified_pair():
    expected = {"curve": 21, "name": "CX-1030", "serial": "X17680",
                "format": 4, "limit": 325.0, "coefficient": 1}
    header = {"name": "CX-1030", "serial": "X17680", "format": 4,
              "limit": 325.0, "coefficient": 1, "limit_text": "325.000"}
    comparison = {"matched": True, "sent_count": 134, "read_count": 134,
                  "problems": []}
    return expected, header, comparison


def test_a_good_readback_verifies():
    expected, header, comparison = _verified_pair()
    verdict, _, _ = LOADER.classify_verify(M350, expected, header, comparison)
    assert verdict == "verified"


def test_a_leftover_tail_is_caught():
    """The failure CRVDEL exists to prevent, named rather than guessed at."""
    expected, header, comparison = _verified_pair()
    verdict, headline, advice = LOADER.classify_verify(
        M350, expected, header, comparison, tail_index=135)
    assert verdict == "tail"
    assert "135" in headline
    assert "CRVDEL 21" in advice


def test_nothing_written_is_told_apart_from_lost_points():
    """Three header fields differing means the curve never landed.

    A short point count in that case is the point count of somebody else's
    curve, not evidence that CRVPT lines were lost, and the advice must not
    send the operator after the wrong thing.
    """
    expected, _, _ = _verified_pair()
    stale = {"name": "DT-470", "serial": "00011134", "format": 2,
             "limit": 475.0, "coefficient": 1, "limit_text": "475.000"}
    comparison = {"matched": False, "sent_count": 134, "read_count": 86,
                  "problems": []}
    verdict, headline, advice = LOADER.classify_verify(
        M340, expected, stale, comparison)
    assert verdict == "not_written"
    assert "NOT evidence" in advice
    assert "CRVSAV" in advice             # the 340's own failure mode
    _, _, advice_350 = LOADER.classify_verify(M350, expected, stale,
                                              comparison)
    assert "CRVSAV" not in advice_350

    # With a baseline it stops being an inference.
    _, headline_with_baseline, _ = LOADER.classify_verify(
        M340, expected, stale, comparison, baseline_header=dict(stale))
    assert "before the send" in headline_with_baseline


def test_a_changed_format_code_is_its_own_diagnosis():
    """A curve stored under the wrong format reads plausible nonsense."""
    expected, header, comparison = _verified_pair()
    header = dict(header, format=3)
    verdict, _, advice = LOADER.classify_verify(M350, expected, header,
                                                comparison)
    assert verdict == "format_only"
    assert "wrong units" in advice


def test_lost_points_with_a_right_header_blames_the_point_commands():
    expected, header, _ = _verified_pair()
    comparison = {"matched": False, "sent_count": 134, "read_count": 129,
                  "problems": []}
    verdict, _, advice = LOADER.classify_verify(M350, expected, header,
                                                comparison)
    assert verdict == "points"
    assert "CRVPT" in advice


def test_the_real_curve_survives_a_full_offline_round_trip():
    """Build the commands, play them back, and compare as the module would."""
    _need_cal_files()
    source = LOADER.load_sensor_file(LS340_17680)
    meta = source["meta"]
    commands = LOADER.build_curve_commands(
        M340, 21, "CX-1030-SD-4L", meta["serial"], meta["format"],
        meta["limit"], meta["coefficient"], source["points"])

    stored = []
    texts = []
    for command in commands:
        if not command.startswith("CRVPT"):
            continue
        fields = command.split(" ", 1)[1].split(",")
        stored.append((float(fields[3]), float(fields[2])))
        texts.append((fields[2], fields[3]))
    assert len(stored) == 129

    comparison = LOADER.compare_curves(source["points"], stored,
                                       read_texts=texts)
    assert comparison["matched"], comparison["problems"]

    header_reply = commands[1].split(" ", 1)[1].split(",", 1)[1]
    header = LOADER.parse_crvhdr_reply(header_reply)
    expected = {"curve": 21, "name": "CX-1030-SD-4L",
                "serial": meta["serial"], "format": meta["format"],
                "limit": meta["limit"], "coefficient": meta["coefficient"]}
    verdict, _, _ = LOADER.classify_verify(M340, expected, header, comparison)
    assert verdict == "verified"


# ---------------------------------------------------------------------------
# The model, and what follows from it
# ---------------------------------------------------------------------------

def test_the_model_comes_from_idn_and_nothing_else():
    assert LOADER.model_from_idn("LSCI,MODEL340,123456,053004") == M340
    assert LOADER.model_from_idn("LSCI,MODEL350,LSA23AR,1.5") == M350
    assert LOADER.model_from_idn("LSCI,MODEL331S,123,1.0") is None
    assert LOADER.model_from_idn("Cryocon,Model 34,204683,3.18A") is None
    assert LOADER.is_lakeshore_idn("LSCI,MODEL331S,123,1.0")


def test_the_model_starts_unset():
    """Neither model is a safe default, so the window has none.

    The curve range, the data formats and whether CRVSAV is needed all follow
    from the model, and guessing writes a curve that is not there.
    """
    assert "self.model_var = tk.StringVar(value=MODEL_UNSET)" in SOURCE
    assert "not set" in LOADER.MODEL_UNSET


def test_a_model_that_contradicts_idn_closes_the_session():
    """Warning about it would not be enough: the next command is a write."""
    connect = SOURCE.split("def connect(")[1].split("\n    def ")[0]
    assert "expected_model" in connect
    assert "self.disconnect()" in connect
    assert "not interchangeable" in connect


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_the_window_builds_and_loads_the_real_file():
    """Loading a .340 must fill every CRVHDR field from the file itself."""
    _need_cal_files()
    import tkinter as tk
    top = tk.Toplevel(_shared_root())
    top.withdraw()
    try:
        gui = LOADER.CurveLoaderGUI(top)
        assert gui.model() is None            # nothing assumed
        gui.model_var.set(f"{M340} - {LOADER.MODEL_SPECS[M340]['label']}")
        gui._on_model_change()
        assert gui.model() == M340
        assert gui._curve_number() == 21

        gui._load_file(LS340_17680)
        assert gui.name_var.get() == "CX-1030-SD-4L"
        assert gui.serial_var.get() == "X17680"
        assert gui._parse_leading_int(gui.format_var.get()) == 4
        assert float(gui.limit_var.get()) == 325.0
        assert gui._parse_leading_int(gui.coefficient_var.get()) == NEGATIVE

        assert not gui.curve_errors, gui.curve_errors
        assert len(gui.curve_points) == 129
        # CRVDEL + CRVHDR + 129 x CRVPT + CRVSAV
        assert len(gui.curve_commands) == 132
        assert gui.curve_commands[0] == "CRVDEL 21"
        assert gui.curve_commands[-1] == "CRVSAV"

        # The same curve for a 350 loses only the CRVSAV.
        gui.model_var.set(f"{M350} - {LOADER.MODEL_SPECS[M350]['label']}")
        gui._on_model_change()
        assert len(gui.curve_commands) == 131
        assert "CRVSAV" not in gui.curve_commands
    finally:
        top.destroy()


def test_without_a_model_the_window_refuses_to_build_commands():
    _need_cal_files()
    import tkinter as tk
    top = tk.Toplevel(_shared_root())
    top.withdraw()
    try:
        gui = LOADER.CurveLoaderGUI(top)
        gui._load_file(LS340_17680)
        assert not gui.curve_commands
        assert any("model" in message.lower() for message in gui.curve_errors)
    finally:
        top.destroy()


def test_the_window_offers_the_extension_and_it_reaches_134_points():
    _need_cal_files()
    import tkinter as tk
    top = tk.Toplevel(_shared_root())
    top.withdraw()
    try:
        gui = LOADER.CurveLoaderGUI(top)
        gui.model_var.set(f"{M350} - {LOADER.MODEL_SPECS[M350]['label']}")
        gui._on_model_change()
        gui._load_file(LS340_17680)
        assert len(gui.curve_points) == 129
        assert gui.use_second_var.get() is False   # opt-in

        gui.second_source = LOADER.load_sensor_file(DAT_17680)
        gui.second_source_path = DAT_17680
        gui.use_second_var.set(True)
        gui._rebuild_curve()
        assert len(gui.curve_points) == 134
        assert not gui.curve_errors, gui.curve_errors
        temperatures = [t for t, _ in gui.curve_points]
        assert min(temperatures) < 3.6
        assert any("uncertified" in message
                   for message in gui.curve_warnings)
    finally:
        top.destroy()


# ---------------------------------------------------------------------------
# It fills empty curves only
# ---------------------------------------------------------------------------

def _free(curve):
    return {"curve": curve, "empty": True, "standard": False, "name": "",
            "serial": "", "format": 1, "limit": 0.0, "coefficient": 1,
            "limit_text": "0.000"}


def _occupied(curve, name="DT-470", serial="00011134"):
    return {"curve": curve, "empty": False, "standard": False, "name": name,
            "serial": serial, "format": 2, "limit": 475.0, "coefficient": 1,
            "limit_text": "475.000"}


def _gui_with_curve(model=M350, curve_map=None):
    """A window with a real curve loaded and an optional pretend listing."""
    _need_cal_files()
    import tkinter as tk
    top = tk.Toplevel(_shared_root())
    top.withdraw()
    gui = LOADER.CurveLoaderGUI(top)
    gui.model_var.set(f"{model} - {LOADER.MODEL_SPECS[model]['label']}")
    gui._on_model_change()
    gui._load_file(LS340_17680)
    if curve_map is not None:
        gui._apply_event(("curves_listed", curve_map))
    return top, gui


def test_an_occupied_curve_is_refused_not_warned_about():
    """The target must be empty. An occupied one blocks the send outright."""
    listing = [_occupied(21), _free(22), _free(23)]
    top, gui = _gui_with_curve(curve_map=listing)
    try:
        # The picker lands on the first FREE curve, not the first curve.
        assert gui._curve_number() == 22
        assert gui._target_state() == "free"
        assert not gui.curve_errors, gui.curve_errors
        assert gui.curve_commands

        # Choosing the occupied one is an error, and nothing is built.
        for value in gui.curve_combo["values"]:
            if gui._parse_leading_int(value) == 21:
                gui.curve_var.set(value)
                break
        gui._rebuild_curve()
        assert gui._target_state() == "in use"
        assert not gui.curve_commands
        assert any("already holds" in message for message in gui.curve_errors)
        assert any("pick an empty curve" in message
                   for message in gui.curve_errors)
        # and it points at the free one
        assert any("22" in message for message in gui.curve_errors)
    finally:
        top.destroy()


def test_a_curve_that_would_not_answer_is_not_treated_as_free():
    listing = [dict(_free(21), empty=None, error="TimeoutError: no reply"),
               _free(22)]
    top, gui = _gui_with_curve(curve_map=listing)
    try:
        for value in gui.curve_combo["values"]:
            if gui._parse_leading_int(value) == 21:
                gui.curve_var.set(value)
                break
        gui._rebuild_curve()
        assert gui._target_state() == "no answer"
        assert not gui.curve_commands
        assert gui.curve_errors
    finally:
        top.destroy()


EMPTY_POINT = (0.0, 0.0, ("0", "0"))
STORED_POINT = (325.0, 1.64523, ("1.64523", "325.000"))


def _fake_points(backend, stored_at=(), log=None):
    """Answer CRVPT? from a table: `stored_at` lists the indices that hold a
    breakpoint; every other index answers 0,0. Records what was asked."""
    def read_curve_point(curve, index):
        if log is not None:
            log.append((curve, index))
        return STORED_POINT if index in stored_at else EMPTY_POINT
    backend.read_curve_point = read_curve_point


def test_the_pre_send_check_reads_the_instrument_not_the_listing():
    """The listing can be minutes old. Reading the curve immediately before
    the write -- header AND breakpoints -- is what actually protects an
    existing curve. The gate answers with a word, never a flag."""
    listing = [_free(21), _free(22)]
    top, gui = _gui_with_curve(curve_map=listing)
    try:
        assert gui._curve_number() == 21
        asked = []

        # The instrument holds points, whatever the listing said. One CRVPT?
        # is enough to refuse: the scan stops at the first stored point.
        gui.backend.read_curve_header = lambda curve: {
            "name": "SOMEBODY ELSE", "serial": "X99999", "format": 4,
            "limit": 325.0, "coefficient": 1, "limit_text": "325.000"}
        _fake_points(gui.backend, stored_at=(1,), log=asked)
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "in use"
        assert header["name"] == "SOMEBODY ELSE"
        assert "holds breakpoints" in detail
        assert "SOMEBODY ELSE" in detail
        assert "nothing was sent" in detail
        assert asked == [(21, 1)]

        # An unreadable reply is not "empty" either.
        def boom(curve):
            raise TimeoutError("no reply")

        gui.backend.read_curve_header = boom
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "unreadable"
        assert header is None
        assert "Nothing was sent" in detail

        # A point that will not answer is just as unreadable as a header.
        gui.backend.read_curve_header = (
            lambda curve: LOADER.parse_crvhdr_reply(
                "               ,          ,1,+0.000,1"))

        def point_boom(curve, index):
            raise TimeoutError("no reply")

        gui.backend.read_curve_point = point_boom
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "unreadable"

        # A header somebody wrote, with nothing behind it, is a stub: not
        # free, not in use, and the whole depth was read to say so.
        gui.backend.read_curve_header = lambda curve: {
            "name": "SOMEBODY ELSE", "serial": "X99999", "format": 4,
            "limit": 325.0, "coefficient": 1, "limit_text": "325.000"}
        asked.clear()
        _fake_points(gui.backend, log=asked)
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "stub"
        assert "no breakpoints" in detail
        assert "SOMEBODY ELSE" in detail
        assert [index for _, index in asked] == list(
            range(1, LOADER.GATE_PROBE_LIMIT + 1))

        # The 340's firmware filler is NOT a stored curve. Header-only
        # judgement called this slot occupied; the breakpoints say free.
        gui.backend.read_curve_header = lambda curve: {
            "name": "User 21", "serial": "", "format": 2,
            "limit": 375.0, "coefficient": 1, "limit_text": "375.0"}
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "free"
        assert "is empty" in detail

        # A blank header with no points is free, and it is the ONLY answer
        # that lets a send through.
        gui.backend.read_curve_header = (
            lambda curve: LOADER.parse_crvhdr_reply(
                "               ,          ,1,+0.000,1"))
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "free"
        assert "is empty" in detail
        assert "firmware default" in detail

        # A stored point deep in the curve, behind an innocent header, is
        # still a stored point.
        _fake_points(gui.backend, stored_at=(134,))
        state, header, detail = gui._target_is_empty_now(21)
        assert state == "in use"
        assert "21,134" in detail
    finally:
        top.destroy()


class _FakeLink:
    """Answers *IDN? and nothing else. The sequence must not need more."""
    address = "GPIB0::12::INSTR"
    is_connected = True

    def __init__(self, idn):
        self.idn = idn
        self.queries = []

    def query(self, command):
        self.queries.append(command)
        if command.strip() == "*IDN?":
            return self.idn
        raise AssertionError(f"unexpected query {command!r}")


def _sequence_ready(gui, state, header=None, detail="gate detail"):
    """Wire a window so _run_full_sequence runs to completion on this thread
    with the gate answering `state`. Returns the list of command batches
    that reached send_curve and the list of curves that reached CRVDEL."""
    sent = []
    erased = []
    gui.is_connected = True
    gui.backend.link = _FakeLink("LSCI,MODEL350,LSA23AR,1.5")
    gui.backend.curve_is_empty = (
        lambda curve, indices=None, progress=None:
            (state == "free", header or {}, state, detail))
    gui.backend.send_curve = (
        lambda commands, progress=None, should_stop=None:
            sent.append(list(commands)))
    gui.backend.delete_curve = lambda curve: erased.append(curve)
    gui._confirm_send = lambda expected, steps=None: True
    gui._run_in_worker = lambda description, function: function()
    gui._verify_against = lambda *args, **kwargs: "verified"
    gui.sequence_assign_var.set(False)
    return sent, erased


def _drain_dialogs(gui):
    dialogs = []
    while not gui._events.empty():
        event = gui._events.get_nowait()
        if event[0] == "dialog":
            dialogs.append(event)
    return dialogs


def test_the_sequence_sends_only_when_the_gate_says_free():
    """REGRESSION. The gate answers with a word. Every word is a non-empty
    string, so a sequence that used the word as its pass flag would have
    passed step 2 for 'in use' and written over a stored calibration."""
    listing = [_free(21), _free(22)]
    for state in ("in use", "unreadable", "stub"):
        top, gui = _gui_with_curve(curve_map=listing)
        try:
            sent, erased = _sequence_ready(gui, state)
            gui._run_full_sequence()
            assert sent == [], f"{state!r} let the send through"
            assert erased == [], f"{state!r} erased the curve"
            dialogs = _drain_dialogs(gui)
            assert any("Sequence Stopped" in d[2] for d in dialogs), state
            assert any("Step 2" in d[3] for d in dialogs), state
            if state == "stub":
                assert any("Use Send" in d[3] for d in dialogs)
        finally:
            top.destroy()

    top, gui = _gui_with_curve(curve_map=listing)
    try:
        sent, erased = _sequence_ready(gui, "free")
        gui._run_full_sequence()
        assert len(sent) == 1
        assert sent[0] == gui.curve_commands
        assert erased == []
        dialogs = _drain_dialogs(gui)
        assert not any("Sequence Stopped" in d[2] for d in dialogs)
    finally:
        top.destroy()


def test_the_sequence_stops_on_the_wrong_model_before_the_gate():
    listing = [_free(21), _free(22)]
    top, gui = _gui_with_curve(curve_map=listing)
    try:
        sent, erased = _sequence_ready(gui, "free")
        gui.backend.link = _FakeLink("LSCI,MODEL340,340219,111196")
        gui._run_full_sequence()
        assert sent == [] and erased == []
        dialogs = _drain_dialogs(gui)
        assert any("Step 1" in d[3] for d in dialogs)
    finally:
        top.destroy()


def test_the_send_dialog_says_it_fills_an_empty_curve():
    """No wording anywhere offers to overwrite."""
    confirm = SOURCE.split("def _confirm_send")[1].split(NEXT_METHOD)[0]
    assert "fills EMPTY user curve" in confirm
    assert "overwrites" not in confirm
    sequence = SOURCE.split("def _run_full_sequence")[1].split(NEXT_METHOD)[0]
    assert "confirm curve {curve} is empty" in sequence


# ---------------------------------------------------------------------------
# The map of what is on the instrument
# ---------------------------------------------------------------------------

class _MapBackend:
    """Just enough of CurveLoaderBackend for list_curves(): headers and
    points come from a table, the emptiness logic is the real one."""
    curve_is_empty = LOADER.CurveLoaderBackend.curve_is_empty
    read_curve_occupancy = LOADER.CurveLoaderBackend.read_curve_occupancy
    model = M350

    def __init__(self):
        self.header_calls = []
        self.point_calls = []

    def _spec(self):
        return LOADER.MODEL_SPECS[M350]

    def read_curve_header(self, curve):
        self.header_calls.append(curve)
        if curve <= 20:
            return LOADER.parse_crvhdr_reply(
                f"Std{curve}          ,          ,2,+475.000,1")
        if curve == 21:
            return LOADER.parse_crvhdr_reply(
                "CX-1030-SD-4L  ,X17680    ,4,+325.000,1")
        if curve == 23:
            # A header, and nothing behind it.
            return LOADER.parse_crvhdr_reply(
                "S700           ,          ,4,+325.000,1")
        if curve == 28:
            # The 340's filler. It used to make every free slot look taken.
            return LOADER.parse_crvhdr_reply(
                "User 28        ,          ,2,+375.000,1")
        return LOADER.parse_crvhdr_reply(
            "               ,          ,1,+0.000,1")

    def read_curve_point(self, curve, index):
        self.point_calls.append((curve, index))
        if curve == 21:
            return STORED_POINT
        return EMPTY_POINT


def test_the_map_covers_the_whole_curve_range_and_marks_standard_curves():
    """list_curves() defaults to the user block and can take the whole lot.
    Emptiness comes from CRVPT?, with the header as context."""
    backend = _MapBackend()
    entries = LOADER.CurveLoaderBackend.list_curves(backend)
    assert [entry["curve"] for entry in entries] == list(range(21, 60))
    assert all(entry["standard"] is False for entry in entries)
    by_curve = {entry["curve"]: entry for entry in entries}
    assert by_curve[21]["empty"] is False
    assert by_curve[21]["state"] == "in use"
    assert by_curve[21]["name"].strip() == "CX-1030-SD-4L"
    assert by_curve[22]["empty"] is True
    assert by_curve[22]["state"] == "free"
    assert by_curve[23]["empty"] is False
    assert by_curve[23]["state"] == "stub"
    assert by_curve[28]["empty"] is True, "filler header must read as free"
    assert by_curve[28]["state"] == "free"
    assert all("detail" in entry for entry in entries)

    # An occupied slot costs one CRVPT?; an empty one the whole ladder.
    asked = {}
    for curve, index in backend.point_calls:
        asked.setdefault(curve, []).append(index)
    assert asked[21] == [1]
    assert asked[22] == list(LOADER.MAP_PROBE_INDICES)
    assert asked[23] == list(LOADER.MAP_PROBE_INDICES)

    backend = _MapBackend()
    whole = LOADER.CurveLoaderBackend.list_curves(backend, 1, 59)
    assert len(whole) == 59
    assert whole[0]["curve"] == 1 and whole[0]["standard"] is True
    assert whole[0]["state"] == "standard"
    assert whole[0]["empty"] is False
    assert whole[20]["curve"] == 21 and whole[20]["standard"] is False
    assert backend.header_calls == list(range(1, 60))
    # Standard curves are never a target, so they are never probed.
    assert not any(curve <= 20 for curve, _ in backend.point_calls)


def test_a_curve_that_will_not_answer_stays_in_the_map():
    """A gap in the map is itself worth seeing, whichever query it was
    that went unanswered."""
    class Backend(_MapBackend):
        def read_curve_header(self, curve):
            if curve == 30:
                raise TimeoutError("no reply")
            return super().read_curve_header(curve)

        def read_curve_point(self, curve, index):
            if curve == 31 and index == 5:
                raise OSError("bus hung")
            return super().read_curve_point(curve, index)

    entries = LOADER.CurveLoaderBackend.list_curves(Backend())
    by_curve = {entry["curve"]: entry for entry in entries}
    assert "TimeoutError" in by_curve[30]["error"]
    assert by_curve[30]["empty"] is None
    assert "OSError" in by_curve[31]["error"]
    assert by_curve[31]["empty"] is None
    assert by_curve[32]["empty"] is True


def test_the_map_tab_shows_a_row_per_curve():
    listing = [dict(_occupied(index, name=f"Std{index}"), standard=True)
               for index in (1, 2)]
    listing += [_occupied(21, name="CX-1030-SD-4L", serial="X17680"),
                _free(22),
                dict(_free(23), empty=None, error="TimeoutError: no reply")]
    top, gui = _gui_with_curve(curve_map=listing)
    try:
        rows = gui.map_table.get_children()
        assert len(rows) == len(listing)
        values = [gui.map_table.item(row, "values") for row in rows]
        by_curve = {int(row[0]): row for row in values}
        assert by_curve[1][1] == "standard (read-only)"
        assert by_curve[21][1] == "in use"
        assert by_curve[21][2] == "CX-1030-SD-4L"
        assert by_curve[21][3] == "X17680"
        assert "V/K" in by_curve[21][4]
        assert by_curve[22][1] == "free"
        assert by_curve[23][1] == "no answer"
        # An empty curve shows no stale format or limit.
        assert by_curve[22][4] == "" and by_curve[22][5] == ""
        # Only the user curves are counted as free.
        assert "1 of 3 user curves are free" in gui.map_status.cget("text")

        # Standard curves are never offered as a target.
        offered = {gui._parse_leading_int(value)
                   for value in gui.curve_combo["values"]}
        assert 1 not in offered and 2 not in offered
        assert {21, 22, 23} <= offered
    finally:
        top.destroy()


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------

def test_the_launcher_lists_the_loader():
    """A module nobody can find is a module nobody uses."""
    key = "Lakeshore Sensor Curve Loader"
    main_source = open(os.path.join(REPO_ROOT, "pica", "main.py"),
                       encoding="utf-8").read()
    assert "lakeshore/Sensor_Curve_Loader_L340_L350_GUI.py" in main_source
    assert f'"{key}"' in main_source


def test_the_direct_control_module_can_reach_the_loader():
    """Direct Control's Input Curve field can only point at a curve that has
    already been installed, so the two modules belong together."""
    direct_control_path = os.path.join(
        REPO_ROOT, "pica", "lakeshore", "T_Control_L350_DirectControl_GUI.py")
    source = open(direct_control_path, encoding="utf-8").read()
    assert "launch_curve_loader" in source
    assert "Sensor_Curve_Loader_L340_L350_GUI.py" in source
    # A missing sibling must disable the button, not crash the window.
    assert "Sensor Curve Loader not found" in source


def test_the_module_writes_only_curve_and_input_curve_commands():
    """Non-destructive by construction.

    Every literal command this module writes is enumerated here. A *RST, a
    SETP, a RANGE or an INTYPE appearing in a write() would show up as a new
    verb and fail this test, which is the point: an operator running a curve
    transfer on a controlling cryostat must not have to read the source to
    know it is safe.
    """
    verbs = set(re.findall(r'\.write\(\s*f?"([A-Z*][A-Z0-9*]*)', SOURCE))
    assert verbs == {"CRVDEL", "CRVSAV", "INCRV"}, verbs

    for model, extra in ((M340, {"CRVSAV"}), (M350, set())):
        built = {command.split()[0] for command in _commands(model=model)}
        assert built == {"CRVDEL", "CRVHDR", "CRVPT"} | extra, (model, built)


def test_the_offline_self_test_passes():
    """The same checks the Advanced panel runs, and --selftest from a shell."""
    messages = []
    assert LOADER.run_self_test(report=messages.append)
    assert any("checks passed" in message for message in messages)
    assert "--selftest" in SOURCE


def test_the_link_is_paced_and_retries_the_first_idn():
    """The pattern the sibling modules already run on."""
    assert LOADER.LAKESHORE_CONNECT_ATTEMPTS >= 3
    assert LOADER.LAKESHORE_OPEN_SETTLE_S > 0
    assert LOADER.LAKESHORE_MIN_GAP_S > 0
    assert LOADER.CURVE_COMMAND_GAP_S > 0
    assert LOADER.CRVSAV_TIMEOUT_S >= 30


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
