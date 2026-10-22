"""Bus-recovery tests for the VISA edition of the Alpha-AN frequency scan.

Ports of the two fixes proven on the BDS PC hardware on 22 Oct 2026 (32-bit
edition, commit 4e3a6c5):
  * after *RST the Alpha leaves the bus for a while; every write fails with
    "no listeners". *IDN? is repeated on that error ONLY until it answers,
    then 1 s, then MODE=IMP. Any other error is raised at once.
  * a board left not Controller-In-Charge by WinALPHA fails every transfer;
    one Interface Clear reclaims it. Never sent on a healthy board.
Also: the scan prefers address 10 (the Alpha in WinALPHA) when no instrument
identifies itself as an Alpha.

No hardware or VISA backend is needed: the instrument, the resource manager
and VisaIOError are faked. Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol",
                           "Frequency_Scan_AlphaAN_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alpha = _load("alphaan_visa_recovery", MODULE_PATH)

ALPHA_IDN = ("ALPHA Analyzer, Novocontrol Technologies, "
             "6.25_200_40_284_ANB_16012009_G2")
VI_ERROR_TMO = -1073807339


class FakeVisaError(Exception):
    """Shaped like pyvisa.errors.VisaIOError: carries .error_code."""

    def __init__(self, code):
        super().__init__(f"VISA error {code}")
        self.error_code = code


class FakeAlpha:
    """Records commands. A query whose scripted answer is an int raises a
    FakeVisaError with that code; lists are consumed in order."""

    def __init__(self, answers=None):
        self.answers = {k: list(v) if isinstance(v, list) else v
                        for k, v in (answers or {}).items()}
        self.commands = []
        self.closed = False
        self.timeout = 0

    def _answer(self, command):
        answer = self.answers.get(command, "OK")
        if isinstance(answer, list):
            answer = answer.pop(0) if answer else "OK"
        if isinstance(answer, int):
            raise FakeVisaError(answer)
        return answer

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        self.commands.append(command)
        return self._answer(command)

    def read(self):
        return "OK"

    def read_stb(self):
        return 0x41

    def wait_for_srq(self, timeout_ms=25000):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeInterface:
    def __init__(self, log):
        self.log = log

    def send_ifc(self):
        self.log.append("IFC")

    def close(self):
        pass


class FakeRM:
    def __init__(self, devices):
        self.devices = devices
        self.ifc = []
        self.opened = []

    def list_resources(self):
        return tuple(self.devices)

    def open_resource(self, address):
        self.opened.append(address)
        if address.endswith("::INTFC"):
            return FakeInterface(self.ifc)
        return self.devices[address]


def _backend(fake, devices=None):
    backend = alpha.AlphaAN_Backend()
    backend.rm = FakeRM(devices if devices is not None
                        else {"GPIB0::10::INSTR": fake})
    backend.instrument = fake
    return backend


NO_LISTENER = alpha.VI_ERROR_NLISTENERS
NOT_CIC = alpha.VI_ERROR_NCIC


# ------------------------------------------------------- *RST recovery

def test_reset_waits_through_no_listener_then_selects_impedance_mode():
    fake = FakeAlpha({"*IDN?": [NO_LISTENER, NO_LISTENER, ALPHA_IDN]})
    with patch.object(alpha.time, "sleep"):
        _backend(fake)._reset_and_set_impedance_mode()
    assert fake.commands == ["*RST", "*IDN?", "*IDN?", "*IDN?", "MODE=IMP"]


def test_reset_does_not_retry_any_other_error():
    fake = FakeAlpha({"*IDN?": [VI_ERROR_TMO, ALPHA_IDN]})
    with patch.object(alpha.time, "sleep"):
        try:
            _backend(fake)._reset_and_set_impedance_mode()
        except FakeVisaError as exc:
            assert exc.error_code == VI_ERROR_TMO
        else:
            raise AssertionError("a timeout was retried")
    assert fake.commands == ["*RST", "*IDN?"]


def test_reset_gives_up_after_the_deadline_without_sending_mode():
    fake = FakeAlpha({"*IDN?": [NO_LISTENER] * 1000})
    clock = iter(range(0, 10000))
    with patch.object(alpha.time, "sleep"), \
            patch.object(alpha.time, "monotonic", lambda: next(clock)):
        try:
            _backend(fake)._reset_and_set_impedance_mode(timeout_s=5)
        except ConnectionError as exc:
            assert "did not rejoin" in str(exc)
        else:
            raise AssertionError("waited forever")
    assert "MODE=IMP" not in fake.commands


def test_sweep_initialisation_and_calibration_both_use_the_wait():
    params = {
        "frequencies": alpha.FREQUENCIES_115PT_1MHZ, "acv": 1.0, "mtm": 0.5,
        "wire_mode": "2", "geometry_mode": "area", "area_cm2": 1.0,
        "thickness_mm": 1.0, "delay": 0.0,
    }
    for run in (lambda b: b.initialize_instrument(params),
                lambda b: b.run_reference_calibration()):
        fake = FakeAlpha({"*IDN?": [NO_LISTENER, ALPHA_IDN],
                          "INTTYP?": "INTTYP=5 284"})
        with patch.object(alpha.time, "sleep"):
            run(_backend(fake))
        assert fake.commands[:4] == ["*RST", "*IDN?", "*IDN?", "MODE=IMP"]


def test_no_fixed_one_second_sleep_follows_rst_directly():
    with open(MODULE_PATH, encoding="utf-8") as fh:
        source = fh.read()
    assert 'write("*RST")\n        time.sleep(1.0)' not in source
    assert source.count("self._reset_and_set_impedance_mode()") == 2


# --------------------------------------------- Controller-In-Charge

def _connect(fake):
    backend = alpha.AlphaAN_Backend()
    rm = FakeRM({"GPIB0::10::INSTR": fake})
    backend.rm = rm
    return backend, rm


def test_connect_reclaims_a_board_that_is_not_cic_once():
    fake = FakeAlpha({"*IDN?": [NOT_CIC, ALPHA_IDN],
                      "INTTYP?": "INTTYP=5 284"})
    backend, rm = _connect(fake)
    backend.connect("GPIB0::10::INSTR")
    assert rm.ifc == ["IFC"]
    assert "GPIB0::INTFC" in rm.opened
    assert backend.instrument is fake


def test_connect_on_a_healthy_board_sends_no_interface_clear():
    fake = FakeAlpha({"*IDN?": ALPHA_IDN, "INTTYP?": "INTTYP=5 284"})
    backend, rm = _connect(fake)
    backend.connect("GPIB0::10::INSTR")
    assert rm.ifc == []
    assert "GPIB0::INTFC" not in rm.opened


def test_connect_still_not_cic_says_to_close_winalpha_and_releases():
    fake = FakeAlpha({"*IDN?": [NOT_CIC, NOT_CIC]})
    backend, rm = _connect(fake)
    try:
        backend.connect("GPIB0::10::INSTR")
    except ConnectionError as exc:
        assert "WinALPHA" in str(exc)
    else:
        raise AssertionError("a not-CIC board was accepted")
    assert rm.ifc == ["IFC"]
    assert fake.closed and backend.instrument is None


def test_connect_does_not_clear_the_interface_on_other_errors():
    fake = FakeAlpha({"*IDN?": [VI_ERROR_TMO]})
    backend, rm = _connect(fake)
    try:
        backend.connect("GPIB0::10::INSTR")
    except FakeVisaError:
        pass
    assert rm.ifc == []


def test_gpib_board_of():
    assert alpha.gpib_board_of("GPIB0::10::INSTR") == 0
    assert alpha.gpib_board_of("GPIB1::24::INSTR") == 1
    assert alpha.gpib_board_of("GPIB::10") == 0
    assert alpha.gpib_board_of("ASRL3::INSTR") is None


# ------------------------------------------------------------ scan

class _Combo(dict):
    def set(self, value):
        self["selected"] = value


def _bare_gui(rm):
    gui = alpha.AlphaAN_FreqScan_GUI.__new__(alpha.AlphaAN_FreqScan_GUI)
    gui.backend = alpha.AlphaAN_Backend()
    gui.backend.rm = rm
    gui.visa_combobox = _Combo()
    gui.logged = []
    gui.log = gui.logged.append
    return gui


def test_scan_falls_back_to_address_10_when_nothing_says_alpha():
    rm = FakeRM({
        "GPIB0::10::INSTR": FakeAlpha({"*IDN?": [VI_ERROR_TMO]}),
        "GPIB0::17::INSTR": FakeAlpha({"*IDN?": "Keysight,E4991B,X,A.01"}),
    })
    gui = _bare_gui(rm)
    with patch.object(alpha, "PYVISA_AVAILABLE", True):
        gui._scan_for_visa()
    assert gui.visa_combobox["selected"].startswith("GPIB0::10::INSTR")


def test_scan_prefers_the_instrument_that_identifies_as_alpha():
    rm = FakeRM({
        "GPIB0::10::INSTR": FakeAlpha({"*IDN?": ALPHA_IDN}),
        "GPIB0::17::INSTR": FakeAlpha({"*IDN?": "Keysight,E4991B,X,A.01"}),
    })
    gui = _bare_gui(rm)
    with patch.object(alpha, "PYVISA_AVAILABLE", True):
        gui._scan_for_visa()
    assert gui.visa_combobox["selected"].startswith("GPIB0::10::INSTR")
    assert rm.ifc == []


def test_scan_clears_a_not_cic_board_once_and_finds_the_alpha():
    rm = FakeRM({
        "GPIB0::10::INSTR": FakeAlpha({"*IDN?": [NOT_CIC, ALPHA_IDN]}),
        "GPIB0::17::INSTR": FakeAlpha({"*IDN?": "Keysight,E4991B"}),
    })
    gui = _bare_gui(rm)
    with patch.object(alpha, "PYVISA_AVAILABLE", True):
        gui._scan_for_visa()
    assert rm.ifc == ["IFC"], "Interface Clear must be sent once per board"
    assert ALPHA_IDN in gui.visa_combobox["selected"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
