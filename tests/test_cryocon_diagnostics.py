"""Tests for the standalone Cryocon 34 diagnostics console (17 Sep 2026):

    pica/cryocon/Diagnostics_CC34_GUI.py

It is the read-only tool that asks the instrument what it really does,
instead of trusting a manual written for a different model in the same
family. Its whole value rests on two properties, so both are pinned here:

  1. It WRITES NOTHING. It is meant to be run against a controller that
     is driving a live experiment; one stray write makes it unusable for
     its own purpose.
  2. A probe that is not answered is a RESULT, not a crash. A Cryo-con
     does not reject an unknown command with an error string - it does
     not answer at all - so every probe that matters may time out.

Also covered: the sensor-table sweep (what sensor types the controller
supports and which user curves are loaded), the deep measurement
sections, the Tk threading contract, and the Diagnostic Tools wiring in
both launchers.

No hardware. Runnable as plain Python as well as under pytest:
    python tests/test_cryocon_diagnostics.py
"""

import importlib.util
import inspect
import os
import queue
import re
import sys
import threading

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import matplotlib                                             # noqa: E402
matplotlib.use("Agg")

MODULE_PATH = os.path.join(project_root, "pica", "cryocon",
                           "Diagnostics_CC34_GUI.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


diag = _load("cc34_diagnostics_module", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()


# ===========================================================================
# A Model 34 that answers only what its own manual documents
# ===========================================================================

class FakeVisaTimeout(IOError):
    """A Cryo-con does not answer a command it does not know."""

    def __init__(self, command=""):
        super().__init__(
            "VI_ERROR_TMO (-1073807339): Timeout expired before operation "
            f"completed. ({command!r})")


DOCUMENTED = (
    "*IDN?", "*OPC?", "*ESR?", "*ESE?",
    "INPUT", "INP",
    "LOOP", "CONTROL?", "STOP",
    "SYSTEM:", "SYST:",
    "OVERTEMP:", "RELAYS?", "STATS:", "PIDTABLE", "HEATER:",
)
# ... except these, which are in NO Model 34 or 24C manual.
UNDOCUMENTED_SUFFIXES = ("OUTPWR?", "MAXPWR?", "MAXSET?",
                         "SENPR?", "ISENIX?", "USENIX?")

# A Master Sensor Table shaped like the one the lab's Rev 3.03A reports:
# the factory block at 0-14 and the twelve user slots at 15-26. That
# matches NEITHER of the two contradictory tables in the manual's
# Appendix A, which is the point of sweeping it off the instrument.
FACTORY_BLOCK = [
    ("None", "SNONE"), ("Cryocon S700", "SIDIODE"),
    ("LS DT-670", "SIDIODE"), ("LS DT-470", "SIDIODE"),
    ("SI 410 Diode", "SIDIODE"), ("Pt100 385", "R625R1MA"),
    ("Pt1K 385", "R2K100UA"), ("Pt10K 385", "R16K10UA"),
    ("RuOx 1K Ohm", "R2K100UA"), ("RuOx 2K Ohm", "R8K10UA"),
    ("TC type K", "TC80"), ("TC type E", "TC80"),
    ("TC type T", "TC40"), ("AuFe 0.07%", "TC40"),
    ("Reserved", "SNONE"),
]
USER_SLOT_FIRST = 15
USER_SLOT_NAMES = [f"User Sensor {n}" for n in
                   list("123456789") + ["A", "B", "C"]]


def _sensor_table(loaded=None):
    table = {ix: (name, stype, "1.000000")
             for ix, (name, stype) in enumerate(FACTORY_BLOCK)}
    for offset, name in enumerate(USER_SLOT_NAMES):
        table[USER_SLOT_FIRST + offset] = (name, "SNONE", "1.000000")
    for index, entry in (loaded or {}).items():
        table[index] = entry
    return table


class FakeCryoconResource:
    """The VISA resource underneath CryoconLink.

    `readings` is cycled through, so a survey can be given a drifting or
    a dead-still thermometer. `strict_syntax` refuses anything that is
    not the exact spelling PICA sends, which is how the syntax-tolerance
    section is exercised both ways.
    """

    def __init__(self, accepts_undocumented=(), fault_channels=("D",),
                 sensor_table=None, senix=None, distc="2", dres="FULL",
                 units=None, readings=None, strict_syntax=False):
        self.accepts = tuple(accepts_undocumented)
        self.fault_channels = tuple(fault_channels)
        self.sensor_table = (_sensor_table() if sensor_table is None
                             else sensor_table)
        self.senix = senix or {"A": "17", "B": "3", "C": "1", "D": "0"}
        self.distc = distc
        self.dres = dres
        self.units = units or {ch: "K" for ch in "ABCD"}
        self.readings = list(readings or ["77.3500K"])
        self.strict_syntax = strict_syntax
        self._reading_index = 0
        self.queries = []
        self.writes = []
        self.timeout = diag.CRYOCON_TIMEOUT_MS
        self.closed = False

    # -- helpers --

    def _next_reading(self):
        value = self.readings[self._reading_index % len(self.readings)]
        self._reading_index += 1
        return value

    def _sentype(self, cmd):
        match = re.match(r"SENTYPE\s*\??\s*(\d+)\s*(?::(\w+)\?)?$", cmd)
        if not match:
            return None
        entry = self.sensor_table.get(int(match.group(1)))
        if entry is None:
            raise FakeVisaTimeout(cmd)       # past the end of the table
        field = (match.group(2) or "NAME").upper()
        return {"NAME": entry[0], "TYPE": entry[1],
                "MULTIPLY": entry[2]}.get(field, entry[0])

    def _known(self, cmd):
        if any(cmd.endswith(s) for s in UNDOCUMENTED_SUFFIXES):
            return any(cmd.endswith(a) for a in self.accepts)
        return any(cmd.startswith(d) for d in DOCUMENTED)

    # -- bus --

    def query(self, command):
        raw = command
        cmd = command.strip()
        self.queries.append(raw)
        if self.strict_syntax and raw not in ("INPUT? A", "INPUT? B",
                                              "INPUT? C", "INPUT? D"):
            if raw.startswith("INPUT?") or raw.upper().startswith("INP"):
                if raw != cmd or raw != cmd.upper() or "?A" in raw.upper():
                    raise FakeVisaTimeout(raw)
        if cmd.startswith("SENTYPE"):
            reply = self._sentype(cmd)
            if reply is not None:
                return reply
        if not self._known(cmd):
            raise FakeVisaTimeout(cmd)
        if cmd == "*IDN?":
            return "Cryocon Model 34, Rev 3.03A"
        if cmd == "*OPC?":
            return "1"
        if cmd in ("*ESR?", "*ESE?"):
            return "0"
        if cmd == "SYSTEM:DISTC?":
            return self.distc
        if cmd == "SYSTEM:DRES?":
            return self.dres
        if cmd == "SYSTEM:ERROR?":
            return "0"
        units = re.match(r"INP(?:UT)? ([A-D]):UNITS?\?$", cmd)
        if units:
            return self.units.get(units.group(1), "K")
        senix = re.match(r"INPUT ([A-D]):SENIX\?$", cmd)
        if senix:
            return self.senix.get(senix.group(1), "0")
        if cmd.endswith(":UNITS?") or cmd.endswith(":UNIT?"):
            return "K"
        if "HTRREAD?" in cmd or "HTRR?" in cmd:
            return "22%"
        if (cmd.upper().startswith("INPUT?")
                or cmd.upper().startswith("INP?")
                or ":TEMPER?" in cmd.upper() or ":TEMP?" in cmd.upper()):
            channel = cmd.replace(";UNIT?", "").strip().split()[-1]
            channel = channel.split(":")[0].upper().lstrip("CH") or "A"
            if channel in self.fault_channels:
                return "-------"
            return self._next_reading()
        return "0"

    def write(self, command):
        # Nothing in this program may reach here. Recorded rather than
        # raised so a test can name the offending command.
        self.writes.append(command.strip())

    def clear(self):
        pass

    def close(self):
        self.closed = True


def _link(resource):
    """A CryoconLink bound to a fake resource, without opening anything."""
    link = object.__new__(diag.CryoconLink)
    link.address = "GPIB0::23::INSTR"
    link.timeout_ms = diag.CRYOCON_TIMEOUT_MS
    link.instrument = resource
    link.idn = "Cryocon Model 34, Rev 3.03A"
    link._log = lambda msg: None
    link._last_io = 0.0
    link.rm = None
    return link


def _run(resource, deep=(), stop=None, shrink=True):
    """Run the survey against a fake and return (lines, rows).

    The pacing gap and the timed sections are real seconds, so they are
    shortened here - the survey's behaviour is what is under test, not
    how long a Rev 3.03A takes to answer.
    """
    link = _link(resource)
    out = queue.Queue()
    stop = stop or threading.Event()
    saved = (diag.time.sleep, diag.CC34_UPDATE_RATE_SECONDS,
             diag.CC34_SOAK_QUERIES, diag.CC34_NOISE_SAMPLES)
    if shrink:
        diag.time.sleep = lambda *_a, **_k: None
        diag.CC34_UPDATE_RATE_SECONDS = 0.05
        diag.CC34_SOAK_QUERIES = 12
    try:
        diag.CryoconSurvey(link, out, stop, deep).run()
    finally:
        (diag.time.sleep, diag.CC34_UPDATE_RATE_SECONDS,
         diag.CC34_SOAK_QUERIES, diag.CC34_NOISE_SAMPLES) = saved
    lines, rows = [], None
    while True:
        try:
            item = out.get_nowait()
        except queue.Empty:
            break
        if item[0] == "line":
            lines.append(item[1])
        elif item[0] == "done":
            rows = item[1]
    return lines, rows


ALL_DEEP = tuple(key for key, _label, _cost, _default
                 in diag.CC34_DEEP_SECTIONS)


# ===========================================================================
# 1. It writes nothing
# ===========================================================================

def test_the_survey_never_writes_to_the_instrument():
    resource = FakeCryoconResource()
    _run(resource, deep=ALL_DEEP)
    assert resource.writes == [], resource.writes


def test_the_link_class_has_no_write_method_at_all():
    """A write path that exists is a write path that can be called by
    mistake."""
    assert not hasattr(diag.CryoconLink, "write")
    assert ".write(" not in inspect.getsource(diag.CryoconSurvey)


def test_every_probe_in_the_tables_is_a_query():
    commands = [p[1] for p in diag.CC34_PROBES]
    commands += [t for t, _st, _n in diag.CC34_CHANNEL_PROBES]
    commands += [v for v, _n in diag.CC34_SYNTAX_VARIANTS]
    commands.append(diag.CC34_BURST_COMMAND)
    for command in commands:
        assert "?" in command, f"{command!r} is not a query"


def test_no_dangerous_mnemonic_is_anywhere_in_the_probe_tables():
    """*RST is a ~15 s hardware reset on a Cryo-con; STOP drops the
    heaters; CONTROL engages the loops; NVSAVE burns flash."""
    for _group, command, _st, _note in diag.CC34_PROBES:
        assert command.strip() not in ("CONTROL", "STOP", "*RST", "*CLS")
    text = repr(diag.CC34_PROBES) + repr(diag.CC34_CHANNEL_PROBES)
    for banned in ("*RST", "*CLS", "NVSAVE", "CALCUR "):
        assert banned not in text, banned


def test_the_deliberate_omissions_are_written_down():
    lines, _rows = _run(FakeCryoconResource())
    text = "\n".join(lines)
    assert "Deliberately NOT sent" in text
    assert "CALCUR" in text
    assert "Sensor_Curve_Viewer_CC34_GUI.py" in text
    assert "no write path" in text


# ===========================================================================
# 2. An unanswered probe is a result, not a crash
# ===========================================================================

def test_a_command_the_unit_does_not_know_is_recorded_as_a_timeout():
    resource = FakeCryoconResource()
    _lines, rows = _run(resource)
    by_command = {r[1]: r for r in rows}
    outpwr = by_command["LOOP 1:OUTPWR?"]
    assert outpwr[2] == "UNDOC" and outpwr[3] == "TIMEOUT", outpwr
    htrread = by_command["LOOP 1:HTRREAD?"]
    assert htrread[3] == "OK" and "22%" in htrread[5], htrread


def test_a_unit_that_does_accept_the_old_mnemonic_is_reported_as_such():
    resource = FakeCryoconResource(accepts_undocumented=("OUTPWR?",))
    lines, rows = _run(resource)
    assert {r[1]: r for r in rows}["LOOP 1:OUTPWR?"][3] == "OK"
    assert "ACCEPTED by this unit" in "\n".join(lines)


def test_all_six_mnemonics_missing_from_the_manual_are_probed():
    probed = {p[1]: p[2] for p in diag.CC34_PROBES}
    for command in ("LOOP 1:OUTPWR?", "LOOP 1:MAXPWR?", "LOOP 1:MAXSET?",
                    "INPUT A:SENPR?", "INPUT A:ISENIX?", "INPUT A:USENIX?"):
        assert probed.get(command) == "UNDOC", (command, probed.get(command))


def test_a_dead_bus_does_not_take_the_worker_down():
    class DeadResource(FakeCryoconResource):
        def query(self, command):
            raise FakeVisaTimeout(command)

    lines, rows = _run(DeadResource(), deep=ALL_DEEP)
    assert rows is not None, "the survey did not finish on a dead bus"
    assert all(r[3] in ("TIMEOUT", "ERROR") for r in rows)
    assert "SUMMARY" in "\n".join(lines)


def test_the_worker_formats_its_own_traceback():
    """traceback.format_exc() on the Tk thread has no live exception and
    prints 'NoneType: None'."""
    src = inspect.getsource(diag.CryoconSurvey.run)
    assert "traceback.format_exc()" in src and "self._emit" in src


def test_an_unexpected_fault_still_ends_with_a_done_message():
    """Without 'done' the window's after() chain spins forever and the
    buttons never come back."""
    survey = diag.CryoconSurvey(_link(FakeCryoconResource()), queue.Queue(),
                                threading.Event())
    survey._run = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    survey.run()
    kinds = [item[0] for item in list(survey.queue.queue)]
    assert kinds[-1] == "done", kinds[-4:]


def test_the_error_queue_section_ties_timeouts_back_to_an_empty_queue():
    """If an unrecognised command leaves no trace, a wrong mnemonic can
    only ever be caught by reading the manual. The log should say so."""
    lines, _rows = _run(FakeCryoconResource())
    text = "\n".join(lines)
    assert "Error queue after the survey" in text
    assert "probe(s) timed out during this survey" in text
    assert "leaves NO trace" in text


# ===========================================================================
# 3. Sensor types and calibration curves
# ===========================================================================

def test_the_whole_master_sensor_table_is_read_off_the_instrument():
    resource = FakeCryoconResource()
    lines, _rows = _run(resource)
    text = "\n".join(lines)
    assert "Master Sensor Table" in text
    for index in range(0, 27):
        assert f"SENTYPE? {index}" in resource.queries, index
    assert "LS DT-470" in text and "SIDIODE" in text and "R8K10UA" in text


def test_the_sweep_stops_once_the_table_runs_out():
    resource = FakeCryoconResource()
    _run(resource)
    asked = [q for q in resource.queries if q.startswith("SENTYPE? ")]
    assert max(int(q.split()[-1]) for q in asked) <= 28, asked
    assert diag.CC34_SENSOR_TABLE_MAX_INDEX >= 26


def test_details_are_only_asked_for_an_index_that_answered():
    resource = FakeCryoconResource()
    _run(resource)
    assert "SENTYPE 27:TYPE?" not in resource.queries
    assert "SENTYPE 3:TYPE?" in resource.queries


def test_the_user_curve_index_map_is_derived_not_assumed():
    lines, _rows = _run(FakeCryoconResource())
    text = "\n".join(lines)
    assert "user curve slots are index 15-26" in text
    assert "user curve n is table index n + 14" in text


def test_a_loaded_user_curve_is_reported_with_its_type_and_sign():
    loaded = {17: ("CX-1030 X17680", "R8K10UA", "-1.000000")}
    lines, _rows = _run(FakeCryoconResource(
        sensor_table=_sensor_table(loaded)))
    text = "\n".join(lines)
    assert "index 17 (user curve 3): CX-1030 X17680" in text
    assert "type=R8K10UA multiplier=-1.000000" in text
    assert "NEGATIVE multiplier" in text


def test_an_empty_user_block_is_said_so_plainly():
    lines, _rows = _run(FakeCryoconResource())
    assert "no calibrated curve is loaded" in "\n".join(lines)


def test_the_type_vocabulary_comes_from_the_instruments_own_replies():
    """CALCUR silently discards a block whose type field it cannot
    identify, so the spellings that matter are the firmware's own."""
    lines, _rows = _run(FakeCryoconResource())
    text = "\n".join(lines)
    assert "Sensor TYPE strings this firmware actually uses" in text
    for spelling in ("SIDIODE", "R8K10UA", "SNONE", "TC80"):
        assert spelling in text, spelling


def test_each_input_is_matched_to_the_curve_it_is_running_on():
    loaded = {17: ("CX-1030 X17680", "R8K10UA", "-1.000000")}
    lines, _rows = _run(FakeCryoconResource(
        sensor_table=_sensor_table(loaded)))
    text = "\n".join(lines)
    assert "input A: SENIX 17 -> CX-1030 X17680" in text
    assert "input D: SENIX 0 -> None - this input is switched OFF" in text


def test_calcur_is_never_sent():
    resource = FakeCryoconResource()
    _run(resource, deep=ALL_DEEP)
    assert not any(q.strip().startswith("CALCUR") for q in resource.queries)


# ===========================================================================
# 4. Settings that quietly shape every logged number
# ===========================================================================

def test_the_display_filter_is_read_and_its_effect_spelled_out():
    lines, _rows = _run(FakeCryoconResource(distc="2"))
    text = "\n".join(lines)
    assert "SYSTEM:DISTC = 2 s display filter" in text
    assert "EVERY temperature" in text


def test_a_long_display_filter_is_called_out_as_a_problem_for_ramps():
    assert "visibly smeared" in "\n".join(
        _run(FakeCryoconResource(distc="16"))[0])
    assert "visibly smeared" not in "\n".join(
        _run(FakeCryoconResource(distc="2"))[0])


def test_a_channel_left_in_celsius_is_named_in_the_summary():
    """INPUT? reports in each channel's OWN display units. A channel left
    in C logs wrong numbers against every point of a run."""
    resource = FakeCryoconResource(
        units={"A": "C", "B": "K", "C": "K", "D": "K"})
    lines, _rows = _run(resource)
    text = "\n".join(lines)
    assert "CHANNELS NOT REPORTING KELVIN" in text
    assert "input A is in 'C'" in text


def test_all_kelvin_channels_raise_no_units_warning():
    lines, _rows = _run(FakeCryoconResource())
    assert "CHANNELS NOT REPORTING KELVIN" not in "\n".join(lines)


# ===========================================================================
# 5. Reply framing and bus timing
# ===========================================================================

def test_the_unstripped_reply_is_shown_because_query_strips_it():
    """CryoconLink.query() returns reply.strip(), so a trailing unit
    character or terminator is invisible everywhere else. The '77.350K'
    bug lived in exactly that gap."""
    lines, _rows = _run(FakeCryoconResource())
    text = "\n".join(lines)
    assert "Reply framing" in text and "UNSTRIPPED" in text
    assert "read_termination" in text and "'77.3500K'" in text


def test_unpaced_traffic_keeps_the_links_clock_honest():
    """The unpaced paths go straight at the resource to skip the gap, so
    they have to put the link's own pacing clock back."""
    for name in ("_burst", "_raw", "_update_rate"):
        src = inspect.getsource(getattr(diag.CryoconSurvey, name))
        assert "_last_io" in src, name


def test_the_bus_timing_section_compares_paced_against_unpaced():
    resource = FakeCryoconResource()
    lines, _rows = _run(resource)
    text = "\n".join(lines)
    assert "Bus timing" in text and "paced" in text and "unpaced" in text
    burst = [q for q in resource.queries if q == diag.CC34_BURST_COMMAND]
    assert len(burst) >= 2 * diag.CC34_BURST_N


# ===========================================================================
# 6. The deep measurements
# ===========================================================================

def test_the_deep_sections_are_all_off_by_default():
    """Each costs real time, so none of them runs unless it is ticked."""
    for _key, _label, _cost, default in diag.CC34_DEEP_SECTIONS:
        assert default is False


def test_nothing_deep_runs_unless_it_is_asked_for():
    lines, _rows = _run(FakeCryoconResource())
    text = "\n".join(lines)
    for heading in ("Reading resolution", "How often the reading actually",
                    "Lowest workable VISA timeout", "Syntax tolerance",
                    "Soak test", "Four-channel round-robin"):
        assert heading not in text, heading


def test_resolution_and_noise_report_the_quantisation_step_and_spread():
    """Both numbers set the floor for any tolerance window: a settle-band
    narrower than the noise can never be met."""
    resource = FakeCryoconResource(
        readings=["77.3500K", "77.3510K", "77.3490K", "77.3500K"])
    lines, _rows = _run(resource, deep=("noise",))
    text = "\n".join(lines)
    assert "Reading resolution and short-term noise" in text
    assert "peak-to-peak" in text
    assert "smallest step between distinct values" in text
    assert "can never be met" in text


def test_a_dead_still_reading_is_explained_rather_than_reported_as_zero():
    lines, _rows = _run(FakeCryoconResource(readings=["77.3500K"]),
                        deep=("noise",))
    text = "\n".join(lines)
    assert "Every reading identical" in text
    assert "SYSTEM:DISTC" in text


def test_the_update_rate_section_says_what_the_shortest_dwell_is():
    resource = FakeCryoconResource(
        readings=["77.3500K", "77.3600K", "77.3700K", "77.3800K"])
    lines, _rows = _run(resource, deep=("update",))
    text = "\n".join(lines)
    assert "How often the reading actually changes" in text
    assert "queries/s unpaced" in text
    assert "shortest dwell worth using" in text or "never changed" in text


def test_a_value_that_never_changes_points_at_the_display_filter():
    lines, _rows = _run(FakeCryoconResource(readings=["77.3500K"]),
                        deep=("update",))
    text = "\n".join(lines)
    assert "never changed in the sample window" in text
    assert "SYSTEM:DISTC" in text


def test_the_timeout_ladder_restores_the_timeout_it_found_with():
    resource = FakeCryoconResource()
    before = resource.timeout
    lines, _rows = _run(resource, deep=("timeout",))
    assert resource.timeout == before, resource.timeout
    text = "\n".join(lines)
    assert "Lowest workable VISA timeout" in text
    assert "ms timeout:" in text


def test_the_timeout_ladder_explains_why_it_matters():
    """A mnemonic the firmware does not know costs one whole timeout
    every time it is sent."""
    src = inspect.getsource(diag.CryoconSurvey._timeout_ladder)
    assert "costs" in src and "timeout" in src
    lines, _rows = _run(FakeCryoconResource(), deep=("timeout",))
    assert "reliable" in "\n".join(lines)


def test_the_syntax_section_tries_the_manuals_own_query_spelling():
    """The manual's formal Query Syntax line is 'INPUT ? <channel>', with
    a space before the '?', while every worked example writes 'INPUT? B'.
    If only one of them works that is a trap."""
    variants = [v for v, _n in diag.CC34_SYNTAX_VARIANTS]
    assert "INPUT? A" in variants
    assert "INPUT ? A" in variants
    assert "input? a" in variants
    lines, _rows = _run(FakeCryoconResource(), deep=("syntax",))
    assert "Syntax tolerance" in "\n".join(lines)


def test_a_fussy_parser_is_reported_as_refused_not_as_a_crash():
    resource = FakeCryoconResource(strict_syntax=True)
    lines, rows = _run(resource, deep=("syntax",))
    text = "\n".join(lines)
    assert "REFUSED" in text
    assert rows is not None


def test_the_four_channel_scan_costs_out_a_poll():
    lines, _rows = _run(FakeCryoconResource(), deep=("scan",))
    text = "\n".join(lines)
    assert "Four-channel round-robin cost" in text
    assert "per round" in text


def test_the_soak_counts_failures_and_names_every_distinct_one():
    calls = {"n": 0}
    original = FakeCryoconResource.query

    class FlakyResource(FakeCryoconResource):
        def query(self, command):
            if command.strip() == "INPUT? A":
                calls["n"] += 1
                if calls["n"] % 4 == 0:
                    raise FakeVisaTimeout(command)
            return original(self, command)

    lines, _rows = _run(FlakyResource(), deep=("soak",))
    text = "\n".join(lines)
    assert "Soak test" in text
    assert "FAILURE" in text
    assert "no comm failure" not in text


def test_a_clean_soak_says_so():
    lines, _rows = _run(FakeCryoconResource(), deep=("soak",))
    assert "no comm failure over the whole run" in "\n".join(lines)


def test_the_banner_names_which_deep_sections_were_run():
    lines, _rows = _run(FakeCryoconResource(), deep=("noise", "soak"))
    text = "\n".join(lines)
    assert "Deep sections  : noise, soak" in text
    lines, _rows = _run(FakeCryoconResource())
    assert "Deep sections  : none" in "\n".join(lines)


# ===========================================================================
# 7. Stop, threading and session contract
# ===========================================================================

def test_stop_is_honoured_before_the_first_probe():
    resource = FakeCryoconResource()
    stop = threading.Event()
    stop.set()
    lines, _rows = _run(resource, deep=ALL_DEEP, stop=stop)
    assert any("stopped by the operator" in ln for ln in lines), lines
    assert resource.queries == [], resource.queries


def test_the_worker_never_touches_tk():
    # Comments and docstrings explain WHY; only the code is checked.
    body = [line for line in inspect.getsource(diag.CryoconSurvey).splitlines()
            if not line.lstrip().startswith("#")]
    src = re.sub(r'(?:r?""")(?:.|\n)*?"""', "", "\n".join(body))
    for banned in ("tk.", "ttk.", "messagebox", ".after(", "StringVar",
                   "self.root", "self.console"):
        assert banned not in src, f"the worker touches Tk: {banned}"


def test_the_window_drains_the_queue_from_an_after_chain():
    src = inspect.getsource(diag.DiagnosticsGUI._pump)
    assert "get_nowait" in src and "self.root.after(" in src
    assert "queue.Empty" in src


def test_the_probe_timeout_is_lowered_and_restored():
    start = inspect.getsource(diag.DiagnosticsGUI._start)
    assert "CC34_SURVEY_TIMEOUT_MS" in start and "self.saved_timeout" in start
    for name in ("_finish", "_on_closing"):
        assert "_restore_timeout" in inspect.getsource(
            getattr(diag.DiagnosticsGUI, name)), name
    assert diag.CC34_SURVEY_TIMEOUT_MS < diag.CRYOCON_TIMEOUT_MS


def test_the_log_is_saved_without_the_operator_remembering_to():
    assert "_autosave()" in inspect.getsource(diag.DiagnosticsGUI._finish)
    save = inspect.getsource(diag.DiagnosticsGUI._autosave)
    assert "os.fsync" in save and "CC34_diagnostics_" in save


def test_connecting_refuses_anything_that_is_not_a_cryocon():
    """The factory address is shared with a Lakeshore 340/350 and a
    K6221, so a survey pointed at the wrong instrument would record that
    instrument's behaviour as the Cryo-con's."""
    src = inspect.getsource(diag.DiagnosticsGUI._connect)
    assert "is_cryocon_idn" in src
    assert "link.close()" in src
    assert diag.is_cryocon_idn("Cryocon Model 34, Rev 3.03A")
    assert diag.is_cryocon_idn("CRYO-CON 32B")
    assert not diag.is_cryocon_idn("LSCI,MODEL350,LSA1234,1.5")


def test_a_reading_with_a_unit_suffix_is_a_number_not_a_fault():
    assert diag.parse_cryocon_number("77.350K", "reading") == 77.350
    assert diag.parse_cryocon_number("-12.5 C", "reading") == -12.5
    assert diag.parse_cryocon_number("22%", "heater") == 22.0


def test_status_strings_are_named_not_swallowed():
    for reply in ("-------", ".......", "N/A", "NACK", ""):
        try:
            diag.parse_cryocon_number(reply, "reading", "A")
        except diag.CryoconStatusError as exc:
            assert "A" in str(exc) or reply == ""
        else:
            raise AssertionError(f"{reply!r} parsed as a number")


# ===========================================================================
# 8. Wiring: Diagnostic Tools in both launchers
# ===========================================================================

def test_the_diagnostics_program_is_in_script_paths_and_on_disk():
    from pica.main import PICALauncherApp
    path = PICALauncherApp.SCRIPT_PATHS["Cryocon Diagnostics"]
    assert os.path.basename(path) == "Diagnostics_CC34_GUI.py"
    assert os.path.isfile(path), path


def test_the_v2_tools_menu_has_a_diagnostic_tools_submenu():
    from pica.main_v2 import DIAGNOSTIC_TOOLS
    import pica.main_v2 as v2
    source = inspect.getsource(v2.PICALauncherV2._build_menubar)
    assert "Diagnostic Tools" in source
    assert "DIAGNOSTIC_TOOLS" in source
    assert "add_cascade" in source
    assert DIAGNOSTIC_TOOLS, "no diagnostic tools registered"


def test_every_diagnostic_tool_resolves_to_a_real_script():
    from pica.main import PICALauncherApp
    from pica.main_v2 import DIAGNOSTIC_TOOLS
    paths = PICALauncherApp.SCRIPT_PATHS
    for label, script_key in DIAGNOSTIC_TOOLS:
        assert script_key in paths, script_key
        assert os.path.isfile(paths[script_key]), script_key
        assert label.strip(), script_key


def test_the_diagnostic_tools_suite_is_in_the_advanced_catalogue():
    from pica.main_v2 import CATALOG, DIAGNOSTIC_TOOLS
    suites = [s for s in CATALOG if s['category'] == "Diagnostic Tools"]
    assert len(suites) == 1, [s['category'] for s in CATALOG]
    keys = {entry[1] for entry in suites[0]['modules']}
    assert keys == {key for _label, key in DIAGNOSTIC_TOOLS}


def test_the_diagnostics_module_is_not_mistaken_for_a_measurement():
    """It has no data file, no sample name and no plot: it interrogates
    an instrument and writes a log."""
    for banned in ("sample_name", "matplotlib", "FigureCanvas"):
        assert banned not in SOURCE, banned


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s).")
    sys.exit(1 if failures else 0)
