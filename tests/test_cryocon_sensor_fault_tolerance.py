"""A Cryo-con sensor fault must not end a run.

On 31 Aug 2026 a passive temperature log died twice in ten minutes:

    CryoconStatusError: Cryocon temperature reading on channel A returned
    '-------': sensor fault: the sensor is open, disconnected or shorted.

Both times it had logged several hundred good readings first and read
normally again on the next attempt, so the fault was transient -- the Model
34 shows dashes for a moment while an input range switches. Raising on it
killed the worker thread, which for an unattended overnight run is far worse
than the missing point.

Every module that reads a Cryo-con for data now:

  1. retries the reading in place (CRYOCON_READ_RETRIES), so a one-second
     glitch costs nothing at all;
  2. returns NaN instead of raising if it still will not read, so the point
     is skipped or logged as NaN and the run carries on;
  3. never enters the comm-retry/reconnect path on a status reply -- the
     instrument answered, the sensor did not, and no reconnect cures that.

A genuine communication failure must still raise, because that IS what the
reconnect loop is for. Both directions are checked here.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import matplotlib  # noqa: E402
matplotlib.use("Agg")

MODULE_PATHS = {
    "t_sensing": ("pica", "cryocon", "T_Sensing_CC34_GUI.py"),
    "passive_lcr": ("pica", "keysight",
                    "Temprature_Scan_Passive_CC34_E4980A_GUI.py"),
    "k2400": ("pica", "keithley", "k2400", "RT_K2400_CC34_T_Sensing_GUI.py"),
    "k2400_2182": ("pica", "keithley", "k2400_2182",
                   "RT_K2400_K2182_CC34_T_Sensing_GUI.py"),
    "delta": ("pica", "keithley", "delta_mode",
              "Delta_RT_K6221_K2182_CC34_Sensing_GUI.py"),
    "k6517b": ("pica", "keithley", "k6517b", "High_Resistance",
               "RT_K6517B_CC34_T_Sensing_GUI.py"),
    "k197a": ("pica", "keithley", "k6221_k197a",
              "RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py"),
    "sr830": ("pica", "lockin", "sr830",
              "RT_AC_K6221_SR830_CC34_T_Sensing_GUI.py"),
    "ppms_master": ("pica", "keysight",
                    "PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py"),
    "ppms_sync": ("pica", "keysight",
                  "PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py"),
    "field_step": ("pica", "keysight", "Field_Step_Freq_Scan_E4980A_GUI.py"),
}

# Modules that read a Cryo-con but log no temperature series of their own,
# so they have no tolerant reader to test here. Their reply PARSER is still
# held to the same table below (test_every_parser_copy_agrees...).
PARSER_ONLY_PATHS = {
    "diagnostics": ("pica", "cryocon", "Diagnostics_CC34_GUI.py"),
    "t_control": ("pica", "cryocon", "T_Control_CC34_DirectControl_GUI.py"),
}


def _load(name, parts):
    path = os.path.join(REPO_ROOT, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULES = {key: _load("cc34_fault_" + key, parts)
           for key, parts in MODULE_PATHS.items()}
PARSER_ONLY = {key: _load("cc34_fault_" + key, parts)
               for key, parts in PARSER_ONLY_PATHS.items()}

# The declared pause is checked below; here it is set to zero so that the
# suite does not sit through the real retry delays. The modules read this
# global at call time, so no patching of time.sleep is needed -- which
# matters, because time is shared with every other test in the session.
RETRY_PAUSES = {key: mod.CRYOCON_READ_RETRY_S for key, mod in MODULES.items()}
for _mod in MODULES.values():
    _mod.CRYOCON_READ_RETRY_S = 0
    # These are private copies loaded under their own names, so switching
    # the bus pacing off here cannot leak into any other test file.
    if hasattr(_mod, "CRYOCON_MIN_GAP_S"):
        _mod.CRYOCON_MIN_GAP_S = 0


class FakeLink:
    """Answers INPUT? with a scripted queue of replies.

    The last reply repeats for ever. An exception INSTANCE in the queue is
    raised instead of returned, so a comm glitch can be scripted between
    two readings. There is deliberately no write(): a reader that tried to
    write would fail loudly here.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.queries = []

    def query(self, command):
        self.queries.append(command)
        if len(self.replies) > 1:
            reply = self.replies.pop(0)
        else:
            reply = self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _glitch():
    return IOError("VI_ERROR_TMO: timeout expired before operation "
                   "completed")


class DeadLink:
    """A link that has genuinely gone away."""

    def query(self, command):
        raise IOError("VI_ERROR_TMO: timeout expired before operation "
                      "completed")


def _bare(cls):
    """An instance with no instrument session behind it."""
    return object.__new__(cls)


def _reader(key):
    """(callable taking no arguments, its module, its fake-link setter).

    Each module names the read differently and hangs it off a different
    attribute, so the differences are collected here rather than repeated
    in every test.
    """
    mod = MODULES[key]

    if key == "t_sensing":
        def build(link):
            backend = _bare(mod.Cryocon34_Backend)
            backend.channel = "A"
            backend.link = link
            backend.log = lambda msg: None
            backend.last_status_error = None
            backend.status_reports = 0
            return backend.read_temperature_tolerant
    elif key == "passive_lcr":
        def build(link):
            backend = _bare(mod.Cryocon34_Backend)
            backend.channel = "A"
            backend.link = link
            backend.log = lambda msg: None
            backend.status_reports = 0
            return backend.read_temperature_tolerant
    elif key in ("k2400", "k2400_2182", "delta"):
        cls = {"k2400": lambda: mod.RT_Backend_Passive,
               "k2400_2182": lambda: mod.VT_Backend_Passive,
               "delta": lambda: mod.Combined_Backend}[key]()

        def build(link):
            backend = _bare(cls)
            backend.CC_CHANNEL = "A"
            backend.cryocon = link
            return backend.read_temperature
    elif key == "k6517b":
        def build(link):
            backend = _bare(mod.Cryocon34_Backend)
            backend.instrument = link
            return lambda: backend.get_temperature("A")
    elif key in ("ppms_master", "ppms_sync"):
        def build(link):
            backend = _bare(mod.Probe_Thermometer_Backend)
            backend.cryocon = link
            backend._last_io = 0.0
            backend._status_reports = 0
            backend.last_status_error = None
            return lambda: backend.get_temperature("A")
    elif key == "field_step":
        # The one reader with a different contract: (kelvin or None, raw).
        # None is this module's NaN, so it is mapped to NaN here and every
        # test below holds it to the same rules as the others.
        def build(link):
            backend = _bare(mod.CryoconLink)
            backend.channel = "A"
            backend.instrument = link
            backend._last_io = 0.0

            def read():
                value, _raw = backend.read_temperature()
                return float("nan") if value is None else value
            return read
    else:                                   # k197a, sr830
        def build(link):
            backend = _bare(mod.Cryocon34Monitor)
            backend.channel = "A"
            backend.instrument = link
            return backend.read_temperature

    return mod, build


# Where each reader counts the points it had to give up on. Field_Step
# keeps no counter: its caller counts the None.
FAULT_COUNTERS = {
    "t_sensing": "status_reports", "passive_lcr": "status_reports",
    "ppms_master": "_status_reports", "ppms_sync": "_status_reports",
    "k2400": "_sensor_faults", "k2400_2182": "_sensor_faults",
    "delta": "_sensor_faults", "k6517b": "_sensor_faults",
    "k197a": "_sensor_faults", "sr830": "_sensor_faults",
}


def _backend_of(read):
    """The object behind a reader: a bound method's self, or the backend a
    lambda closed over."""
    owner = getattr(read, "__self__", None)
    if owner is not None:
        return owner
    for cell in read.__closure__ or ():
        if not callable(cell.cell_contents) and \
                hasattr(cell.cell_contents, "__dict__"):
            return cell.cell_contents
    return None


def test_transient_fault_is_retried_and_costs_no_point():
    """Dashes once, then a number: the reading must come back, not NaN."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        link = FakeLink(["-------", "77.350"])
        read = build(link)
        value = read()
        assert value == 77.350, (
            f"{key}: a transient sensor fault lost a good reading "
            f"(got {value!r})")
        assert len(link.queries) == 2, (
            f"{key}: expected the reading to be retried once, "
            f"saw {len(link.queries)} queries")


def test_sustained_fault_returns_nan_instead_of_raising():
    """A channel that never reads gives NaN, so the run carries on."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        for reply in ("-------", "......."):
            link = FakeLink([reply])
            read = build(link)
            value = read()
            assert value != value, (
                f"{key}: reply {reply!r} should have given NaN, "
                f"got {value!r}")
            assert len(link.queries) == mod.CRYOCON_READ_RETRIES + 1, (
                f"{key}: expected {mod.CRYOCON_READ_RETRIES + 1} attempts "
                f"on {reply!r}, saw {len(link.queries)}")


def test_communication_failure_still_raises():
    """A dead link is NOT a sensor fault: the reconnect path must see it."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        read = build(DeadLink())
        try:
            value = read()
        except IOError:
            continue
        raise AssertionError(
            f"{key}: a comm failure was swallowed as {value!r}; the "
            "reconnect loop would never run")


def test_good_reading_costs_exactly_one_query():
    """The retry loop must not slow down the normal case."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        link = FakeLink(["300.125"])
        read = build(link)
        assert read() == 300.125, key
        assert len(link.queries) == 1, (
            f"{key}: a healthy reading took {len(link.queries)} queries")


def test_retry_budget_is_declared_in_every_module():
    for key, mod in MODULES.items():
        assert getattr(mod, "CRYOCON_READ_RETRIES", 0) >= 1, key
        assert RETRY_PAUSES.get(key, 0) > 0, key


def test_every_module_with_a_tolerant_reader_is_tested_here():
    """Discovered, not listed: the lesson of the 17 Sep mnemonic audit.

    Any module that declares a read-retry budget has a tolerant reader, and
    a tolerant reader nobody tests is one nobody knows works.
    """
    tested = {os.path.join(*parts) for parts in MODULE_PATHS.values()}
    missing = []
    for folder, _dirs, names in os.walk(os.path.join(REPO_ROOT, "pica")):
        if "__pycache__" in folder:
            continue
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                if "CRYOCON_READ_RETRIES" not in handle.read():
                    continue
            rel = os.path.relpath(path, REPO_ROOT)
            if rel not in tested:
                missing.append(rel)
    assert not missing, missing


def test_a_fault_that_clears_on_the_last_allowed_attempt_is_kept():
    """The boundary: RETRIES faults then a number is still a reading."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        link = FakeLink(["-------"] * mod.CRYOCON_READ_RETRIES + ["12.500"])
        assert build(link)() == 12.5, key
        assert len(link.queries) == mod.CRYOCON_READ_RETRIES + 1, key


def test_one_fault_more_than_the_budget_gives_nan_and_stops_asking():
    """The good reply queued after the budget must never be read: the
    reader gives the point up and the next poll starts afresh."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        link = FakeLink(["......."] * (mod.CRYOCON_READ_RETRIES + 1)
                        + ["12.500"])
        value = build(link)()
        assert value != value, (key, value)
        assert len(link.queries) == mod.CRYOCON_READ_RETRIES + 1, key


def test_every_fault_shape_the_instrument_sends_is_retried():
    """Different status replies in a row, then a number.

    Input D on the lab unit answers with dots, not dashes; every shape must
    be handled the same way, and none may raise."""
    shapes = ["-------", ".......", "N/A", "", "NACK", "?.??????", "--"]
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        for start in range(len(shapes)):
            burst = (shapes[start:] + shapes[:start])
            link = FakeLink(burst[:mod.CRYOCON_READ_RETRIES] + ["4.200"])
            assert build(link)() == 4.2, (key, burst)


def test_a_reply_with_line_ending_whitespace_and_unit_is_a_reading():
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        for reply, expected in ((" 77.350\r\n", 77.35),
                                ("77.350K", 77.35),
                                ("+2.95000E+02", 295.0),
                                ("77.35;78.10;-------;.......", 77.35)):
            link = FakeLink([reply])
            assert build(link)() == expected, (key, reply)
            assert len(link.queries) == 1, (key, reply)


def test_every_reader_sends_the_one_spelling_the_lab_unit_accepts():
    """25 Sep 2026: 'INPUT? A' answers; 'INPUT?A', 'INPUT ? A', 'INP?A'
    and a padded '  INPUT? A  ' all time out. Exactly one space."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        link = FakeLink(["-------", "300.0"])
        build(link)()
        assert link.queries == ["INPUT? A", "INPUT? A"], (key, link.queries)


def test_a_comm_failure_during_fault_retries_raises_rather_than_nan():
    """Dashes, then the bus dies. That is a comm failure and the reconnect
    loop must see it: returning NaN here would log fault points for ever
    against an instrument that is no longer there."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        read = build(FakeLink(["-------", _glitch()]))
        try:
            value = read()
        except IOError:
            continue
        raise AssertionError(f"{key}: a dead bus was reported as {value!r}")


def test_the_ppms_readers_ride_out_a_single_comm_glitch():
    """The two PPMS programs are the only ones that retry a comm error in
    place (CC34-8): a multi-day protocol must not reconnect on one slow
    reply. Every other module hands a comm error straight to its worker's
    reconnect loop on the FIRST failure - also fine, but it must be one or
    the other, never a NaN."""
    for key in MODULE_PATHS:
        mod, build = _reader(key)
        link = FakeLink([_glitch(), "150.000"])
        read = build(link)
        if key in ("ppms_master", "ppms_sync"):
            assert read() == 150.0, key
            assert len(link.queries) == 2, key
            continue
        try:
            read()
        except IOError:
            assert len(link.queries) == 1, key
            continue
        raise AssertionError(f"{key}: swallowed a comm error")


def test_a_given_up_point_is_counted_once_not_once_per_attempt():
    """The fault counter drives the 'first five, then every 25th' log
    throttle. Counting attempts would flood an overnight log four times
    faster than intended and misstate the count."""
    for key, attr in FAULT_COUNTERS.items():
        mod, build = _reader(key)
        read = build(FakeLink(["-------"]))
        backend = _backend_of(read)
        assert backend is not None, key
        for _ in range(3):
            value = read()
            assert value != value, key
        assert getattr(backend, attr) == 3, (key, getattr(backend, attr))


def test_a_recovered_reading_is_not_counted_as_a_fault():
    for key, attr in FAULT_COUNTERS.items():
        mod, build = _reader(key)
        read = build(FakeLink(["-------", "20.0"]))
        assert read() == 20.0, key
        assert getattr(_backend_of(read), attr, 0) == 0, key


# ------------------------------------------------ one parser, many copies
#
# Every module carries its own copy of the reply parser (self-contained
# programs, by design). A copy that drifts would read the same reply
# differently in two programs on the same bench, so every copy is held to
# ONE table here. None means 'not a reading': a CryoconStatusError, or the
# None that Field_Step's parse_cryocon_temperature returns instead.
PARSER_TABLE = (
    ("77.350", 77.35), (" 77.350\r\n", 77.35), ("77.350K", 77.35),
    ("77.350 K", 77.35), ("+77.35", 77.35), ("1.5E+02", 150.0),
    ("0.000", 0.0), ("77.35;78.10", 77.35),
    ("-------", None), ("----", None), ("--", None), (".......", None),
    ("..", None), ("N/A", None), ("NACK", None), ("", None), ("   ", None),
    ("?.??????", None), ("abc", None), (";77.35", None),
    ("-------;77.35", None), (".......;77.35", None),
)


def _every_parser():
    parsers = {}
    for key, mod in list(MODULES.items()) + list(PARSER_ONLY.items()):
        if hasattr(mod, "parse_cryocon_number"):
            def parse(raw, mod=mod):
                try:
                    return mod.parse_cryocon_number(raw, "temperature", "A")
                except mod.CryoconStatusError:
                    return None
        else:
            parse = mod.parse_cryocon_temperature
        parsers[key] = parse
    return parsers


def test_every_parser_copy_agrees_on_every_reply_shape():
    parsers = _every_parser()
    assert len(parsers) >= 13, sorted(parsers)
    wrong = []
    for key, parse in sorted(parsers.items()):
        for raw, expected in PARSER_TABLE:
            got = parse(raw)
            if got != expected:
                wrong.append(f"{key}: {raw!r} -> {got!r}, want {expected!r}")
    assert not wrong, chr(10).join(wrong)


def test_a_status_reply_raises_the_status_error_and_nothing_broader():
    """The tolerant readers catch CryoconStatusError ONLY. A parser that
    raised a bare ValueError on a fault would kill the worker thread - the
    31 Aug 2026 failure this file exists for."""
    for key, mod in list(MODULES.items()) + list(PARSER_ONLY.items()):
        if not hasattr(mod, "parse_cryocon_number"):
            continue
        for raw in ("-------", ".......", "", "garbage"):
            try:
                mod.parse_cryocon_number(raw, "temperature", "A")
            except mod.CryoconStatusError as exc:
                assert "channel A" in str(exc), (key, raw, str(exc))
                continue
            raise AssertionError(f"{key}: {raw!r} did not raise")


def test_every_copy_names_the_same_status_strings():
    reference = MODULES["t_sensing"].CRYOCON_STATUS_STRINGS
    for key, mod in list(MODULES.items()) + list(PARSER_ONLY.items()):
        assert mod.CRYOCON_STATUS_STRINGS == reference, key


def _run_all():
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
