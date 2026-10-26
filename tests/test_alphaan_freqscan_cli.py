"""Alpha-AN headless Fscan (AlphaAN_FreqScan_Instrument_Control.py).

The CLI keeps its own copy of the GUI Fscan's WinDETA layout and list
builder (PICA modules are self-contained). These tests pin the copy to the
GUI value for value, read the 11 WinDETA columns back by header name
against a parallel RC, and drive main() against a fake Alpha to check the
file it writes. No hardware, VISA backend or Tk window is needed.
"""

import importlib.util
import math
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol",
                        "Frequency_Scan_AlphaAN_GUI.py")
CLI_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol", "Instrument_Control",
                        "AlphaAN_FreqScan_Instrument_Control.py")

EPS0 = 8.8541878128e-12           # F/m
FREQS = (20.0, 1e3, 1e5, 1e7)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gui = _load("alpha_fscan_gui_for_cli_test", GUI_PATH)
cli = _load("alpha_fscan_cli_under_test", CLI_PATH)


def _close(a, b, rel=1e-9):
    return math.isclose(a, b, rel_tol=rel)


def _parallel_rc(f, rp, cp):
    """Series R, X of a resistor rp in parallel with a capacitor cp."""
    z = 1.0 / complex(1.0 / rp, 2 * math.pi * f * cp)
    return z.real, z.imag


def _columns(row):
    names = [h.strip().split(" [")[0] for h in cli.WINDETA_HEADER.split("\t")]
    return dict(zip(names, row))


# --- layout -----------------------------------------------------------------

def test_the_windeta_header_is_the_gui_fscan_header():
    assert cli.WINDETA_HEADER == gui.WINDETA_HEADER
    assert len(cli.WINDETA_HEADER.split("\t")) == 11
    assert cli.WINDETA_HEADER.isascii()


def test_the_cli_writes_no_pica_dat_layout():
    # The CLI has only ever written the WinDETA .txt; no .dat was added.
    assert not hasattr(cli, "PICA_HEADER")
    assert ".dat" not in open(CLI_PATH, encoding="utf-8").read().split(
        "def main", 1)[1]


def test_the_columns_read_back_a_parallel_rc_by_header_name():
    rp, cp = 1e6, 100e-12
    c0 = cli.compute_c0(10.0, 1.0)
    for f in FREQS:
        w = 2 * math.pi * f
        R, X = _parallel_rc(f, rp, cp)
        row = cli.windeta_row(cli.impedance_to_dielectric(R, X, f, c0),
                              cli.loss_phase_cp_g(R, X, f))
        assert len(row) == 11, f
        c = _columns(row)
        assert c["Freq."] == f
        assert _close(c["Eps'"], cp / c0), f
        assert _close(c["Eps''"], 1 / (rp * w * c0)), f
        assert _close(c["Tan(Delta)"], 1 / (w * rp * cp)), f
        assert _close(c["Tan(Delta)"], c["Eps''"] / c["Eps'"]), f
        assert _close(c["Tan(Delta)"], -R / X), f
        assert _close(c["Phi"], math.degrees(math.atan2(X, R))), f
        assert -90.0 < c["Phi"] < 0.0, f          # capacitive: negative
        assert _close(c["Zp'"], rp), f
        assert _close(c["Zp''"], 1 / (w * cp)), f
        eps_sq = c["Eps'"] ** 2 + c["Eps''"] ** 2
        assert _close(c["Modulus'"], c["Eps'"] / eps_sq), f
        assert _close(c["Modulus''"], c["Eps''"] / eps_sq), f
        assert _close(c["Sig'"], w * EPS0 / 100 * c["Eps''"]), f
        assert _close(c["Sig''"], -w * EPS0 / 100 * (c["Eps'"] - 1)), f


def test_the_row_matches_the_gui_fscan_value_for_value():
    c0 = cli.compute_c0(10.0, 1.0)
    for R, X in ((1e3, -2e3), (37.0, -5.0), (50.0, 0.0), (10.0, 4.0)):
        for f in FREQS:
            extra = cli.loss_phase_cp_g(R, X, f)
            assert extra == gui.loss_phase_cp_g(R, X, f), (R, X, f)
            row = cli.impedance_to_dielectric(R, X, f, c0)
            want = gui.file_rows(row, extra, gui.derived_columns(f, R, X))[0]
            assert cli.windeta_row(row, extra) == want, (R, X, f)


# --- frequency list ---------------------------------------------------------

def test_the_list_builder_matches_the_gui_fscan():
    for args in ((20, 3e6, 1.4), (20, 1e7, 1.4), (20, 1e6, 1.1),
                 (3e6, 1e6, 1.4)):
        assert cli.build_log_frequency_list(*args) == \
            gui.build_log_frequency_list(*args), args


def test_the_default_list_is_the_gui_default_start_end_factor():
    assert (cli.START_HZ, cli.END_HZ, cli.FACTOR) == tuple(
        float(v) for v in gui.LOG_FREQ_DEFAULTS)
    assert cli.FREQUENCIES_HZ == gui.build_log_frequency_list(20, 3e6, 1.4)
    assert len(cli.FREQUENCIES_HZ) == 37
    assert cli.FREQUENCIES_HZ[0] == 20.0 and cli.FREQUENCIES_HZ[-1] == 3e6


def test_the_list_builder_refuses_bad_input():
    for args in ((20, 3e6, 1.0), (20, 30e6, 1.4), (20, 20, 1.4),
                 (float("nan"), 3e6, 1.4)):
        try:
            cli.build_log_frequency_list(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {args}")


def test_validate_uses_the_list_it_is_given():
    cli.validate(3.0, 0.5, "2", 10.0, 1.0)            # 3 MHz: 3 Vrms is fine
    hi = cli.build_log_frequency_list(20, 1e7, 1.4)   # reaches the 2 V band
    for bad in ((3.0, hi), (1.0, ())):
        try:
            cli.validate(bad[0], 0.5, "2", 10.0, 1.0, bad[1])
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad[0]} V on {len(bad[1])} pts")


# --- main() against a fake Alpha ------------------------------------------

class _FakeAlpha:
    """Answers the CLI's handshake, acks and ZRE? for a parallel RC."""

    def __init__(self, rp=1e6, cp=100e-12):
        self.rp, self.cp = rp, cp
        self.freq = None
        self.sent = []
        self.timeout = 60000

    def query(self, cmd):
        self.sent.append(cmd)
        if cmd == "*IDN?":
            return "ALPHA Analyzer, Novocontrol Technologies, test"
        if cmd == "INTTYP?":
            return "INTTYP=5 0"
        if cmd == "ZRE?":
            R, X = _parallel_rc(self.freq, self.rp, self.cp)
            return f"ZRE={R:.9e} {X:.9e} {self.freq:.9e} 2 1\x00\x10"
        if cmd.startswith("GFR="):
            self.freq = float(cmd[4:])
        return "OK"

    def write(self, cmd):
        self.sent.append(cmd)

    def wait_for_srq(self, timeout_ms):
        pass

    def read_stb(self):
        return 0x41

    def clear(self):
        pass

    def close(self):
        pass


class _FakeRM:
    def __init__(self, inst):
        self.inst = inst

    def open_resource(self, address):
        return self.inst


def test_main_writes_the_11_column_file_for_the_requested_list():
    tmp = tempfile.mkdtemp()
    fake = _FakeAlpha()
    old = (cli.pyvisa.ResourceManager, sys.argv, cli.time.sleep)
    cli.pyvisa.ResourceManager = lambda *a, **k: _FakeRM(fake)
    cli.time.sleep = lambda s: None
    sys.argv = ["cli", "--diameter", "10", "--thickness", "1",
                "--path", tmp, "--filename", "fake", "--delay", "0",
                "--start", "100", "--end", "1e5", "--factor", "10"]
    try:
        cli.main()
        (name,) = os.listdir(tmp)
        with open(os.path.join(tmp, name), "rb") as fh:
            raw = fh.read()
    finally:
        cli.pyvisa.ResourceManager, sys.argv, cli.time.sleep = old
        shutil.rmtree(tmp, ignore_errors=True)

    assert b"\r\n" in raw and b"\r\r" not in raw            # CRLF, once
    lines = raw.decode("utf-8").split("\r\n")
    assert lines[2] == cli.WINDETA_HEADER
    data = [l for l in lines[3:] if l]
    assert [float(l.split("\t")[0]) for l in data] == [100.0, 1e3, 1e4, 1e5]
    gfr = [c for c in fake.sent if c.startswith("GFR=")]
    assert len(gfr) == 4
    assert not any(c.startswith(("DCV", "DCE")) for c in fake.sent)
    c0 = cli.compute_c0(10.0, 1.0)
    for line in data:
        cells = [float(v) for v in line.split("\t")]
        assert len(cells) == 11
        c = _columns(cells)
        w = 2 * math.pi * c["Freq."]
        assert _close(c["Eps'"], 100e-12 / c0, rel=1e-4)
        assert _close(c["Tan(Delta)"], 1 / (w * 1e6 * 100e-12), rel=1e-4)
        assert c["Phi"] < 0


if __name__ == "__main__":
    failed = 0
    for test_name, fn in sorted(globals().items()):
        if test_name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {test_name}")
            except Exception as exc:  # report every failure, then exit non-zero
                failed += 1
                print(f"FAIL  {test_name}: {exc!r}")
    sys.exit(1 if failed else 0)
