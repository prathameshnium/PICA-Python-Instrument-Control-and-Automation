"""Shared fakes and checks for the Alpha-AN ports of the E4980A modules.

Every port carries its own copy of the Alpha-AN backend (PICA modules are
self-contained), so the same safety rules are asserted against each copy:
read-every-ack, MST written directly, the ZG4 AC ceiling, DC bias never
sent unless the module is a C-V module, and the safe-state order on close.

No hardware, VISA backend or Tk window is needed.
"""

import ast
import importlib.util
import os
import sys
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

NOVO_DIR = os.path.join(REPO_ROOT, "pica", "novocontrol")


def load_port(file_name, subdir=""):
    """Import a novocontrol module by file name, without running main()."""
    path = os.path.join(NOVO_DIR, subdir, file_name)
    name = "alpha_port_" + os.path.splitext(file_name)[0].lower()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, path


class FakeAlpha:
    """Records every byte sent. Answers 'OK' to set commands by default.

    ZRE? answers with a valid capacitive point unless told otherwise.
    """

    def __init__(self, answers=None, srq_raises=False):
        self.traffic = []
        self.answers = dict(answers or {})
        self.answers.setdefault("ZRE?", "ZRE=1.0E+05 -2.0E+06 1.0E+03 2 1")
        self.answers.setdefault("INTTYP?", "INTTYP=5 284")
        self.answers.setdefault("*IDN?", "ALPHA Analyzer, Novocontrol "
                                         "Technologies, 6.25_200_40_284")
        self.srq_raises = srq_raises
        self.closed = False
        self.timeout = 0
        self.idn_timeouts = []
        self.read_termination = None
        self.write_termination = None
        self.send_end = None

    def write(self, command):
        self.traffic.append(("write", command))

    def query(self, command):
        self.traffic.append(("query", command))
        if command == "*IDN?":
            self.idn_timeouts.append(self.timeout)
        answer = self.answers.get(command, "OK")
        if isinstance(answer, list):
            answer = answer.pop(0) if answer else "OK"
        if isinstance(answer, Exception):
            raise answer
        return answer

    def clear(self):
        self.traffic.append(("clear", None))

    def read(self):
        self.traffic.append(("read", None))
        return self.answers.get("__read__", "OK")

    def read_stb(self):
        return 0x41

    def wait_for_srq(self, timeout_ms=25000):
        self.traffic.append(("wait_for_srq", timeout_ms))
        if self.srq_raises:
            raise TimeoutError("no SRQ")

    def close(self):
        self.closed = True

    @property
    def commands(self):
        return [p for k, p in self.traffic if k in ("write", "query")]

    @property
    def clears(self):
        return sum(1 for k, _ in self.traffic if k == "clear")

    def kind_of(self, command):
        for kind, payload in self.traffic:
            if payload == command:
                return kind
        return None


class FakeRM:
    """Stands in for pyvisa.ResourceManager; hands out one FakeAlpha."""

    def __init__(self, fake):
        self.fake = fake
        self.opened = []

    def open_resource(self, address):
        self.opened.append(address)
        return self.fake


def connected_backend(module, fake=None):
    """An AlphaAN_Backend wired to a FakeAlpha, as if connect() succeeded."""
    fake = fake or FakeAlpha()
    backend = module.AlphaAN_Backend.__new__(module.AlphaAN_Backend)
    backend.instrument = fake
    backend.params = {}
    backend.rm = FakeRM(fake)
    backend._task_may_be_running = False
    return backend, fake


# --- checks every port must pass -------------------------------------------

def check_set_commands_are_read_as_acks(module):
    backend, fake = connected_backend(module)
    backend._exec("ACV=0.5")
    assert fake.kind_of("ACV=0.5") == "query"


def check_non_ok_ack_raises(module):
    backend, fake = connected_backend(module, FakeAlpha({"ACV=9": "IP"}))
    try:
        backend._exec("ACV=9")
    except RuntimeError as exc:
        assert "IP" in str(exc)
    else:
        raise AssertionError("a non-OK ack must raise")


def check_mst_written_directly(module):
    backend, fake = connected_backend(module)
    zr, zi, f_act, status, _ref = backend.measure_point(1000.0)
    assert fake.kind_of("MST") == "write"
    assert fake.commands[-1] == "ZRE?"
    assert status == module.STATUS_RESULT_VALID
    assert f_act == 1000.0 and zr == 1.0e5 and zi == -2.0e6


def check_close_parks_generator_first(module):
    backend, fake = connected_backend(module)
    backend._task_may_be_running = True
    backend.close_instrument()
    cmds = fake.commands
    assert cmds[:3] == ["MBK", "ACV=0", "ZCONSPL=0"], cmds
    assert "*RST" in cmds and fake.closed
    assert backend.instrument is None


def check_acv_ceiling(module):
    assert module.acv_ceiling(1e6) == 3.0
    assert module.acv_ceiling(5e6) == 2.0
    assert module.acv_ceiling(15e6) == 1.0


def check_dc_bias_never_sent(path):
    """No write/query/_exec call in the source starts with DCV or DCE."""
    source = open(path, encoding="utf-8").read()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") not in (
                "write", "query", "_exec", "_exec_tolerant"):
            continue
        for arg in node.args:
            value = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
            elif isinstance(arg, ast.JoinedStr):
                value = "".join(v.value for v in arg.values
                                if isinstance(v, ast.Constant))
            if value is not None:
                assert not value.startswith(("DCV", "DCE")), value


def check_source_has_instrument_metadata(path):
    source = open(path, encoding="utf-8").read()
    assert "SN 284" in source and "6.25_200_40_284_ANB_16012009_G2" in source


class FakeVisaError(Exception):
    """Shaped like pyvisa.errors.VisaIOError: carries .error_code."""

    def __init__(self, code):
        super().__init__(f"VISA error {code}")
        self.error_code = code


ALPHA_IDN = "ALPHA Analyzer, Novocontrol Technologies, 6.25_200_40_284"


def check_reset_waits_through_timeout_and_no_listener(module):
    """23 Oct 2026: through 64-bit Keysight VISA the first *IDN? after *RST
    timed out; through NI-488.2 it failed with "no listeners"."""
    fake = FakeAlpha({"*IDN?": [FakeVisaError(module.VI_ERROR_TMO),
                                FakeVisaError(module.VI_ERROR_NLISTENERS),
                                ALPHA_IDN]})
    fake.timeout = 60000
    backend, _ = connected_backend(module, fake)
    sleeps = []
    with patch.object(module.time, "sleep", sleeps.append):
        backend._reset_and_set_impedance_mode()
    assert fake.commands == ["*RST", "*IDN?", "*IDN?", "*IDN?", "MODE=IMP"]
    assert fake.clears == 1, "a device clear follows a timeout only"
    assert fake.idn_timeouts == [module.RESET_POLL_TIMEOUT_MS] * 3
    assert fake.timeout == 60000, "session timeout restored"
    assert sleeps[0] == module.RESET_SETTLE_S >= 2.0
    assert module.RESET_READY_TIMEOUT_S >= 60.0


def check_reset_gives_up_and_restores_the_timeout(module):
    fake = FakeAlpha({"*IDN?": [FakeVisaError(module.VI_ERROR_TMO)] * 1000})
    fake.timeout = 60000
    backend, _ = connected_backend(module, fake)
    clock = iter(range(10000))
    with patch.object(module.time, "sleep"), \
            patch.object(module.time, "monotonic", lambda: next(clock)):
        try:
            backend._reset_and_set_impedance_mode(timeout_s=5)
        except ConnectionError as exc:
            assert "did not rejoin" in str(exc)
        else:
            raise AssertionError("the reset wait never gave up")
    assert "MODE=IMP" not in fake.commands
    assert fake.timeout == 60000


def check_reset_raises_any_other_error_at_once(module):
    fake = FakeAlpha({"*IDN?": [FakeVisaError(-1073807298), ALPHA_IDN]})
    backend, _ = connected_backend(module, fake)
    with patch.object(module.time, "sleep"):
        try:
            backend._reset_and_set_impedance_mode()
        except FakeVisaError:
            pass
        else:
            raise AssertionError("an unexpected error was retried")
    assert fake.commands == ["*RST", "*IDN?"] and fake.clears == 0


def check_connect_waits_for_an_alpha_still_resetting(module):
    """Start pressed a moment after a park (*RST): connect must wait."""
    fake = FakeAlpha({"*IDN?": [FakeVisaError(module.VI_ERROR_TMO),
                                ALPHA_IDN]})
    backend, _ = connected_backend(module, fake)
    backend.instrument = None
    with patch.object(module.time, "sleep"):
        backend.connect("GPIB0::10::INSTR")
    assert backend.instrument is fake
    assert fake.commands[:2] == ["*IDN?", "*IDN?"] and fake.clears == 1
    assert fake.timeout == 60000, "session timeout restored"


def run_common_backend_checks(module, path, dc_bias_allowed=False):
    check_reset_waits_through_timeout_and_no_listener(module)
    check_reset_gives_up_and_restores_the_timeout(module)
    check_reset_raises_any_other_error_at_once(module)
    check_connect_waits_for_an_alpha_still_resetting(module)
    check_set_commands_are_read_as_acks(module)
    check_non_ok_ack_raises(module)
    check_mst_written_directly(module)
    check_close_parks_generator_first(module)
    check_acv_ceiling(module)
    check_source_has_instrument_metadata(path)
    if not dc_bias_allowed:
        check_dc_bias_never_sent(path)


class FakeLakeshore:
    """Minimal temperature-controller stand-in for the combined backends."""

    def __init__(self, temps=(300.0,), heater=12.5):
        self.temps = list(temps)
        self.heater = heater
        self.calls = []

    def get_temperature(self, *args):
        self.calls.append(("get_temperature", args))
        return self.temps.pop(0) if len(self.temps) > 1 else self.temps[0]

    def get_heater_output(self, *args):
        return self.heater

    def set_heater_range(self, *args):
        self.calls.append(("set_heater_range", args))

    def close(self):
        self.calls.append(("close", ()))
