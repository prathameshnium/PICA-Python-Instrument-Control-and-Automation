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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc34 = _load("cc34_curve_viewer", CC34_PATH)
l350 = _load("l350_curve_viewer", L350_PATH)


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

