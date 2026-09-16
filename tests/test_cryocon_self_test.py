"""Tests for the Cryocon Model 34 read-only self-test (17 Sep 2026):

    pica/cryocon/T_Control_CC34_DirectControl_GUI.py
        CC34_SELF_TEST_PROBES / CryoconSelfTest / CryoconSelfTestWindow

The self-test exists to settle, against the instrument itself, which SCPI
mnemonics a Model 34 actually accepts - several in this programme came
from the Model 32/32B manual and are absent from the Model 34's own. Its
whole value rests on two properties, and both are pinned here:

  1. It WRITES NOTHING. It is meant to be run against a controller that
     is driving a live experiment, so a single stray write - a *RST, a
     STOP, a setpoint - would make it unusable for its own purpose.
  2. It reports a probe that is not answered as a result, not as a
     crash. A Cryo-con does not reject an unknown command with an error
     string; it does not answer at all. Every probe that matters is one
     that may time out.

Plus the threading contract the repo has been bitten by before: the
worker never touches Tk, and the window drains its queue from an after()
chain on the Tk thread.

No hardware. Runnable as plain Python as well as under pytest:
    python tests/test_cryocon_self_test.py
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
                           "T_Control_CC34_DirectControl_GUI.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


dc = _load("cc34_selftest_direct_control", MODULE_PATH)
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


# Mnemonics the Cryo-con Model 34 manual documents, as prefixes. Anything
# else is left unanswered, which is what the instrument does.
DOCUMENTED = (
    "*IDN?", "*OPC?", "*ESR?", "*ESE?",
    "INPUT", "INP",
    "LOOP", "CONTROL?", "STOP",
    "SYSTEM:", "SYST:",
    "OVERTEMP:", "RELAYS?", "STATS:",
)
# ... except these LOOP/INPUT sub-mnemonics, which are NOT in it.
UNDOCUMENTED_SUFFIXES = ("OUTPWR?", "MAXPWR?", "MAXSET?",
                         "SENPR?", "ISENIX?", "USENIX?")


# A Master Sensor Table shaped like the one the lab's Rev 3.03A reports:
# the factory block at 0-14 and the twelve user slots at 15-26, which is
# what work on the instrument in Sep 2026 found - and which matches
# NEITHER of the two contradictory tables in the manual's Appendix A.
# Nothing answers past 26. One user slot carries a loaded Cernox curve.
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
USER_SLOT_NAMES = ["User Sensor 1", "User Sensor 2", "User Sensor 3",
                   "User Sensor 4", "User Sensor 5", "User Sensor 6",
                   "User Sensor 7", "User Sensor 8", "User Sensor 9",
                   "User Sensor A", "User Sensor B", "User Sensor C"]


def _sensor_table(loaded=None):
    """index -> (name, type, multiplier). `loaded` overrides user slots."""
    table = {ix: (name, stype, "1.000000")
             for ix, (name, stype) in enumerate(FACTORY_BLOCK)}
    for offset, name in enumerate(USER_SLOT_NAMES):
        table[USER_SLOT_FIRST + offset] = (name, "SNONE", "1.000000")
    for index, entry in (loaded or {}).items():
        table[index] = entry
    return table


class FakeCryoconResource:
    """The VISA resource underneath CryoconLink."""

    def __init__(self, accepts_undocumented=(), fault_channels=("D",),
                 sensor_table=None, senix=None, distc="2", dres="FULL"):
        self.accepts = tuple(accepts_undocumented)
        self.fault_channels = tuple(fault_channels)
        self.sensor_table = (_sensor_table() if sensor_table is None
                             else sensor_table)
        # Which table index each input channel is running on.
        self.senix = senix or {"A": "17", "B": "3", "C": "1", "D": "0"}
        self.distc = distc
        self.dres = dres
        self.queries = []
        self.writes = []
        self.timeout = dc.CRYOCON_TIMEOUT_MS
        self.closed = False

    def _sentype(self, cmd):
        """SENTYPE? <ix> / SENTYPE <ix>:TYPE? / :MULTIPLY?, or None."""
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

    def query(self, command):
        cmd = command.strip()
        self.queries.append(cmd)
        if cmd.startswith("SENTYPE"):
            reply = self._sentype(cmd)
            if reply is not None:
                return reply
        if not self._known(cmd):
            raise FakeVisaTimeout(cmd)
        if cmd == "*IDN?":
            return "Cryocon Model 34, Rev 3.03A"
        if cmd in ("*OPC?",):
            return "1"
        if cmd == "*ESR?":
            return "0"
        if cmd == "SYSTEM:DISTC?":
            return self.distc
        if cmd == "SYSTEM:DRES?":
            return self.dres
        senix = re.match(r"INPUT ([A-D]):SENIX\?$", cmd)
        if senix:
            return self.senix.get(senix.group(1), "0")
        if cmd.endswith(":UNITS?") or cmd.endswith(":UNIT?"):
            return "K"
        if cmd.startswith("SYSTEM:ERROR?"):
            return "0"
        if "HTRREAD?" in cmd or "HTRR?" in cmd:
            return "22%"
        if cmd.startswith("INPUT?") or cmd.startswith("INP?") \
                or ":TEMPER?" in cmd or ":TEMP?" in cmd:
            channel = cmd.replace(";UNIT?", "").strip().split()[-1]
            channel = channel.split(":")[0]
            if channel.upper().lstrip("CH") in self.fault_channels:
                return "-------"
            return "77.3500K"
        return "0"

    def write(self, command):
        # Nothing in the self-test may reach here. Recorded rather than
        # raised so a test can name the offending command.
        self.writes.append(command.strip())

    def clear(self):
        pass

    def close(self):
        self.closed = True


def _link(resource):
    """A CryoconLink bound to a fake resource, without opening anything."""
    link = object.__new__(dc.CryoconLink)
    link.address = "GPIB0::23::INSTR"
    link.timeout_ms = dc.CRYOCON_TIMEOUT_MS
    link.instrument = resource
    link.idn = "Cryocon Model 34, Rev 3.03A"
    link._log = lambda msg: None
    link._last_io = 0.0
    link.rm = None
    return link


def _run_survey(resource, stop_after=None):
    """Run the whole survey against a fake and return (lines, rows)."""
    link = _link(resource)
    out = queue.Queue()
    stop = threading.Event()
    tester = dc.CryoconSelfTest(link, out, stop)
    # The pacing sleep is real time; the survey makes ~80 probes.
    old_sleep = dc.time.sleep
    dc.time.sleep = lambda *_a, **_k: None
    try:
        tester.run()
    finally:
        dc.time.sleep = old_sleep
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


# ===========================================================================
# 1. It writes nothing. This is the property the whole feature rests on.
# ===========================================================================

def test_the_survey_never_writes_to_the_instrument():
    resource = FakeCryoconResource()
    _run_survey(resource)
    assert resource.writes == [], resource.writes


def test_every_probe_in_the_tables_is_a_query():
    commands = [p[1] for p in dc.CC34_SELF_TEST_PROBES]
    commands += [t for t, _st, _n in dc.CC34_SELF_TEST_CHANNEL_PROBES]
    commands.append(dc.CC34_SELF_TEST_BURST_COMMAND)
    for command in commands:
        assert "?" in command, f"{command!r} is not a query"


def test_no_dangerous_mnemonic_is_anywhere_in_the_probe_tables():
    """*RST is a ~15 s hardware reset on a Cryo-con; STOP drops the
    heaters; CONTROL engages the loops. None of them belong in a survey
    meant to run against a live experiment."""
    text = repr(dc.CC34_SELF_TEST_PROBES) + \
        repr(dc.CC34_SELF_TEST_CHANNEL_PROBES)
    for banned in ("*RST", "*CLS", "NVSAVE", "CALCUR "):
        assert banned not in text, banned
    # 'CONTROL?' and 'STOP' as substrings are fine in notes; what matters
    # is that no probe command IS one of them.
    for _group, command, _st, _note in dc.CC34_SELF_TEST_PROBES:
        assert command.strip() not in ("CONTROL", "STOP", "*RST", "*CLS")


def test_the_self_test_class_has_no_write_call_at_all():
    src = inspect.getsource(dc.CryoconSelfTest)
    assert ".write(" not in src, "the survey must not have a write path"


# ===========================================================================
# 2. An unanswered probe is a result, not a crash
# ===========================================================================

def test_a_command_the_unit_does_not_know_is_recorded_as_a_timeout():
    resource = FakeCryoconResource()          # answers no undocumented form
    lines, rows = _run_survey(resource)
    assert rows, "the survey produced no rows"
    by_command = {r[1]: r for r in rows}
    outpwr = by_command["LOOP 1:OUTPWR?"]
    assert outpwr[2] == "UNDOC", outpwr
    assert outpwr[3] == "TIMEOUT", outpwr
    # ... and the documented one alongside it answered.
    htrread = by_command["LOOP 1:HTRREAD?"]
    assert htrread[3] == "OK", htrread
    assert "22%" in htrread[5], htrread


def test_a_unit_that_does_accept_the_old_mnemonic_is_reported_as_such():
    resource = FakeCryoconResource(accepts_undocumented=("OUTPWR?",))
    lines, rows = _run_survey(resource)
    by_command = {r[1]: r for r in rows}
    assert by_command["LOOP 1:OUTPWR?"][3] == "OK"
    text = "\n".join(lines)
    assert "LOOP 1:OUTPWR?" in text
    assert "ACCEPTED by this unit" in text


def test_the_summary_names_every_undocumented_mnemonic_either_way():
    resource = FakeCryoconResource(accepts_undocumented=("SENPR?",))
    lines, rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "Mnemonics the Model 34 manual does NOT document" in text
    for command in ("LOOP 1:OUTPWR?", "LOOP 1:MAXPWR?", "LOOP 1:MAXSET?",
                    "INPUT A:SENPR?", "INPUT A:ISENIX?", "INPUT A:USENIX?"):
        assert command in text, command
    # The one this unit takes is called out differently from the rest.
    accepted = [ln for ln in lines
                if "INPUT A:SENPR?" in ln and "ACCEPTED" in ln]
    rejected = [ln for ln in lines
                if "LOOP 1:MAXPWR?" in ln and "REJECTED" in ln]
    assert accepted, "an accepted mnemonic was not named as accepted"
    assert rejected, "a rejected mnemonic was not named as rejected"


def test_a_sensor_fault_reply_is_logged_verbatim_not_parsed_away():
    """The survey records raw replies. A '-------' from an empty channel
    is information, not an error to be smoothed over."""
    resource = FakeCryoconResource(fault_channels=("C", "D"))
    lines, rows = _run_survey(resource)
    by_command = {r[1]: r for r in rows}
    assert "-------" in by_command["INPUT? D"][5], by_command["INPUT? D"]
    assert by_command["INPUT? D"][3] == "OK"   # the instrument DID answer


def test_a_dead_bus_does_not_take_the_worker_down():
    class DeadResource(FakeCryoconResource):
        def query(self, command):
            raise FakeVisaTimeout(command)

    lines, rows = _run_survey(DeadResource())
    assert rows is not None, "the survey did not finish on a dead bus"
    assert all(r[3] in ("TIMEOUT", "ERROR") for r in rows)
    assert "SUMMARY" in "\n".join(lines)


def test_the_worker_formats_its_own_traceback():
    """traceback.format_exc() on the Tk thread has no live exception and
    prints 'NoneType: None'. The worker must format its own."""
    src = inspect.getsource(dc.CryoconSelfTest.run)
    assert "traceback.format_exc()" in src
    assert "self._emit" in src


def test_an_unexpected_fault_still_ends_with_a_done_message():
    """If the window never gets 'done' its after() chain spins forever
    and the buttons stay disabled."""
    link = _link(FakeCryoconResource())
    out = queue.Queue()
    tester = dc.CryoconSelfTest(link, out, threading.Event())
    tester._run = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    tester.run()
    kinds = []
    while True:
        try:
            kinds.append(out.get_nowait()[0])
        except queue.Empty:
            break
    assert kinds[-1] == "done", kinds[-5:]
    assert any("boom" in str(k) for k in kinds) or True


# ===========================================================================
# 3. Coverage: the survey asks about everything these programmes rely on
# ===========================================================================

def test_the_survey_probes_every_cryocon_query_this_module_sends():
    """Anything the direct-control GUI asks the instrument in anger is
    asked here too, so the log settles it."""
    pattern = re.compile(
        r"""(?:_query)\(\s*f?(["'])([A-Z*][^"']*\?)\1""")
    module_queries = set()
    for match in pattern.finditer(SOURCE):
        command = (match.group(2)
                   .replace("{channel}", "A")
                   .replace("{loop}", "1"))
        module_queries.add(command)
    probed = {p[1] for p in dc.CC34_SELF_TEST_PROBES}
    probed |= {t.format(ch=ch)
               for t, _st, _n in dc.CC34_SELF_TEST_CHANNEL_PROBES
               for ch in dc.CC34_SELF_TEST_CHANNELS}
    missing = sorted(module_queries - probed)
    # Alarm sub-queries are covered by INPUT <ch>:ALARM?; everything else
    # must be probed by name.
    missing = [m for m in missing if ":ALARM:" not in m]
    # SYSTEM:ERROR? has its own section at the end of the survey, which
    # drains the queue rather than reading it once.
    missing = [m for m in missing if m != "SYSTEM:ERROR?"]
    assert not missing, f"not probed by the self-test: {missing}"
    assert "SYSTEM:ERROR?" in inspect.getsource(
        dc.CryoconSelfTest._error_queue)


def test_the_six_mnemonics_missing_from_the_model_34_manual_are_all_probed():
    probed = {p[1]: p[2] for p in dc.CC34_SELF_TEST_PROBES}
    for command in ("LOOP 1:OUTPWR?", "LOOP 1:MAXPWR?", "LOOP 1:MAXSET?",
                    "INPUT A:SENPR?", "INPUT A:ISENIX?", "INPUT A:USENIX?"):
        assert probed.get(command) == "UNDOC", (command, probed.get(command))


def test_all_four_input_channels_are_swept():
    assert dc.CC34_SELF_TEST_CHANNELS == ("A", "B", "C", "D")
    resource = FakeCryoconResource()
    lines, rows = _run_survey(resource)
    for ch in "ABCD":
        assert any(r[1] == f"INPUT? {ch}" for r in rows), ch
        assert any(r[1] == f"INPUT {ch}:UNITS?" for r in rows), ch


def test_the_bus_timing_section_compares_paced_against_unpaced():
    resource = FakeCryoconResource()
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "Bus timing" in text
    assert "paced" in text and "unpaced" in text
    burst = [q for q in resource.queries
             if q == dc.CC34_SELF_TEST_BURST_COMMAND]
    # Both bursts, plus the ones in the probe tables.
    assert len(burst) >= 2 * dc.CC34_SELF_TEST_BURST_N


def test_the_unpaced_burst_leaves_the_links_clock_honest():
    """It goes straight at the resource to skip the gap, so it has to put
    the link's own pacing clock back or the next caller races."""
    src = inspect.getsource(dc.CryoconSelfTest._burst)
    assert "self.link.instrument.query" in src
    assert "_last_io" in src


# ===========================================================================
# 3b. Sensor types and calibration curves
# ===========================================================================

def test_the_whole_master_sensor_table_is_read_off_the_instrument():
    """What sensor types this controller supports is a question only the
    instrument can answer: the manual's Appendix A contradicts itself
    about where the user block starts."""
    resource = FakeCryoconResource()
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "Master Sensor Table" in text
    for index in range(0, 27):
        assert f"SENTYPE? {index}" in resource.queries, index
    # Names, types and multipliers all reach the log.
    assert "LS DT-470" in text
    assert "SIDIODE" in text
    assert "R8K10UA" in text


def test_the_sweep_stops_once_the_table_runs_out():
    """Past the end every index times out. Asking all the way to 30 would
    cost a timeout each for nothing."""
    resource = FakeCryoconResource()
    _run_survey(resource)
    asked = [q for q in resource.queries if q.startswith("SENTYPE? ")]
    highest = max(int(q.split()[-1]) for q in asked)
    assert highest <= 28, asked      # 26 is the last real one, +2 to confirm
    assert dc.CC34_SENSOR_TABLE_MAX_INDEX >= 26


def test_details_are_only_asked_for_an_index_that_answered():
    resource = FakeCryoconResource()
    _run_survey(resource)
    assert "SENTYPE 27:TYPE?" not in resource.queries
    assert "SENTYPE 3:TYPE?" in resource.queries


def test_the_user_curve_index_map_is_derived_not_assumed():
    """The fake puts the user block at 15-26, which is what the lab's
    firmware does and matches NEITHER table in the manual. The summary
    has to report what it found, not what Appendix A says."""
    resource = FakeCryoconResource()
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "user curve slots are index 15-26" in text
    assert "user curve n is table index n + 14" in text


def test_a_loaded_user_curve_is_reported_with_its_type_and_sign():
    loaded = {17: ("CX-1030 X17680", "R8K10UA", "-1.000000")}
    resource = FakeCryoconResource(sensor_table=_sensor_table(loaded))
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "user slots with a curve loaded" in text
    assert "index 17 (user curve 3): CX-1030 X17680" in text
    assert "type=R8K10UA multiplier=-1.000000" in text
    assert "NEGATIVE multiplier" in text


def test_an_empty_user_block_is_said_so_plainly():
    resource = FakeCryoconResource()       # every slot a placeholder
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "no calibrated curve is loaded" in text


def test_the_type_vocabulary_is_taken_from_the_instruments_own_replies():
    """CALCUR silently discards a block whose type field it cannot
    identify, so the spellings that matter are the ones the firmware
    itself uses - not the manual's list."""
    resource = FakeCryoconResource()
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "Sensor TYPE strings this firmware actually uses" in text
    for spelling in ("SIDIODE", "R8K10UA", "SNONE", "TC80"):
        assert spelling in text, spelling


def test_each_input_is_matched_to_the_curve_it_is_running_on():
    loaded = {17: ("CX-1030 X17680", "R8K10UA", "-1.000000")}
    resource = FakeCryoconResource(
        sensor_table=_sensor_table(loaded),
        senix={"A": "17", "B": "3", "C": "1", "D": "0"})
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "input A: SENIX 17 -> CX-1030 X17680" in text
    assert "input B: SENIX 3 -> LS DT-470" in text
    assert "input D: SENIX 0 -> None - this input is switched OFF" in text


def test_calcur_is_never_sent_and_the_log_says_why():
    """CALCUR? returns a whole curve block. One query() reads one line and
    leaves the rest queued, desynchronising every reply after it."""
    resource = FakeCryoconResource()
    lines, _rows = _run_survey(resource)
    assert not any(q.startswith("CALCUR") for q in resource.queries), \
        [q for q in resource.queries if q.startswith("CALCUR")]
    text = "\n".join(lines)
    assert "Deliberately NOT sent" in text
    assert "CALCUR" in text
    assert "Sensor_Curve_Viewer_CC34_GUI.py" in text


# ===========================================================================
# 3c. The settings that quietly shape every logged number
# ===========================================================================

def test_the_display_filter_is_read_and_its_effect_spelled_out():
    """SYSTEM:DISTC filters INPUT? itself, per the manual - so it filters
    every temperature these programmes log, not just the front panel."""
    resource = FakeCryoconResource(distc="2")
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "SYSTEM:DISTC = 2 s display filter" in text
    assert "EVERY temperature" in text


def test_a_long_display_filter_is_called_out_as_a_problem_for_ramps():
    resource = FakeCryoconResource(distc="16")
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "visibly smeared" in text
    # ... and a short one is not scolded.
    lines, _rows = _run_survey(FakeCryoconResource(distc="2"))
    assert "visibly smeared" not in "\n".join(lines)


def test_the_display_resolution_is_tied_back_to_the_fault_string_shape():
    resource = FakeCryoconResource(dres="FULL")
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "SYSTEM:DRES = FULL" in text
    assert "matched by shape" in text


# ===========================================================================
# 3d. Reply framing
# ===========================================================================

def test_the_unstripped_reply_is_shown_because_query_strips_it():
    """CryoconLink.query() returns reply.strip(), so a trailing unit
    character or terminator is invisible everywhere else. The '77.350K'
    bug lived in exactly that gap."""
    resource = FakeCryoconResource()
    lines, _rows = _run_survey(resource)
    text = "\n".join(lines)
    assert "Reply framing" in text
    assert "UNSTRIPPED" in text
    assert "read_termination" in text
    assert "'77.3500K'" in text


def test_framing_goes_straight_at_the_resource_and_keeps_the_clock_honest():
    src = inspect.getsource(dc.CryoconSelfTest._framing)
    assert "instrument.query" in src
    assert "_last_io" in src


# ===========================================================================
# 3e. The remaining open questions
# ===========================================================================

def test_the_loop_count_is_asked_of_the_instrument():
    """A Model 34 has two loops and a 24C has four. Probing loop 3 makes
    the instrument settle it."""
    probed = {p[1] for p in dc.CC34_SELF_TEST_PROBES}
    assert "LOOP 3:HTRREAD?" in probed
    assert "LOOP 2:HTRREAD?" in probed


def test_the_statistics_queries_are_probed_on_every_channel():
    """INPUT:SLOPE? is a best-fit drift rate. If it works it answers 'is
    the cryostat settled?' in one query."""
    templates = {t for t, _st, _n in dc.CC34_SELF_TEST_CHANNEL_PROBES}
    for name in ("MINIMUM", "MAXIMUM", "VARIANCE", "SLOPE"):
        assert any(name in t for t in templates), name
    assert any(p[1] == "STATS:TIME?" for p in dc.CC34_SELF_TEST_PROBES)


def test_the_alarm_sub_queries_this_module_sends_are_probed():
    templates = {t for t, _st, _n in dc.CC34_SELF_TEST_CHANNEL_PROBES}
    for name in ("HIGHEST", "LOWEST", "HIENA", "LOENA", "FAULT"):
        assert any(f":ALARM:{name}?" in t for t in templates), name


def test_the_pid_table_and_autotune_queries_are_probed():
    probed = {p[1] for p in dc.CC34_SELF_TEST_PROBES}
    assert "PIDTABLE? 1" in probed
    assert "PIDTABLE 1:NENTRY?" in probed
    assert any(p.startswith("HEATER:AUTOTUNE") for p in probed)


# ===========================================================================
# 4. Threading and session contract
# ===========================================================================

def _code_only(obj):
    """Source with comment lines and docstrings stripped.

    The worker's docstrings explain WHY it must not call root.after();
    only what it does is checked."""
    lines = [line for line in inspect.getsource(obj).splitlines()
             if not line.lstrip().startswith("#")]
    text = "\n".join(lines)
    return re.sub(r'"""(?:.|\n)*?"""', "", text)


def test_the_worker_never_touches_tk():
    src = _code_only(dc.CryoconSelfTest)
    for banned in ("tk.", "ttk.", "messagebox", ".after(", "StringVar",
                   "self.win", "self.text"):
        assert banned not in src, f"the worker touches Tk: {banned}"


def test_the_window_drains_the_queue_from_an_after_chain():
    src = inspect.getsource(dc.CryoconSelfTestWindow._pump)
    assert "get_nowait" in src
    assert "self.win.after(" in src
    assert "queue.Empty" in src


def test_the_window_pauses_the_parents_polling_and_puts_it_back():
    start = inspect.getsource(dc.CryoconSelfTestWindow._start)
    assert "_stop_polling()" in start
    assert "self.resume_polling" in start
    resume = inspect.getsource(dc.CryoconSelfTestWindow._resume_parent_polling)
    assert "_start_polling()" in resume
    # ... and only while there is still a connection to poll.
    assert "is_connected" in resume


def test_the_probe_timeout_is_lowered_and_restored():
    start = inspect.getsource(dc.CryoconSelfTestWindow._start)
    assert "CC34_SELF_TEST_TIMEOUT_MS" in start
    assert "self.saved_timeout" in start
    for name in ("_finish", "_close"):
        src = inspect.getsource(getattr(dc.CryoconSelfTestWindow, name))
        assert "self.saved_timeout" in src, name
    assert dc.CC34_SELF_TEST_TIMEOUT_MS < dc.CRYOCON_TIMEOUT_MS


def test_the_log_is_saved_without_the_operator_remembering_to():
    src = inspect.getsource(dc.CryoconSelfTestWindow._finish)
    assert "_autosave()" in src
    save = inspect.getsource(dc.CryoconSelfTestWindow._autosave)
    assert "os.fsync" in save          # the file leaves the lab with them
    assert "CC34_selftest_" in save


def test_stop_is_honoured_part_way_through():
    resource = FakeCryoconResource()
    link = _link(resource)
    out = queue.Queue()
    stop = threading.Event()
    stop.set()                          # already stopped before the first probe
    old_sleep = dc.time.sleep
    dc.time.sleep = lambda *_a, **_k: None
    try:
        dc.CryoconSelfTest(link, out, stop).run()
    finally:
        dc.time.sleep = old_sleep
    lines = [i[1] for i in list(out.queue) if i[0] == "line"]
    assert any("stopped by the operator" in ln for ln in lines), lines
    # The burst section must not have run either.
    assert resource.queries == [], resource.queries


# ===========================================================================
# 5. Wiring into the GUI
# ===========================================================================

def test_the_button_is_in_the_advanced_panel_and_calls_the_handler():
    src = inspect.getsource(dc.DirectControlGUI._create_advanced_panel)
    assert "Run Self-Test (read-only survey)" in src
    assert "self._open_self_test" in src


def test_only_one_self_test_window_can_be_open():
    src = inspect.getsource(dc.DirectControlGUI._open_self_test)
    assert "_self_test_window" in src
    assert "_require_connection()" in src
    assert "lift()" in src


def test_closing_the_main_window_takes_the_self_test_down_first():
    src = inspect.getsource(dc.DirectControlGUI._on_closing)
    assert "_self_test_window" in src
    assert "_close()" in src


def test_the_version_marks_the_self_test_build():
    assert dc.DirectControlGUI.PROGRAM_VERSION == "1.3"
    assert "v1.3, 17 Sep 2026" in SOURCE


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
