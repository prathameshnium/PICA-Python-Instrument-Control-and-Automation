"""Regression tests for the three faults seen on the lab bus, 28 Aug 2026.

These replay the actual bus as the GPIB scanner reported it -- a Cryo-con on
GPIB0::12 and a Lakeshore 350 on GPIB1::12, which is the Cryo-con's factory
address -- and drive the real GUI code paths that failed:

  1. VI_ERROR_TMO raised inside viWrite on the very first '*IDN?' of a
     session (the 16:05 traceback), which a second Start press cleared.
  2. 'RUNTIME ERROR in worker thread: NoneType: None' -- the reporting bug
     that destroyed the real exception before anyone could read it.
  3. AttributeError: 'DirectControlGUI' object has no attribute '_poll_stage'
     (the 18:07 traceback), which the earlier GUI test missed because it
     built the window but never pressed Connect.

The existing suite checks the backends. This one presses the buttons, so the
connect-then-poll path is covered end to end.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CONTROL_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                            "T_Control_CC34_DirectControl_GUI.py")
SENSING_PATH = os.path.join(REPO_ROOT, "pica", "cryocon",
                            "T_Sensing_CC34_GUI.py")
SCAN_PATH = os.path.join(REPO_ROOT, "pica", "keysight",
                         "Temprature_Scan_Passive_CC34_E4980A_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


control = _load("cc34_control_lab_bus", CONTROL_PATH)
sensing = _load("cc34_sensing_lab_bus", SENSING_PATH)
dscan = _load("cc34_dscan_lab_bus", SCAN_PATH)

# Exactly what the scanner printed at 18:07 on 28 Aug 2026.
LAB_BUS = {
    "GPIB0::12::INSTR": "Cryocon Model 34, Rev 3.03A",
    "GPIB0::17::INSTR": None,
    "GPIB1::11::INSTR": None,
    "GPIB1::12::INSTR": "LSCI,MODEL350,LSA2FKB/#######,1.7",
    "GPIB1::20::INSTR": None,
    "GPIB1::24::INSTR": None,
    "GPIB1::8::INSTR": None,
}


class FakeVisaTimeout(IOError):
    """Stands in for pyvisa.errors.VisaIOError VI_ERROR_TMO."""

    def __init__(self):
        super().__init__(
            "VI_ERROR_TMO (-1073807339): Timeout expired before operation "
            "completed.")


class FakeInstrument:
    """One address on the fake bus. Records every byte in order."""

    def __init__(self, idn, temps=None, write_timeouts=0):
        self.idn = idn
        self.temps = temps or {"A": "77.350K", "B": "-------",
                               "C": "-------", "D": "-------"}
        self.writes = []
        self.queries = []
        self.closed = False
        self.timeout = None
        # Number of leading queries that die the way viWrite died at 16:05.
        self._write_timeouts = write_timeouts

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        if self._write_timeouts > 0:
            self._write_timeouts -= 1
            raise FakeVisaTimeout()
        self.queries.append(command)
        if self.idn is None:
            raise FakeVisaTimeout()
        cmd = command.strip()
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith("INPUT") and cmd.endswith(":UNITS?"):
            return "K"
        if cmd.startswith("INPUT?"):
            return self.temps.get(cmd.split()[-1].strip(), "-------")
        if cmd.startswith("LOOP") and "OUTPWR?" in cmd:
            return "0.0"
        # Every other status query the panel makes: a plausible scalar is
        # enough, the point here is that the traffic happens at all.
        return "0"

    def close(self):
        self.closed = True

    @property
    def traffic(self):
        return self.writes + self.queries


class FakeBus:
    """A pyvisa stand-in holding the whole lab bus."""

    def __init__(self, write_timeouts_on=None, write_timeouts=0):
        self.instruments = {}
        for addr, idn in LAB_BUS.items():
            self.instruments[addr] = FakeInstrument(
                idn,
                write_timeouts=(write_timeouts
                                if addr == write_timeouts_on else 0))
        self.opened = []

    def list_resources(self):
        return tuple(LAB_BUS)

    def open_resource(self, resource):
        self.opened.append(resource)
        if resource not in self.instruments:
            raise FakeVisaTimeout()
        return self.instruments[resource]

    def close(self):
        pass

    @property
    def cryocon(self):
        return self.instruments["GPIB0::12::INSTR"]

    @property
    def lakeshore(self):
        return self.instruments["GPIB1::12::INSTR"]


class patch_bus:
    """Point a module's pyvisa at a fake bus, and make sleeps free."""

    def __init__(self, module, bus):
        self.module = module
        self.bus = bus

    def __enter__(self):
        self._old_visa = getattr(self.module, "pyvisa", None)
        bus = self.bus

        class _FakeVisa:
            ResourceManager = staticmethod(lambda: bus)

        self.module.pyvisa = _FakeVisa
        # The connect retry waits CRYOCON_RETRY_WAIT_S between attempts and
        # settles for CRYOCON_OPEN_SETTLE_S after opening. Neither needs to
        # cost real seconds here.
        self._old_sleep = self.module.time.sleep
        self.module.time.sleep = lambda *_a, **_k: None
        # A modal dialog would block a headless run forever. Record the
        # calls instead so a test can still assert the user was told.
        self.dialogs = []
        self._old_box = getattr(self.module, "messagebox", None)
        if self._old_box is not None:
            recorder = self.dialogs

            class _FakeBox:
                @staticmethod
                def showerror(title, msg):
                    recorder.append(("error", title, msg))

                @staticmethod
                def showwarning(title, msg):
                    recorder.append(("warning", title, msg))

                @staticmethod
                def showinfo(title, msg):
                    recorder.append(("info", title, msg))

                @staticmethod
                def askyesno(title, msg):
                    recorder.append(("askyesno", title, msg))
                    return False

            self.module.messagebox = _FakeBox
        bus.dialogs = self.dialogs
        return bus

    def __exit__(self, *exc):
        self.module.pyvisa = self._old_visa
        self.module.time.sleep = self._old_sleep
        if self._old_box is not None:
            self.module.messagebox = self._old_box
        return False


def _headless_root():
    """A real Tk root, or None when there is no display."""
    try:
        import tkinter as tk
    except ImportError:
        return None
    try:
        root = tk.Tk()
    except Exception:
        return None
    root.withdraw()
    return root


def _skip(reason):
    try:
        import pytest
        pytest.skip(reason)
    except ImportError:
        print(f"SKIP: {reason}")
    return None


# --------------------------------------------------- fault 3: the 18:07 crash

def test_connecting_then_polling_does_not_raise_the_18_07_attributeerror():
    """Scan, connect and let the panel poll -- the path that crashed.

    _do_connect() ends by calling _start_polling(), which calls _poll_loop()
    immediately. That is where '_poll_stage' did not exist yet.
    """
    root = _headless_root()
    if root is None:
        return _skip("no display for a Tk root")
    try:
        bus = FakeBus()
        with patch_bus(control, bus):
            gui = control.DirectControlGUI(root)
            gui.backend.rm = bus
            gui._scan_visa()

            # The scanner must have picked the Cryo-con by its *IDN?, not by
            # the factory address -- which on this bus is the Lakeshore.
            assert gui.visa_cb.get() == "GPIB0::12::INSTR", gui.visa_cb.get()

            gui._do_connect()
            assert gui.is_connected, "connect did not complete"
            assert gui.polling_active, "polling did not start"

            # A full refresh is POLL_STAGE_COUNT ticks; run two cycles so
            # every stage runs at least twice.
            for _ in range(2 * gui.POLL_STAGE_COUNT):
                gui._poll_loop()

            log = gui.console.get("1.0", "end")
            assert "_poll_stage" not in log, log
            assert "AttributeError" not in log, log
            assert "Traceback" not in log, log
            gui._do_disconnect()
    finally:
        root.destroy()


def test_every_polling_stage_runs_and_a_temperature_reaches_the_panel():
    root = _headless_root()
    if root is None:
        return _skip("no display for a Tk root")
    try:
        bus = FakeBus()
        with patch_bus(control, bus):
            gui = control.DirectControlGUI(root)
            gui.backend.rm = bus
            gui._scan_visa()
            gui._do_connect()

            seen = set()
            real = gui._poll_stage_dispatch

            def spy(stage):
                seen.add(stage)
                return real(stage)

            gui._poll_stage_dispatch = spy
            for _ in range(2 * gui.POLL_STAGE_COUNT):
                gui._poll_loop()

            assert seen == set(range(gui.POLL_STAGE_COUNT)), seen
            shown = gui.status_labels['temp_A'].cget('text')
            assert "77.35" in shown, shown
            # An empty channel must name the condition, not show junk.
            assert gui.status_labels['temp_B'].cget('text') == "no sensor"
            gui._do_disconnect()
    finally:
        root.destroy()


# ------------------------------------ the Lakeshore sitting on GPIB1::12

def test_the_lakeshore_on_the_cryocon_factory_address_is_never_driven():
    """GPIB1::12 is the factory address AND the Lakeshore. Refuse it."""
    bus = FakeBus()
    with patch_bus(control, bus):
        backend = control.Cryocon34Backend()
        backend.rm = bus
        try:
            backend.connect("GPIB1::12::INSTR")
        except ConnectionError as e:
            assert "not a Cryo-con" in str(e), e
        else:
            raise AssertionError("connected to the Lakeshore")
        assert bus.lakeshore.writes == [], bus.lakeshore.writes


def test_the_gui_refuses_the_lakeshore_and_never_starts_polling():
    root = _headless_root()
    if root is None:
        return _skip("no display for a Tk root")
    try:
        bus = FakeBus()
        with patch_bus(control, bus):
            gui = control.DirectControlGUI(root)
            gui.backend.rm = bus
            gui.visa_cb.set("GPIB1::12::INSTR")
            gui._do_connect()
            assert not gui.is_connected
            assert not getattr(gui, 'polling_active', False)
            assert bus.lakeshore.writes == [], bus.lakeshore.writes
    finally:
        root.destroy()


# ------------------------------------- fault 1: VI_ERROR_TMO inside viWrite

def test_a_write_timeout_on_the_first_idn_is_retried_not_fatal():
    """The 16:05 fault: the first '*IDN?' died inside viWrite."""
    for module in (control, sensing, dscan):
        bus = FakeBus(write_timeouts_on="GPIB0::12::INSTR", write_timeouts=1)
        with patch_bus(module, bus):
            link = module.CryoconLink("GPIB0::12::INSTR")
            assert "Cryocon" in link.idn, (module.__name__, link.idn)
            link.close()


def test_a_dead_address_gives_up_after_the_configured_attempts():
    bus = FakeBus(write_timeouts_on="GPIB0::12::INSTR", write_timeouts=99)
    with patch_bus(control, bus):
        try:
            control.CryoconLink("GPIB0::12::INSTR")
        except Exception as e:
            assert not isinstance(e, AttributeError), e
        else:
            raise AssertionError("a permanently dead address connected")
        assert len(bus.opened) <= control.CRYOCON_CONNECT_ATTEMPTS


# ------------------------------ fault 2: 'RUNTIME ERROR ... NoneType: None'

def test_the_worker_traceback_survives_the_trip_to_the_gui_thread():
    """The monitor must print the worker's traceback, not 'NoneType: None'.

    traceback.format_exc() on the GUI thread has no live exception, so it
    renders 'NoneType: None' and the real fault is destroyed. The worker
    formats its own traceback and ships it across the queue instead.
    """
    root = _headless_root()
    if root is None:
        return _skip("no display for a Tk root")
    try:
        import queue as _queue
        import traceback as _tb
        bus = FakeBus()
        with patch_bus(sensing, bus):
            gui = sensing.TempMonitorGUI(root)
            gui.data_queue = _queue.Queue()
            gui.is_running = False

            # Exactly what the worker does when a reading blows up.
            def worker_that_fails():
                try:
                    raise ValueError("could not convert '-------' to float")
                except Exception as e:
                    gui.data_queue.put((e, _tb.format_exc()))

            worker_that_fails()
            gui._process_data_queue()
            log = gui.console_widget.get("1.0", "end")
        assert "NoneType: None" not in log, log
        assert "could not convert" in log, log
        assert "ValueError" in log, log
        # The worker's own frame must be in there -- that is the whole point.
        assert "worker_that_fails" in log, log
    finally:
        root.destroy()


def test_the_dielectric_scan_ships_the_exception_and_its_traceback():
    """Same payload shape in the scan: ('ERROR', exception, traceback)."""
    import queue as _queue
    import traceback as _tb
    q = _queue.Queue()
    try:
        raise ValueError("could not convert '-------' to float")
    except Exception as e:
        q.put(('ERROR', e, _tb.format_exc()))
    tag, exc, tb_text = q.get_nowait()
    assert tag == 'ERROR'
    assert isinstance(exc, Exception)
    assert "NoneType: None" not in tb_text
    assert "ValueError" in tb_text
    # And the module must actually send that three-part payload.
    source = open(SCAN_PATH, encoding="utf-8").read()
    assert "self.data_queue.put(('ERROR', e, traceback.format_exc()))" in source


# ------------------------------------------- the passive modules stay silent

def test_neither_passive_module_can_write_to_the_cryocon():
    for module in (sensing, dscan):
        assert not hasattr(module.CryoconLink, "write"), module.__name__
    assert hasattr(control.CryoconLink, "write")


def test_a_passive_session_leaves_no_writes_on_the_bus():
    """Open, verify the channel, read a temperature, close -- zero writes.

    The monitor verifies in two steps (configure_for_monitoring queries the
    units, probe_channel takes the trial reading); the scan does both in
    verify_channel. Drive whichever the module actually offers.
    """
    for module in (sensing, dscan):
        bus = FakeBus()
        with patch_bus(module, bus):
            backend = module.Cryocon34_Backend("GPIB0::12::INSTR",
                                               channel="A")
            if hasattr(backend, "verify_channel"):
                backend.verify_channel()
            else:
                backend.configure_for_monitoring()
                backend.probe_channel()
            temp = backend.get_temperature()
            backend.close()
        assert abs(temp - 77.350) < 1e-6, (module.__name__, temp)
        assert bus.cryocon.writes == [], (module.__name__,
                                          bus.cryocon.writes)


def test_the_scan_reads_the_heater_without_touching_it():
    """LOOP 1:OUTPWR? is a query. The scan must never own the heater."""
    bus = FakeBus()
    with patch_bus(dscan, bus):
        backend = dscan.Cryocon34_Backend("GPIB0::12::INSTR", channel="A")
        power = backend.get_heater_output()
        backend.close()
    assert power == 0.0, power
    assert bus.cryocon.writes == [], bus.cryocon.writes
    assert any("OUTPWR?" in q for q in bus.cryocon.queries), bus.cryocon.queries


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
