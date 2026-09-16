"""Tests for the two read-only sensor curve viewers.

  * pica/cryocon/Sensor_Curve_Viewer_CC34_GUI.py
  * pica/lakeshore/Sensor_Curve_Viewer_L350_GUI.py

Both browse and export the calibration curves an instrument already holds.
Neither may ever change one. The Cryocon sits on the same bus as a running
cryostat and its *RST is a fifteen-second hardware reset; the Lake Shore is
usually mid-ramp when somebody wants to look at a curve. So the property that
matters most here is not that the parsing is right, it is that nothing this
module can do reaches the instrument as anything but a question.

The three that matter most:

  * test_*_link_refuses_every_setting_command
    The proof, on a fake instrument that records what reached it, that a
    setting command is refused AND that nothing was transmitted when it was.

  * test_cc34_curve_points_are_reading_then_temperature
    A Cryo-con stores the sensor reading first and the temperature second.
    Once the numbers are in a file with no labels the two orders are
    indistinguishable, and a swapped export would be silently wrong.

  * test_l350_340_export_round_trips
    A .340 written from a read-back curve has to be re-loadable, and its
    stated breakpoint count has to match the rows that follow -- the loader
    module in this repository refuses a file where it does not.

Each module also carries its own offline self-test; the last two tests here
run those, so a check added there is covered by pytest without being written
twice.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import re
import sys
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

CC34_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                         "Sensor_Curve_Viewer_CC34_GUI.py")
L350_PATH = os.path.join(REPO_ROOT, "pica", "lakeshore",
                         "Sensor_Curve_Viewer_L350_GUI.py")
L340_PATH = os.path.join(REPO_ROOT, "pica", "lakeshore",
                         "Sensor_Curve_Viewer_L340_GUI.py")
# The loader holds the authoritative sensor-type tables. It is loaded here
# only so the viewers' copies can be checked against it; see
# test_the_viewers_sensor_type_tables_match_the_loader.
LS_LOADER_PATH = os.path.join(REPO_ROOT, "pica", "lakeshore",
                              "Sensor_Curve_Loader_L340_L350_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc34 = _load("cc34_curve_viewer", CC34_PATH)
l350 = _load("l350_curve_viewer", L350_PATH)
l340 = _load("l340_curve_viewer", L340_PATH)
ls_loader = _load("ls_curve_loader", LS_LOADER_PATH)


SAMPLE_CALCUR = ("X17680\n"
                 "R8K10UA\n"
                 "-1.0\n"
                 "LOGOHM\n"
                 "1.64523   325.0\n"
                 "2.50000   100.0\n"
                 "3.10000   4.0\n"
                 ";")

# Every setting command either viewer could plausibly be asked to send, plus
# the ones that would be worst. None of them carries a '?', which is exactly
# why the guard catches them all without a list of names to keep up to date.
CC34_SETTING_COMMANDS = (
    "*RST", "*CLS", "STOP", "CONTROL",
    "CALCUR 15", 'SENTYPE 15:NAME "X17680"', "SENTYPE 15:TYPE ACR",
    "SENTYPE 15:MULTIPLY -1.0", "INPUT A:SENIX 15",
    "LOOP 1:SETPT 300", "LOOP 1:RANGE HI", "LOOP 1:PGAIN 0.2",
)
L350_SETTING_COMMANDS = (
    "*RST", "*CLS",
    "CRVDEL 21", "CRVSAV", "CRVHDR 21,NAME,SN,4,325.0,1",
    "CRVPT 21,1,1.5,300.0", "INCRV A,21", "INTYPE A,3,1,0,0,1",
    "SETP 1,300", "RANGE 1,3", "RAMP 1,1,0.5", "MOUT 1,50",
)


class FakeCryocon:
    """Records everything that reached the bus. Nothing should."""

    def __init__(self, lines=()):
        self.written = []
        self.queried = []
        self.timeout = 1000
        self._lines = list(lines)

    def query(self, command):
        self.queried.append(command)
        return "Cryocon,34,204683,3.03A"

    def write(self, command):
        self.written.append(command)

    def read(self):
        if not self._lines:
            raise TimeoutError("no more lines")
        return self._lines.pop(0)

    def close(self):
        pass

    @property
    def traffic(self):
        return self.written + self.queried


class FakeLakeshore:
    """Records everything that reached the bus. Nothing should."""

    def __init__(self, reply="LSCI,MODEL350,1234,1.0"):
        self.written = []
        self.queried = []
        self.timeout = 1000
        self.reply = reply

    def query(self, command):
        self.queried.append(command)
        return self.reply

    def write(self, command):
        self.written.append(command)

    def close(self):
        pass

    @property
    def traffic(self):
        return self.written + self.queried


def _cc34_link(lines=()):
    """A CryoconReadOnlyLink wired to a fake, with no VISA and no connect."""
    link = cc34.CryoconReadOnlyLink.__new__(cc34.CryoconReadOnlyLink)
    link.instrument = FakeCryocon(lines)
    link._last_io = 0.0
    link.commands_sent = 0
    return link


def _l350_link(reply="LSCI,MODEL350,1234,1.0"):
    link = l350.LakeshoreReadOnlyLink.__new__(l350.LakeshoreReadOnlyLink)
    link.instrument = FakeLakeshore(reply)
    link._last_io = 0.0
    link.commands_sent = 0
    return link


# ---------------------------------------------------------------------------
# THE READ-ONLY GUARANTEE
# ---------------------------------------------------------------------------

def test_cc34_link_refuses_every_setting_command():
    """Both paths to the bus refuse a setting command and transmit nothing.

    Refusing is only half of it. A guard that raised after writing would be
    worse than none, so the fake instrument is checked afterwards to confirm
    the command never left.
    """
    link = _cc34_link()
    for command in CC34_SETTING_COMMANDS:
        for method in (link.ask, link.ask_block):
            try:
                method(command)
            except cc34.ReadOnlyViolation:
                continue
            raise AssertionError(
                f"{method.__name__} accepted the setting command {command!r}")
    assert link.instrument.traffic == [], (
        f"a refused command still reached the instrument: "
        f"{link.instrument.traffic}")


def test_l350_link_refuses_every_setting_command():
    link = _l350_link()
    for command in L350_SETTING_COMMANDS:
        try:
            link.ask(command)
        except l350.ReadOnlyViolation:
            continue
        raise AssertionError(
            f"ask() accepted the setting command {command!r}")
    assert link.instrument.traffic == [], (
        f"a refused command still reached the instrument: "
        f"{link.instrument.traffic}")


def test_queries_are_still_allowed():
    """The guard must not be so tight that the modules cannot do their job."""
    link = _cc34_link()
    assert link.ask("*IDN?").startswith("Cryocon")
    for command in ("CALCUR? 15", "SENTYPE? 15", "SENTYPE 15:TYPE?",
                    "INPUT A:SENIX?", "INPUT? A"):
        assert cc34.is_query(command), command

    link = _l350_link()
    assert link.ask("*IDN?").startswith("LSCI")
    for command in ("CRVHDR? 21", "CRVPT? 21,1", "INCRV? A", "INTYPE? B"):
        assert l350.is_query(command), command


def test_neither_backend_exposes_a_write_path():
    """No method on either link takes an arbitrary command and writes it.

    The guard lives in ask()/ask_block(). A public write() alongside them
    would be a way around it, so its absence is the thing being asserted --
    if one is ever added, this test says so before it reaches an instrument.
    """
    for link_class in (cc34.CryoconReadOnlyLink, l350.LakeshoreReadOnlyLink):
        public = [name for name in dir(link_class)
                  if not name.startswith('_')]
        assert 'write' not in public, f"{link_class.__name__} has write()"
        assert 'write_raw' not in public, f"{link_class.__name__}.write_raw"
        assert 'write_line' not in public, f"{link_class.__name__}.write_line"


def test_cc34_backend_sends_only_queries_during_a_table_scan():
    """A whole slot-list scan, end to end, transmits nothing but questions."""
    backend = cc34.CurveViewerBackend(log=lambda msg: None)
    backend.link = _cc34_link()
    entries = backend.scan_sensor_table(first=0, last=3)
    assert len(entries) == 4
    for command in backend.link.instrument.traffic:
        assert '?' in command, f"a non-query was sent: {command!r}"


def test_l350_backend_sends_only_queries_during_a_catalogue_scan():
    backend = l350.CurveViewerBackend(log=lambda msg: None)
    backend.link = _l350_link("NAME,SN,4,325.0,1")
    entries = backend.scan_catalogue(first=21, last=24)
    assert len(entries) == 4
    for command in backend.link.instrument.traffic:
        assert '?' in command, f"a non-query was sent: {command!r}"


# ---------------------------------------------------------------------------
# CRYOCON: PARSING AND EXPORT
# ---------------------------------------------------------------------------

def test_cc34_curve_points_are_reading_then_temperature():
    """The Cryo-con order, not the Lake Shore .dat order.

    '1.64523   325.0' is a log-ohm reading of 1.64523 at 325 K, not a reading
    of 325 at 1.64 K. Once the numbers are in a file with no labels the two
    are indistinguishable, so this is checked directly rather than inferred
    from the plot looking sensible.
    """
    header, points = cc34.parse_calcur_block(SAMPLE_CALCUR, "slot 15")
    assert header['units'] == 'LOGOHM'
    assert points[0] == (1.64523, 325.0)
    assert points[-1] == (3.10000, 4.0)
    # Reading ascending, temperature falling: an NTC sensor, which is what a
    # Cernox is. The other way round would be a swapped read.
    assert points[0][0] < points[-1][0]
    assert points[0][1] > points[-1][1]


def test_cc34_block_read_stops_at_the_semicolon():
    link = _cc34_link(SAMPLE_CALCUR.split('\n') + ["not part of the curve"])
    text = link.ask_block("CALCUR? 15")
    assert link.instrument.written == ["CALCUR? 15"]
    assert text.strip().endswith(';')
    assert "not part of the curve" not in text


def test_cc34_truncated_reply_is_refused():
    """A partial read must not be shown as a curve.

    A CALCUR? that times out half way leaves a plausible-looking header and
    some points. Accepting it would put a curve on screen that is not the one
    in the instrument, and exporting that would put it in a file.
    """
    truncated = "\n".join(SAMPLE_CALCUR.split('\n')[:-1])
    try:
        cc34.parse_calcur_block(truncated, "slot 15")
    except cc34.CurveReadError as exc:
        assert "semicolon" in str(exc)
    else:
        raise AssertionError("a block with no semicolon should be refused")


def test_cc34_unknown_units_are_refused_not_assumed():
    bad = SAMPLE_CALCUR.replace("LOGOHM", "KELVIN")
    try:
        cc34.parse_calcur_block(bad, "slot 15")
    except cc34.CurveReadError as exc:
        assert "units" in str(exc)
    else:
        raise AssertionError("KELVIN is not a Cryo-con curve unit")


def test_cc34_crv_export_round_trips():
    header, points = cc34.parse_calcur_block(SAMPLE_CALCUR, "slot 15")
    text = cc34.crv_file_text(cc34.build_crv_lines(header, points))
    again_header, again_points = cc34.parse_calcur_block(text, "the file")
    assert again_header['name'] == header['name']
    assert again_header['sensor_type'] == header['sensor_type']
    assert again_header['multiplier'] == header['multiplier']
    assert again_header['units'] == header['units']
    assert again_points == points


def test_cc34_crv_export_keeps_the_instruments_own_digits():
    """What the instrument printed is the limit of what is known.

    Re-formatting a reply through a six-digit float writer would quietly drop
    digits the instrument gave, or invent ones it did not.
    """
    header, points = cc34.parse_calcur_block(
        "NAME\nACR\n-1.0\nOHMS\n1.2345678   300.0\n2.0   100.0\n;")
    text = cc34.crv_file_text(cc34.build_crv_lines(header, points))
    assert "1.2345678" in text, text


def test_cc34_csv_has_one_row_per_point_and_ohms_where_meaningful():
    header, points = cc34.parse_calcur_block(SAMPLE_CALCUR)
    text = cc34.build_curve_csv(15, header, points, idn="Cryocon,34",
                                address="GPIB0::12")
    rows = [line for line in text.splitlines()
            if line and not line.startswith('#')
            and not line.startswith('Index')]
    assert len(rows) == len(points)
    # LOGOHM 1.64523 is 10**1.64523 ohm, about 44.2.
    first_ohms = float(rows[0].split(',')[3])
    assert abs(first_ohms - 10 ** 1.64523) < 1e-3

    volts = dict(header, units='VOLTS')
    text = cc34.build_curve_csv(15, volts, points)
    rows = [line for line in text.splitlines()
            if line and not line.startswith('#')
            and not line.startswith('Index')]
    assert rows[0].endswith(','), (
        "volts have no resistance to report, so the column must be blank "
        "rather than carry a number that looks like one")


def test_cc34_empty_and_factory_slot_names():
    for name in ('', '   ', '.', 'NONE', 'none'):
        assert cc34.looks_like_empty_slot(name), repr(name)
    assert not cc34.looks_like_empty_slot("X17680")
    assert cc34.looks_like_factory_entry("Lakeshore 10")
    assert not cc34.looks_like_factory_entry("X17680")


# ---------------------------------------------------------------------------
# LAKE SHORE: PARSING AND EXPORT
# ---------------------------------------------------------------------------

def test_l350_header_is_parsed_and_an_empty_slot_is_recognised():
    header = l350.parse_crvhdr("CX-1030-SD  ,X17680    ,4,325.0,1", 21)
    assert header['name'] == "CX-1030-SD"
    assert header['serial'] == "X17680"
    assert header['format_code'] == 4
    assert header['units_label'] == "log(Ohm)"
    assert header['coefficient_name'] == "Negative"
    assert header['is_user_slot'] is True
    assert header['is_empty'] is False

    empty = l350.parse_crvhdr("            ,          ,0,0.0,1", 45)
    assert empty['is_empty'] is True
    assert empty['format_name'] == "unknown"


def test_l350_short_header_is_refused():
    try:
        l350.parse_crvhdr("DT-670,,3", 2)
    except l350.CurveReadError:
        return
    raise AssertionError("a three-field header should be refused")


def test_l350_point_parsing_tolerates_an_extra_field():
    assert l350.parse_crvpt("1.64523,325.000", 21, 1) == (1.64523, 325.0)
    assert l350.parse_crvpt("+1.64523,+325.000,0", 21, 1) == (1.64523, 325.0)
    try:
        l350.parse_crvpt("no data", 21, 1)
    except l350.CurveReadError:
        return
    raise AssertionError("a non-numeric reply should be refused")


def test_l350_340_export_round_trips():
    """The .340 has to be re-loadable, and honest about its own length.

    pica/cryocon/Sensor_Curve_Loader_CC34_GUI.py refuses a .340 whose stated
    'Number of Breakpoints' disagrees with the rows that follow, so a curve
    exported here with a stale count could not be loaded anywhere.
    """
    header = l350.parse_crvhdr("CX-1030-SD,X17680,4,325.0,1", 21)
    points = [(1.0, 300.0), (2.0, 100.0), (3.0, 4.0)]
    text = l350.build_340_text(header, points)

    stated = int(re.search(r'Number of Breakpoints:\s*(\d+)', text).group(1))
    read = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) != 3:
            continue
        try:
            index, units, temperature = (float(token) for token in tokens)
        except ValueError:
            continue
        if index != int(index):
            continue
        read.append((units, temperature))
    assert stated == len(read) == len(points)
    assert read == points
    assert "Data Format:    4" in text
    assert "Temperature coefficient:  1 (Negative)" in text


def test_l350_340_export_refuses_a_format_it_cannot_name():
    """A .340 header has to say what its middle column holds.

    If the instrument gave a format code this module does not know, writing
    the file anyway would mean guessing the units, and a curve loaded from it
    would be wrong in a way nothing downstream could detect.
    """
    header = l350.parse_crvhdr("SOMETHING,SN,9,325.0,1", 21)
    try:
        l350.build_340_text(header, [(1.0, 300.0)])
    except ValueError as exc:
        assert "Data Format" in str(exc)
        return
    raise AssertionError("format 9 should not produce a .340")


def test_l350_read_points_stops_at_the_instruments_end_marker():
    """A CRVPT? reply of 0,0 means the curve ended, and is not a breakpoint.

    A single zero pair followed by real data is a gap inside the curve, not
    its end, so it is reported rather than treated as a stop.
    """
    class ScriptedLink:
        def __init__(self, replies):
            self.replies = list(replies)
            self.asked = []

        def ask(self, command):
            self.asked.append(command)
            assert '?' in command, command
            return self.replies.pop(0)

    backend = l350.CurveViewerBackend(log=lambda msg: None)
    backend.link = ScriptedLink(["1.0,300.0", "2.0,100.0",
                                 "0.0,0.0", "0.0,0.0"])
    points, notes = backend.read_points(21)
    assert points == [(1.0, 300.0), (2.0, 100.0)]
    assert len(backend.link.asked) == 4

    backend.link = ScriptedLink(["1.0,300.0", "0.0,0.0", "2.0,100.0",
                                 "0.0,0.0", "0.0,0.0"])
    points, notes = backend.read_points(21)
    assert points == [(1.0, 300.0), (2.0, 100.0)]
    assert any("gaps inside the curve" in note for note in notes), notes


def test_l350_statistics_report_a_non_monotonic_curve():
    good = l350.curve_statistics([(1.0, 4.0), (2.0, 100.0), (3.0, 300.0)],
                                 "log(Ohm)")
    assert good['temperature_monotonic'] is True
    bad = l350.curve_statistics([(1.0, 4.0), (2.0, 300.0), (3.0, 100.0)],
                                "log(Ohm)")
    assert bad['temperature_monotonic'] is False


def test_l350_catalogue_csv_marks_empty_slots():
    entries = [l350.parse_crvhdr("DT-670,,2,500.0,2", 2),
               l350.parse_crvhdr("      ,      ,0,0.0,1", 45)]
    text = l350.build_catalogue_csv(entries, idn="LSCI", address="GPIB1::12")
    lines = [line for line in text.splitlines() if not line.startswith('#')]
    assert len(lines) == 3            # header row plus two slots
    assert lines[0].endswith(",EmptyByHeader,EmptyByPoints,Disagrees")
    assert ",standard," in lines[1]
    # Not probed: the header verdict stands alone and the points column is
    # left blank rather than guessed.
    assert lines[1].endswith(",no,,no")
    assert lines[2].endswith(",yes,,no")

    # Probed, and the two verdicts disagree: a 340's filler header reads as
    # occupied but CRVPT? found nothing. Both columns say what they saw.
    filler = l350.parse_crvhdr("User 28,,2,375.0,1", 28)
    filler.update(is_empty=False, points_empty=True, disagrees=True)
    agreed = l350.parse_crvhdr("      ,      ,0,0.0,1", 45)
    agreed.update(points_empty=True, disagrees=False)
    text = l350.build_catalogue_csv([filler, agreed])
    lines = [line for line in text.splitlines() if not line.startswith('#')]
    assert lines[1].endswith(",no,yes,yes")
    assert lines[2].endswith(",yes,yes,no")


# ---------------------------------------------------------------------------
# BOTH MODULES ARE REGISTERED, AND THEIR OWN SELF-TESTS PASS
# ---------------------------------------------------------------------------

def test_both_viewers_are_in_the_launcher():
    with open(os.path.join(REPO_ROOT, "pica", "main.py"),
              encoding='utf-8') as handle:
        main_source = handle.read()
    assert "cryocon/Sensor_Curve_Viewer_CC34_GUI.py" in main_source
    assert "lakeshore/Sensor_Curve_Viewer_L350_GUI.py" in main_source
    assert '"Cryocon Sensor Curve Viewer"' in main_source
    assert '"Lakeshore Sensor Curve Viewer"' in main_source


def test_cc34_module_self_test_passes():
    assert cc34.run_self_test(report=lambda message: None)


def test_l350_query_guard_admits_a_query_by_shape_only():
    for command in ("*IDN?", "CRVHDR? 21", "CRVPT? 21,1", "INCRV? A",
                    "INTYPE? B", "KRDG? A", "crvhdr? 21"):
        assert l350.is_query(command), command
    for command in ("CRVHDR? 21;CRVDEL 21", "CRVDEL 21 ?", "*RST?",
                    "CRVSAV?", "CRVHDR? 21; CRVSAV", "INCRV A,21?",
                    "CRVHDR?? 21", "CRVDEL 21", "", "?",
                    "CRVHDR? 21\nCRVDEL 21", "CRVPT? 21,1\r\n*RST"):
        assert not l350.is_query(command), command


def test_l350_slot_range_follows_the_instrument():
    assert l350.max_curve_for_idn("LSCI,MODEL340,340219,111196") == 60
    assert l350.max_curve_for_idn("LSCI,MODEL350,LSA23AR,1.5") == 59
    backend = l350.CurveViewerBackend.__new__(l350.CurveViewerBackend)
    backend.link = _SlotLink("CX,SN,4,325.000,1", {})
    backend.max_curve = 60
    assert backend.read_header(60)["curve"] == 60
    backend.max_curve = 59
    with pytest.raises(ValueError):
        backend.read_header(60)


def test_l350_an_incomplete_read_is_marked_and_not_exported():
    class FailingLink(_SlotLink):
        def ask(self, command):
            if command.startswith("CRVPT?") and command.endswith(",3"):
                raise TimeoutError("no reply")
            return super().ask(command)

    backend = l350.CurveViewerBackend.__new__(l350.CurveViewerBackend)
    backend.link = FailingLink("CX,SN,4,325.000,1",
                               {1: (1.5, 300.0), 2: (1.6, 200.0),
                                3: (1.7, 100.0)})
    points, notes = backend.read_points(21)
    assert points == [(1.5, 300.0), (1.6, 200.0)]
    assert l350.read_is_incomplete(notes)
    header = backend.read_header(21)
    header["_incomplete"] = True
    with pytest.raises(ValueError, match="did not reach the end"):
        l350.build_340_text(header, points)
    header["_incomplete"] = False
    assert "Number of Breakpoints:   2" in l350.build_340_text(header, points)


def test_l350_340_export_never_invents_a_header_field():
    header = l350.parse_crvhdr("CX,SN,4,junk,junk", 21)
    with pytest.raises(ValueError, match="inventing"):
        l350.build_340_text(header, [(1.5, 300.0)])
    five = l350.parse_crvhdr("S700,,5,325.000,1", 21)
    assert five["format_name"] == "Log Ohm/Log K"
    with pytest.raises(ValueError, match="log10 of kelvin"):
        l350.build_340_text(five, [(1.5, 2.4)])


def test_l350_describe_slot_believes_the_breakpoints():
    blank = l350.parse_crvhdr(",,1,0.000,1", 23)
    filler = l350.parse_crvhdr("User 28,,2,375.000,1", 28)
    named = l350.parse_crvhdr("CX-1030,X17680,4,325.000,1", 24)
    stored = [(2.94699, 4.0), (2.0, 100.0)]
    assert l350.describe_slot(blank, stored)[0] is True
    assert l350.describe_slot(filler, stored)[0] is True
    assert l350.describe_slot(named, stored) == (True, "")
    holds, note = l350.describe_slot(named, [])
    assert holds is False and "not a calibration" in note
    holds, note = l350.describe_slot(blank, [])
    assert holds is False and "holds no curve" in note


class _SlotLink:
    """CRVHDR? and CRVPT? from a table; every other command is refused."""
    is_connected = True
    address = "GPIB1::12"

    def __init__(self, header, points):
        self.header = header
        self.points = points
        self.asked = []

    def ask(self, command):
        self.asked.append(command)
        assert '?' in command, command
        if command.startswith("CRVHDR?"):
            return self.header
        index = int(command.split(',')[1])
        value, temperature = self.points.get(index, (0.0, 0.0))
        return f"{value:.5f},{temperature:.3f}"


def _viewer_window():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display for a Tk root: {exc}")
    root.withdraw()
    return root, l350.CurveViewerGUI(root)


def _read_slot_synchronously(gui, curve, link):
    gui.backend.link = link
    gui._require_connection = lambda: True
    gui._selected_slot = lambda: curve
    gui._run_in_worker = lambda description, function: function()
    gui._read_slot()
    posted = []
    while not gui._events.empty():
        event = gui._events.get_nowait()
        if event[0] == 'curve':
            posted.append(event)
    assert len(posted) == 1, posted
    _kind, header, points, notes = posted[0]
    return header, points, notes


def test_l350_reading_a_slot_with_a_blank_header_still_reads_its_points():
    """REGRESSION. The single-slot read used to stop at a blank header and
    report 'holds no curve' without asking for one breakpoint, so a curve
    written without a header could not be viewed at all."""
    root, gui = _viewer_window()
    try:
        link = _SlotLink(",,1,0.000,1", {1: (2.94699, 4.0), 2: (2.0, 100.0)})
        header, points, notes = _read_slot_synchronously(gui, 23, link)
        assert header['is_empty'] is True
        assert points == [(2.94699, 4.0), (2.0, 100.0)]
        assert any("breakpoints are the ones to believe" in n for n in notes)
        assert any(c.startswith("CRVPT? 23,1") for c in link.asked)
        gui._show_curve(header, points, notes)
        assert "(no name)" in gui.headline_label.cget("text")
        assert "2 breakpoints" in gui.headline_label.cget("text")
        assert "is empty" not in gui.headline_label.cget("text")

        # A truly empty slot still reads as empty, and says why.
        link = _SlotLink(",,1,0.000,1", {})
        header, points, notes = _read_slot_synchronously(gui, 45, link)
        assert points == []
        assert any("holds no curve" in n for n in notes)
        gui._show_curve(header, points, notes)
        assert gui.headline_label.cget("text") == "Slot 45 is empty."
        assert "CRVPT? found no breakpoints" in gui.detail_label.cget("text")
        # It cost STOP_AFTER_ZERO_PAIRS queries, not zero and not two hundred.
        assert sum(c.startswith("CRVPT?") for c in link.asked) == \
            l350.STOP_AFTER_ZERO_PAIRS
    finally:
        root.destroy()


def test_l350_module_self_test_passes():
    assert l350.run_self_test(report=lambda message: None)


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s).")
    sys.exit(1 if failures else 0)

def test_cc34_query_guard_admits_a_query_by_shape_only():
    for command in ("*IDN?", "CALCUR? 15", "SENTYPE? 15", "SENTYPE 15:TYPE?",
                    "SENTYPE 15:MULTIPLY?", "INPUT A:SENIX?",
                    "INPUT A:ISENIX?", "INPUT A:USENIX?", "INPUT? A",
                    "calcur? 15"):
        assert cc34.is_query(command), command
    for command in ("CALCUR? 15;CALCUR 15", "CALCUR 15 ?", "*RST?", "STOP?",
                    'SENTYPE 15:NAME "X"?', "CALCUR?? 15",
                    "SENTYPE? 15; CONTROL", "CALCUR 15", "", "?",
                    "SENTYPE? 15\nSTOP", "CALCUR? 15\r\nCALCUR 15"):
        assert not cc34.is_query(command), command


def test_cc34_block_read_can_be_stopped_between_lines():
    """The stop button used to apply to the slot list only. A curve read
    stopped part-way returns the lines in hand, which the parser then
    refuses as a curve because the semicolon never came."""
    link = _cc34_link(lines=["CX1030", "ACR", "-1.0", "LOGOHM",
                             "1.0 300.0", "2.0 100.0", ";"])
    seen = []

    def stop_after_two():
        return len(seen) >= 2

    def progress(done, total):
        seen.append(done)

    text = link.ask_block("CALCUR? 10", progress=progress,
                          should_stop=stop_after_two)
    assert text.splitlines() == ["CX1030", "ACR"]
    with pytest.raises(cc34.CurveReadError):
        cc34.parse_calcur_block(text)


def test_l350_read_points_says_where_the_curve_ended_and_fills_no_tail():
    """September 2026: a read of a short curve looked as if it never
    finished. The read did stop at the end marker, but said nothing about
    it; now the notes say where the curve ended. A firmware that echoes the
    last breakpoint past the end instead of 0,0 is also treated as the end,
    because a Lake Shore never stores two identical breakpoints."""
    class ScriptedLink:
        def __init__(self, replies):
            self.replies = list(replies)

        def ask(self, command):
            assert '?' in command, command
            return self.replies.pop(0)

    backend = l350.CurveViewerBackend(log=lambda msg: None)
    backend.link = ScriptedLink(["1.0,300.0", "2.0,100.0",
                                 "0.0,0.0", "0.0,0.0"])
    points, notes = backend.read_points(21)
    assert points == [(1.0, 300.0), (2.0, 100.0)]
    assert any(note.startswith("End of curve") and "holds 2 breakpoints"
               in note for note in notes), notes
    assert not l350.read_is_incomplete(notes)

    backend.link = ScriptedLink(["1.0,300.0", "2.0,100.0", "2.0,100.0",
                                 "2.0,100.0", "2.0,100.0"])
    points, notes = backend.read_points(21)
    assert points == [(1.0, 300.0), (2.0, 100.0)]
    assert any("repeated the last real breakpoint" in note for note in notes)
    assert not l350.read_is_incomplete(notes)

# ---------------------------------------------------------------------------
# THE SENSOR TYPE AGAINST THE CURVE'S OWN UNITS
# ---------------------------------------------------------------------------
#
# A diode measured with a resistance excitation does not fail. It reads a
# plausible wrong temperature, and every measurement made against it is
# quietly wrong. Each viewer therefore compares the type the channel is set to
# against the unit family of the curve it is using, in BOTH directions.
#
# Every module here is deliberately self-contained, so these tables are
# COPIES. Copies drift. The first test is the one that stops them.

def test_the_viewers_sensor_type_tables_match_the_loader():
    spec340 = ls_loader.MODEL_SPECS['340']
    spec350 = ls_loader.MODEL_SPECS['350']
    assert dict(l340.SENSOR_TYPES_340) == dict(spec340['sensor_types'])
    assert tuple(l340.RESISTIVE_TYPES_340) == tuple(spec340['resistive_types'])
    assert tuple(l340.VOLTAGE_TYPES_340) == tuple(spec340['voltage_types'])
    assert dict(l350.SENSOR_TYPES_350) == dict(spec350['sensor_types'])
    assert tuple(l350.RESISTIVE_TYPES_350) == tuple(spec350['resistive_types'])
    assert tuple(l350.VOLTAGE_TYPES_350) == tuple(spec350['voltage_types'])


def test_the_two_models_number_their_sensor_types_differently():
    # 8 is Cernox on a 340 and does not exist on a 350; 3 is Platinum 100 on a
    # 340 and NTC RTD on a 350. A code read under one model means something
    # else under the other, which is why each viewer has its own table.
    assert l340.type_unit_family_340(8) == 'ohm'
    assert l350.type_unit_family_350(8) is None
    assert l340.SENSOR_TYPES_340[3] != l350.SENSOR_TYPES_350[3]


def test_a_diode_curve_on_a_resistance_input_is_flagged_on_both_models():
    # Format 2 is V/K -- the DT-470 curve. Put it on an NTC/Cernox input and
    # the families disagree.
    assert l350.FORMAT_FAMILY_350[2] == 'V'
    assert l350.type_unit_family_350(3) == 'ohm'          # NTC RTD
    assert l350.FORMAT_FAMILY_350[2] != l350.type_unit_family_350(3)
    assert l340.FORMAT_FAMILY_340[2] == 'V'
    assert l340.type_unit_family_340(8) == 'ohm'          # Cernox
    assert l340.FORMAT_FAMILY_340[2] != l340.type_unit_family_340(8)


def test_a_resistance_curve_on_a_diode_input_is_flagged_on_both_models():
    # The other direction: a Cernox curve (format 4, log ohm) on a diode input.
    assert l350.FORMAT_FAMILY_350[4] == 'ohm'
    assert l350.type_unit_family_350(1) == 'V'            # Diode
    assert l350.FORMAT_FAMILY_350[4] != l350.type_unit_family_350(1)
    assert l340.FORMAT_FAMILY_340[4] == 'ohm'
    assert l340.type_unit_family_340(1) == 'V'            # Silicon Diode
    assert l340.FORMAT_FAMILY_340[4] != l340.type_unit_family_340(1)


def test_the_matching_pairs_do_not_raise_a_false_alarm():
    assert l350.FORMAT_FAMILY_350[2] == l350.type_unit_family_350(1)
    assert l350.FORMAT_FAMILY_350[4] == l350.type_unit_family_350(3)
    assert l340.FORMAT_FAMILY_340[2] == l340.type_unit_family_340(1)
    assert l340.FORMAT_FAMILY_340[4] == l340.type_unit_family_340(8)


def test_a_type_that_decides_nothing_is_not_guessed_at():
    # 340 type 0 is 'Special', which is whatever its units and excitation
    # fields were set to; 350 type 0 is Disabled and 5 is Capacitance. None of
    # them implies a unit family, and a guess would be a false alarm on every
    # such input.
    assert l340.type_unit_family_340(0) is None
    assert l350.type_unit_family_350(0) is None
    assert l350.type_unit_family_350(5) is None
    assert l340.type_unit_family_340(99) is None
    assert l350.type_unit_family_350(99) is None
    assert l340.type_unit_family_340(None) is None
    assert l350.type_unit_family_350(None) is None


def test_the_cryocon_viewer_checks_its_own_types_both_ways():
    # Same idea, the Cryocon's two vocabularies. Before this it flagged only
    # a diode type on a resistance curve, not a resistance type on a volts
    # curve -- which is the DT-470-on-the-Cernox-defaults case.
    assert cc34.type_unit_family('SiDiode') == 'V'
    assert cc34.type_unit_family('Diode') == 'V'
    assert cc34.type_unit_family('R8K10UA') == 'ohm'
    assert cc34.type_unit_family('ACR') == 'ohm'
    assert cc34.UNITS_FAMILY['VOLTS'] == 'V'
    assert cc34.UNITS_FAMILY['LOGOHM'] == 'ohm'
    assert cc34.type_unit_family('R8K10UA') != cc34.UNITS_FAMILY['VOLTS']
    assert cc34.type_unit_family('Diode') != cc34.UNITS_FAMILY['LOGOHM']
    assert cc34.type_unit_family('WhatIsThis') is None
    assert cc34.type_unit_family('') is None


def test_every_viewer_still_sends_only_queries_after_the_change():
    # These modules are query-only. The cross-checks reuse replies that were
    # already being read, and must not have added a command.
    for module in (cc34, l340, l350):
        assert module.run_self_test(report=lambda message: None)

# ---------------------------------------------------------------------------
# _show_curve MUST REACH ITS OWN END
# ---------------------------------------------------------------------------
#
# On 13 Sep 2026 a new method was inserted into the MIDDLE of the L340's
# _show_curve, which left its last two lines stranded after that method's
# return. flake8 caught the dead code as F821 ("undefined name 'points'"), but
# the real damage was invisible to everything else: _show_curve stopped
# calling _draw_plot, so the curve tab went blank while the header, the table
# and every self-test carried on passing.
#
# Nothing here asserts what the plot looks like. It asserts only that the tail
# of the method is still reached, which is the part that silently stopped.

def _slot_window(module):
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display for a Tk root: {exc}")
    root.withdraw()
    return root, module.CurveViewerGUI(root)


@pytest.mark.parametrize("module", [l340, l350], ids=["l340", "l350"])
def test_showing_a_curve_still_draws_it(module):
    root, gui = _slot_window(module)
    try:
        header = module.parse_crvhdr("DT-470,STANDARD,2,475.000,1", 23)
        points = [(0.51892, 300.0), (1.01525, 80.0), (1.62622, 4.2)]
        drawn = []
        gui._draw_plot = lambda h, p: drawn.append((h, list(p)))
        gui._show_curve(header, points, [])
        assert drawn, "_show_curve returned before drawing the plot"
        assert len(drawn[0][1]) == len(points)
        assert "3 breakpoints" in gui.headline_label.cget("text")
    finally:
        root.destroy()


@pytest.mark.parametrize("module", [l340, l350], ids=["l340", "l350"])
def test_an_empty_slot_still_clears_the_plot(module):
    # The empty branch returns early, so it has its own call to _draw_plot.
    # It has to keep it, or an empty slot leaves the previous curve on screen.
    root, gui = _slot_window(module)
    try:
        header = module.parse_crvhdr(",,1,0.000,1", 45)
        drawn = []
        gui._draw_plot = lambda h, p: drawn.append((h, list(p)))
        gui._show_curve(header, [], [])
        assert drawn, "an empty slot left the previous plot on screen"
        assert drawn[0][1] == []
        assert "is empty" in gui.headline_label.cget("text")
    finally:
        root.destroy()


def test_the_cryocon_viewer_also_reaches_the_end_of_show_curve():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display for a Tk root: {exc}")
    root.withdraw()
    gui = cc34.CurveViewerGUI(root)
    try:
        header, points = cc34.parse_calcur_block(SAMPLE_CALCUR, "slot 15")
        drawn = []
        gui._draw_plot = lambda h, p: drawn.append((h, list(p)))
        gui._show_curve(15, header, points, SAMPLE_CALCUR)
        assert drawn, "_show_curve returned before drawing the plot"
        assert len(drawn[0][1]) == len(points)
    finally:
        root.destroy()


# ---------------------------------------------------------------------------
# THE CC34 VIEWER AND THE CC34 LOADER HAVE TO AGREE  (16 Sep 2026)
# ---------------------------------------------------------------------------
#
# Every module in this suite is self-contained on purpose: the two Cryocon
# modules each hold their own copy of what a sensor type is and what a curve
# looks like, so either can be dropped on a machine on its own. The cost of
# that is drift, and drift here is not cosmetic -- the viewer exists to tell
# an operator whether what the loader sent is right, and it cannot do that
# from a different idea of what right means.
#
# These tests are the seam. They do not ask the two files to share code; they
# ask them to agree about the instrument.

CC34_LOADER_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                                "Sensor_Curve_Loader_CC34_GUI.py")
cc34_loader = _load("cc34_curve_loader", CC34_LOADER_PATH)


def _cc34_real_table():
    """The Master Sensor Table this lab's Model 34 answered with, 15 Sep 2026.

    Read off the instrument with SENTYPE?, not invented. Tests that ask what
    this firmware does should ask it of this rather than of something shaped
    to make them pass.
    """
    rows = [(0, 'None', 'SNONE', '0.0'),
            (1, 'Lakeshore 10', 'SIDIODE', '-1.0'),
            (2, 'Lakeshore 11', 'SIDIODE', '-1.0'),
            (3, 'Cryocal D3', 'SIDIODE', '-1.0'),
            (4, 'SI 410', 'SIDIODE', '-1.0'),
            (5, 'Pt100 3902', 'R312R1MA', '1.0'),
            (6, 'Pt100 385', 'R312R1MA', '1.0'),
            (7, 'Pt1K 385', 'R2K100UA', '10.0'),
            (8, 'Pt1K 375', 'R2K100UA', '10.0'),
            (9, 'TC K Extern', 'TC80', '0.1'),
            (10, 'TC E Extern', 'TC80', '0.1'),
            (11, 'TC T Extern', 'TC80', '0.1'),
            (12, 'TC type K', 'TC80', '1.0'),
            (13, 'TC type E', 'TC80', '1.0'),
            (14, 'TC type T', 'TC80', '1.0'),
            (15, 'S700', 'SIDIODE', '-1.0'),
            (16, 'CX1030 X17680', 'R8K10UA', '-1.0'),
            (17, 'CX1030 X17681', 'R8K10UA', '-1.0'),
            (18, 'P17 R8K10UA', 'R8K10UA', '-1.0'),
            (19, 'DT470 STANDARD1', 'SIDIODE', '-1.0')]
    rows += [(20 + n, "User Sensor " + "6789ABC"[n], 'SIDIODE', '-1.0')
             for n in range(7)]
    entries = [{'index': i, 'name': n, 'type': t, 'multiplier': m}
               for i, n, t, m in rows]
    entries += [{'index': i, 'name': None, 'type': None, 'multiplier': None}
                for i in range(27, 32)]
    return entries


_REAL_CC34_TABLE = _cc34_real_table()


def test_the_real_table_maps_to_the_offset_the_instrument_uses():
    """Appendix A says 9. This instrument says 14, and it is the instrument.

    The 29 and 31 Aug failures were both writes into the factory block on
    the strength of the manual's number.
    """
    blocks = cc34.map_table_blocks(_REAL_CC34_TABLE)
    assert blocks['offset'] == 14
    assert (blocks['user_first'], blocks['user_last']) == (15, 26)
    assert blocks['factory_last'] == 14
    assert blocks['disagreement'] == 0
    assert blocks['offset'] != cc34.APPENDIX_A_OFFSET
    # The two Cryocon modules derive this separately, from their own copies
    # of the rule, and they must not differ: the loader picks the index to
    # write to and the viewer says whether that index was a user slot. A
    # disagreement here means one of them calls a factory entry writable.
    loader_map = cc34_loader.analyse_sensor_table(_REAL_CC34_TABLE)
    assert loader_map['senix_offset'] == blocks['offset'], (
        loader_map['senix_offset'], blocks['offset'])
    assert loader_map['user_block_start'] == blocks['user_first'], loader_map
    assert loader_map['confidence'] == 'confirmed', loader_map['notes']


def test_both_cc34_modules_refuse_to_guess_an_offset_with_no_placeholder():
    """A table with every user slot filled gives no evidence, so neither
    module may fall back on Appendix A's number."""
    filled = [entry for entry in _REAL_CC34_TABLE
              if cc34.user_slot_number(entry['name']) is None]
    assert cc34.map_table_blocks(filled)['offset'] is None
    loader_map = cc34_loader.analyse_sensor_table(filled)
    assert loader_map['senix_offset'] is None, loader_map
    assert loader_map['confidence'] != 'confirmed', loader_map


def test_the_cc34_viewer_and_loader_agree_on_what_each_type_measures():
    """Volts or ohms, for every name either module knows.

    The viewer decides 'this diode is stored on a resistance range' from
    TYPE_UNIT_FAMILY; the loader refuses the same thing from the unit field
    of its own type tables. If they ever disagree, one of them passes a curve
    the other calls broken, and an operator has no way to tell which.
    """
    for name, (_, unit, _) in cc34_loader.SENSOR_TYPES.items():
        if not unit:                      # 'None'/'Snone' measure nothing
            continue
        expected = 'V' if unit == 'V' else 'ohm'
        seen = cc34.type_unit_family(name)
        assert seen == expected, (name, seen, expected)


def test_the_cc34_viewer_knows_every_type_the_loader_can_send():
    """A type the loader will send must be one the viewer can check.

    An unknown type is reported by the viewer as 'not checked', which is
    honest but useless, and it is exactly the curve most worth checking.
    """
    for name in cc34_loader.CALCUR_VERIFIED_TYPES:
        assert cc34.type_unit_family(name) is not None, name
        assert cc34.normalise_type(name) in cc34.TYPE_FULL_SCALE, name
    for name in cc34_loader.SENTYPE_SENSOR_TYPES:
        if name.lower() in ('snone', 'none'):
            continue
        assert cc34.type_unit_family(name) is not None, name


def test_the_two_modules_agree_on_the_full_scale_of_each_input():
    for name, (full_scale, unit, _) in cc34_loader.SENSOR_TYPES.items():
        folded = cc34.normalise_type(name)
        if folded not in cc34.TYPE_FULL_SCALE:
            continue
        viewer_scale, viewer_unit = cc34.TYPE_FULL_SCALE[folded]
        assert viewer_scale == full_scale, (name, viewer_scale, full_scale)
        if unit:
            assert viewer_unit == unit, (name, viewer_unit, unit)


def test_the_two_modules_agree_on_the_manual_type_lists():
    assert set(cc34.MANUAL_CALCUR_TYPES) == \
        set(cc34_loader.CALCUR_MANUAL_TYPE_LIST)


def test_the_viewer_agrees_that_this_firmware_discards_acr_and_diode():
    """One fact, recorded in two files, which must not drift apart."""
    report = "\n".join(cc34.type_vocabulary_report(_REAL_CC34_TABLE))
    for rejected in cc34_loader.CALCUR_REJECTED_TYPES:
        assert "not seen  " + rejected in report, rejected
    # And the spelling the loader will actually send is one the instrument
    # was seen using.
    seen = {cc34.normalise_type(t)
            for t in cc34.summarise_observed_types(_REAL_CC34_TABLE)}
    for verified in cc34_loader.CALCUR_VERIFIED_TYPES:
        assert cc34.normalise_type(verified) in seen, verified


# ---------------------------------------------------------------------------
# THE .340 THE VIEWER WRITES IS ONE THE LOADER CAN READ
# ---------------------------------------------------------------------------
#
# This is the round trip that proves a transfer: read a curve off the Cryocon,
# write it as a .340, put it back through the loader and get the same numbers.
# It is also the only check that the export is a real Lake Shore file rather
# than something only this suite can read.

def _cc34_curve(units="VOLTS", sensor_type="SiDiode", rising=False):
    if rising:
        points = [(50.0, 20.0), (117.0, 50.0), (260.0, 100.0),
                  (500.0, 200.0)]
    else:
        points = [(0.079330, 480.0), (0.458600, 325.0), (0.975500, 100.0),
                  (1.107020, 30.0), (1.704190, 1.0)]
    header = {'name': 'DT470 STANDARD1', 'sensor_type': sensor_type,
              'multiplier': 1.0 if rising else -1.0,
              'multiplier_text': '1.000000' if rising else '-1.000000',
              'units': units,
              'point_texts': [("%.6f" % r, "%.6f" % t) for r, t in points],
              'no_points': False}
    return header, points


def test_a_340_written_by_the_cc34_viewer_loads_in_the_cc34_loader():
    for units, kind, rising in (("VOLTS", "SiDiode", False),
                                ("OHMS", "R312R1MA", True),
                                ("LOGOHM", "R8K10UA", False)):
        header, points = _cc34_curve(units, kind, rising)
        text = cc34.build_lakeshore_340_text(
            header, points, index=19, idn="Cryocon Model 34, Rev 3.03A",
            address="GPIB0::23::INSTR")
        lines = cc34_loader._clean_lines(text)
        read, read_units, meta = cc34_loader._parse_lakeshore_340(
            lines, "exported")
        assert read_units == units, (units, read_units)
        # _parse_lakeshore_340 returns (temperature, reading); the viewer
        # holds (reading, temperature). Same pairs, opposite order.
        assert sorted((r, t) for t, r in read) == sorted(points), units
        # The coefficient has to survive, or the sensor loads inverted.
        expected_sign = 1.0 if rising else -1.0
        assert meta['stated_multiplier'] == expected_sign, (units, meta)


def test_a_340_written_by_the_cc34_viewer_states_a_count_that_is_true():
    """The loader refuses a file whose stated count and rows disagree.

    That refusal is what makes a truncated export loud instead of silent, so
    the export must never be the thing that trips it.
    """
    header, points = _cc34_curve()
    text = cc34.build_lakeshore_340_text(header, points, index=19)
    stated = [line for line in text.splitlines()
              if line.startswith("Number of Breakpoints")][0]
    assert str(len(points)) in stated
    # Parsing must not raise, which is what a disagreement would do.
    cc34_loader._parse_lakeshore_340(cc34_loader._clean_lines(text), "x")


def test_the_cc34_340_export_keeps_the_instruments_own_numerals():
    """Six digits is what the instrument stores; re-rounding loses the last.

    The .crv export already promises this. The .340 must make the same
    promise, or the two exports of one curve disagree in the last digit and
    nobody can say which is the instrument.
    """
    header, points = _cc34_curve()
    text = cc34.build_lakeshore_340_text(header, points, index=19)
    for reading_text, temperature_text in header['point_texts']:
        assert reading_text in text, reading_text
        assert temperature_text in text, temperature_text


def test_the_cc34_340_export_round_trips_the_curve_that_is_really_loaded():
    """The DT-470 in slot 19, end to end, against its own source file.

    The instrument holds it, the viewer writes it out, the loader reads it
    back. If any step reorders the columns the values survive but the sensor
    is inverted, and nothing downstream would notice.
    """
    crv = ("DT470 STANDARD1\nSiDiode\n-1.000000\nVOLTS\n"
           "0.079330   480.000000\n"
           "0.975500   100.000000\n"
           "1.107020   30.000000\n"
           "1.704190   1.000000\n;")
    header, points = cc34.parse_calcur_block(crv, "slot 19")
    text = cc34.build_lakeshore_340_text(header, points, index=19)
    read, units, meta = cc34_loader._parse_lakeshore_340(
        cc34_loader._clean_lines(text), "round trip")
    assert units == "VOLTS"
    assert meta['format_code'] == 2
    # Coldest point is the highest voltage, both before and after.
    assert min(t for t, _ in read) == 1.0
    assert max(r for _, r in read) == 1.704190
    coldest = [r for t, r in read if t == 1.0][0]
    assert coldest == 1.704190, "the columns must not have been swapped"
    # And the loader would pick the diode type for it, not the Cernox range.
    assert cc34_loader.types_for_source(units, meta['format_code']) == \
        ("SIDiode", "SIDiode")


# ---------------------------------------------------------------------------
# THE VIEWER STAYS PASSIVE
# ---------------------------------------------------------------------------

def test_nothing_added_to_the_cc34_viewer_can_write():
    """The type query and the checks are new code on a read-only module.

    The link refuses anything without a '?', so this asserts the new paths
    ask for nothing that could be refused -- a feature that trips the guard
    is a feature that does not work, and it would only be found on a bench.
    """
    asked = []

    class Link:
        def ask(self, command):
            assert cc34.is_query(command), command
            asked.append(command)
            if ":TYPE?" in command.upper():
                return "SIDIODE"
            if ":MULTIPLY?" in command.upper():
                return "-1.0"
            return "User Sensor 5"

    backend = cc34.CurveViewerBackend.__new__(cc34.CurveViewerBackend)
    backend.link = Link()
    backend.log = lambda message: None
    entries = backend.scan_sensor_table(first=15, last=17)
    assert len(entries) == 3
    assert asked and all(cc34.is_query(command) for command in asked)
    # The type report is pure arithmetic on what came back: it must not send
    # anything of its own.
    before = len(asked)
    cc34.type_vocabulary_report(entries)
    cc34.table_geometry_report(entries)
    cc34.audit_curve(19, {'name': 'X', 'sensor_type': 'SiDiode',
                          'multiplier': -1.0, 'units': 'VOLTS'},
                     [(0.1, 300.0), (0.2, 100.0)], entries)
    assert len(asked) == before, asked[before:]


def test_the_channel_read_stops_asking_for_commands_that_do_not_exist():
    """ISENIX and USENIX time out on this firmware, ten seconds each.

    Asked on all four inputs that is eighty seconds of waiting for
    information that is not there, which is what the log of 15 Sep shows.
    They are still asked ONCE, because a module that stops looking cannot
    notice it was wrong.
    """
    asked = []

    class Link:
        def ask(self, command):
            asked.append(command)
            if "ISENIX" in command or "USENIX" in command:
                raise IOError("VI_ERROR_TMO: Timeout expired")
            if command.startswith("INPUT?"):
                return "77.35"
            return "17"

    backend = cc34.CurveViewerBackend.__new__(cc34.CurveViewerBackend)
    backend.link = Link()
    backend.log = lambda message: None
    answers = backend.read_channel_sensors()

    assert set(answers) == set(cc34.INPUT_CHANNELS)
    assert sum(1 for c in asked if "ISENIX" in c) == 1, asked
    assert sum(1 for c in asked if "USENIX" in c) == 1, asked
    # SENIX and the reading are still asked on every channel.
    assert sum(1 for c in asked if ":SENIX?" in c) == len(cc34.INPUT_CHANNELS)
    # Every channel reports something for the ones that were not asked, so
    # the display never shows a blank that reads like an answer.
    for channel in cc34.INPUT_CHANNELS:
        for key in ("ISENIX", "USENIX"):
            assert answers[channel][key].startswith("<"), answers[channel]
        assert answers[channel]["SENIX"] == "17"


def test_a_firmware_that_answers_the_alternates_is_still_asked_all_three():
    """The other half of the rule above: do not stop looking on a unit that
    has them."""
    asked = []

    class Link:
        def ask(self, command):
            asked.append(command)
            return "11"

    backend = cc34.CurveViewerBackend.__new__(cc34.CurveViewerBackend)
    backend.link = Link()
    backend.log = lambda message: None
    backend.read_channel_sensors()
    for name in ("ISENIX", "USENIX"):
        assert sum(1 for c in asked if name in c) == len(cc34.INPUT_CHANNELS)
