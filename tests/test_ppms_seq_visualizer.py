"""PPMS Sequence Visualizer: the timeline redraw only re-runs tight_layout
when the layout would change, and still looks the same as before.

_plot_timeline used to call figure.tight_layout() on every redraw. It now
compares a cheap signature (figure size, labels, tick label text, banners
that can poke out of the axes) and skips tight_layout when nothing that
affects the margins changed. Checked here against the always-tight_layout
behaviour, on a bare instance with an Agg canvas: no Tk window.
Synthetic segments only. Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys
from datetime import datetime, timedelta

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEQ_PATH = os.path.join(REPO_ROOT, "pica", "PPMS", "PPMS_SeqVisualizer_GUI.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("ppms_seqvis_under_test",
                                                  SEQ_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seqvis = _load_module()
START = datetime(2026, 1, 5, 9, 30)


def _segments(scale=1.0, field=5000.0, rems=("Section A", "Section B")):
    """A generic protocol: cool, two measurements, a field step, REMs."""
    segs, t, T, H = [], 0.0, 300.0, 0.0

    def add(dur, T1, H1, cmd, desc, kind='op'):
        nonlocal t, T, H
        segs.append(dict(t0=t, t1=t + dur, T0=T, T1=T1, H0=H, H1=H1,
                         cmd=cmd, desc=desc, kind=kind))
        t += dur
        T, H = T1, H1

    add(0, T, H, 'REM', rems[0], kind='rem')
    add(3600 * scale, 10.0, 0.0, 'TMP', 'cool to 10 K')
    add(600, 10.0, field, 'FLD', f'field to {field:g} Oe')
    add(5400 * scale, 300.0, field, 'VSMMT', 'M(T) warming', kind='meas')
    if len(rems) > 1:
        add(0, T, H, 'REM', rems[1], kind='rem')
    add(1800, 10.0, field, 'TMP', 'cool to 10 K')
    add(3000, 10.0, -field, 'VSMMH', 'M(H) loop', kind='meas')
    add(600, 10.0, 0.0, 'FLD', 'field to zero')
    return segs


def _bare_app(always_tight=False):
    cls = seqvis.SeqVisualizerGUI
    if always_tight:
        class Old(cls):
            def _relayout_if_needed(self):
                self.figure.tight_layout()
        cls = Old
    app = object.__new__(cls)
    app.figure = Figure(figsize=(9, 6), dpi=60)
    FigureCanvasAgg(app.figure)
    app.canvas = app.figure.canvas
    app.ax_T = app.figure.add_subplot(211)
    app.ax_H = app.figure.add_subplot(212, sharex=app.ax_T)
    app.figure.tight_layout()
    app.filepath = os.path.join("x", "generic_protocol.seq")
    app.segments = []
    return app


def _render(fig):
    fig.canvas.draw()
    return np.asarray(fig.canvas.buffer_rgba()).copy()


REDRAWS = [
    dict(start=START, segs=_segments()),
    dict(start=START, segs=_segments()),                     # identical
    dict(start=START + timedelta(minutes=5), segs=_segments()),
    dict(start=START, segs=_segments(scale=1.4)),            # longer ramps
    dict(start=START, segs=_segments(field=70000.0)),        # wider ticks
    dict(start=START, segs=_segments(rems=("Only one section",))),
    dict(start=START, segs=_segments()),
]


def test_timeline_redraws_keep_the_tight_layout_look():
    """Margins stay those of tight_layout-on-every-redraw (to well under a
    tick-label height; the old code itself drifted that much between two
    identical redraws, because a new axes height re-spaces the ticks), and
    nothing - labels, ticks, REM banners, suptitle - is pushed off the
    figure."""
    new, old = _bare_app(), _bare_app(always_tight=True)
    for step, r in enumerate(REDRAWS):
        for app in (new, old):
            app.segments = r['segs']
            app._plot_timeline(r['start'])
        for a, b in zip(new.figure.axes, old.figure.axes):
            assert np.allclose(a.get_position().bounds,
                               b.get_position().bounds,
                               rtol=0, atol=0.01), step
        _render(new.figure)
        _assert_everything_on_the_figure(new.figure, step)


def _assert_everything_on_the_figure(fig, step):
    renderer = fig.canvas.get_renderer()
    W, H = fig.bbox.width, fig.bbox.height
    boxes = [ax.get_tightbbox(renderer) for ax in fig.axes]
    if fig._suptitle is not None:
        boxes.append(fig._suptitle.get_window_extent(renderer))
    for bb in boxes:
        assert bb.x0 >= -1 and bb.y0 >= -1, (step, bb)
        assert bb.x1 <= W + 1 and bb.y1 <= H + 1, (step, bb, W, H)


def test_an_unchanged_redraw_skips_tight_layout():
    app = _bare_app()
    calls = []
    real = app.figure.tight_layout
    app.figure.tight_layout = lambda *a, **k: (calls.append(1), real())
    app.segments = _segments()
    app._plot_timeline(START)
    assert len(calls) == 1
    app._plot_timeline(START)                 # same plan again
    assert len(calls) == 1
    app.segments = _segments(field=70000.0)   # tick labels change
    app._plot_timeline(START)
    assert len(calls) == 2


def test_a_resized_figure_is_laid_out_again():
    app = _bare_app()
    calls = []
    real = app.figure.tight_layout
    app.figure.tight_layout = lambda *a, **k: (calls.append(1), real())
    app.segments = _segments()
    app._plot_timeline(START)
    app.figure.set_size_inches(12, 7)
    app._plot_timeline(START)
    assert len(calls) == 2


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
