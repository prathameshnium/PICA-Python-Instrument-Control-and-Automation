# tests/test_plot_refresh.py
"""Behavioral regression tests for the non-blitted plot refresh pattern.

Each fixed GUI decouples data acquisition from drawing: the acquisition
path only appends to data_storage and sets _plot_dirty; _refresh_plot
(on a 250 ms Tk timer) does set_data + _autoscale_axis + draw_idle().

These tests drive the REAL acquisition path with a mocked instrument
backend, then call _refresh_plot() directly (no Tk mainloop) and assert
that the axis limits actually grow to cover the new data - the exact
symptom the old blitting code broke.

No Tk widgets are created: instances are built with Class.__new__ and
given a real Agg figure/canvas, so the matplotlib side is fully real.
Plain Figure objects are used (never pyplot), so no figure cleanup fixture
is needed - nothing is registered with pyplot's figure manager.
"""
import math
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from pica.keithley.k2400.RT_K2400_L350_T_Control_GUI import RT_GUI_Active
from pica.keithley.k2400_2182.RT_K2400_K2182_T_Control_GUI import VT_GUI_Active
from pica.keithley.delta_mode.Delta_RT_K6221_K2182_L350_Sensing_GUI import (
    MeasurementAppGUI,
)
from pica.keithley.delta_mode.Delta_RT_K6221_K2182_L350_T_Control_GUI import (
    Advanced_Delta_GUI,
)

ALL_GUI_CLASSES = [RT_GUI_Active, VT_GUI_Active,
                   MeasurementAppGUI, Advanced_Delta_GUI]


def _bare_gui(cls):
    """Instance without __init__: no Tk, no hardware, mocked root/log."""
    gui = cls.__new__(cls)
    gui.root = MagicMock()
    gui.log = MagicMock()
    gui._plot_dirty = False
    return gui


def _attach_canvas(gui, fig):
    gui.figure = fig
    gui.canvas = FigureCanvasAgg(fig)


def _brackets(lo_hi, value):
    lo, hi = lo_hi
    return lo <= value <= hi


# ---------------------------------------------------------------------------
# 1. RT_GUI_Active (K2400 + L350): two shared-x axes, fixed log-y main axis
# ---------------------------------------------------------------------------

def _make_rt_gui(tmp_path):
    gui = _bare_gui(RT_GUI_Active)
    fig = Figure()
    gui.ax_main, gui.ax_sub = fig.subplots(2, 1, sharex=True)
    gui.ax_main.set_yscale('log')
    gui.line_main, = gui.ax_main.plot([], [])
    gui.line_sub, = gui.ax_sub.plot([], [])
    _attach_canvas(gui, fig)
    gui.data_storage = {'temperature': [], 'voltage': [], 'resistance': []}
    gui.data_filepath = str(tmp_path / "rt.csv")
    return gui


def test_rt_k2400_real_update_path_rescales_axes(tmp_path):
    gui = _make_rt_gui(tmp_path)
    gui.experiment_state = 'ramping'
    gui.start_time = time.time()
    gui.params = {'current_ma': 1.0, 'cutoff': 999.0, 'end_temp': 999.0,
                  'rate': 1.0, 'delay_s': 0.01}
    gui.backend = MagicMock()
    gui.backend.get_measurement.return_value = (150.0, 0.5)

    gui._experiment_loop()  # the real acquisition path

    assert gui.data_storage['temperature'] == [150.0]
    assert gui.data_storage['resistance'] == [500.0]  # 0.5 V / 1 mA
    assert gui._plot_dirty is True

    gui._refresh_plot()

    assert gui._plot_dirty is False
    assert list(gui.line_main.get_xdata()) == [150.0]
    assert _brackets(gui.ax_main.get_xlim(), 150.0)
    assert _brackets(gui.ax_main.get_ylim(), 500.0)
    assert _brackets(gui.ax_sub.get_ylim(), 0.5)


# ---------------------------------------------------------------------------
# 2. VT_GUI_Active (K2400 + K2182 + L350): single fixed log-y axis
# ---------------------------------------------------------------------------

def _make_vt_gui(tmp_path):
    gui = _bare_gui(VT_GUI_Active)
    fig = Figure()
    gui.ax_main = fig.add_subplot(111)
    gui.ax_main.set_yscale('log')
    gui.line_main, = gui.ax_main.plot([], [])
    _attach_canvas(gui, fig)
    gui.data_storage = {'temperature': [], 'voltage': []}
    gui.data_filepath = str(tmp_path / "vt.csv")
    return gui


def test_vt_k2400_2182_real_update_path_rescales_axes(tmp_path):
    gui = _make_vt_gui(tmp_path)
    gui.experiment_state = 'ramping'
    gui.start_time = time.time()
    gui.params = {'current_ma': 1.0, 'cutoff': 999.0, 'end_temp': 999.0,
                  'rate': 1.0, 'delay_s': 0.01}
    gui.backend = MagicMock()
    gui.backend.get_measurement.return_value = (120.0, 2.5e-3)

    gui._experiment_loop()

    assert gui.data_storage['voltage'] == [2.5e-3]
    assert gui._plot_dirty is True

    gui._refresh_plot()

    assert gui._plot_dirty is False
    assert _brackets(gui.ax_main.get_xlim(), 120.0)
    assert _brackets(gui.ax_main.get_ylim(), 2.5e-3)


def test_vt_negative_voltage_on_log_axis_does_not_crash(tmp_path):
    gui = _make_vt_gui(tmp_path)
    gui.experiment_state = 'idle'
    gui.data_storage['temperature'].append(120.0)
    gui.data_storage['voltage'].append(-0.5)  # invalid on a log axis
    gui._plot_dirty = True

    gui._refresh_plot()  # must not raise

    assert gui.ax_main.get_ylim()[0] > 0  # log axis kept positive limits


# ---------------------------------------------------------------------------
# 3. MeasurementAppGUI (Delta sensing): 3 linear axes, queue-fed
# ---------------------------------------------------------------------------

def _make_delta_sensing_gui(tmp_path):
    gui = _bare_gui(MeasurementAppGUI)
    fig = Figure()
    gui.ax_main = fig.add_subplot(211)
    gui.ax_sub1 = fig.add_subplot(223)
    gui.ax_sub2 = fig.add_subplot(224)
    gui.line_main, = gui.ax_main.plot([], [])
    gui.line_sub1, = gui.ax_sub1.plot([], [])
    gui.line_sub2, = gui.ax_sub2.plot([], [])
    _attach_canvas(gui, fig)
    gui.data_storage = {'time': [], 'voltage': [],
                        'resistance': [], 'temperature': []}
    gui.data_filepath = str(tmp_path / "delta.dat")
    gui.is_running = False  # _refresh_plot must not reschedule
    return gui


def test_delta_sensing_real_update_path_rescales_axes(tmp_path):
    gui = _make_delta_sensing_gui(tmp_path)

    # Real data-arrival path (order: res, volt, temp, elapsed)
    gui._handle_new_data_point((1234.5, 0.012, 77.4, 3.2))

    assert gui.data_storage['resistance'] == [1234.5]
    assert gui.data_storage['temperature'] == [77.4]
    assert gui._plot_dirty is True

    gui._refresh_plot()

    assert gui._plot_dirty is False
    assert _brackets(gui.ax_main.get_xlim(), 77.4)
    assert _brackets(gui.ax_main.get_ylim(), 1234.5)
    assert _brackets(gui.ax_sub1.get_ylim(), 0.012)
    assert _brackets(gui.ax_sub2.get_xlim(), 3.2)
    assert _brackets(gui.ax_sub2.get_ylim(), 77.4)
    gui.root.after.assert_not_called()  # is_running False: no reschedule


# ---------------------------------------------------------------------------
# 4. Advanced_Delta_GUI (Delta T-control): log toggle + inf resistance
# ---------------------------------------------------------------------------

def _make_delta_tcontrol_gui(log_scale=True):
    gui = _bare_gui(Advanced_Delta_GUI)
    fig = Figure()
    gui.ax_main = fig.add_subplot(211)
    gui.ax_sub1 = fig.add_subplot(223)
    gui.ax_sub2 = fig.add_subplot(224)
    if log_scale:
        gui.ax_main.set_yscale('log')
    gui.line_main, = gui.ax_main.plot([], [])
    gui.line_sub1, = gui.ax_sub1.plot([], [])
    gui.line_sub2, = gui.ax_sub2.plot([], [])
    _attach_canvas(gui, fig)
    gui.data_storage = {'time': [], 'temperature': [],
                        'voltage': [], 'resistance': []}
    # Real tk.BooleanVar needs a Tk root; a stand-in with .get() suffices.
    gui.log_scale_var = SimpleNamespace(get=lambda: log_scale)
    gui.data_file_handle = None  # skipped by the loop's `if` guard
    gui.current_heater_range = 'high'
    return gui


def test_delta_tcontrol_real_update_path_rescales_log_axis():
    gui = _make_delta_tcontrol_gui(log_scale=True)
    gui.is_running = True
    gui.start_time = time.time()
    gui.params = {'current': 1e-3, 'cutoff': 999.0, 'end_temp': 999.0}
    gui.backend = MagicMock()
    gui.backend.get_temperature.return_value = 50.0
    gui.backend.get_heater_output.return_value = 12.0
    gui.backend.get_delta_measurement.return_value = 0.02

    gui._update_measurement_loop()  # the real acquisition path

    assert gui.data_storage['resistance'] == [20.0]  # 0.02 V / 1 mA
    assert gui._plot_dirty is True

    gui._refresh_plot()

    assert gui._plot_dirty is False
    assert _brackets(gui.ax_main.get_xlim(), 50.0)
    assert _brackets(gui.ax_main.get_ylim(), 20.0)
    assert gui.ax_main.get_yscale() == 'log'


def test_delta_tcontrol_infinite_resistance_keeps_limits_finite():
    gui = _make_delta_tcontrol_gui(log_scale=False)
    gui.is_running = False
    gui.data_storage['time'] = [1.0, 2.0]
    gui.data_storage['temperature'] = [50.0, 60.0]
    gui.data_storage['voltage'] = [0.02, 0.03]
    gui.data_storage['resistance'] = [20.0, float('inf')]  # I = 0 case
    gui._plot_dirty = True

    gui._refresh_plot()  # must not raise

    assert all(math.isfinite(v) for v in gui.ax_main.get_xlim())
    assert all(math.isfinite(v) for v in gui.ax_main.get_ylim())
    assert _brackets(gui.ax_main.get_ylim(), 20.0)


# ---------------------------------------------------------------------------
# _autoscale_axis unit tests: each file carries its own copy (self-contained
# scripts by design) - test all four so the copies cannot silently drift.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", ALL_GUI_CLASSES,
                         ids=lambda c: c.__name__)
def test_autoscale_axis_filters_invalid_values(cls):
    gui = cls.__new__(cls)
    fig = Figure()
    ax = fig.add_subplot(111)
    ax.set_yscale('log')

    gui._autoscale_axis(ax, x=[1.0, 2.0, 3.0, 4.0],
                        y=[1.0, float('nan'), float('inf'), -5.0],
                        log_y=True)

    ylo, yhi = ax.get_ylim()
    assert ylo > 0 and math.isfinite(yhi)
    assert _brackets((ylo, yhi), 1.0)


@pytest.mark.parametrize("cls", ALL_GUI_CLASSES,
                         ids=lambda c: c.__name__)
def test_autoscale_axis_all_invalid_leaves_limits_unchanged(cls):
    gui = cls.__new__(cls)
    fig = Figure()
    ax = fig.add_subplot(111)
    before_x, before_y = ax.get_xlim(), ax.get_ylim()

    gui._autoscale_axis(ax, x=[1.0], y=[float('nan')], log_y=False)
    gui._autoscale_axis(ax, x=[], y=[], log_y=False)

    assert ax.get_xlim() == before_x
    assert ax.get_ylim() == before_y
