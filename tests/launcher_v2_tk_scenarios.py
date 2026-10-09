"""Live Tk scenarios for the launcher v2 hover cards and Ctrl+F search.

NOT collected by a plain `pytest tests/` (the name does not start with
"test_"). tests/test_launcher_v2_search_hover_regressions.py runs this file
in a FRESH Python interpreter and fails if any scenario here fails.

Why a fresh interpreter: these scenarios type into the search panel, and a key
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


# One Tk root for the whole run; each scenario builds its launcher on a
# fresh Toplevel and destroys only that. Creating and destroying a Tk root
# per test is unreliable on Windows: the second or third root can fail with
# "Can't find a usable tk.tcl" (seen on the GitHub Windows runner), and the
# scenario then skipped instead of running. tests/test_lakeshore_curve_loader.py
# shares a root for the same reason.
_SHARED_ROOT = None
_SHARED_ROOT_ERROR = None


def _shared_root():
    global _SHARED_ROOT, _SHARED_ROOT_ERROR
    if _SHARED_ROOT_ERROR is not None:
        pytest.skip(f"no usable Tk: {_SHARED_ROOT_ERROR}")
    if _SHARED_ROOT is None:
        try:
            import tkinter as tk
            _SHARED_ROOT = tk.Tk()
            _SHARED_ROOT.withdraw()
        except Exception as e:  # no display, incomplete Tcl
            _SHARED_ROOT_ERROR = e
            pytest.skip(f"no usable Tk: {e}")
    return _SHARED_ROOT


def _cancel_every_timer(root):
    """Drop every pending after() job, so none fires into the next scenario.

    Raw 'after cancel' on purpose: tkinter's after_cancel also deletes the
    job's Tcl command, which the window that scheduled it deletes again when
    it is destroyed -- the very fault these scenarios guard against.
    """
    try:
        for ident in root.tk.splitlist(root.tk.call("after", "info")):
            root.tk.call("after", "cancel", ident)
    except Exception:
        pass


@pytest.fixture
def app():
    import tkinter as tk
    shared = _shared_root()
    _cancel_every_timer(shared)
    shared.update()
    errors = TkErrors(shared)
    window = tk.Toplevel(shared)
    launcher_app = V2.__new__(V2)
    launcher_app.start_scan = lambda: None
    launcher_app._open_startup_status = lambda: None
    launcher_app._auto_launch_gpib_scanner = lambda: None
    launcher_app.launched = []
    try:
        V2.__init__(launcher_app, window)
    except Exception as e:
        window.destroy()
        pytest.fail(f"launcher could not be built: {e}")
    launcher_app.launch_script = lambda key, argv=None: launcher_app.launched.append(key)
    window.geometry("1400x860+0+0")
    window.deiconify()
    _pump(window, 50)
    launcher_app.errors = errors
    yield launcher_app
    try:
        alive = bool(window.winfo_exists())
    except Exception:
        alive = False           # the test destroyed the launcher itself
    try:
        if alive:
            if launcher_app._adv_win is not None:
                launcher_app._close_advanced()
            _pump(window, 30)
        leftover = errors.all()
    finally:
        try:
            if alive:
                window.destroy()
        except Exception as e:
            leftover = list(leftover) + [f"destroying the launcher raised: {e}"]
        _cancel_every_timer(shared)
        shared.update()
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


def _ctrl_f(app, widget):
    """Press Ctrl+F in `widget`; returns the search entry it opened."""
    widget.focus_force()
    _pump(app.root, 30)
    widget.event_generate("<Control-f>")
    _pump(app.root, 40)
    entry = app._search_owner
    assert entry is not None, "Ctrl+F opened no search"
    return entry


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
    entry = _ctrl_f(app, win)
    _type(app, entry, "lcr")
    _settle_search(app)
    assert app._search_win is not None
    _close_by_title_bar(win)
    _pump(app.root, 30)
    assert not win.winfo_exists()
    assert app._search_win is None
    assert str(win) not in app._search_panels


def test_advanced_opens_and_closes_again_and_again(app):
    for _ in range(3):
        win = _open_advanced(app)
        rows = _rows(win)
        for row in rows[:6] + rows[-6:]:
            row.event_generate("<Enter>")
            row.event_generate("<Leave>")
        app.root.update()
        entry = _ctrl_f(app, win)
        _type(app, entry, "rt")
        _settle_search(app)
        _close_by_title_bar(win)
        _pump(app.root, 30)
        assert not win.winfo_exists()
        assert app._search_win is None
        assert str(win) not in app._search_panels


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
    entry = _ctrl_f(app, app.cat_combo)
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
    for caption in ("MEASURES", "INSTRUMENTS", "INPUT FIELDS", "SCRIPT"):
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
        # Headed by the program's full name, which contains the row label.
        full = mi.display_name(key, row.cget("text"), family_of[key])
        assert full in texts, key
        app._hide_hover()
    app.root.update()


def test_the_hover_card_is_a_child_of_the_window_not_of_the_row(app):
    win = _open_advanced(app)
    row = _rows(win)[0]
    app._show_hover(row, "Delta Mode I-V Sweep", "Sweep Mode I-V", "Low")
    assert app._hover_win.master is win
    app._hide_hover()


# ------------------------------------------------------------------ search
def _entries(widget):
    """Every plain text box under a widget (the search box is one).

    type() rather than isinstance(): a ttk Combobox -- the Quick Select
    dropdowns -- is an Entry subclass, and is not a search box.
    """
    import tkinter as tk
    out = []
    for c in widget.winfo_children():
        if type(c) is tk.Entry:
            out.append(c)
        out.extend(_entries(c))
    return out


def test_no_search_box_sits_on_either_window(app):
    """The search is a Ctrl+F panel now; no window carries a search field."""
    assert app._search_panels == {}
    assert _entries(app.root) == []
    win = _open_advanced(app)
    assert _entries(win) == []
    assert app._search_panels == {}


def test_ctrl_f_opens_the_panel_centred_with_the_cursor_in_it(app):
    entry = _ctrl_f(app, app.cat_combo)
    panel = app._search_win
    assert panel.winfo_ismapped()
    assert app.root.focus_get() is entry
    left = panel.winfo_x()
    right = app.root.winfo_width() - (left + panel.winfo_width())
    assert abs(left - right) <= 2, (left, right)          # centred
    assert panel.winfo_y() < app.root.winfo_height() // 3  # near the top
    assert V2.SEARCH_HINT in _labels(panel)               # empty: the hint


def test_the_panel_is_part_of_the_window_not_a_window_of_its_own(app):
    import tkinter as tk
    _ctrl_f(app, app.cat_combo)
    assert not isinstance(app._search_win, tk.Toplevel)
    assert app._search_win.winfo_toplevel() is app.root


def test_ctrl_f_in_advanced_opens_over_advanced(app):
    win = _open_advanced(app)
    entry = _ctrl_f(app, win)
    assert app._search_window is win
    assert app._search_win.winfo_toplevel() is win
    assert win.focus_get() is entry


def test_ctrl_f_from_any_other_window_opens_over_the_main_window(app):
    import tkinter as tk
    other = tk.Toplevel(app.root)       # e.g. the console or status window
    other.geometry("300x200+50+50")
    _pump(app.root, 40)
    try:
        _ctrl_f(app, other)
        assert app._search_window is app.root
    finally:
        other.destroy()


def test_ctrl_f_again_keeps_the_text_and_selects_it(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    entry.event_generate("<Control-f>")
    _pump(app.root, 40)
    assert app._search_owner is entry
    assert entry.get() == "delta"
    assert entry.selection_present()


def test_the_tools_menu_opens_the_search_in_both_windows(app):
    def item(win):
        bar = app.root.nametowidget(win.cget("menu"))
        for i in range(bar.index("end") + 1):
            if bar.type(i) == "cascade" and bar.entrycget(i, "label") == "Tools":
                tools = app.root.nametowidget(bar.entrycget(i, "menu"))
                for j in range(tools.index("end") + 1):
                    if tools.type(j) == "command" and \
                            tools.entrycget(j, "label") == "Search Modules…":
                        return tools, j
        raise AssertionError("no Tools > Search Modules… entry")

    tools, j = item(app.root)
    assert tools.entrycget(j, "accelerator") == "Ctrl+F"
    tools.invoke(j)
    _pump(app.root, 40)
    assert app._search_window is app.root
    app.close_search()

    win = _open_advanced(app)
    tools, j = item(win)
    tools.invoke(j)
    _pump(app.root, 40)
    assert app._search_window is win


def test_typing_keeps_the_focus_in_the_box(app):
    """The old fault: a popup window took the focus from the search box."""
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "k2400")
    _settle_search(app)
    assert app._search_results
    assert app.root.focus_get() is entry
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    assert entry.get() == "k2400 rt"
    assert app.root.focus_get() is entry


def test_the_panel_is_built_once_per_window_and_reused(app):
    entry = _ctrl_f(app, app.cat_combo)
    first = app._search_win
    _type(app, entry, "del")
    _settle_search(app)
    _type(app, entry, "delta mode")
    _settle_search(app)
    assert app._search_win is first
    entry.event_generate("<Escape>")
    app.root.update()
    again = _ctrl_f(app, app.cat_combo)
    assert again is entry and app._search_win is first


def test_typing_is_debounced(app):
    entry = _ctrl_f(app, app.cat_combo)
    calls = []
    real = app.run_search
    app.run_search = lambda e: (calls.append(e.get()), real(e))[1]
    _type(app, entry, "lakeshore")
    assert calls == []          # nothing ranked while keys are arriving
    _settle_search(app)
    assert calls == ["lakeshore"]


def test_clearing_the_text_brings_the_hint_back(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    assert app._search_results
    entry.delete(0, "end")
    entry.event_generate("<KeyRelease>", keysym="BackSpace")
    _settle_search(app)
    assert app._search_win is not None              # still open
    assert app._search_results == []
    assert V2.SEARCH_HINT in _labels(app._search_win)


def test_down_and_up_move_the_highlight_and_the_card_follows(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    first = app._search_selected()
    entry.event_generate("<Down>")
    app.root.update()
    second = app._search_selected()
    assert second["key"] != first["key"]
    assert second["name"] in _labels(app._search_card)
    entry.event_generate("<Up>")
    app.root.update()
    assert app._search_selected()["key"] == first["key"]
    for _ in range(3):
        entry.event_generate("<Up>")    # stops at the top, never wraps
    app.root.update()
    assert app._search_selected()["key"] == first["key"]


def test_enter_before_the_pause_only_refreshes(app):
    """Nothing is launched that the user has not seen highlighted."""
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "k2400 rt")
    entry.event_generate("<Return>")      # before the debounce fires
    app.root.update()
    assert app.launched == []
    assert app._search_results
    shown = app._search_selected()["key"]
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == [shown]
    assert app._search_win is None


def test_enter_launches_the_highlighted_result_and_gives_the_focus_back(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "k2400 rt")
    _settle_search(app)
    entry.event_generate("<Down>")
    app.root.update()
    picked = app._search_selected()["key"]
    entry.event_generate("<Return>")
    _pump(app.root, 30)
    assert app.launched == [picked]
    assert app._search_win is None
    assert app.root.focus_get() is app.cat_combo


def test_enter_with_no_match_launches_nothing(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "zzqx")
    _settle_search(app)
    assert any("No module matches" in t for t in _labels(app._search_win))
    entry.event_generate("<Return>")
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == []


def test_enter_on_an_empty_box_launches_nothing(app):
    entry = _ctrl_f(app, app.cat_combo)
    entry.event_generate("<Return>")
    entry.event_generate("<Return>")
    app.root.update()
    assert app.launched == []
    assert app._search_win is not None


def test_escape_closes_and_gives_the_focus_back(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    entry.event_generate("<Escape>")
    _pump(app.root, 30)
    assert app._search_win is None
    assert app.root.focus_get() is app.cat_combo
    # The text is kept for the next Ctrl+F.
    assert _ctrl_f(app, app.cat_combo).get() == "delta"


def test_clicking_a_result_selects_it_and_keeps_the_panel(app):
    entry = _ctrl_f(app, app.cat_combo)
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
    """A double-click on a result launches that result.

    Tk decides what counts as a double-click from the timestamps of real
    pointer events; synthetic presses carry made-up times, and whether Tk
    pairs them differs between machines. So the two halves are tested
    exactly: the binding is on the list, and what it runs launches the
    clicked row.
    """
    import inspect
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "plotter")
    _settle_search(app)
    lb = app._search_list
    assert lb.bind("<Double-Button-1>"), "no double-click binding on the list"
    want = app._search_results[1]["key"]
    bbox = lb.bbox(1)
    lb.event_generate("<Button-1>", x=bbox[0] + 5, y=bbox[1] + 2)
    app.root.update()
    assert app._search_selected()["key"] == want
    panel_source = inspect.getsource(V2._search_panel_for)
    assert ('listbox.bind("<Double-Button-1>", lambda _e: self._search_launch())'
            in panel_source)
    app._search_launch()
    app.root.update()
    assert app.launched == [want]
    assert app._search_win is None


def test_a_click_elsewhere_closes_the_panel(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    app.launch_btn.event_generate("<Button-1>")
    app.root.update()
    assert app._search_win is None


def test_a_click_inside_the_panel_keeps_it_open(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    app._search_card.event_generate("<Button-1>")
    app.root.update()
    assert app._search_win is not None


def test_tab_away_closes_the_panel(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    app.cat_combo.focus_force()
    _pump(app.root, 40)
    assert app._search_win is None


def test_opening_in_the_other_window_moves_the_panel(app):
    main_entry = _ctrl_f(app, app.cat_combo)
    _type(app, main_entry, "delta")
    _settle_search(app)
    main_panel = app._search_win
    win = _open_advanced(app)
    adv_entry = _ctrl_f(app, win)
    assert adv_entry is not main_entry
    assert app._search_window is win
    assert not main_panel.winfo_ismapped()


def test_a_pending_search_on_a_closed_window_is_harmless(app):
    win = _open_advanced(app)
    entry = _ctrl_f(app, win)
    _type(app, entry, "lcr")             # debounce pending
    _close_by_title_bar(win)
    _settle_search(app)                  # the timer's moment passes
    assert app._search_win is None
    assert str(win) not in app._search_panels


def test_the_panel_stays_centred_after_a_resize(app):
    entry = _ctrl_f(app, app.cat_combo)
    _type(app, entry, "delta")
    _settle_search(app)
    app.root.geometry("1200x800+0+0")
    _pump(app.root, 120)
    panel = app._search_win
    left = panel.winfo_x()
    right = app.root.winfo_width() - (left + panel.winfo_width())
    assert abs(left - right) <= 2, (left, right)
    assert left >= 0 and right >= 0


def test_the_panel_fits_a_narrow_window(app):
    app.root.geometry("700x600+0+0")
    _pump(app.root, 80)
    _ctrl_f(app, app.cat_combo)
    panel = app._search_win
    assert panel.winfo_x() >= 0
    assert panel.winfo_x() + panel.winfo_width() <= app.root.winfo_width()


# ------------------------------------------------- Advanced Options opening
def test_advanced_builds_its_grid_once_while_hidden(app):
    """Opening Advanced Options lays the cards out once, before it is shown.

    It used to be shown first and filled in on screen card by card, then laid
    out a second time when the maximise arrived -- slow, and it flickered.
    """
    builds = []
    real = V2._render_cards_inner

    def counting(self):
        builds.append(bool(self._adv_win.winfo_ismapped()))
        real(self)
    app._render_cards_inner = counting.__get__(app)
    win = _open_advanced(app)
    _pump(app.root, 200)
    assert builds == [False], builds        # one build, window still hidden
    assert win.winfo_ismapped()             # and then it is shown
    assert len(_rows(win)) == sum(len(c["modules"]) for c in launcher.CATALOG)


def test_advanced_opened_maximised_is_still_built_once(app):
    """Where the window opens maximised (Windows), the grid is laid out for
    the maximised width up front, so the maximise does not rebuild it."""
    import tkinter as tk
    width = app.root.winfo_screenwidth()
    builds = []
    real = V2._render_cards_inner
    app._render_cards_inner = (lambda self: (builds.append(1), real(self))[1]).__get__(app)
    original_platform = launcher.sys.platform
    original_state = tk.Toplevel.state

    def maximise(self, newstate=None):
        if newstate == "zoomed":
            self.geometry(f"{width}x800+0+0")
            return None
        return original_state(self, newstate)
    launcher.sys.platform = "win32"
    tk.Toplevel.state = maximise
    try:
        _open_advanced(app)
        _pump(app.root, 300)
    finally:
        launcher.sys.platform = original_platform
        tk.Toplevel.state = original_state
    assert builds == [1], builds


def test_reopening_advanced_reuses_the_scaled_logo(app):
    win = _open_advanced(app)
    first = app._adv_logo_image
    _close_by_title_bar(win)
    _pump(app.root, 30)
    _open_advanced(app)
    if first is not None:                   # PIL and the logo are available
        assert app._adv_logo_image is first


def test_the_scanner_is_not_started_for_a_closed_advanced_window(app):
    started = []
    win = _open_advanced(app)
    _close_by_title_bar(win)
    _pump(app.root, 30)
    launcher_scanner = launcher.launch_gpib_scanner
    launcher.launch_gpib_scanner = lambda: started.append(1)
    try:
        V2._auto_launch_gpib_scanner(app)   # the timer firing late
    finally:
        launcher.launch_gpib_scanner = launcher_scanner
    assert started == []
