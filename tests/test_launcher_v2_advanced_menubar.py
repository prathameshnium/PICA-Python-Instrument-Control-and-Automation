"""The Advanced Options window carries the same menu bar as the main window.

The expert window opens maximised and covers the main launcher, so it must
be a complete launcher on its own: File / Tools / View / Help on top, the
same entries in the same order. Runs as plain python (builds a real Tk root)
and skips under pytest where a Tk root is not available.
"""
import inspect
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import pica.main_v2 as v2  # noqa: E402

CASCADES = ["File", "Tools", "View", "Help"]


def _cascade_labels(menu):
    end = menu.index('end')
    if end is None:
        return []
    return [menu.entrycget(i, 'label') for i in range(end + 1)
            if menu.type(i) == 'cascade']


def _entry_labels(menu):
    end = menu.index('end')
    if end is None:
        return []
    return [menu.entrycget(i, 'label') for i in range(end + 1)
            if menu.type(i) in ('command', 'cascade')]


def _submenu(root, bar, label):
    end = bar.index('end')
    for i in range(end + 1):
        if bar.type(i) == 'cascade' and bar.entrycget(i, 'label') == label:
            return root.nametowidget(bar.entrycget(i, 'menu'))
    raise AssertionError(f"no {label} cascade")


def _build_launcher():
    root = tk.Tk()
    root.withdraw()
    app = v2.PICALauncherV2.__new__(v2.PICALauncherV2)
    # No bus scan, no startup windows, no scanner subprocess.
    app.start_scan = lambda: None
    app._open_startup_status = lambda: None
    app._auto_launch_gpib_scanner = lambda: None
    v2.PICALauncherV2.__init__(app, root)
    return root, app


def test_the_builder_attaches_to_the_window_it_is_given():
    source = inspect.getsource(v2.PICALauncherV2._build_menubar)
    assert "def _build_menubar(self, win=None)" in source
    assert "win.config(menu=menubar)" in source
    assert "self.root.config(menu=menubar)" not in source


def test_open_advanced_builds_the_menubar():
    source = inspect.getsource(v2.PICALauncherV2.open_advanced)
    assert "self._build_menubar(win)" in source


def test_advanced_window_has_the_same_menus_as_the_main_window():
    try:
        root, app = _build_launcher()
    except tk.TclError:
        import pytest
        pytest.skip("no Tk display")
    try:
        app.open_advanced()
        win = app._adv_win
        assert win is not None and win.winfo_exists()

        main_bar = root.nametowidget(root.cget('menu'))
        adv_name = win.cget('menu')
        assert adv_name, "Advanced Options window has no menu bar"
        adv_bar = root.nametowidget(adv_name)
        assert adv_bar is not main_bar

        assert _cascade_labels(adv_bar) == CASCADES
        assert _cascade_labels(main_bar) == CASCADES
        for label in CASCADES:
            main_sub = _submenu(root, main_bar, label)
            adv_sub = _submenu(root, adv_bar, label)
            assert _entry_labels(adv_sub) == _entry_labels(main_sub), label

        # The Tools menu on the Advanced window still opens Advanced Options
        # (a no-op there) and still carries the diagnostic submenu.
        tools = _submenu(root, adv_bar, "Tools")
        labels = _entry_labels(tools)
        assert labels[0].startswith("Advanced Options")
        assert "Diagnostic Tools" in labels

        app._close_advanced()
        assert app._adv_win is None
    finally:
        root.destroy()


if __name__ == "__main__":
    test_the_builder_attaches_to_the_window_it_is_given()
    test_open_advanced_builds_the_menubar()
    test_advanced_window_has_the_same_menus_as_the_main_window()
    print("ok")
