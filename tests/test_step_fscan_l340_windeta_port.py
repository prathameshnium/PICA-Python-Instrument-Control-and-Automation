"""Tests for the 27 Oct 2026 Fscan features ported into
novocontrol/Step_Frequency_Scan_L340_AlphaAN_GUI.py, the Lakeshore 340
sibling (mirrors tests/test_step_fscan_windeta_port.py): the 11-column
WinDETA .txt, the Start / End / Factor frequency list, the Diameter
geometry default and the eps' / tan(delta) spectrum panels. The ported
code must stay identical to the Lakeshore 350 Step module's copy.

No Tk window is opened: the GUI object is built with __new__, the sweep
runs against FakeAlpha and the plots use a bare matplotlib Figure.
Runnable as plain Python as well as under pytest.
"""

import inspect
import math
import os
import queue
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeAlpha, connected_backend, load_port,
)

mod, PATH = load_port("Step_Frequency_Scan_L340_AlphaAN_GUI.py")
fscan, _ = load_port("Frequency_Scan_AlphaAN_GUI.py")
step350, _ = load_port("Step_Frequency_Scan_AlphaAN_GUI.py")

EPS0_SI = 8.8541878128e-12
EPS0_CM = 8.8541878128e-14


class _Entry:
    def __init__(self, value):
        self.value = value

    def get(self, *_args):
        return self.value

    def focus_set(self):
        self.focused = True


def _sweep_gui(fake, freqs):
    """A CombinedGUI with just what _run_frequency_sweep touches."""
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod, fake)
    gui.lcr_params = {"sample_name": "TestSample", "acv": 1.0, "mtm": 0.5,
                      "delay": 0.0, "wire_mode": "2",
                      "lcr_visa": "GPIB0::10::INSTR",
                      "geometry_mode": "diameter", "diameter_mm": 10.0,
                      "thickness_mm": 0.5, "save_pica": True,
                      "save_windeta": True,
                      "temp_controller": "Lakeshore 340"}
    gui.c0_farads = mod.c0_from_params(gui.lcr_params)
    gui.save_dir = tempfile.mkdtemp(prefix="pica_stepfscan_l340_port_")
    gui.lcr_params["save_dir"] = gui.save_dir   # Start's snapshot
    gui.sweep_frequencies = list(freqs)
    gui.is_running = True
    gui._paused = False
    gui._skip_requested = False
    gui._worker_phase = None
    gui.gui_queue = queue.Queue()
    gui.cmd_queue = queue.Queue()
    gui._process_cmd_queue = lambda: False
    gui._log_temperature_point = lambda target, measuring_flag: None
    gui._comm_recover = lambda context, err: True
    return gui


def _parallel_rc(rp, cp, f):
    """Series (R, X) of a resistor Rp parallel to a capacitor Cp."""
    w = 2 * math.pi * f
    d = 1 + (w * rp * cp) ** 2
    return rp / d, -w * rp ** 2 * cp / d


def _validate_gui(choice, start="20", end="3e6", factor="1.4", acv="1.0"):
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.lcr_backend, fake = connected_backend(mod)
    # The L340 module's AC-voltage entry key is "ac_bias" (pre-existing).
    values = {"sample_name": "S", "ac_bias": acv, "mtm": "0.5", "delay": "0",
              "geom_value": "1.0", "thickness_mm": "1.0"}
    gui.lcr_entries = {k: _Entry(v) for k, v in values.items()}
    gui.wire_cb = _Entry("2")
    gui.lcr_cb = _Entry("GPIB0::10::INSTR  ->  ALPHA Analyzer")
    gui.geom_mode_var = _Entry("diameter")
    gui.fmt_pica_var = _Entry(True)
    gui.fmt_windeta_var = _Entry(True)
    gui.freq_cb = _Entry(choice)
    gui.custom_freq_text = _Entry("")
    gui.log_freq_entries = {"start": _Entry(start), "end": _Entry(end),
                            "factor": _Entry(factor)}
    gui.log = lambda msg: None
    return gui, fake


# --- WinDETA .txt: 11 columns -----------------------------------------------

def test_the_eleven_windeta_columns_read_back_by_name_match_a_parallel_rc():
    rp, cp, freqs = 1e7, 50e-12, (100.0, 1e4, 1e6)
    answers = []
    for f in freqs:
        r, x = _parallel_rc(rp, cp, f)
        answers.append(f"ZRE={r:.10e} {x:.10e} {f:.6e} 2 1")
    gui = _sweep_gui(FakeAlpha({"ZRE?": answers}), freqs)
    assert gui._run_frequency_sweep(150.0, 0, len(freqs)) == len(freqs)

    names = [n for n in os.listdir(gui.save_dir)
             if n.endswith("_FreqScan_windeta_format.txt")]
    with open(os.path.join(gui.save_dir, names[0]), "rb") as fh:
        raw = fh.read()
    assert b"\n" not in raw.replace(b"\r\n", b"")          # CRLF only
    lines = raw.decode("cp1252").split("\r\n")
    header = [h.strip() for h in lines[2].split("\t")]
    assert header == ["Freq. [Hz]", "Eps'", "Eps''", "Tan(Delta)",
                      "Modulus'", "Modulus''", "Zp' [Ohms]", "Zp'' [Ohms]",
                      "Sig' [S/cm]", "Sig'' [S/cm]", "Phi [Deg]"]
    rows = [dict(zip(header, (float(v) for v in ln.split("\t"))))
            for ln in lines[3:-1]]
    assert len(rows) == len(freqs)
    assert all(len(ln.split("\t")) == 11 for ln in lines[3:-1])

    c0 = EPS0_SI * math.pi * (10e-3 / 2) ** 2 / 0.5e-3
    for f, row in zip(freqs, rows):
        w = 2 * math.pi * f
        eps1, eps2 = cp / c0, 1 / (w * rp * c0)
        r, x = _parallel_rc(rp, cp, f)
        want = {
            "Freq. [Hz]": f, "Eps'": eps1, "Eps''": eps2,
            "Tan(Delta)": 1 / (w * rp * cp),
            "Modulus'": eps1 / (eps1 ** 2 + eps2 ** 2),
            "Modulus''": eps2 / (eps1 ** 2 + eps2 ** 2),
            "Zp' [Ohms]": rp, "Zp'' [Ohms]": 1 / (w * cp),
            "Sig' [S/cm]": w * EPS0_CM * eps2,
            "Sig'' [S/cm]": -w * EPS0_CM * (eps1 - 1),
            "Phi [Deg]": math.degrees(math.atan2(x, r)),
        }
        for name, value in want.items():
            assert math.isclose(row[name], value, rel_tol=1e-4), (f, name)
        assert row["Phi [Deg]"] < 0                       # capacitive


def test_the_pica_dat_keeps_its_twenty_lcr_columns():
    gui = _sweep_gui(FakeAlpha(), (1000.0,))
    gui._run_frequency_sweep(80.0, 0, 1)
    names = [n for n in os.listdir(gui.save_dir)
             if n.endswith("_FreqScan_pica_format.dat")]
    with open(os.path.join(gui.save_dir, names[0]), encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines()
                 if not ln.startswith("#")]
    assert lines[0] == mod.AlphaAN_Backend.DATA_HEADER
    assert len(lines[0].split("\t")) == 20
    assert lines[0].split("\t")[-1] == "T_set(K)"
    assert len(lines[1].split("\t")) == 20


# --- Start / End / Factor list ----------------------------------------------

def test_the_list_builder_matches_the_frequency_scan_one():
    for args in ((20, 3e6, 1.4), (20, 1e7, 1.4), (20, 1e6, 1.1),
                 (3e6, 1e6, 1.4)):
        assert (mod.build_log_frequency_list(*args)
                == fscan.build_log_frequency_list(*args)), args
        freqs = mod.build_log_frequency_list(*args)
        assert (mod.log_list_label(freqs, args[2])
                == fscan.log_list_label(freqs, args[2]))
    assert len(mod.build_log_frequency_list(20, 3e6, 1.4)) == 37
    for bad in ((20, 3e6, 1.0), (20, 20, 1.4), (20, 3e8, 1.4),
                (float("nan"), 3e6, 1.4)):
        try:
            mod.build_log_frequency_list(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad}")


def test_the_default_choice_is_start_end_factor():
    assert mod.LOG_FREQ_LABEL == fscan.LOG_FREQ_LABEL
    assert mod.LOG_FREQ_DEFAULTS == ("20", "3e6", "1.4")
    assert mod.DEFAULT_FREQ_CHOICE == mod.LOG_FREQ_LABEL
    assert mod.HINT_FULL_LIST_MAX == fscan.HINT_FULL_LIST_MAX
    assert mod.LOG_FREQ_LABEL not in mod.FREQ_PRESETS
    with open(PATH, encoding="utf-8") as fh:
        src = fh.read()
    assert "self.freq_cb.set(DEFAULT_FREQ_CHOICE)" in src
    assert "values=[LOG_FREQ_LABEL] + list(FREQ_PRESETS) + " \
           "[CUSTOM_FREQ_LABEL]" in src


def test_start_takes_the_start_end_factor_list():
    gui, fake = _validate_gui(mod.LOG_FREQ_LABEL)
    p = gui._validate_lcr_params()
    want = mod.build_log_frequency_list(20.0, 3e6, 1.4)
    assert tuple(gui.sweep_frequencies) == want
    assert p["freq_preset"] == "37 pts, 20 Hz - 3e+06 Hz (x1.4)"
    assert p["freq_preset"].isascii()
    assert fake.commands == []


def test_the_acv_ceiling_applies_to_the_built_list():
    # 3 Vrms is fine up to 4 MHz but not with End = 10 MHz.
    gui, _ = _validate_gui(mod.LOG_FREQ_LABEL, acv="3.0")
    gui._validate_lcr_params()
    gui, fake = _validate_gui(mod.LOG_FREQ_LABEL, end="1e7", acv="3.0")
    gui.sweep_frequencies = ("untouched",)
    try:
        gui._validate_lcr_params()
    except ValueError as exc:
        assert "Vrms" in str(exc)
    else:
        raise AssertionError("3 Vrms accepted for a list ending at 10 MHz")
    assert gui.sweep_frequencies == ("untouched",)
    assert fake.commands == []


def test_bad_start_end_factor_entries_are_refused_with_a_reason():
    for over in ({"start": "abc"}, {"factor": "1"}, {"end": "5e7"}):
        gui, _ = _validate_gui(mod.LOG_FREQ_LABEL, **over)
        try:
            gui._validate_lcr_params()
        except ValueError as exc:
            assert str(exc)
        else:
            raise AssertionError(f"accepted {over}")


def test_the_hint_lists_every_point_up_to_the_limit():
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    gui.hint_show_all = False
    short = mod.build_log_frequency_list(20, 3e6, 1.4)
    text = gui._point_list_text(short)
    assert text.startswith("37 points: 20, 28, 39.2, ")
    assert text.endswith("2.603e+06, 3e+06 Hz") and "click" not in text
    long_list = mod.build_log_frequency_list(20, 3e6, 1.1)
    assert "click to show all" in gui._point_list_text(long_list)
    gui.hint_show_all = True
    assert "click to shorten" in gui._point_list_text(long_list)


# --- geometry default --------------------------------------------------------

def test_diameter_is_the_default_geometry():
    with open(PATH, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"self\.geom_mode_var = tk\.StringVar\(value=\"(\w+)\"\)",
                  src)
    assert m and m.group(1) == "diameter"
    assert '"Diameter (mm):", "geom_value"' in src


# --- spectrum panels: eps' and tan(delta) -----------------------------------

def test_the_plotted_tan_delta_is_eps2_over_eps1():
    rp, cp, freqs = 1e6, 20e-12, (100.0, 1e5)
    answers = []
    for f in freqs:
        r, x = _parallel_rc(rp, cp, f)
        answers.append(f"ZRE={r:.10e} {x:.10e} {f:.6e} 2 1")
    gui = _sweep_gui(FakeAlpha({"ZRE?": answers}), freqs)
    gui._run_frequency_sweep(150.0, 0, len(freqs))
    points = [m for m in list(gui.gui_queue.queue)
              if m["type"] == "scan_point"]
    assert len(points) == len(freqs)
    for m, f in zip(points, freqs):
        r, x = _parallel_rc(rp, cp, f)
        diel = mod.impedance_to_dielectric(r, x, f, gui.c0_farads)
        assert math.isclose(m["eps1"], diel[1], rel_tol=1e-6)
        assert math.isclose(m["tand"], diel[2] / diel[1], rel_tol=1e-6)
        assert "cp" not in m and "g" not in m

    # The GUI side stores them in scan_eps1 / scan_tand.
    gui.MAX_MSGS_PER_CYCLE = 100
    gui.worker_thread = None
    gui.log = lambda msg: None
    gui.scan_f, gui.scan_eps1, gui.scan_tand = [], [], []
    gui._pending_progress = None
    gui._freq_plot_dirty = False
    gui._process_gui_queue()
    assert gui.scan_eps1 == [m["eps1"] for m in points]
    assert gui.scan_tand == [m["tand"] for m in points]
    assert gui._freq_plot_dirty


def _plot_gui():
    from matplotlib.figure import Figure
    gui = mod.CombinedGUI.__new__(mod.CombinedGUI)
    fig = Figure()
    gui.ax_eps1 = fig.add_subplot(211)
    gui.ax_tand = fig.add_subplot(212, sharex=gui.ax_eps1)
    gui.line_eps1, = gui.ax_eps1.plot([], [])
    gui.line_tand, = gui.ax_tand.plot([], [])
    gui.y_scale_var = _Entry("log")
    gui._decade_ylims = {}

    class _Canvas:
        def draw_idle(self):
            pass

    class _Root:
        def after(self, *_args):
            pass
    gui.canvas_plots = _Canvas()
    gui.root = _Root()
    gui._band_dirty = False
    gui._temp_plot_dirty = False
    gui._pending_progress = None
    gui._scan_info = ("done", 150.0)
    return gui


def test_the_spectrum_panels_plot_eps1_and_tan_delta():
    gui = _plot_gui()
    gui.scan_f = [100.0, 1e4, 1e6]
    gui.scan_eps1 = [50.0, 20.0, 10.0]
    gui.scan_tand = [1.0, 0.01, 0.001]
    gui._freq_plot_dirty = True
    gui._redraw_tick()
    assert list(gui.line_eps1.get_ydata()) == gui.scan_eps1
    assert list(gui.line_tand.get_ydata()) == gui.scan_tand
    assert gui.ax_tand.get_yscale() == "log"
    assert "150.00 K" in gui.ax_eps1.get_title()
    with open(PATH, encoding="utf-8") as fh:
        src = fh.read()
    assert 'set_ylabel("Permittivity, ε′")' in src
    assert 'set_ylabel("Loss factor, tan δ")' in src
    for gone in ("ax_cp", "ax_g", "line_cp", "line_g", "scan_cp", "scan_g"):
        assert not re.search(rf"\b{gone}\b", src), gone


def test_a_negative_tan_delta_forces_a_linear_axis():
    gui = _plot_gui()
    gui.scan_f = [100.0, 1e4, 1e6]
    gui.scan_eps1 = [50.0, 20.0, 10.0]
    gui.scan_tand = [0.5, 0.01, 0.001]
    gui._freq_plot_dirty = True
    gui._redraw_tick()
    assert gui.ax_tand.get_yscale() == "log"
    gui.scan_tand.append(-0.02)          # inductive point
    gui.scan_f.append(3e6)
    gui.scan_eps1.append(9.0)
    gui._freq_plot_dirty = True
    gui._redraw_tick()
    assert gui.ax_tand.get_yscale() == "linear"
    assert gui.ax_tand.get_autoscaley_on()
    lo, hi = gui.ax_tand.get_ylim()
    assert lo <= -0.02 and hi >= 0.5
    assert "tand" not in gui._decade_ylims


def test_a_stopped_sweep_is_titled_partial_not_measuring():
    # worker_done turns ('measuring', T) into ('partial', T)
    gui = _plot_gui()
    gui.scan_f, gui.scan_eps1, gui.scan_tand = [100.0], [50.0], [0.1]
    gui._scan_info = ("partial", 150.0)
    gui._freq_plot_dirty = True
    gui._redraw_tick()
    assert gui.ax_eps1.get_title() == "Stopped: 150.00 K (partial)"


# --- same code as the Lakeshore 350 Step module ------------------------------

def test_the_ported_code_is_identical_to_the_350_step_module():
    for name in ("WINDETA_HEADER", "LOG_FREQ_LABEL", "LOG_FREQ_DEFAULTS",
                 "DEFAULT_FREQ_CHOICE", "HINT_FULL_LIST_MAX"):
        assert getattr(mod, name) == getattr(step350, name), name
    for name in ("loss_phase_cp_g", "windeta_row", "build_log_frequency_list",
                 "log_list_label", "impedance_to_dielectric"):
        assert (inspect.getsource(getattr(mod, name))
                == inspect.getsource(getattr(step350, name))), name
    for name in ("_log_list_from_entries", "_point_list_text",
                 "_toggle_hint_list", "_on_freq_list_change",
                 "_update_custom_freq_hint", "_selected_frequencies",
                 "_apply_y_scale"):
        assert (inspect.getsource(getattr(mod.CombinedGUI, name))
                == inspect.getsource(getattr(step350.CombinedGUI, name))), name


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
