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

# The declared pause is checked below; here it is set to zero so that the
# suite does not sit through the real retry delays. The modules read this
# global at call time, so no patching of time.sleep is needed -- which
# matters, because time is shared with every other test in the session.
RETRY_PAUSES = {key: mod.CRYOCON_READ_RETRY_S for key, mod in MODULES.items()}
for _mod in MODULES.values():
    _mod.CRYOCON_READ_RETRY_S = 0


class FakeLink:
    """Answers INPUT? with a scripted queue of replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.queries = []

    def query(self, command):
        self.queries.append(command)
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]


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
    else:                                   # k197a, sr830
        def build(link):
            backend = _bare(mod.Cryocon34Monitor)
            backend.channel = "A"
            backend.instrument = link
            return backend.read_temperature

    return mod, build


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
