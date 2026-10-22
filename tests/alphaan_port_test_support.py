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
        self.read_termination = None
        self.write_termination = None
        self.send_end = None

    def write(self, command):
        self.traffic.append(("write", command))

    def query(self, command):
        self.traffic.append(("query", command))
        answer = self.answers.get(command, "OK")
        if isinstance(answer, list):
            return answer.pop(0) if answer else "OK"
        return answer

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


def run_common_backend_checks(module, path, dc_bias_allowed=False):
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
