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
import sys
import types

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
    z = m.ZONE_DEFAULTS
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
    gen = m.generate_equal_zones(4)
    assert [round(g[0], 2) for g in gen] == [79.75, 156.5, 233.25, 310.0]
    assert gen[0][1:] == (1.5, 4.0, 0.0, 4)         # 79.75 K falls in the 50-100 zone
    assert gen[-1][1:] == (0.5, 4.0, 0.0, 5)
    assert m.generate_equal_zones(1) == [(310.0, 0.5, 4.0, 0.0, 5)]
    with pytest.raises(ValueError):
        m.generate_equal_zones(0)


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
    b.stop_ramp()
    assert dev.writes[-2:] == ["RAMP 1,0,0", "RANGE 0"]
    assert dev.range == 0
    b.close()
    assert dev.closed


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
    sys.exit(pytest.main([__file__, "-v"]))
