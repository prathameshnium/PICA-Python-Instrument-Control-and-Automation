"""Tests for the launcher's continuous temperature / pressure logging
(long press on Reload) and the gauge dialog's COM3 default.

No Tk window and no instrument: the readers are module-level functions fed a
fake VISA resource manager, and the click / long-press logic is exercised on
a stand-in object. Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")
TPG_PATH = os.path.join(REPO_ROOT, "pica", "pfeiffer", "Pressure_Log_TPG361_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v2 = _load("pica_main_v2_tplog", V2_PATH)


class _FakeInst:
    def __init__(self, replies, sent):
        self.replies, self.sent, self.timeout = replies, sent, None

    def query(self, cmd):
        self.sent.append(cmd)
        return self.replies[cmd]

    def close(self):
        pass


class _FakeRM:
    def __init__(self, replies):
        self.replies, self.sent, self.opened = replies, [], []

    def open_resource(self, res):
        self.opened.append(res)
        return _FakeInst(self.replies, self.sent)

    def close(self):
        pass


def _snap(**kw):
    s = {'time': datetime(2026, 11, 14, 10, 0, 30), 'temperature': None,
         'temp_units': None, 'temp_source': None, 'temp_note': None,
         'pressure': None, 'pressure_units': None, 'pressure_source': None,
         'pressure_error': None}
    s.update(kw)
    return s


# ------------------------------------------------------------ the readers

def test_lakeshore_is_read_with_the_same_query_as_the_scan():
    rm = _FakeRM({"KRDG? A": "+77.3000"})
    t, units, src, err = v2.read_temperature_snapshot(
        rm, {"Lakeshore 350": "GPIB0::12::INSTR", "Cryocon 34": "GPIB0::23::INSTR"})
    assert (t, units, err) == (77.3, 'K', None)
    assert "Lakeshore 350" in src
    assert rm.opened == ["GPIB0::12::INSTR"] and rm.sent == ["KRDG? A"]


def test_cryocon_reports_its_own_units():
    rm = _FakeRM({"INPUT? A": "295.10", "INPUT A:UNITS?": "K"})
    t, units, _src, err = v2.read_temperature_snapshot(
        rm, {"Cryocon 34": "GPIB0::23::INSTR"})
    assert (t, units, err) == (295.1, 'K', None)
    # Queries only -- the logger never configures anything.
    assert all(cmd.endswith("?") or "?" in cmd for cmd in rm.sent)


def test_no_controller_means_no_bus_traffic():
    rm = _FakeRM({})
    t, _u, _s, err = v2.read_temperature_snapshot(rm, {"Lakeshore 350": None})
    assert t is None and "no temperature controller" in err
    assert rm.opened == []


def test_temperature_is_skipped_when_asked_and_says_why():
    class _VisaStub:
        @staticmethod
        def ResourceManager():
            return _FakeRM({"KRDG? A": "10.0"})
    old_visa, old_avail = v2._visa, v2.PYVISA_AVAILABLE
    v2._visa, v2.PYVISA_AVAILABLE = (lambda: _VisaStub), True
    try:
        snap = v2.read_logging_snapshot(
            {"Lakeshore 350": "GPIB0::12::INSTR"}, None, read_temperature=False,
            temp_skip_reason="skipped: a measurement is running")
    finally:
        v2._visa, v2.PYVISA_AVAILABLE = old_visa, old_avail
    assert snap['temperature'] is None
    assert snap['temp_note'] == "skipped: a measurement is running"
    assert snap['pressure_error'] == "no gauge configured"


# --------------------------------------------------------------- the file

def test_a_row_writes_nan_for_a_missing_reading_and_stays_ascii():
    started = datetime(2026, 11, 14, 10, 0, 0)
    row = v2.tp_log_row(_snap(temp_note="skipped: a measurement is running",
                              pressure_error="Underrange"), started)
    assert row[1] == "30.0"
    assert row[2] == "NaN" and row[5] == "NaN"
    assert row[7] == "Underrange"
    row = v2.tp_log_row(_snap(temperature=4.2, temp_units='K',
                              temp_source="Lakeshore 350 · Input A",
                              pressure=1.5e-6, pressure_units="mbar"), started)
    assert row[2] == "4.2000" and row[5] == "1.5000E-06" and row[7] == "OK"
    for cell in row:
        cell.encode("ascii")            # cp1252 lab PCs: nothing exotic


def test_the_file_gets_a_header_and_every_row_lands():
    path = os.path.join(tempfile.mkdtemp(), "log.csv")
    started = datetime.now()
    assert v2.start_tp_log_file(path, 60, {"resource": "ASRL3::INSTR", "baud": 9600},
                                started)
    for i in range(3):
        snap = _snap(time=started + timedelta(seconds=60 * i), pressure=1e-3,
                     pressure_units="mbar")
        assert v2.append_tp_log_row(path, v2.tp_log_row(snap, started)) is None
    text = open(path, encoding="utf-8").read()
    text.encode("ascii")
    lines = text.strip().splitlines()
    assert lines[3] == ",".join(v2.TP_LOG_COLUMNS)
    assert len(lines) == 4 + 3
    assert "ASRL3::INSTR" in lines[2]


def test_a_write_failure_is_returned_not_raised():
    bad = os.path.join(tempfile.mkdtemp(), "no_such_dir", "log.csv")
    assert v2.append_tp_log_row(bad, ["x"])


# ------------------------------------------------- long press vs. click

class _Stub:
    def __init__(self, long_fired):
        self._reload_long_fired = long_fired
        self.scans = 0

    def start_scan(self):
        self.scans += 1


def test_a_short_click_still_scans():
    s = _Stub(False)
    v2.PICALauncherV2._on_reload_click(s)
    assert s.scans == 1


def test_the_release_after_a_long_press_does_not_scan():
    s = _Stub(True)
    v2.PICALauncherV2._on_reload_click(s)
    assert s.scans == 0
    # ...and the flag is spent, so the next ordinary click scans again.
    v2.PICALauncherV2._on_reload_click(s)
    assert s.scans == 1


def test_interval_wording():
    f = v2.PICALauncherV2._fmt_interval
    assert f(60.0) == "1 min" and f(300.0) == "5 min"
    assert f(90.0) == "90 s" and f(2.5) == "2.5 s"


def test_the_interval_has_a_floor():
    assert v2.TP_LOG_MIN_INTERVAL_S >= 1


# ------------------------------------------------------- COM3 default

def test_com3_is_the_default_in_both_places_but_not_auto_enabled():
    assert v2.GAUGE_DEFAULT_PORT == "COM3"
    assert v2.normalise_gauge_resource(v2.GAUGE_DEFAULT_PORT) == "ASRL3::INSTR"
    # Still opt-in: no file, no gauge.
    assert v2.load_pressure_gauge(os.path.join(tempfile.mkdtemp(), "x.json")) is None
    src = open(TPG_PATH, encoding="utf-8").read()
    assert 'DEFAULT_PORT = "COM3"' in src
    assert "self.port_cb.set(self.DEFAULT_PORT)" in src


def test_the_gauge_dialog_offers_usb_and_ethernet():
    src = open(V2_PATH, encoding="utf-8").read()
    dialog = src.split("def open_pressure_gauge_dialog", 1)[1].split("\n    def ", 1)[0]
    assert "USB cable" in dialog and "Ethernet" in dialog
    assert "Scan for USB / Serial Ports" in dialog
    assert "Find on LAN" in dialog
    assert "Test connection" in dialog


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {exc!r}")
    print("all passed" if not failures else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
