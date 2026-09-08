"""
Purpose: Hardware-free test of the E4980A open/short correction module
(pica/keysight/Correction_OpenShort_E4980A_GUI.py): the backend against a
fake SCPI meter, the restorable spot-data file format round trip, the
residual pass/fail judge, and the invariant that the module never sends
*RST or :MMEM commands and sends :SYST:PRES only through system_preset().

Runnable as plain python too:
    python tests/test_correction_openshort_sim.py
"""

import os
import sys
import tempfile

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import Correction_OpenShort_E4980A_GUI as m  # noqa: E402


# ------------------------------------------------------------------
# Fake E4980A: enough SCPI to drive the backend
# ------------------------------------------------------------------
class FakeE4980A:
    def __init__(self):
        self.timeout = 15000
        self.read_termination = "\n"
        self.write_termination = "\n"
        self.sent = []
        self.open_on = True
        self.short_on = False
        self.load_on = False
        self.length = 1
        self.spot = [0.0] * m.N_SPOT_VALUES
        self.spot_freq = [20.0] * m.N_SPOTS
        self.spot_on = [False] * m.N_SPOTS
        self.func = "CPD"
        self.freq = 1000.0
        self.n_open_exec = 0
        self.n_short_exec = 0
        self.preset_count = 0
        # a pre-existing spot 3 at 1 kHz with data
        self.spot_freq[2] = 1000.0
        self.spot_on[2] = True
        self.spot[12:18] = [1e-9, 2e-9, 0.05, 0.001, 0, 0]
        self._pending = None

    def write(self, cmd):
        self.sent.append(cmd)
        c = cmd.strip()
        u = c.upper()
        if u == ":CORR:OPEN":
            self.n_open_exec += 1
            self.open_on = False   # meter does not auto-enable
        elif u == ":CORR:SHOR":
            self.n_short_exec += 1
        elif u.startswith(":CORR:OPEN:STAT"):
            self.open_on = u.endswith("ON")
        elif u.startswith(":CORR:SHOR:STAT"):
            self.short_on = u.endswith("ON")
        elif u.startswith(":CORR:LENG"):
            self.length = int(u.split()[1])
        elif u.startswith(":CORR:USE:DATA:SING "):
            vals = [float(x) for x in c.split(" ", 1)[1].split(",")]
            assert len(vals) == m.N_SPOT_VALUES
            self.spot = vals
        elif u.startswith(":CORR:SPOT"):
            head, _, arg = c.partition(" ")
            parts = head.split(":")
            n = int(parts[2][4:])
            sub = parts[3].upper() if len(parts) > 3 else ""
            if sub == "FREQ":
                self.spot_freq[n - 1] = float(arg)
            elif sub == "STAT":
                self.spot_on[n - 1] = arg.upper() == "ON"
            elif sub == "OPEN":
                self.spot[(n-1)*6:(n-1)*6+2] = [3e-10, 4e-10]
            elif sub == "SHOR":
                self.spot[(n-1)*6+2:(n-1)*6+4] = [0.02, 0.0005]
        elif u.startswith(":FUNC:IMP ") :
            self.func = u.split()[1]
        elif u.startswith(":FREQ"):
            self.freq = float(u.split()[1])
        elif u == ":SYST:PRES":
            self.preset_count += 1
            self.spot = [0.0] * m.N_SPOT_VALUES
            self.spot_on = [False] * m.N_SPOTS
            self.open_on = self.short_on = False
            self.length = 0
        elif u == "*RST" or u.startswith(":MMEM"):
            raise AssertionError(f"forbidden command sent: {cmd}")

    def query(self, cmd):
        self.sent.append(cmd)
        u = cmd.strip().upper()
        if u == "*IDN?":
            return "Keysight Technologies,E4980A,MY46101234,A.02.10\n"
        if u == "*OPC?":
            return "1\n"
        if u == ":SYST:ERR?":
            return '+0,"No error"\n'
        if u == ":CORR:OPEN:STAT?":
            return ("1" if self.open_on else "0") + "\n"
        if u == ":CORR:SHOR:STAT?":
            return ("1" if self.short_on else "0") + "\n"
        if u == ":CORR:LOAD:STAT?":
            return "0\n"
        if u == ":CORR:LENG?":
            return f"{self.length}\n"
        if u == ":CORR:METH?":
            return "SING\n"
        if u == ":CORR:LOAD:TYPE?":
            return "CPD\n"
        if u == ":VOLT?":
            return "1.0\n"
        if u == ":AMPL:ALC?":
            return "1\n"
        if u == ":CORR:USE:DATA:SING?":
            return ",".join(f"{v:.9g}" for v in self.spot) + "\n"
        if u.startswith(":CORR:SPOT") and u.endswith(":FREQ?"):
            n = int(u.split(":")[2][4:])
            return f"{self.spot_freq[n-1]}\n"
        if u.startswith(":CORR:SPOT") and u.endswith(":STAT?"):
            n = int(u.split(":")[2][4:])
            return ("1" if self.spot_on[n-1] else "0") + "\n"
        if u == ":FETC?":
            corr = self.open_on and self.short_on
            if self.func == "CPG":
                return (f"{5e-14 if corr else 2e-10:.6g},{2e-7 if corr else 3e-6:.6g},0\n")
            return (f"{1e-9 if corr else 5e-7:.6g},{0.03 if corr else 0.8:.6g},0\n")
        raise AssertionError(f"unexpected query: {cmd}")

    def close(self):
        pass


class FakeRM:
    def __init__(self, inst):
        self.inst = inst

    def open_resource(self, addr):
        return self.inst


def make_backend():
    fake = FakeE4980A()
    be = m.E4980A_CorrectionBackend()
    be.rm = FakeRM(fake)
    m.PYVISA_AVAILABLE = True
    be.connect("FAKE::E4980A")
    return be, fake


# ------------------------------------------------------------------
def test_inspect_is_read_only_and_finds_existing_spot():
    be, fake = make_backend()
    n_writes_before = sum(1 for c in fake.sent if not c.strip().endswith("?"))
    st = be.read_state()
    n_writes_after = sum(1 for c in fake.sent if not c.strip().endswith("?"))
    assert n_writes_after == n_writes_before, "inspect must not write"
    assert st["length_m"] == 1 and st["open_on"] and not st["short_on"]
    assert 3 in st["spots"] and st["spots"][3]["on"] and st["spots"][3]["freq"] == 1000.0
    assert len(st["spot_data"]) == m.N_SPOT_VALUES


def test_spot_file_round_trip(tmp_path=None):
    be, fake = make_backend()
    st = be.read_state()
    text = m.format_spot_file(st, {"file": "backup_before", "visa": "FAKE"})
    meta, values, spots = m.parse_spot_file(text)
    assert meta["length_m"] == "1" and meta["open_on"] == "True"
    assert values == st["spot_data"]
    assert spots[3]["freq"] == 1000.0 and spots[3]["on"]
    # restore path writes exactly those values back
    fake.spot = [0.0] * m.N_SPOT_VALUES
    be.write_spot_data(values)
    assert fake.spot == values


def test_all_points_execute_overwrites_and_records_moment():
    be, fake = make_backend()
    errs = be.execute_all_points("OPEN")
    assert errs == [] and fake.n_open_exec == 1
    be.execute_all_points("SHOR")
    assert fake.n_short_exec == 1
    kinds = [k for (_, k, c, _) in be.record if c == ":CORR:OPEN"]
    assert kinds == ["W"], "the write moment must be in the command record"
    be.set_switches(True, True)
    assert fake.open_on and fake.short_on


def test_spot_opt_in_and_never_forbidden_commands():
    be, fake = make_backend()
    for i, f in enumerate(m.TSCAN_FREQS_HZ[:3], start=1):
        be.setup_spot(i, f, on=True)
        be.execute_spot(i, "OPEN")
        be.execute_spot(i, "SHOR")
    assert fake.spot_on[0] and fake.spot_freq[2] == m.TSCAN_FREQS_HZ[2]
    assert fake.spot[0] == 3e-10 and fake.spot[2] == 0.02
    assert not any(c.strip().upper() == "*RST" or c.strip().upper().startswith(":MMEM")
                   for c in fake.sent)
    assert fake.preset_count == 0


def test_measure_and_judge():
    be, fake = make_backend()
    be.set_switches(True, True)
    cp, g, _ = be.measure("CPG", 1e5)
    ls, rs, _ = be.measure("LSRS", 1e5)
    rows = [{"f": 1e5, "open_cp_on": cp, "open_g_on": g, "short_rs_on": rs, "short_ls_on": ls}]
    ok, worst = m.judge_residuals(rows, m.DEFAULT_THRESH)
    assert ok, worst
    rows[0]["open_cp_on"] = 0.5e-12
    ok, worst = m.judge_residuals(rows, m.DEFAULT_THRESH)
    assert not ok and "open Cp" in worst


def test_preset_only_via_named_method():
    be, fake = make_backend()
    be.system_preset()
    assert fake.preset_count == 1 and fake.length == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
    print("all passed")
