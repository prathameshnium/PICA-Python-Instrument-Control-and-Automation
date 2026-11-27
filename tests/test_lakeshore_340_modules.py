"""
Lake Shore Model 340 ports: T_Sensing_L340_GUI, T_Control_L340_DirectControl_GUI,
T_Control_L340_RangeControl_GUI.

These tests drive the three backends against a fake Model 340 that answers
like the real one (User's Manual, Chapter 9) and refuses the Model 350
command forms that broke the first attempt (RANGE 1,0 / HTR? 1 / MODE 0).
No VISA hardware is needed. Runnable as plain python too.
"""
import importlib
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# A fake Model 340 on the other end of a pyvisa-like session
# ---------------------------------------------------------------------------

class Fake340:
    """Minimal Model 340 command interpreter.

    Raises on the 350-only forms so a port that still sends them fails the
    test instead of silently timing out on the bench.
    """

    IDN = "LSCI,MODEL340,340219,111196"

    def __init__(self, idn=None):
        self.idn = idn or self.IDN
        self.writes = []
        self.range = 3
        self.setp = {1: 300.0, 2: 0.0}
        self.ramp = {1: (0, 0.0), 2: (0, 0.0)}
        self.pid = {1: (50.0, 20.0, 0.0), 2: (50.0, 20.0, 0.0)}
        self.cset = {1: ['A', 1, 0, 0], 2: ['B', 1, 0, 0]}
        self.cmode = {1: 4, 2: 1}
        self.climit = {1: [325.0, 0.0, 0.0, 3, 5], 2: [325.0, 0.0, 0.0, 1, 0]}
        self.mode = 2
        self.temps = {'A': 300.0, 'B': 299.0}
        self.rdgst = {'A': 0, 'B': 0}
        self.htr = 12.5
        self.htrst = 0
        self.mout = {1: 0.0, 2: 0.0}
        self.filters = {}
        self.intype = {'A': [8, 2, 1, 6, 8], 'B': [1, 1, 1, 0, 11]}
        self.incrv = {'A': 21, 'B': 1}
        self.zones = {}
        self.display = [2, 60, 1]
        self.cdisp = [1, 25, 1, 1]     # loops shown, heater ohm, I/P, large
        self.climi = 1.0
        self.dispfld = {}
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.closed = False

    # -- pyvisa-like surface --

    def write(self, cmd):
        self.writes.append(cmd)
        self._exec(cmd)

    def query(self, cmd):
        self.writes.append(cmd)
        return self._exec(cmd)

    def close(self):
        self.closed = True

    # -- interpreter --

    def _exec(self, cmd):
        cmd = cmd.strip()
        name, _, rest = cmd.partition(' ')
        args = [a.strip() for a in rest.split(',')] if rest else []
        name = name.upper()

        if name == '*IDN?':
            return self.idn
        if name in ('*CLS', '*RST', '*OPC'):
            return None
        if name == 'RANGE':
            if len(args) != 1:
                raise AssertionError(f"340 RANGE takes ONE argument: '{cmd}'")
            r = int(args[0])
            assert 0 <= r <= 5, cmd
            self.range = min(r, self.climit[1][4])
            return None
        if name == 'RANGE?':
            assert not args, f"340 RANGE? takes no argument: '{cmd}'"
            return str(self.range)
        if name == 'HTR?':
            assert not args, f"340 HTR? takes no argument: '{cmd}'"
            return f"{self.htr:.1f}"
        if name == 'HTRST?':
            return f"{self.htrst:02d}"
        if name == 'KRDG?':
            return f"{self.temps[args[0]]:+.3E}"
        if name == 'SRDG?':
            return "+1.234E+03"
        if name == 'RDGST?':
            return str(self.rdgst.get(args[0], 0))
        if name == 'SETP':
            self.setp[int(args[0])] = float(args[1])
            return None
        if name == 'SETP?':
            return f"{self.setp[int(args[0])]:+.3E}"
        if name == 'RAMP':
            self.ramp[int(args[0])] = (int(args[1]), float(args[2]))
            return None
        if name == 'RAMP?':
            on, rate = self.ramp[int(args[0])]
            return f"{on},{rate:.1f}"
        if name == 'RAMPST?':
            return "0"
        if name == 'PID':
            self.pid[int(args[0])] = tuple(float(a) for a in args[1:4])
            return None
        if name == 'PID?':
            p, i, d = self.pid[int(args[0])]
            return f"{p:.1f},{i:.1f},{d:.0f}"
        if name == 'CSET':
            loop = int(args[0])
            cur = self.cset[loop]
            if len(args) > 1 and args[1]:
                cur[0] = args[1].upper()
            if len(args) > 2 and args[2]:
                cur[1] = int(args[2])
            if len(args) > 3 and args[3]:
                cur[2] = int(args[3])
            if len(args) > 4 and args[4]:
                cur[3] = int(args[4])
            return None
        if name == 'CSET?':
            return ",".join(str(x) for x in self.cset[int(args[0])])
        if name == 'CMODE':
            m = int(args[1])
            assert 1 <= m <= 6, cmd
            self.cmode[int(args[0])] = m
            return None
        if name == 'CMODE?':
            return str(self.cmode[int(args[0])])
        if name == 'CLIMIT':
            loop = int(args[0])
            vals = self.climit[loop]
            for k, a in enumerate(args[1:6]):
                if a:
                    vals[k] = float(a) if k < 3 else int(float(a))
            return None
        if name == 'CLIMIT?':
            v = self.climit[int(args[0])]
            return f"{v[0]:+.3E},{v[1]:.1f},{v[2]:.1f},{v[3]},{v[4]}"
        if name == 'CDISP':
            assert len(args) == 5 and int(args[0]) == 1, cmd
            self.cdisp = [int(args[1]), int(args[2]), int(args[3]), int(args[4])]
            return None
        if name == 'CDISP?':
            return ",".join(str(x) for x in self.cdisp)
        if name == 'CLIMI':
            assert len(args) == 1 and 0.1 <= float(args[0]) <= 2.0, cmd
            self.climi = float(args[0])
            return None
        if name == 'CLIMI?':
            return f"{self.climi:.3f}"
        if name == 'MODE':
            m = int(args[0])
            assert m in (1, 2, 3), f"340 MODE is 1/2/3, got '{cmd}'"
            self.mode = m
            return None
        if name == 'MODE?':
            return str(self.mode)
        if name == 'MOUT':
            self.mout[int(args[0])] = float(args[1])
            return None
        if name == 'MOUT?':
            return f"{self.mout[int(args[0])]:+.2f}"
        if name == 'FILTER':
            self.filters[args[0]] = args[1:]
            return None
        if name == 'FILTER?':
            return ",".join(self.filters.get(args[0], ['0', '10', '2']))
        if name == 'INTYPE':
            assert len(args) == 2, f"only INTYPE <input>,<type> is safe: '{cmd}'"
            self.intype[args[0]][0] = int(args[1])
            return None
        if name == 'INTYPE?':
            return ",".join(str(x) for x in self.intype[args[0]])
        if name == 'INCRV':
            self.incrv[args[0]] = int(args[1])
            return None
        if name == 'INCRV?':
            return str(self.incrv[args[0]])
        if name == 'ZONE':
            assert len(args) == 8, f"340 ZONE has 8 fields (no input/rate): '{cmd}'"
            self.zones[(int(args[0]), int(args[1]))] = args[2:]
            return None
        if name == 'ZONE?':
            return ",".join(self.zones.get((int(args[0]), int(args[1])),
                                           ['0', '0', '0', '0', '0', '0']))
        if name == 'DISPLAY':
            self.display[0] = int(args[0])
            return None
        if name == 'DISPLAY?':
            return ",".join(str(x) for x in self.display)
        if name == 'DISPFLD':
            self.dispfld[int(args[0])] = (args[1], int(args[2]))
            return None
        if name == 'DISPFLD?':
            inp, src = self.dispfld.get(int(args[0]), ('A', 1))
            return f"{inp},{src}"
        if name == 'CRVHDR?':
            assert len(args) == 1 and 1 <= int(args[0]) <= 60, cmd
            return {1: "DT-470,Standard C,2,+475.000,1",
                    6: "PT-100,STANDARD,3,+800.000,2",
                    21: "CX-1050,X999999,4,+325.000,1"}.get(
                int(args[0]), "               ,          ,0,+000.000,0")
        if name.startswith('CRV'):
            raise AssertionError(f"curve write sent: '{cmd}'")
        raise AssertionError(f"Fake340 does not know '{cmd}' (350-only command?)")


class FakeRM:
    def __init__(self, devices):
        self.devices = devices      # address -> Fake340 or Exception
        self.opened = []

    def list_resources(self):
        return tuple(self.devices.keys())

    def open_resource(self, address):
        self.opened.append(address)
        dev = self.devices[address]
        if isinstance(dev, Exception):
            raise dev
        return dev


def _load(name):
    return importlib.import_module(f"pica.lakeshore.{name}")


@pytest.fixture
def sensing():
    return _load("T_Sensing_L340_GUI")


@pytest.fixture
def direct():
    return _load("T_Control_L340_DirectControl_GUI")


@pytest.fixture
def ramp():
    return _load("T_Control_L340_RangeControl_GUI")


def _make_direct_backend(direct, dev):
    b = direct.Lakeshore340Backend.__new__(direct.Lakeshore340Backend)
    b.lakeshore = None
    b.idn = ""
    b.rm = FakeRM({"GPIB1::12::INSTR": dev})
    return b


def _make_ramp_backend(ramp, dev):
    b = ramp.Lakeshore340_Backend.__new__(ramp.Lakeshore340_Backend)
    b.lakeshore = None
    b.idn = ""
    b.rm = FakeRM({"GPIB1::12::INSTR": dev})
    return b


# ---------------------------------------------------------------------------
# Sensing
# ---------------------------------------------------------------------------

def test_sensing_heater_off_uses_340_range_syntax_and_no_rst(sensing):
    dev = Fake340()
    b = sensing.Lakeshore340_Backend("GPIB1::12::INSTR",
                                     resource_manager=FakeRM({"GPIB1::12::INSTR": dev}))
    assert b.is_model_340()
    rng = b.configure_for_monitoring(heater_off=True)
    assert rng == 0 and dev.range == 0
    assert "RANGE 0" in dev.writes
    assert "*RST" not in dev.writes
    assert not any(w.startswith("RANGE 1,") for w in dev.writes)


def test_sensing_heater_left_alone_when_unticked(sensing):
    dev = Fake340()
    dev.range = 4
    b = sensing.Lakeshore340_Backend("GPIB1::12::INSTR",
                                     resource_manager=FakeRM({"GPIB1::12::INSTR": dev}))
    rng = b.configure_for_monitoring(heater_off=False)
    assert rng == 4 and dev.range == 4
    assert not any(w.startswith("RANGE ") for w in dev.writes)


def test_sensing_reads_use_no_argument_htr_and_flag_bad_readings(sensing):
    dev = Fake340()
    dev.rdgst['B'] = 1 + 16
    b = sensing.Lakeshore340_Backend("GPIB1::12::INSTR",
                                     resource_manager=FakeRM({"GPIB1::12::INSTR": dev}))
    assert b.get_temperature('B') == pytest.approx(299.0)
    assert b.get_heater_output() == pytest.approx(12.5)
    assert "HTR?" in dev.writes
    st = b.get_reading_status('B')
    assert sensing.describe_reading_status(st) == "invalid reading, temp underrange"
    assert sensing.describe_reading_status(0) == ""


def test_sensing_refuses_non_340(sensing):
    dev = Fake340(idn="LSCI,MODEL350,LSA1234,1.0")
    b = sensing.Lakeshore340_Backend("GPIB1::12::INSTR",
                                     resource_manager=FakeRM({"GPIB1::12::INSTR": dev}))
    assert not b.is_model_340()


def test_sensing_close_sends_nothing(sensing):
    dev = Fake340()
    b = sensing.Lakeshore340_Backend("GPIB1::12::INSTR",
                                     resource_manager=FakeRM({"GPIB1::12::INSTR": dev}))
    n = len(dev.writes)
    b.close()
    assert dev.closed and len(dev.writes) == n


# ---------------------------------------------------------------------------
# Direct control backend
# ---------------------------------------------------------------------------

def test_direct_range_and_heater_are_loop1_only_forms(direct):
    dev = Fake340()
    b = _make_direct_backend(direct, dev)
    b.connect("GPIB1::12::INSTR")
    assert b.is_model_340()
    assert b.set_range(4) == "RANGE 4"
    assert b.get_range() == 4
    assert b.get_heater_output() == pytest.approx(12.5)
    assert b.get_heater_status() == (0, "No error")
    dev.htrst = 5
    assert b.get_heater_status()[1] == "OPEN HEATER LOAD"


def test_direct_pid_limits_follow_340_spec(direct):
    dev = Fake340()
    b = _make_direct_backend(direct, dev)
    b.connect("GPIB1::12::INSTR")
    assert b.set_pid(1, 0.5, 4, 0) == "PID 1,0.5,4,0"
    assert b.get_pid(1) == (0.5, 4.0, 0.0)
    with pytest.raises(ValueError):
        b.set_pid(1, 5000, 4, 0)          # 350 allows 9999, the 340 stops at 1000
    with pytest.raises(ValueError):
        b.set_setpoint_with_ramp(1, 100, 0.05)   # 340 minimum is 0.1 K/min


def test_direct_cset_cmode_climit(direct):
    dev = Fake340()
    b = _make_direct_backend(direct, dev)
    b.connect("GPIB1::12::INSTR")
    assert b.set_control_loop(1, 'A', 1, 1) == "CSET 1,A,1,1"
    assert b.get_control_loop(1) == {'input': 'A', 'units': 1, 'enabled': 1,
                                     'powerup': 0}
    assert b.set_control_mode(1, 1) == "CMODE 1,1"
    assert b.get_control_mode(1) == 1
    lim = b.get_control_limits(1)
    assert lim == {'sp_limit': 325.0, 'pos_slope': 0.0, 'neg_slope': 0.0,
                   'max_current': 3, 'max_range': 5}
    # read-modify-write keeps the max-current code
    cmd = b.set_control_limits(1, sp_limit=330, max_range=4)
    assert cmd == "CLIMIT 1,330.0,0.0,0.0,3,4"
    assert dev.climit[1] == [330.0, 0.0, 0.0, 3, 4]


def test_direct_interface_mode_uses_1_2_3(direct):
    dev = Fake340()
    b = _make_direct_backend(direct, dev)
    b.connect("GPIB1::12::INSTR")
    assert b.set_interface_mode(1) == "MODE 1"
    with pytest.raises(ValueError):
        b.set_interface_mode(0)


def test_direct_intype_sends_type_only_and_zone_has_eight_fields(direct):
    dev = Fake340()
    b = _make_direct_backend(direct, dev)
    b.connect("GPIB1::12::INSTR")
    assert b.set_input_type('A', 8) == "INTYPE A,8"
    assert b.get_input_type('A').startswith("8,")
    assert b.set_zone(1, 1, 20.0, 5.0, 4.0, 0.0, 0, 3) == "ZONE 1,1,20.0,5.0,4.0,0.0,0,3"
    assert b.set_display_fields(2) == "DISPLAY 2"
    assert b.set_display_field(1, 'A', 1) == "DISPFLD 1,A,1"
    assert b.set_filter('A', 1, 10, 2) == "FILTER A,1,10,2"


def test_direct_identify_labels_errors_without_raising(direct):
    dev = Fake340()
    b = direct.Lakeshore340Backend.__new__(direct.Lakeshore340Backend)
    b.lakeshore = None
    b.idn = ""
    b.rm = FakeRM({"GPIB0::12::INSTR": RuntimeError("VI_ERROR_ALLOC (-1073807300)"),
                   "GPIB1::12::INSTR": dev})
    out = b.identify_resources(b.rm.list_resources())
    assert out["GPIB1::12::INSTR"] == Fake340.IDN
    assert out["GPIB0::12::INSTR"].startswith("ERROR:")
    assert dev.closed
    assert "GPIB1" in direct.explain_visa_error(out["GPIB0::12::INSTR"])


# ---------------------------------------------------------------------------
# Zone table logic (shared by both control modules)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("modname", ["T_Control_L340_DirectControl_GUI",
                                     "T_Control_L340_RangeControl_GUI"])
def test_zone_selection_and_generation(modname):
    m = _load(modname)
    # The Sep 2026 lab rule (still the Direct Control default; one pick
    # away in the Range Control window, whose default is now CCR 1).
    z = getattr(m, 'ZONES_LAB_RULE', m.ZONE_DEFAULTS)
    assert [row[0] for row in z] == [20.0, 50.0, 100.0, 310.0]
    assert z[-1][1:] == (0.5, 4.0, 0.0, 5)         # the tested CCR setting
    assert m.select_zone(z, 3.0) == 0
    assert m.select_zone(z, 20.0) == 0              # bound belongs to the lower zone
    assert m.select_zone(z, 20.001) == 1
    assert m.select_zone(z, 100.0) == 2
    assert m.select_zone(z, 250.0) == 3
    assert m.select_zone(z, 400.0) == 3             # above the table: last zone
    # lower range goes with higher P, never the other way round
    for lower, upper in zip(z, z[1:]):
        assert lower[4] <= upper[4]
        assert lower[1] >= upper[1]
    gen = m.generate_equal_zones(4, template=z)
    assert [round(g[0], 2) for g in gen] == [79.75, 156.5, 233.25, 310.0]
    assert gen[0][1:] == (1.5, 4.0, 0.0, 4)         # 79.75 K falls in the 50-100 zone
    assert gen[-1][1:] == (0.5, 4.0, 0.0, 5)
    assert m.generate_equal_zones(1, template=z) == [(310.0, 0.5, 4.0, 0.0, 5)]
    with pytest.raises(ValueError):
        m.generate_equal_zones(0)


def test_ramp_ccr1_is_the_default_and_switches_without_a_power_jump(ramp):
    z = ramp.ZONE_DEFAULTS
    assert z is ramp.ZONES_CCR1 and ramp.DEFAULT_ZONE_SET in ramp.ZONE_SETS
    assert z == ((8.0, 6.3, 2.0, 0.0, 4), (310.0, 2.0, 2.0, 0.0, 5))
    assert z[0][1] == pytest.approx(ramp.bumpless_p(z[1][1], 5, 4), rel=0.01)
    assert z[0][2] == z[1][2]                                   # same I
    full = ramp.loop1_full_power(4, 25.0)                       # 2 A, 25 ohm: 100 W
    amps = [(row[1] / 100) * (ramp.range_power(row[4], full) / 25.0) ** 0.5 for row in z]
    assert amps[0] == pytest.approx(amps[1], rel=0.01)
    assert ramp.PID_DEFAULT_PRESET.startswith("CCR 1")
    caps, default = ramp.parse_rate_table(ramp.RATE_TABLE_DEFAULT)
    assert caps == ((77.0, 0.3), (100.0, 0.5)) and default == 5.0
    assert ramp.rate_cap_for(50, caps, default) == 0.3


def test_ramp_curve_list_is_read_only(ramp):
    import re
    dev = Fake340()
    b = ramp.Lakeshore340_Backend.__new__(ramp.Lakeshore340_Backend)
    b.lakeshore, b.idn, b.rm = None, "", FakeRM({"GPIB1::19::INSTR": dev})
    b.connect("GPIB1::19::INSTR")
    dev.writes.clear()
    heads = b.read_curve_headers()
    assert sorted(heads) == [1, 6, 21]
    assert dev.writes == [f"CRVHDR? {n}" for n in range(1, 61)]
    assert [n for n in heads if ramp.curve_matches(3, heads[n])] == [6]     # Platinum 100
    assert [n for n in heads if ramp.curve_matches(8, heads[n])] == [21]    # Cernox
    assert [n for n in heads if ramp.curve_matches(1, heads[n])] == [1]     # Si diode
    src = open(os.path.join(REPO_ROOT, "pica", "lakeshore",
                            "T_Control_L340_RangeControl_GUI.py"), encoding="utf-8").read()
    assert not re.search(r"CRV(DEL|PT|SAV)", src)
    assert not re.search(r"CRVHDR(?!\?)", src), "only the CRVHDR? query may appear"


# ---------------------------------------------------------------------------
# Ramp control backend
# ---------------------------------------------------------------------------

def test_ramp_prepare_loop_enables_and_pins_manual_pid(ramp):
    dev = Fake340()
    b = _make_ramp_backend(ramp, dev)
    b.connect("GPIB1::12::INSTR")
    cset, cmode, limits = b.prepare_loop('A')
    assert "CSET 1,A,1,1" in dev.writes
    assert "CMODE 1,1" in dev.writes
    assert cset['enabled'] == 1 and cmode == 1
    assert limits['max_range'] == 5
    assert "*RST" not in dev.writes


def test_ramp_start_pins_setpoint_to_present_temperature_first(ramp):
    dev = Fake340()
    dev.setp[1] = 300.0
    b = _make_ramp_backend(ramp, dev)
    b.connect("GPIB1::12::INSTR")
    b.start_ramp(3.0, 2.0, 50.123)
    idx = [w for w in dev.writes if w.startswith(("RAMP", "SETP"))]
    assert idx == ["RAMP 1,0,0", "SETP 1,50.123", "RAMP 1,1,2.0", "SETP 1,3.0"]
    assert dev.ramp[1] == (1, 2.0) and dev.setp[1] == 3.0
    with pytest.raises(ValueError):
        b.start_ramp(3.0, 0.05, 50.0)


def test_ramp_range_is_verified_against_climit_cap(ramp):
    dev = Fake340()
    dev.climit[1][4] = 4       # instrument caps Loop 1 at range 4
    b = _make_ramp_backend(ramp, dev)
    b.connect("GPIB1::12::INSTR")
    b.set_heater_range(4)
    assert dev.range == 4
    with pytest.raises(RuntimeError):
        b.set_heater_range(5)


def test_ramp_status_and_stop(ramp):
    dev = Fake340()
    dev.temps['A'] = 77.5
    dev.rdgst['A'] = 32
    b = _make_ramp_backend(ramp, dev)
    b.connect("GPIB1::12::INSTR")
    temp, htr, setp, status = b.get_status('A')
    assert temp == pytest.approx(77.5) and htr == pytest.approx(12.5)
    assert setp == pytest.approx(300.0) and status == "temp overrange"
    assert "HTR?" in dev.writes
    assert b.stop_ramp() == []
    assert dev.writes[-2:] == ["RANGE 0", "RAMP 1,0,0"]       # heater off first
    assert dev.range == 0
    b.close()
    assert dev.closed


def test_ramp_watts_follow_340_table_1_6(ramp):
    assert ramp.loop1_full_power(4, 25) == pytest.approx(100.0)   # 2 A into 25 ohm
    assert ramp.loop1_full_power(3, 25) == pytest.approx(25.0)    # 1 A
    assert ramp.loop1_full_power(4, 10) == pytest.approx(40.0)
    assert ramp.loop1_full_power(4, 50) == pytest.approx(50.0)    # 50 V compliance
    assert ramp.loop1_full_power(5, 25, 0.5) == pytest.approx(6.25)
    assert ramp.range_label(4, 25.0) == "4 - Medium (2.5 W)"
    assert ramp.range_label(1, 100.0) == "1 - Very low (10 mW)"
    assert all(name.startswith("CCR ") for name in ramp.PID_PRESETS)


def test_ramp_heater_setup_reads_and_writes_without_touching_limits(ramp):
    dev = Fake340()
    b = _make_ramp_backend(ramp, dev)
    b.connect("GPIB1::12::INSTR")
    cfg = b.get_heater_setup()
    assert cfg['max_current'] == 3 and cfg['resistance'] == 25.0
    assert "CLIMI?" not in dev.writes          # only asked when the code is User
    cmd = b.set_heater_setup(5, 1.5, 25, 2)
    assert cmd == "CLIMIT 1,325.0,0.0,0.0,5,5; CLIMI 1.500; CDISP 1,1,25,2,1"
    assert dev.climit[1] == [325.0, 0.0, 0.0, 5, 5]   # SP limit and max range kept
    assert b.get_heater_setup()['user_current'] == 1.5
    with pytest.raises(ValueError):
        b.set_heater_setup(5, 2.5, 25, 1)
    with pytest.raises(ValueError):
        b.set_heater_setup(4, 0, 0, 1)


def test_ramp_mout_input_and_display(ramp):
    dev = Fake340()
    b = _make_ramp_backend(ramp, dev)
    b.connect("GPIB1::12::INSTR")
    b.set_mout(12.5)
    assert dev.mout[1] == 12.5 and b.get_mout() == 12.5
    with pytest.raises(ValueError):
        b.set_mout(-5)
    assert b.set_input_type('A', 8) == "INTYPE A,8"
    assert b.get_input_type('A') == 8
    with pytest.raises(ValueError):
        b.set_input_type('A', 0)               # Special is front-panel only
    assert b.set_display_fields(3) == "DISPLAY 3"
    assert b.set_display_field(2, 'B', 1) == "DISPFLD 2,B,1"
    assert b.get_display_field(2) == ('B', 1)


def _ramp_gui_scenario(tmp_path):
    """Runs in a child interpreter (see the test below): a second Tk root in
    the pytest process can fail to load init.tcl after an earlier one."""
    import pathlib
    import tkinter as tk
    ramp = _load("T_Control_L340_RangeControl_GUI")
    tmp_path = pathlib.Path(tmp_path)
    os.chdir(tmp_path)
    ramp.messagebox.showerror = lambda *a, **k: None
    root = tk.Tk()
    try:
        gui = ramp.TempControlGUI(root)
        dev = Fake340()
        dev.temps['A'] = 45.0
        dev.mout[1] = 30.0                    # leftover from the front panel
        gui.backend.rm = FakeRM({"GPIB1::19::INSTR": dev})
        gui.resource_labels = {"GPIB1::19::INSTR": "GPIB1::19::INSTR"}
        gui.ls_cb.set("GPIB1::19::INSTR")
        root.update()
        assert gui.pid_mode_var.get() == 'zones'          # v1 default kept
        assert str(gui.pid_w['range']['state']) == 'disabled'
        assert [float(r['ub'].get()) for r in gui.zone_rows] == [8.0, 310.0]   # CCR 1
        # The mouse wheel over a drop-down scrolls the panel, never the value.
        before = gui.sens_type_var.get()
        gui.sens_type_cb.event_generate('<MouseWheel>', delta=-120)
        gui.zone_set_cb.event_generate('<MouseWheel>', delta=-120)
        root.update()
        assert gui.sens_type_var.get() == before
        assert gui.zone_set_var.get() == ramp.DEFAULT_ZONE_SET
        # The rest of this scenario uses the Sep 2026 lab-rule table.
        gui.zone_set_var.set("Lab rule, Sep 2026")
        gui._reset_zone_rows()
        assert [float(r['ub'].get()) for r in gui.zone_rows] == [20.0, 50.0, 100.0, 310.0]
        gui._toggle_connect()
        assert gui.backend.is_connected
        assert not any(w.startswith(("RANGE ", "PID ", "SETP ", "CSET ", "MOUT "))
                       for w in dev.writes), "Connect must only read"
        assert gui.heater_cfg['known'] and gui._full_power() == pytest.approx(25.0)
        gui.entries['target'].delete(0, 'end')
        gui.entries['target'].insert(0, "60")
        gui.entries['dwell'].delete(0, 'end')
        gui.entries['dwell'].insert(0, "0")
        dev.climit[1][4] = 5
        gui.start_ramp()
        assert gui.is_running, gui.console.get("1.0", "end")
        assert dev.range == 4 and dev.pid[1] == (1.5, 4.0, 0.0)   # 45 K: the 20-50 K zone
        assert dev.mout[1] == 0.0                          # leftover MOUT cleared
        assert dev.ramp[1] == (1, 2.0) and dev.setp[1] == 60.0
        dev.temps['A'] = 60.2
        gui._monitoring_tick()
        assert gui.active_zone == 2
        gui._monitoring_tick()
        assert not gui.is_running and dev.range == 0
        files = os.listdir(tmp_path / "data")
        assert len(files) == 1
        (tmp_path / "data" / files[0]).read_text(encoding="utf-8").encode("ascii")

        # A target above the CLIMIT setpoint limit is refused before any
        # write: the loop is not even enabled.
        dev.cset[1][2] = 0
        dev.range = 3
        dev.climit[1][0] = 50.0
        dev.writes.clear()
        gui.start_ramp()
        assert not gui.is_running
        assert not any(w.split()[0] in ("CSET", "CMODE", "RANGE", "SETP", "PID", "MOUT", "RAMP")
                       for w in dev.writes), dev.writes
        assert dev.cset[1][2] == 0 and dev.range == 3
        dev.climit[1][0] = 325.0
        dev.range = 0

        # Order: heater off and setpoint pinned before the loop is enabled.
        dev.climit[1][4] = 4
        gui.zone_rows[-1]['range'].set("4")
        dev.temps['A'] = 45.0
        dev.writes.clear()
        gui.start_ramp()
        assert gui.is_running, gui.console.get("1.0", "end")
        assert dev.writes.index("RANGE 0") < dev.writes.index("CSET 1,A,1,1") \
            < dev.writes.index("SETP 1,45.000")
        # A mid-run zone edit above the CLIMIT max range is refused, the old
        # table stays.
        gui.zone_rows[-1]['range'].set("5")
        assert gui._apply_zone_table() is False
        assert max(z[4] for z in gui.zones) == 4
        gui.stop_ramp()
        assert dev.range == 0

        # CCR 1: at a switch the write that lowers the heater power goes
        # first; a heater stuck at 100 % below the setpoint is reported once.
        dev.climit[1][4] = 5
        gui.zone_set_var.set(ramp.DEFAULT_ZONE_SET)
        gui._reset_zone_rows()
        dev.temps['A'] = 20.0
        gui.start_ramp()
        assert gui.is_running, gui.console.get("1.0", "end")
        assert dev.range == 5 and dev.pid[1] == (2.0, 2.0, 0.0)
        dev.writes.clear()
        dev.temps['A'] = 7.5
        gui._monitoring_tick()
        w = [x for x in dev.writes if x.startswith(("RANGE ", "PID 1,"))]
        assert w == ["RANGE 4", "PID 1,6.3,2.0,0.0"], w
        dev.writes.clear()
        dev.temps['A'] = 9.0
        gui._monitoring_tick()
        w = [x for x in dev.writes if x.startswith(("RANGE ", "PID 1,"))]
        assert w == ["PID 1,2.0,2.0,0.0", "RANGE 5"], w
        dev.htr = 100.0
        gui._monitoring_tick()
        assert gui.sat_since is not None
        gui.sat_since -= ramp.SATURATION_WARN_S + 1
        gui._monitoring_tick()
        gui._monitoring_tick()
        assert gui.console.get("1.0", "end").count("heater at 100 %") == 1
        gui.stop_ramp()

        # Input -> sensor type -> curve from the list read from the 340.
        ramp.messagebox.askyesno = lambda *a, **k: True
        gui.sensor_edit_var.set(True)
        gui.sens_input_var.set('A')
        gui._read_sensor()
        dev.writes.clear()
        gui._read_curve_list()
        assert all(w.startswith("CRVHDR? ") for w in dev.writes)
        gui.sens_type_var.set(ramp.Lakeshore340_Backend.SENSOR_TYPES[3])   # Platinum 100
        gui._refresh_curve_choices()
        pt100 = "06 - PT-100 (STANDARD) to 800 K, ohm/K"
        values = list(gui.sens_curve_cb['values'])
        present = gui.sens_curve_var.get()               # the input's curve, named now
        assert "press Read curve list" not in present
        assert pt100 in values and not any(v.startswith("01 ") for v in values)
        assert [v for v in values if v.startswith("21 ")] in ([], [present])
        gui.sens_curve_var.set(pt100)
        dev.writes.clear()
        gui._send_sensor()
        assert "INTYPE A,3" in dev.writes and "INCRV A,6" in dev.writes
        gui._cancel_poll()
        gui.backend.close()
    finally:
        root.destroy()


@pytest.mark.real_windows
def test_ramp_gui_zone_run_end_to_end(tmp_path):
    out = subprocess.run([sys.executable, __file__, "--ramp-gui-scenario", str(tmp_path)],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------------------
# The three modules are registered everywhere the L350 ones are
# ---------------------------------------------------------------------------

def test_modules_are_registered_in_cli_and_launchers():
    cli = importlib.import_module("pica.cli")
    for name in ("T_Sensing_L340_GUI", "T_Control_L340_DirectControl_GUI",
                 "T_Control_L340_RangeControl_GUI"):
        assert f"pica.lakeshore.{name}" in cli.ALL_GUI_MODULES, name
    main_src = open(os.path.join(REPO_ROOT, "pica", "main.py"), encoding="utf-8").read()
    v2_src = open(os.path.join(REPO_ROOT, "pica", "main_v2.py"), encoding="utf-8").read()
    for key in ("Lakeshore 340 Temp Control", "Lakeshore 340 Direct Control",
                "Lakeshore 340 Temp Monitor"):
        assert key in main_src, key
        assert key in v2_src, key
    for path in ("T_Sensing_L340_GUI.py", "T_Control_L340_DirectControl_GUI.py",
                 "T_Control_L340_RangeControl_GUI.py"):
        assert os.path.exists(os.path.join(REPO_ROOT, "pica", "lakeshore", path))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--ramp-gui-scenario":
        _ramp_gui_scenario(sys.argv[2])
        sys.exit(0)
    sys.exit(pytest.main([__file__, "-v"]))
