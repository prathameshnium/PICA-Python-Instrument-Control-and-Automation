"""Live Tk scenarios for the launcher v2 hover cards and search box.

NOT collected by a plain `pytest tests/` (the name does not start with
"test_"). tests/test_launcher_v2_search_hover_regressions.py runs this file
in a FRESH Python interpreter and fails if any scenario here fails.

Why a fresh interpreter: these scenarios type into the search box, and a key
event reaches a widget only if that widget's window holds the keyboard focus.
Earlier tests in a full run keep a second Tk interpreter alive for the rest
of the process (tests/test_lakeshore_curve_loader.py shares one root across
the module, on purpose), and two interpreters in one process share the
display's focus state -- the launcher's window then cannot take the focus and
the typed keys go nowhere. The launcher itself never runs two interpreters,
so this is a property of the test process, not of the program; a fresh
interpreter per run is what tests/test_gui_windows_open_for_real.py does for
the same reason.

Run it directly while working on the launcher:

    xvfb-run -a python -m pytest tests/launcher_v2_tk_scenarios.py

The scenarios fail on any error a Tk callback or a Tcl background job raises,
not only on their own assertions.
"""
import importlib.util
import os
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")


def _load_launcher():
    spec = importlib.util.spec_from_file_location("pica_main_v2_scenarios", V2_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pica_main_v2_scenarios"] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()
V2 = launcher.PICALauncherV2

from pica import module_info as mi  # noqa: E402


def _pump(root, ms):
    """Run the Tk event loop for `ms` milliseconds."""
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)
    root.update()


class TkErrors:
    """Collects Tk callback exceptions and Tcl background errors."""

    def __init__(self, root):
        self.callback = []
        root.report_callback_exception = (
            lambda exc, val, tb: self.callback.append(f"{exc.__name__}: {val}"))
        root.tk.eval("set ::pica_bgerrors {}")
        root.tk.eval("proc bgerror {msg} {lappend ::pica_bgerrors $msg}")
        self.root = root

    def all(self):
        try:
            bg = list(self.root.tk.splitlist(self.root.tk.eval("set ::pica_bgerrors")))
        except Exception:
            bg = []
        return self.callback + bg


@pytest.fixture
def app():
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:  # no display, mocked tkinter, incomplete Tcl
        pytest.skip(f"no usable Tk: {e}")
    if not hasattr(root, "winfo_exists") or type(root).__module__ != "tkinter":
        pytest.skip("tkinter is mocked in this run")
    errors = TkErrors(root)
    launcher_app = V2.__new__(V2)
    launcher_app.start_scan = lambda: None
    launcher_app._open_startup_status = lambda: None
    launcher_app._auto_launch_gpib_scanner = lambda: None
    launcher_app.launched = []
    try:
        V2.__init__(launcher_app, root)
    except Exception as e:
        root.destroy()
        pytest.skip(f"launcher could not be built here: {e}")
    launcher_app.launch_script = lambda key, argv=None: launcher_app.launched.append(key)
    root.geometry("1400x860+0+0")
    root.deiconify()
    _pump(root, 50)
    launcher_app.errors = errors
    yield launcher_app
    try:
        alive = bool(root.winfo_exists())
    except Exception:
        alive = False           # the test destroyed the launcher itself
    try:
        if alive:
            if launcher_app._adv_win is not None:
                launcher_app._close_advanced()
            _pump(root, 30)
        leftover = errors.all()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    assert leftover == [], f"Tk raised during the test: {leftover}"


def _rows(win):
    from tkinter import ttk
    found = []

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Button) and str(c.cget("style")) == "Mod.TButton":
                found.append(c)
            walk(c)
    walk(win)
    return found


def _labels(widget):
    import tkinter as tk
    out = []
    for c in widget.winfo_children():
        if isinstance(c, tk.Label):
            out.append(c.cget("text"))
        out.extend(_labels(c))
    return out


def _open_advanced(app):
    app.open_advanced()
    _pump(app.root, 60)
    return app._adv_win


def _close_by_title_bar(win):
    """What the window manager's X button does: run WM_DELETE_WINDOW."""
    win.tk.call(win.protocol("WM_DELETE_WINDOW"))


def _focus(app, entry):
    entry.focus_force()
    _pump(app.root, 30)


def _type(app, entry, text):
    """Type `text` into the box as key events; returns once they landed."""
    for ch in text:
        keysym = {" ": "space"}.get(ch, ch)
        entry.event_generate("<KeyPress>", keysym=keysym, when="tail")
        entry.event_generate("<KeyRelease>", keysym=keysym, when="tail")
        app.root.update()
    if entry.get() != text:
        # Some X servers do not map synthetic keysyms to characters. The
        # text is then put in directly and the release that drives the
        # search is still a real event.
        entry.delete(0, "end")
        entry.insert(0, text)
        entry.event_generate("<KeyRelease>", keysym=text[-1:] or "a")
        app.root.update()


def _settle_search(app):
    _pump(app.root, V2.SEARCH_DEBOUNCE_MS + 120)


# ---------------------------------------------------------------- Advanced
def test_advanced_closes_after_the_pointer_crossed_rows(app):
    """THE reported fault: hover a few rows, then the X button."""
    win = _open_advanced(app)
    for row in _rows(win)[:12]:
        row.event_generate("<Enter>")
        app.root.update()
        row.event_generate("<Leave>")
        app.root.update()
    _close_by_title_bar(win)
    _pump(app.root, 30)
    assert not win.winfo_exists()
    assert app._adv_win is None


def test_advanced_closes_with_a_hover_card_open(app):
    win = _open_advanced(app)
    row = _rows(win)[5]
    row.event_generate("<Enter>")
    _pump(app.root, V2.HOVER_DELAY_MS + 150)
    card = app._hover_win
    assert card is not None and card.winfo_exists()
    _close_by_title_bar(win)
    _pump(app.root, 30)
    assert not win.winfo_exists()
    assert not card.winfo_exists()
    assert app._hover_win is None


def test_advanced_closes_with_a_hover_timer_still_pending(app):
    win = _open_advanced(app)
    _rows(win)[2].event_generate("<Enter>")
    app.root.update()
    assert app._hover_after is not None
    _close_by_title_bar(win)
    # Let the cancelled timer's moment pass: nothing may fire into the
    # destroyed window.
    _pump(app.root, V2.HOVER_DELAY_MS + 200)
    assert not win.winfo_exists()
    assert app._hover_win is None


def test_advanced_closes_with_its_search_panel_open(app):
    win = _open_advanced(app)
    entry = app._search_boxes[-1]
    _focus(app, entry)
    _type(app, entry, "lcr")
    _settle_search(app)
    assert app._search_win is not None
    _close_by_title_bar(win)
    _pump(app.root, 30)
    assert not win.winfo_exists()
    assert app._search_win is None
    assert len(app._search_boxes) == 1


def test_advanced_opens_and_closes_again_and_again(app):
    for _ in range(3):
        win = _open_advanced(app)
        assert len(app._search_boxes) == 2
        rows = _rows(win)
        for row in rows[:6] + rows[-6:]:
            row.event_generate("<Enter>")
            row.event_generate("<Leave>")
        app.root.update()
        _close_by_title_bar(win)
        _pump(app.root, 30)
        assert not win.winfo_exists()
        assert len(app._search_boxes) == 1


def test_advanced_closes_even_if_the_clean_up_fails(app):
    win = _open_advanced(app)

    def broken():
        raise RuntimeError("simulated clean-up failure")
    real = app._hide_hover
    app._hide_hover = broken
    try:
        _close_by_title_bar(win)
        _pump(app.root, 30)
    finally:
        app._hide_hover = real
    assert not win.winfo_exists()
    assert app._adv_win is None
    # The rows' own teardown called the broken function too; those errors
    # were provoked on purpose. Anything else still fails the test.
    app.errors.callback[:] = [e for e in app.errors.callback
                              if "simulated clean-up failure" not in e]


def test_the_card_grid_reflows_after_a_hover_without_error(app):
    """A reflow destroys and rebuilds every row; it hit the same fault."""
    win = _open_advanced(app)
    for row in _rows(win)[:10]:
        row.event_generate("<Enter>")
        row.event_generate("<Leave>")
    app.root.update()
    for cols in (1, 3, 2):
        app._browse_cols = cols
        app._render_cards()
        _pump(app.root, 20)
    assert win.winfo_exists()
    assert len(_rows(win)) == sum(len(c["modules"]) for c in launcher.CATALOG)


def test_quitting_the_launcher_mid_use_is_clean(app):
    """Exit with Advanced open, a hover timer armed and a search pending."""
    win = _open_advanced(app)
    rows = _rows(win)
    for row in rows[:5]:
        row.event_generate("<Enter>")
        row.event_generate("<Leave>")
    rows[6].event_generate("<Enter>")       # hover timer left armed
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")              # debounce left pending
    app.root.destroy()
    assert app.errors.callback == []


# ------------------------------------------------------------------- hover
def test_resting_on_a_row_opens_its_card(app):
    win = _open_advanced(app)
    row = next(r for r in _rows(win) if r.cget("text") == "R vs. T (T Control)")
    row.event_generate("<Enter>")
    _pump(app.root, V2.HOVER_DELAY_MS + 150)
    card = app._hover_win
    assert card is not None and card.winfo_exists()
    texts = _labels(card)
    for caption in ("MEASURES", "INSTRUMENTS", "YOU ENTER", "SCRIPT"):
        assert caption in texts
    row.event_generate("<Leave>")
    app.root.update()
    assert app._hover_win is None
    assert not card.winfo_exists()


def test_passing_over_a_row_opens_no_card(app):
    win = _open_advanced(app)
    row = _rows(win)[0]
    row.event_generate("<Enter>")
    _pump(app.root, V2.HOVER_DELAY_MS // 3)
    row.event_generate("<Leave>")
    _pump(app.root, V2.HOVER_DELAY_MS + 150)
    assert app._hover_win is None


def test_clicking_a_row_closes_the_card_and_launches(app):
    win = _open_advanced(app)
    row = _rows(win)[0]
    row.event_generate("<Enter>")
    _pump(app.root, V2.HOVER_DELAY_MS + 150)
    assert app._hover_win is not None
    row.event_generate("<ButtonPress-1>")
    row.invoke()
    app.root.update()
    assert app._hover_win is None
    assert app.launched == [launcher.CATALOG[0]["modules"][0][1]]


def test_every_row_arms_and_disarms_its_own_card(app):
    win = _open_advanced(app)
    rows = _rows(win)
    assert len(rows) == sum(len(c["modules"]) for c in launcher.CATALOG)
    for row in rows:
        row.event_generate("<Enter>")
        assert app._hover_after is not None, row.cget("text")
        row.event_generate("<Leave>")
        assert app._hover_after is None, row.cget("text")
    app.root.update()


def test_every_row_card_describes_that_row(app):
    """Each row's card carries that row's own description, not a neighbour's."""
    win = _open_advanced(app)
    category_of = {key: cat["category"] for cat in launcher.CATALOG
                   for _label, key, _family in cat["modules"]}
    family_of = {key: family for cat in launcher.CATALOG
                 for _label, key, family in cat["modules"]}
    rows = _rows(win)
    assert {row._pica_key for row in rows} == set(category_of)
    for row in rows:
        key = row._pica_key
        app._show_hover(row, key, row.cget("text"), category_of[key],
                        family_of[key])
        card = app._hover_win
        assert card is not None
        texts = _labels(card)
        assert mi.MODULE_INFO[key]["what"] in texts, key
        assert row.cget("text") in texts, key
        app._hide_hover()
    app.root.update()


def test_the_hover_card_is_a_child_of_the_window_not_of_the_row(app):
    win = _open_advanced(app)
    row = _rows(win)[0]
    app._show_hover(row, "Delta Mode I-V Sweep", "Sweep Mode I-V", "Low")
    assert app._hover_win.master is win
    app._hide_hover()


# ------------------------------------------------------------------ search
def test_typing_keeps_the_focus_in_the_box(app):
    """THE search fault: the results window took the focus from the box."""
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "k2400")
    _settle_search(app)
    assert app._search_win is not None
    assert app.root.focus_get() is entry
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    assert entry.get() == "k2400 rt"
    assert app.root.focus_get() is entry


def test_the_panel_lives_inside_the_window_that_owns_the_box(app):
    import tkinter as tk
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")
    _settle_search(app)
    panel = app._search_win
    assert not isinstance(panel, tk.Toplevel)
    assert panel.winfo_toplevel() is app.root
    assert panel.winfo_ismapped()
    # Fully inside the window.
    x, y = panel.winfo_x(), panel.winfo_y()
    assert x >= 0 and y >= 0
    assert x + panel.winfo_width() <= app.root.winfo_width() + 1


def test_the_panel_is_built_once_and_reused(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "del")
    _settle_search(app)
    first = app._search_win
    _type(app, entry, "delta mode")
    _settle_search(app)
    assert app._search_win is first
    app._search_escape(entry)
    _type(app, entry, "lcr")
    _settle_search(app)
    assert app._search_win is first


def test_typing_is_debounced(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    calls = []
    real = app.run_search
    app.run_search = lambda e: (calls.append(e.get()), real(e))[1]
    _type(app, entry, "lakeshore")
    assert calls == []          # nothing ranked while keys are arriving
    _settle_search(app)
    assert calls == ["lakeshore"]


def test_down_and_up_move_the_highlight_and_the_card_follows(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    first = app._search_selected()
    entry.event_generate("<Down>")
    app.root.update()
    second = app._search_selected()
    assert second["key"] != first["key"]
    assert second["label"] in _labels(app._search_card)
    entry.event_generate("<Up>")
    app.root.update()
    assert app._search_selected()["key"] == first["key"]
    for _ in range(3):
        entry.event_generate("<Up>")    # stops at the top, never wraps
    app.root.update()
    assert app._search_selected()["key"] == first["key"]


def test_enter_before_the_pause_only_refreshes(app):
    """Nothing is launched that the user has not seen highlighted."""
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "k2400 rt")
    entry.event_generate("<Return>")      # before the debounce fires
    app.root.update()
    assert app.launched == []
    assert app._search_win is not None
    shown = app._search_selected()["key"]
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == [shown]
    assert app._search_win is None


def test_enter_launches_the_highlighted_result(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    entry.event_generate("<Down>")
    app.root.update()
    picked = app._search_selected()["key"]
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == [picked]


def test_enter_with_no_match_launches_nothing(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "zzqx")
    _settle_search(app)
    assert any("No module matches" in t for t in _labels(app._search_win))
    entry.event_generate("<Return>")
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == []


def test_escape_closes_then_clears(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")
    _settle_search(app)
    entry.event_generate("<Escape>")
    app.root.update()
    assert app._search_win is None
    assert entry.get() == "delta"
    entry.event_generate("<Escape>")
    app.root.update()
    assert entry.get() in ("", V2.SEARCH_PLACEHOLDER)


def test_clearing_the_box_closes_the_panel(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")
    _settle_search(app)
    entry.delete(0, "end")
    entry.event_generate("<KeyRelease>", keysym="BackSpace")
    _settle_search(app)
    assert app._search_win is None


def test_clicking_a_result_selects_it_and_keeps_the_panel(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    lb = app._search_list
    want = app._search_results[2]["key"]
    bbox = lb.bbox(2)
    lb.event_generate("<Button-1>", x=bbox[0] + 5, y=bbox[1] + 2)
    app.root.update()
    assert app._search_win is not None
    assert app._search_selected()["key"] == want
    assert app.root.focus_get() is entry     # keys still go to the box
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == [want]


def test_double_click_launches_the_result(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "plotter")
    _settle_search(app)
    lb = app._search_list
    want = app._search_results[1]["key"]
    bbox = lb.bbox(1)
    x, y = bbox[0] + 5, bbox[1] + 2
    # Two presses 100 ms apart at one spot: Tk reads them as a double-click.
    lb.event_generate("<ButtonPress-1>", x=x, y=y, time=1000)
    lb.event_generate("<ButtonRelease-1>", x=x, y=y, time=1020)
    lb.event_generate("<ButtonPress-1>", x=x, y=y, time=1100)
    lb.event_generate("<ButtonRelease-1>", x=x, y=y, time=1120)
    app.root.update()
    assert app.launched == [want]
    assert app._search_win is None


def test_a_click_elsewhere_closes_the_panel(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")
    _settle_search(app)
    app.launch_btn.event_generate("<Button-1>")
    app.root.update()
    assert app._search_win is None


def test_tab_away_closes_the_panel(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")
    _settle_search(app)
    app.cat_combo.focus_force()
    _pump(app.root, 40)
    assert app._search_win is None


def test_the_placeholder_is_never_searched(app):
    entry = app._search_boxes[0]
    assert entry.get() == V2.SEARCH_PLACEHOLDER
    assert app.run_search(entry) == []
    assert app._search_win is None
    _focus(app, entry)
    assert entry.get() == ""
    app.launch_btn.focus_force()
    _pump(app.root, 40)
    assert entry.get() == V2.SEARCH_PLACEHOLDER


def test_ctrl_f_focuses_the_box_of_the_window_in_front(app):
    main_entry = app._search_boxes[0]
    app.cat_combo.focus_force()
    _pump(app.root, 30)
    app.cat_combo.event_generate("<Control-f>")
    _pump(app.root, 30)
    assert app.root.focus_get() is main_entry

    win = _open_advanced(app)
    adv_entry = app._search_boxes[1]
    win.focus_force()
    _pump(app.root, 30)
    win.event_generate("<Control-f>")
    _pump(app.root, 30)
    assert app.root.focus_get() is adv_entry


def test_the_two_boxes_do_not_share_one_panel(app):
    win = _open_advanced(app)
    main_entry, adv_entry = app._search_boxes
    _focus(app, main_entry)
    _type(app, main_entry, "delta")
    _settle_search(app)
    main_panel = app._search_win
    assert main_panel.winfo_toplevel() is app.root
    win.focus_force()
    _focus(app, adv_entry)
    _type(app, adv_entry, "lcr")
    _settle_search(app)
    assert app._search_win is not main_panel
    assert app._search_win.winfo_toplevel() is win
    assert not main_panel.winfo_ismapped()


def test_a_pending_search_on_a_destroyed_box_is_harmless(app):
    win = _open_advanced(app)
    adv_entry = app._search_boxes[1]
    _focus(app, adv_entry)
    _type(app, adv_entry, "lcr")         # debounce pending
    _close_by_title_bar(win)
    _settle_search(app)                  # the timer's moment passes
    assert app._search_win is None


def test_the_panel_follows_a_window_resize(app):
    entry = app._search_boxes[0]
    _focus(app, entry)
    _type(app, entry, "delta")
    _settle_search(app)
    app.root.geometry("1200x800+0+0")
    _pump(app.root, 80)
    panel = app._search_win
    assert panel is not None
    assert panel.winfo_x() + panel.winfo_width() <= app.root.winfo_width() + 1
