"""
T_Control_L350_RangeControl_GUI v9 (27 Nov 2026 rewrite).

The backend is driven against a fake Model 350 that answers like the real
one (User's Manual, chapter 6) and refuses the Model 340 forms (RANGE n with
no output, HTR? with no output, CSET / CMODE / CLIMIT) and *RST. The GUI test
opens a real window, so it only runs with PICA_REAL_WINDOWS=1.
Runnable as plain python too.
"""
import importlib
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MOD = "pica.lakeshore.T_Control_L350_RangeControl_GUI"


class Fake350:
    IDN = "LSCI,MODEL350,FAKE000,1.0"

    def __init__(self, idn=None):
        self.idn = idn or self.IDN
        self.writes = []
        self.outmode = {1: [0, 1, 0], 2: [0, 1, 0]}
        self.pid = {1: (50.0, 20.0, 0.0), 2: (50.0, 20.0, 0.0)}
        self.mout = {1: 12.5, 2: 0.0}
        self.range = {1: 0, 2: 0}
        self.ramp = {1: (0, 0.0), 2: (0, 0.0)}
        self.setp = {1: 300.0, 2: 300.0}
        self.htrset = {1: [1, 4, 0.0, 1], 2: [1, 3, 0.0, 1]}
        self.intype = {k: [1, 0, 0, 0, 1, 0] for k in "ABCD"}
        self.incrv = {k: 1 for k in "ABCD"}
        self.display = [0, 0, 1]
        self.dispfld = {}
        self.temps = {k: 295.0 for k in "ABCD"}
        self.rdgst = {k: 0 for k in "ABCD"}
        self.htr = {1: 10.0, 2: 0.0}
        self.htrst = {1: 0, 2: 0}
        # CRVHDR? replies; every other slot answers as an empty curve.
        self.curves = {1: "DT-470,STANDARD,2,+475.000,1",
                       6: "PT-100,STANDARD,3,+800.000,2",
                       7: "PT-1000,STANDARD,3,+800.000,2",
                       21: "CX-1050,X999999,4,+325.000,1"}
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.closed = False
        self.fail_on = None          # write prefix that raises (a dropped bus)

    def write(self, cmd):
        if self.fail_on and cmd.startswith(self.fail_on):
            raise OSError(f"VI_ERROR_TMO on '{cmd}'")
        self.writes.append(cmd)
        self._exec(cmd)

    def query(self, cmd):
        self.writes.append(cmd)
        return self._exec(cmd)

    def close(self):
        self.closed = True

    def _exec(self, cmd):
        name, _, rest = cmd.strip().partition(' ')
        a = [x.strip() for x in rest.split(',')] if rest else []
        name = name.upper()
        if name == '*IDN?':
            return self.idn
        if name == '*CLS':
            return None
        if name == 'OUTMODE':
            assert len(a) == 4, cmd
            self.outmode[int(a[0])] = [int(a[1]), int(a[2]), int(a[3])]
            return None
        if name == 'OUTMODE?':
            return ",".join(str(x) for x in self.outmode[int(a[0])])
        if name == 'PID':
            p, i, d = (float(x) for x in a[1:4])
            assert 0.1 <= p <= 1000 and 0.1 <= i <= 1000 and 0 <= d <= 200, cmd
            self.pid[int(a[0])] = (p, i, d)
            return None
        if name == 'PID?':
            p, i, d = self.pid[int(a[0])]
            return f"+{p:.1f},+{i:.1f},+{d:.0f}"
        if name == 'MOUT':
            self.mout[int(a[0])] = float(a[1])
            return None
        if name == 'MOUT?':
            return f"+{self.mout[int(a[0])]:.2f}"
        if name == 'RANGE':
            assert len(a) == 2, f"350 RANGE takes <output>,<range>: '{cmd}'"
            self.range[int(a[0])] = int(a[1])
            return None
        if name == 'RANGE?':
            assert len(a) == 1, f"350 RANGE? takes the output: '{cmd}'"
            return str(self.range[int(a[0])])
        if name == 'RAMP':
            self.ramp[int(a[0])] = (int(a[1]), float(a[2]))
            return None
        if name == 'SETP':
            self.setp[int(a[0])] = float(a[1])
            return None
        if name == 'SETP?':
            return f"+{self.setp[int(a[0])]:.3f}"
        if name == 'HTRSET':
            assert len(a) == 5, cmd
            self.htrset[int(a[0])] = [int(a[1]), int(a[2]), float(a[3]), int(a[4])]
            return None
        if name == 'HTRSET?':
            r, c, u, d = self.htrset[int(a[0])]
            return f"{r},{c},+{u:.3f},{d}"
        if name == 'INTYPE':
            assert len(a) == 7, f"350 INTYPE needs all 7 fields: '{cmd}'"
            self.intype[a[0]] = [int(x) for x in a[1:]]
            return None
        if name == 'INTYPE?':
            return ",".join(str(x) for x in self.intype[a[0]])
        if name == 'INCRV':
            self.incrv[a[0]] = int(a[1])
            return None
        if name == 'INCRV?':
            return f"{self.incrv[a[0]]:02d}"
        if name == 'DISPLAY':
            self.display = [int(x) for x in a]
            return None
        if name == 'DISPLAY?':
            return ",".join(str(x) for x in self.display)
        if name == 'DISPFLD':
            self.dispfld[int(a[0])] = (int(a[1]), int(a[2]))
            return None
        if name == 'DISPFLD?':
            inp, u = self.dispfld.get(int(a[0]), (1, 1))
            return f"{inp},{u}"
        if name == 'CRVHDR?':
            assert len(a) == 1 and 1 <= int(a[0]) <= 59, cmd
            return self.curves.get(int(a[0]), "               ,          ,0,+000.000,0")
        if name.startswith('CRV'):
            raise AssertionError(f"curve write sent: '{cmd}'")
        if name == 'KRDG?':
            return f"+{self.temps[a[0]]:.4f}"
        if name == 'RDGST?':
            return f"{self.rdgst[a[0]]:03d}"
        if name == 'HTR?':
            assert len(a) == 1, f"350 HTR? takes the output: '{cmd}'"
            return f"+{self.htr[int(a[0])]:.1f}"
        if name == 'HTRST?':
            assert len(a) == 1, cmd
            return str(self.htrst[int(a[0])])
        raise AssertionError(f"Fake350 does not know '{cmd}' (340-only command or *RST?)")


class FakeRM:
    def __init__(self, devices):
        self.devices = devices

    def list_resources(self):
        return tuple(self.devices)

    def open_resource(self, address):
        dev = self.devices[address]
        if isinstance(dev, Exception):
            raise dev
        return dev


@pytest.fixture
def mod():
    return importlib.import_module(MOD)


def _backend(mod, dev):
    b = mod.Lakeshore350_Backend.__new__(mod.Lakeshore350_Backend)
    b.lakeshore = None
    b.idn = ""
    b.rm = FakeRM({"GPIB0::12::INSTR": dev})
    b.connect("GPIB0::12::INSTR")
    return b


# --------------------------------------------------------------- watts

def test_output1_power_follows_manual_table_4_14(mod):
    assert mod.out1_full_power(1, 4) == pytest.approx(75.0, rel=1e-3)   # 25 ohm, 1.732 A
    assert mod.out1_full_power(1, 3) == pytest.approx(50.0, rel=1e-3)   # 1.414 A
    assert mod.out1_full_power(1, 2) == pytest.approx(25.0)
    assert mod.out1_full_power(1, 1) == pytest.approx(12.5, rel=1e-3)
    assert mod.out1_full_power(2, 2) == pytest.approx(50.0)             # 50 ohm, 1 A
    assert mod.out1_full_power(2, 4) == pytest.approx(50.0)             # capped at 1 A
    assert mod.out1_full_power(1, 0, 0.5) == pytest.approx(6.25)        # user current


def test_output2_and_ranges_are_decades(mod):
    assert mod.OUT2_DEFAULT_RESISTANCE == 25.0                      # the lab heater
    assert mod.out2_full_power() == pytest.approx(0.25)             # 0.1 A^2 x 25 ohm
    assert mod.out2_full_power(100) == pytest.approx(1.0)
    assert mod.out2_full_power(10) == pytest.approx(0.1)
    assert mod.out2_full_power(1000) == pytest.approx(0.1)
    assert [mod.range_power(k, 75.0) for k in (5, 4, 3, 2, 1, 0)] == \
        pytest.approx([75, 7.5, 0.75, 0.075, 0.0075, 0])
    assert mod.range_label(5, 75.0) == "5 - High (75 W)"
    assert mod.range_label(2, 75.0) == "2 - Low (75 mW)"
    assert mod.range_label(0, 75.0) == "0 - Off"
    assert mod.parse_range("4 - Medium (7.5 W)") == 4
    assert mod.format_power(7.5e-4) == "750 uW"
    assert mod.heater_watts(50, 1.0, display_is_power=False) == pytest.approx(0.25)
    assert mod.heater_watts(50, 1.0, display_is_power=True) == pytest.approx(0.5)


def test_presets_are_named_for_their_system(mod):
    vals = set(mod.PID_PRESETS.values())
    assert vals == {(2.0, 2.0, 0.0), (6.3, 2.0, 0.0),                       # CCR 1
                    (0.2, 1.0, 0.0), (0.5, 4.0, 0.0), (20.0, 15.0, 0.0), (50.0, 20.0, 0.0)}
    assert all(name.startswith(("CCR 1, ", "LN2 ")) for name in mod.PID_PRESETS)
    assert mod.PID_DEFAULT_PRESET in mod.PID_PRESETS
    assert mod.PID_DEFAULT_PRESET.startswith("CCR 1")
    for s in mod.SYSTEMS.values():
        assert s['preset'] in mod.PID_PRESETS


def test_ccr1_zones_switch_range_without_a_power_jump(mod):
    z = mod.ZONES_CCR1
    assert mod.ZONE_DEFAULTS is z and mod.DEFAULT_SYSTEM == 'CCR'
    (b1, h1, low, _), (b2, h2, high, _) = z
    assert (b1, b2, h1, h2) == (8.0, 350.0, 1, 1)
    assert low == (6.3, 2.0, 0.0, 0.0, 4) and high == (2.0, 2.0, 0.0, 0.0, 5)
    # Same I, P x sqrt(10) for one range lower: same current, same power.
    assert low[1] == high[1]
    assert low[0] == pytest.approx(mod.bumpless_p(high[0], 5, 4), rel=0.01)
    full = mod.out1_full_power(1, 4)
    amps = [(p / 100) * (mod.range_power(r, full) / 25.0) ** 0.5
            for p, r in ((low[0], low[4]), (high[0], high[4]))]
    assert amps[0] == pytest.approx(amps[1], rel=0.01)       # mA per K of error
    assert mod.bumpless_p(1.0, 5, 3) == pytest.approx(10.0)
    assert mod.bumpless_p(10.0, 3, 4) == pytest.approx(10 / 10 ** 0.5)
    assert mod.bumpless_p(1.0, 5, 4, display_is_power=True) == pytest.approx(10.0)
    assert mod.SYSTEMS['CCR']['max_temp'] > b2 and not mod.SYSTEMS['CCR']['ln2_checks']
    assert mod.SYSTEMS['LN2']['ln2_checks']


def test_rate_table_parsing(mod):
    caps, default = mod.parse_rate_table(mod.RATE_TABLE_DEFAULT)
    assert caps == mod.LN2_RATE_CAPS and default == mod.LN2_RATE_CAP_ELSE
    assert mod.parse_rate_table("20:1; else:3 , 10:0.2") == (((10.0, 0.2), (20.0, 1.0)), 3.0)
    assert mod.rate_cap_for(15, ((10.0, 0.2), (20.0, 1.0)), 3.0) == 1.0
    for bad in ("77:0.3", "77-0.3, else:5", "77:0, else:5", "else:x"):
        with pytest.raises(ValueError):
            mod.parse_rate_table(bad)


def test_curve_headers_are_only_queried(mod):
    dev = Fake350()
    b = _backend(mod, dev)
    dev.writes.clear()
    heads = b.read_curve_headers()
    assert sorted(heads) == [1, 6, 7, 21]                    # empty slots skipped
    assert heads[6] == {'name': 'PT-100', 'sn': 'STANDARD', 'format': 3,
                        'limit': 800.0, 'coefficient': 2}
    assert dev.writes == [f"CRVHDR? {n}" for n in range(1, 60)]
    assert [n for n in heads if mod.curve_matches(2, heads[n])] == [6, 7]     # platinum
    assert [n for n in heads if mod.curve_matches(1, heads[n])] == [1]        # diode
    assert [n for n in heads if mod.curve_matches(3, heads[n])] == [21]       # NTC
    assert mod.curve_label(6, heads[6]) == "06 - PT-100 (STANDARD) to 800 K, ohm/K"


def test_module_never_writes_a_curve():
    import re
    path = os.path.join(REPO_ROOT, "pica", "lakeshore", "T_Control_L350_RangeControl_GUI.py")
    src = open(path, encoding="utf-8").read()
    assert not re.search(r"CRV(DEL|PT|SAV)", src)
    assert not re.search(r"CRVHDR(?!\?)", src), "only the CRVHDR? query may appear"


def test_rate_caps_and_zone_helpers(mod):
    assert mod.ln2_rate_cap(50) == 0.3
    assert mod.ln2_rate_cap(77.0) == 0.5          # "below" is strict
    assert mod.ln2_rate_cap(99.9) == 0.5
    assert mod.ln2_rate_cap(150) == 5.0
    z = mod.ZONES_LN2
    assert mod.select_zone(z, 80) == 0 and mod.select_zone(z, 100.0) == 0
    assert mod.select_zone(z, 100.1) == 1 and mod.select_zone(z, 500) == 1
    assert z[0][2][:3] == (0.2, 1.0, 0.0)          # anti-windup below 100 K
    gen = mod.generate_equal_zones(2, 80, 340, template=z)
    assert [g[0] for g in gen] == [210.0, 340.0]
    assert gen[0][1:] == z[1][1:]
    c = mod.ZONE_DEFAULTS
    assert mod.select_zone(c, 8.0) == 0 and mod.select_zone(c, 8.01) == 1
    assert mod.generate_equal_zones(1, 4, 350)[0][1:] == c[1][1:]
    with pytest.raises(ValueError):
        mod.validate_pid(0.0, 1, 0)                # 350 P starts at 0.1
    with pytest.raises(ValueError):
        mod.validate_pid(1, 0, 0)                  # and I has no 0
    with pytest.raises(ValueError):
        mod.validate_pid(1, 1, 250)


# --------------------------------------------------------------- backend

def test_prepare_output_and_ramp_pin_every_setpoint(mod):
    dev = Fake350()
    b = _backend(mod, dev)
    assert b.is_model_350()
    assert b.prepare_output(2, 'B') == {'mode': 1, 'input': 2, 'powerup': 0}
    assert "OUTMODE 2,1,2,0" in dev.writes
    dev.writes.clear()
    b.start_ramp((1, 2), 80.0, 0.5, 290.123)
    seq = [w for w in dev.writes if w.startswith(("RAMP", "SETP"))]
    assert seq == ["RAMP 1,0,0", "SETP 1,290.123", "RAMP 2,0,0", "SETP 2,290.123",
                   "RAMP 1,1,0.5", "SETP 1,80.0", "RAMP 2,1,0.5", "SETP 2,80.0"]
    with pytest.raises(ValueError):
        b.start_ramp((1,), 80.0, 0.0005, 290.0)
    assert "*RST" not in dev.writes


def test_range_is_verified_and_stop_turns_off_only_used_outputs(mod):
    dev = Fake350()
    b = _backend(mod, dev)
    b.set_heater_range(1, 4)
    assert dev.range[1] == 4
    dev.range[2] = 3
    assert b.stop_ramp((1,)) == []
    assert dev.writes[-2:] == ["RANGE 1,0", "RAMP 1,0,0"]     # heater off first
    assert dev.range == {1: 0, 2: 3}           # output 2 was not ours: untouched
    # A RAMP write that fails must not stop RANGE 0; a RANGE failure is reported.
    dev.range[1] = 4
    dev.fail_on = "RAMP"
    assert b.stop_ramp((1,)) == [] and dev.range[1] == 0
    dev.fail_on = "RANGE"
    assert b.stop_ramp((1,))[0].startswith("RANGE 1,0 failed")


def test_heater_setup_validation(mod):
    dev = Fake350()
    b = _backend(mod, dev)
    assert b.get_heater_setup(1) == {'resistance': 1, 'max_current': 4,
                                     'user_current': 0.0, 'display': 1}
    assert b.set_heater_setup(2, 2, 0, 2) == "HTRSET 1,2,2,0.000,2"
    with pytest.raises(ValueError):
        b.set_heater_setup(2, 4, 0, 1)         # 1.732 A into the 50 ohm setting
    with pytest.raises(ValueError):
        b.set_heater_setup(2, 0, 1.5, 1)       # user current above 1 A at 50 ohm
    assert b.set_heater_setup(1, 0, 1.2, 1) == "HTRSET 1,1,0,1.200,1"


def test_intype_sends_seven_fields_with_unused_ones_zero(mod):
    dev = Fake350()
    b = _backend(mod, dev)
    # Diode: autorange, compensation and excitation do not apply -> 0.
    assert b.set_input_type('A', 1, 1, 0, 1, 1, 1) == "INTYPE A,1,0,0,0,1,0"
    assert b.set_input_type('B', 3, 1, 5, 1, 1, 1) == "INTYPE B,3,1,5,1,1,1"
    with pytest.raises(ValueError):
        b.set_input_type('A', 1, 0, 3, 0, 1, 0)    # a diode has 2 ranges
    t = b.get_input_type('B')
    assert t == {'sensor': 3, 'autorange': 1, 'range': 5, 'compensation': 1,
                 'units': 1, 'excitation': 1}
    assert b.set_input_curve('A', 21) == "INCRV A,21"
    assert b.set_display(4, 0, 1) == "DISPLAY 4,0,1"
    assert b.set_display_field(1, 2, 1) == "DISPFLD 1,2,1"


def test_status_reads(mod):
    dev = Fake350()
    dev.rdgst['A'] = 1 + 32
    dev.htrst[2] = 2
    b = _backend(mod, dev)
    assert b.get_reading_status('A') == "invalid reading, temp overrange"
    assert b.get_heater_status(2)[0] == 2
    assert b.get_heater_output(1) == pytest.approx(10.0)


# --------------------------------------------------------------- GUI (real window)

def _gui_scenario(tmp_path):
    """Runs in a child interpreter (see the test below): a second Tk root in
    the pytest process can fail to load init.tcl after an earlier one."""
    import pathlib
    import tkinter as tk
    mod = importlib.import_module(MOD)
    tmp_path = pathlib.Path(tmp_path)
    os.chdir(tmp_path)
    mod.messagebox.showerror = lambda *a, **k: None
    mod.messagebox.askyesno = lambda *a, **k: True
    root = tk.Tk()
    try:
        gui = mod.TempControlGUI(root)
        dev = Fake350()
        dev.temps['A'] = 95.0
        gui.backend.rm = FakeRM({"GPIB0::12::INSTR": dev})
        gui.resource_labels = {"GPIB0::12::INSTR": "GPIB0::12::INSTR"}
        gui.ls_cb.set("GPIB0::12::INSTR")
        root.update()

        # Defaults: CCR with the CCR 1 zone table in use.
        assert gui.system == 'CCR' and gui.pid_mode_var.get() == 'zones'
        assert [float(r['ub'].get()) for r in gui.zone_rows] == [8.0, 350.0]
        assert gui.entries['max_temp'].get() == "355"
        assert gui.out2_res_entry.get() == "25"
        # The mouse wheel over a drop-down scrolls the panel, never the value.
        before = gui.sens_type_var.get()
        for cls in ('TCombobox', 'TSpinbox'):
            assert root.bind_class(cls, '<MouseWheel>')
        gui.sens_type_cb.event_generate('<MouseWheel>', delta=-120)
        gui.input_cb.event_generate('<MouseWheel>', delta=120)
        root.update()
        assert gui.sens_type_var.get() == before and gui.input_var.get() == 'A'
        gui.pid_mode_var.set('fixed')
        gui._refresh_states()

        # Before connecting: nothing that talks to the instrument is live.
        assert str(gui.htr_send_btn['state']) == 'disabled'
        assert str(gui.load_pid_btn['state']) == 'disabled'
        # Zone widgets greyed in fixed mode, output 2 greyed for "Output 1 only".
        assert str(gui.zone_add_btn['state']) == 'disabled'
        assert str(gui.out_widgets[2]['p']['state']) == 'disabled'
        assert str(gui.out_widgets[1]['p']['state']) == 'disabled'   # preset, not custom

        gui._toggle_connect()
        assert gui.backend.is_connected
        assert dev.writes[:2] == ["*IDN?", "*CLS"]
        assert not any(w.startswith(("RANGE ", "PID ", "SETP ", "OUTMODE ", "MOUT "))
                       for w in dev.writes), "Connect must only read"
        gui._load_present_pid()
        assert gui.out_widgets[1]['preset_var'].get() == mod.PID_CUSTOM
        assert gui.out_widgets[1]['p'].get() == "50"
        assert str(gui.out_widgets[1]['p']['state']) == 'normal'

        # LN2 probe: its zone defaults, safety maximum and wind-up checks.
        gui.system_var.set(mod.SYSTEMS['LN2']['label'])
        gui._on_system_changed()
        assert gui.system == 'LN2'
        assert [float(r['ub'].get()) for r in gui.zone_rows] == [100.0, 340.0]
        assert gui.entries['max_temp'].get() == "340"

        # Zone table: below 100 K Output 2, above it both.
        gui.pid_mode_var.set('zones')
        gui._refresh_states()
        gui._set_zone_heater(gui.zone_rows[0], 2)
        gui._set_zone_heater(gui.zone_rows[1], 3)
        # Unticking the only heater of a zone is undone.
        gui.zone_rows[0]['use'][2].set(False)
        gui._on_zone_heater_toggled(gui.zone_rows[0], 2)
        assert gui._zone_heater_code(gui.zone_rows[0]) == 2
        assert str(gui.zone_rows[0][1]['p']['state']) == 'disabled'
        assert str(gui.zone_rows[0][2]['p']['state']) == 'normal'
        gui.entries['target'].delete(0, 'end')
        gui.entries['target'].insert(0, "120")
        gui.entries['rate'].delete(0, 'end')
        gui.entries['rate'].insert(0, "1")
        gui.entries['dwell'].delete(0, 'end')
        gui.entries['dwell'].insert(0, "0")
        gui.rate_cap_var.set(True)
        dev.writes.clear()
        gui.start_ramp()
        assert gui.is_running, gui.console.get("1.0", "end")
        assert gui.run_outputs == (1, 2)
        assert gui.params['rate'] == 0.5                  # LN2 cap at 95 K
        assert "OUTMODE 1,1,1,0" in dev.writes and "OUTMODE 2,1,1,0" in dev.writes
        assert dev.range == {1: 0, 2: 5}                  # zone 1: Output 2 only
        assert dev.mout == {1: 0.0, 2: 0.0}               # leftover MOUT 1 cleared
        # Order: heater off and setpoints pinned before any loop / range write.
        first_on = next(k for k, w in enumerate(dev.writes)
                        if w.startswith("RANGE ") and not w.endswith(",0"))
        assert dev.writes.index("RANGE 1,0") < dev.writes.index("OUTMODE 1,1,1,0") \
            < dev.writes.index("SETP 1,95.000") < first_on
        assert dev.ramp[1] == (1, 0.5) and dev.ramp[2] == (1, 0.5)
        assert dev.setp == {1: 120.0, 2: 120.0}
        assert str(gui.start_button['state']) == 'disabled'
        assert str(gui.zone_rows[0]['heater'][2]['state']) == 'disabled'

        dev.temps['A'] = 101.0                            # crosses into zone 2
        gui._monitoring_tick()
        assert gui.active_zone == 1
        assert dev.range == {1: 5, 2: 5}
        assert dev.pid[1] == (20.0, 15.0, 0.0)

        dev.temps['A'] = 120.1                            # arrives; dwell 0
        gui._monitoring_tick()
        gui._monitoring_tick()
        assert not gui.is_running
        assert dev.range == {1: 0, 2: 0}
        assert "COMPLETE" in gui.lbl_status.cget('text')
        files = os.listdir(tmp_path / "data")
        assert len(files) == 1
        text = (tmp_path / "data" / files[0]).read_text(encoding="utf-8")
        assert "Heater1_pct" in text and "zone 1" in text and "zone 2" in text
        assert "outputs [1, 2]" in text
        text.encode("ascii")                              # data file stays ASCII

        # Apply now cannot switch on an output this run did not set up.
        gui._set_zone_heater(gui.zone_rows[1], 2)
        gui.start_ramp()
        assert gui.is_running and gui.run_outputs == (2,)
        gui.pid_mode_var.set('fixed')
        gui._on_mode_changed()
        gui._apply_fixed_live()                           # the form says Output 1 only
        assert dev.range[1] == 0
        assert "Not applied" in gui.console.get("1.0", "end")
        gui.stop_ramp()
        assert dev.range == {1: 0, 2: 0}

        # Stop that cannot send RANGE 0 says so instead of "heater off".
        gui.pid_mode_var.set('zones')
        gui._refresh_states()
        gui.start_ramp()
        assert gui.is_running
        dev.fail_on = "RANGE"
        gui.stop_ramp()
        dev.fail_on = None
        assert not gui.is_running
        assert "NOT CONFIRMED" in gui.lbl_status.cget('text')

        # Below 100 K with the Medium preset: the warning can cancel Start,
        # and then nothing at all is written.
        gui.pid_mode_var.set('fixed')
        gui.out_widgets[1]['preset_var'].set(mod.PID_DEFAULT_PRESET)
        gui._on_preset(1)
        gui.entries['target'].delete(0, 'end')
        gui.entries['target'].insert(0, "80")
        asked = []
        mod.messagebox.askyesno = lambda *a, **k: asked.append(a) or False
        dev.writes.clear()
        gui.start_ramp()
        assert asked and not gui.is_running
        assert not any(w.split()[0] in ("RANGE", "SETP", "OUTMODE", "PID", "MOUT", "RAMP")
                       for w in dev.writes), dev.writes
        mod.messagebox.askyesno = lambda *a, **k: True

        # CCR: no below-100 K dialog; at a zone switch the write that lowers
        # the heater power goes first; a heater stuck at 100 % is reported.
        gui.system_var.set(mod.SYSTEMS['CCR']['label'])
        gui._on_system_changed()
        gui.pid_mode_var.set('zones')
        gui._refresh_states()
        gui.entries['target'].delete(0, 'end')
        gui.entries['target'].insert(0, "30")
        gui.entries['max_temp'].delete(0, 'end')
        gui.entries['max_temp'].insert(0, "355")
        gui.rate_cap_var.set(False)
        dev.temps['A'] = 20.0
        asked = []
        mod.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
        gui.start_ramp()
        assert gui.is_running and not asked, gui.console.get("1.0", "end")
        assert dev.range[1] == 5 and dev.pid[1] == (2.0, 2.0, 0.0)
        dev.writes.clear()
        dev.temps['A'] = 7.5                                # into zone 1: range down
        gui._monitoring_tick()
        w = [x for x in dev.writes if x.startswith(("RANGE 1,", "PID 1,"))]
        assert w == ["RANGE 1,4", "PID 1,6.3,2.0,0.0"], w
        dev.writes.clear()
        dev.temps['A'] = 9.0                                # back up: P first
        gui._monitoring_tick()
        w = [x for x in dev.writes if x.startswith(("RANGE 1,", "PID 1,"))]
        assert w == ["PID 1,2.0,2.0,0.0", "RANGE 1,5"], w
        dev.htr[1] = 100.0
        gui._monitoring_tick()
        assert gui.sat_since is not None
        gui.sat_since -= mod.SATURATION_WARN_S + 1
        gui._monitoring_tick()
        gui._monitoring_tick()
        assert gui.console.get("1.0", "end").count("heater at 100 %") == 1
        dev.htr[1] = 10.0
        gui.stop_ramp()
        mod.messagebox.askyesno = lambda *a, **k: True

        # A data file that cannot be opened stops Start before any write.
        import shutil
        shutil.rmtree(tmp_path / "data")
        (tmp_path / "data").write_text("not a folder")
        dev.writes.clear()
        gui.start_ramp()
        assert not gui.is_running and gui.run_outputs == ()
        assert not any(w.split()[0] in ("RANGE", "SETP", "OUTMODE", "PID", "MOUT", "RAMP")
                       for w in dev.writes), dev.writes

        # Sensor Send stays greyed until that input has been read.
        gui.sensor_edit_var.set(True)
        gui._refresh_states()
        assert str(gui.sens_send_btn['state']) == 'disabled'
        gui._read_sensor()
        assert str(gui.sens_send_btn['state']) == 'normal'
        gui.sens_input_var.set('B')
        gui._refresh_states()
        assert str(gui.sens_send_btn['state']) == 'disabled'

        # Input -> sensor type -> a curve from the list read from the 350.
        gui.sens_input_var.set('A')
        gui._read_sensor()
        dev.writes.clear()
        gui._read_curve_list()
        assert all(w.startswith("CRVHDR? ") for w in dev.writes)
        gui.sens_type_var.set(mod.Lakeshore350_Backend.SENSOR_TYPES[2])     # platinum
        gui._on_sensor_type()
        values = list(gui.sens_curve_cb['values'])
        pt100 = "06 - PT-100 (STANDARD) to 800 K, ohm/K"
        present = gui.sens_curve_var.get()               # the input's curve, named now
        assert present.startswith("01 - DT-470")
        assert pt100 in values and not any(v.startswith("21 ") for v in values)
        assert [v for v in values if v.startswith("01 ")] == [present]
        gui.sens_curve_var.set(pt100)
        dev.writes.clear()
        gui._send_sensor()
        assert "INCRV A,6" in dev.writes and dev.intype['A'][0] == 2
        assert not any(w.startswith("CRV") and not w.startswith("CRVHDR?") for w in dev.writes)
        gui.sens_curve_var.set(mod.CURVE_KEEP)
        dev.writes.clear()
        gui._send_sensor()
        assert not any(w.startswith("INCRV ") for w in dev.writes)
        gui._cancel_poll()
        gui.backend.close()
    finally:
        root.destroy()


@pytest.mark.real_windows
def test_gui_run_with_both_outputs_and_zone_handover(tmp_path):
    out = subprocess.run([sys.executable, __file__, "--gui-scenario", str(tmp_path)],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stdout + out.stderr


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--gui-scenario":
        _gui_scenario(sys.argv[2])
        sys.exit(0)
    sys.exit(pytest.main([__file__, "-v", "-rA"]))
