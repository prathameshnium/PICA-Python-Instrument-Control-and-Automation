"""Tests for the raw NI-488.2 transport in the 32-bit Alpha-AN Fscan edition.

No hardware, no driver DLL and no 32-bit interpreter are needed: the session
is exercised against a fake driver that records every ib* call, so the
framing, the timeout rounding and the SRQ handshake are all checked on an
ordinary machine.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol",
                           "Frequency_Scan_AlphaAN_32bit_GUI.py")
VISA_EDITION_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol",
                                 "Frequency_Scan_AlphaAN_GUI.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alpha = _load("alphaan_32bit", MODULE_PATH)


class FakeDriver:
    """Stand-in for RawGpib. Records calls; replies with canned data."""

    def __init__(self, reply=b"OK\x00\x10", wait_sta=None):
        self.calls = []
        self.reply = reply
        self.wait_sta = (alpha.RQS | alpha.CMPL if wait_sta is None
                         else wait_sta)
        self.status_byte = 0x41
        self._last_count = 0

    def open_device(self, board, pad, timeout_code):
        self.calls.append(("ibdev", board, pad, timeout_code))
        return 7

    def ibonl(self, handle, online):
        self.calls.append(("ibonl", handle, online))
        return alpha.CMPL

    def ibwrt(self, handle, payload, length):
        self.calls.append(("ibwrt", payload.decode("ascii")))
        return alpha.CMPL

    def ibrd(self, handle, buffer, max_len):
        buffer.raw = self.reply + b"\x00" * (max_len - len(self.reply))
        self._last_count = len(self.reply)
        self.calls.append(("ibrd",))
        return alpha.CMPL | alpha.END

    def ibtmo(self, handle, code):
        self.calls.append(("ibtmo", code))
        return alpha.CMPL

    def ibwait(self, handle, mask):
        self.calls.append(("ibwait", mask))
        return self.wait_sta

    def ibrsp(self, handle, status_ref):
        self.calls.append(("ibrsp",))
        status_ref._obj.value = bytes([self.status_byte])
        return alpha.CMPL

    def count(self):
        return self._last_count

    def error_code(self):
        return 0


def _session(**kwargs):
    driver = FakeDriver(**kwargs)
    return driver, alpha.AlphaGpibSession(driver, 0, 5)


# --------------------------------------------------------------- addresses

def test_parse_gpib_address_accepts_the_visa_spelling():
    assert alpha.parse_gpib_address("GPIB0::5::INSTR") == (0, 5)
    assert alpha.parse_gpib_address("GPIB1::24::INSTR") == (1, 24)
    assert alpha.parse_gpib_address("gpib0::5") == (0, 5)
    assert alpha.parse_gpib_address("5") == (0, 5)


def test_parse_gpib_address_rejects_nonsense():
    for bad in ("", "GPIB0::INSTR", "GPIB0::0::INSTR", "GPIB0::31::INSTR"):
        try:
            alpha.parse_gpib_address(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


# ----------------------------------------------------------------- timeout

def test_timeout_code_always_rounds_up():
    # 60 s has no code; rounding down onto T30s would abort slow low-frequency
    # points, so it must land on T100s (code 15).
    assert alpha._timeout_code(60) == 15
    assert alpha._timeout_code(30) == 14
    assert alpha._timeout_code(0.05) == 10
    assert alpha._timeout_code(5000) == 17


def test_session_opens_with_the_rounded_up_timeout():
    driver, session = _session()
    assert ("ibdev", 0, 5, 15) in driver.calls    # 60 s default -> T100s
    assert session.resource_name == "GPIB0::5::INSTR"


def test_setting_timeout_installs_a_code():
    driver, session = _session()
    session.timeout = 3000
    assert ("ibtmo", 12) in driver.calls          # 3 s
    assert session.timeout == 3000


# --------------------------------------------------------------- transfers

def test_query_writes_then_reads_and_strips_alpha_padding():
    driver, session = _session(reply=b"  NOVOCONTROL,ALPHA\r\n\x00\x10")
    assert session.query("*IDN?") == "NOVOCONTROL,ALPHA"
    assert [c[0] for c in driver.calls if c[0] in ("ibwrt", "ibrd")] == \
        ["ibwrt", "ibrd"]
    assert ("ibwrt", "*IDN?") in driver.calls


def test_write_sends_ascii_without_a_terminator():
    driver, session = _session()
    session.write("ACV=1.0")
    sent = [c[1] for c in driver.calls if c[0] == "ibwrt"]
    assert sent == ["ACV=1.0"]
    assert not sent[0].endswith("\n")


def test_termination_attributes_exist_for_source_parity():
    _driver, session = _session()
    assert session.read_termination == ""
    assert session.write_termination == ""
    assert session.send_end is True


def test_read_stb_returns_the_status_byte():
    driver, session = _session()
    driver.status_byte = 0x41
    assert session.read_stb() == 0x41


def test_close_takes_the_device_offline_once():
    driver, session = _session()
    session.close()
    session.close()
    assert [c for c in driver.calls if c[0] == "ibonl"] == [("ibonl", 7, 0)]


# --------------------------------------------------------------------- SRQ

def test_wait_for_srq_returns_when_rqs_is_set():
    driver, session = _session()
    session.wait_for_srq(120000)
    assert ("ibwait", alpha.RQS | alpha.TIMO) in driver.calls


def test_wait_for_srq_raises_timeout_error_when_it_times_out():
    driver, session = _session(wait_sta=alpha.TIMO | alpha.ERR)
    try:
        session.wait_for_srq(1000)
    except TimeoutError:
        pass
    else:
        raise AssertionError("a timed-out SRQ wait must raise TimeoutError")


def test_wait_for_srq_restores_the_working_timeout():
    driver, session = _session()
    session.timeout = 60000
    driver.calls.clear()
    session.wait_for_srq(120000)
    codes = [c[1] for c in driver.calls if c[0] == "ibtmo"]
    # Install the SRQ window (120 s -> T300s), then put 60 s (T100s) back.
    assert codes == [16, 15]


class _FakeClock:
    """Replaces time.monotonic; each ibwait timeout advances it fully."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _wait_with_fake_clock(wait_ms, wait_sta):
    driver, session = _session(wait_sta=wait_sta)
    clock = _FakeClock()
    seconds_of = {code: limit for limit, code in alpha.TIMEOUT_CODES}
    real_ibwait = driver.ibwait
    installed = []

    def ibtmo(handle, code):
        installed.append(code)
        driver.calls.append(("ibtmo", code))
        return alpha.CMPL

    def ibwait(handle, mask):
        clock.now += seconds_of[installed[-1]]
        return real_ibwait(handle, mask)

    driver.ibtmo, driver.ibwait = ibtmo, ibwait
    real_monotonic = alpha.time.monotonic
    alpha.time.monotonic = clock
    try:
        session.wait_for_srq(wait_ms)
        raised = False
    except TimeoutError:
        raised = True
    finally:
        alpha.time.monotonic = real_monotonic
    waits = sum(1 for c in driver.calls if c[0] == "ibwait")
    return raised, waits, clock.now


def test_a_wait_longer_than_t1000s_is_split_and_not_cut_short():
    # T1000s is the longest NI-488.2 code; a 1e-4 Hz point needs far more.
    raised, waits, elapsed = _wait_with_fake_clock(
        2500 * 1000, alpha.TIMO | alpha.ERR)
    assert raised
    assert waits == 3
    assert elapsed >= 2500


def test_a_split_wait_returns_on_the_first_srq():
    raised, waits, _ = _wait_with_fake_clock(
        2500 * 1000, alpha.RQS | alpha.CMPL)
    assert not raised
    assert waits == 1


# ------------------------------------------------------- waiting after *RST

class _ResettingAlpha:
    """Fails *IDN? with ENOL `misses` times, as the Alpha does after *RST."""

    def __init__(self, misses, iberr=2):
        self.misses = misses
        self.iberr = iberr
        self.commands = []

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        self.commands.append(command)
        if command == "*IDN?" and self.misses:
            self.misses -= 1
            raise alpha.GpibError("ENOL", iberr=self.iberr)
        return "OK" if command != "*IDN?" else "ALPHA Analyzer"


def _backend_with(inst):
    backend = alpha.AlphaAN_Backend.__new__(alpha.AlphaAN_Backend)
    backend.instrument = inst
    return backend


def test_reset_settles_then_waits_through_no_listener():
    inst = _ResettingAlpha(misses=3)
    sleeps = []
    old_sleep = alpha.time.sleep
    alpha.time.sleep = sleeps.append
    try:
        _backend_with(inst)._reset_and_set_impedance_mode()
    finally:
        alpha.time.sleep = old_sleep
    assert inst.commands == ["*RST"] + ["*IDN?"] * 4 + ["MODE=IMP"]
    assert sleeps[0] == alpha.RESET_SETTLE_S >= 2.0
    assert alpha.RESET_READY_TIMEOUT_S >= 60.0


def test_connect_idn_waits_for_an_alpha_still_resetting():
    inst = _ResettingAlpha(misses=2)
    old_sleep = alpha.time.sleep
    alpha.time.sleep = lambda s: None
    try:
        idn = _backend_with(inst)._query_idn_until_answered(
            alpha.CONNECT_READY_TIMEOUT_S, "while connecting")
    finally:
        alpha.time.sleep = old_sleep
    assert idn == "ALPHA Analyzer"
    assert inst.commands == ["*IDN?"] * 3


def test_idn_wait_raises_any_other_error_at_once():
    inst = _ResettingAlpha(misses=5, iberr=6)    # EABO, not ENOL
    try:
        _backend_with(inst)._query_idn_until_answered(15.0, "while connecting")
    except alpha.GpibError as exc:
        assert exc.iberr == 6
    else:
        raise AssertionError("a non-ENOL error was retried")
    assert inst.commands == ["*IDN?"]


# ------------------------------------------------------------- the editions

def test_the_32bit_edition_never_imports_pyvisa():
    with open(MODULE_PATH, encoding="utf-8") as handle:
        source = handle.read()
    assert "import pyvisa" not in source
    # The name may appear in the header prose explaining why VISA is absent,
    # but never as code the module would run.
    assert not hasattr(alpha, "pyvisa")
    assert not hasattr(alpha, "PYVISA_AVAILABLE")
    assert hasattr(alpha, "GPIB_AVAILABLE")


def test_the_visa_edition_is_left_alone():
    """The two editions are separate files precisely so this stays true."""
    with open(VISA_EDITION_PATH, encoding="utf-8") as handle:
        source = handle.read()
    assert "import pyvisa" in source
    assert "ctypes" not in source


def test_both_editions_share_the_safety_ceilings():
    with open(MODULE_PATH, encoding="utf-8") as handle:
        raw_source = handle.read()
    with open(VISA_EDITION_PATH, encoding="utf-8") as handle:
        visa_source = handle.read()
    for marker in ("FREQ_HW_MIN, FREQ_HW_MAX = 3e-5, 20e6",
                   "ZG4_INTERFACE_CODE"):
        assert marker in raw_source and marker in visa_source


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"ok   {name}")
    sys.exit(1 if failures else 0)
