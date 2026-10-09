"""Launcher v2: the Ctrl+F module search and the hover card in Advanced Options.

Two layers:

* pica/module_info.py is Tk-free: the per-script descriptions (what the
  program measures, which instruments it opens, what it asks for) and the
  fuzzy search over them. Those tests need nothing but Python.
* The wiring into the launcher is checked in the source, and -- where a Tk
  display exists -- by building the real launcher, typing into the search
  box and opening a hover card. Those build a real Tk root, so they skip
  under a pytest run that has tkinter mocked or no display.
"""
import importlib.util
import inspect
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")
V2_SOURCE = open(V2_PATH, encoding="utf-8").read()

from pica import module_info as mi  # noqa: E402


def _load_launcher():
    spec = importlib.util.spec_from_file_location("pica_main_v2_search", V2_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pica_main_v2_search"] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()
SCRIPT_PATHS = launcher.PICALauncherV2.SCRIPT_PATHS
CATALOG_KEYS = {key for cat in launcher.CATALOG for _l, key, _f in cat["modules"]}
INDEX = mi.build_search_index(launcher.CATALOG, launcher.QUICK_CATALOG,
                              SCRIPT_PATHS, mi.UTILITY_TOOLS)


def _search(query, limit=12):
    return mi.search_modules(INDEX, query, limit=limit)


def _keys(query, limit=12):
    return [r["key"] for r in _search(query, limit)]


# --------------------------------------------------------------- the entries
def test_every_catalogue_module_has_a_description():
    missing = sorted(CATALOG_KEYS - set(mi.MODULE_INFO))
    assert not missing, f"Advanced Options rows without a hover card: {missing}"


def test_every_utility_has_a_description():
    missing = sorted({k for _l, k, _g in mi.UTILITY_TOOLS} - set(mi.MODULE_INFO))
    assert not missing


def test_every_description_names_a_real_script_key():
    orphans = sorted(set(mi.MODULE_INFO) - set(SCRIPT_PATHS))
    assert not orphans, f"MODULE_INFO keys that are not SCRIPT_PATHS keys: {orphans}"


def test_every_utility_key_resolves_to_a_script_on_disk():
    for _label, key, _group in mi.UTILITY_TOOLS:
        assert key in SCRIPT_PATHS, key
        assert os.path.exists(os.path.abspath(SCRIPT_PATHS[key])), key


def test_each_entry_carries_the_three_lines_and_they_are_short():
    for key, info in mi.MODULE_INFO.items():
        for field in ("what", "instruments", "inputs"):
            assert info.get(field), f"{key}: '{field}' is empty"
        # A card, not a manual page: the longest line must still fit beside
        # the pointer. 'what' is the one allowed to run to three sentences.
        assert len(info["what"]) <= 520, f"{key}: 'what' too long ({len(info['what'])})"
        assert len(info["inputs"]) <= 420, f"{key}: 'inputs' too long"
        assert len(info["instruments"]) <= 160, f"{key}: 'instruments' too long"


def test_instrument_names_are_written_out_for_newcomers():
    """The card is for someone who has not learned the shorthand yet."""
    for key, info in mi.MODULE_INFO.items():
        text = info["instruments"]
        for short in ("K2400", "K6221", "K2182", "K6517B", "L350", "L340", "CC34"):
            assert short not in text, f"{key}: '{short}' in instruments line"


def test_thermometer_editions_name_their_own_thermometer():
    assert "Lakeshore 350" in mi.MODULE_INFO["K2400 R-T"]["instruments"]
    assert "Lakeshore 340" in mi.MODULE_INFO["K2400 R-T (L340)"]["instruments"]
    assert "Cryo-con 34" in mi.MODULE_INFO["K2400 R-T (T_Sensing, CC34)"]["instruments"]
    # The T Sensing edition says it never commands the controller.
    assert "only read" in mi.MODULE_INFO["K2400 R-T (T_Sensing)"]["what"]
    assert "drives" in mi.MODULE_INFO["K2400 R-T"]["what"]


def test_module_info_never_raises_for_an_unknown_key():
    info = mi.module_info("No Such Module")
    assert info == {"what": "", "instruments": "", "inputs": ""}
    assert mi.module_info("No Such Module", launcher.QUICK_CATALOG) == info


def test_a_missing_entry_borrows_the_quick_select_description():
    """One source of truth: a script described on Quick Select but not yet
    in MODULE_INFO still gets a card, with the same words."""
    key = "K2400 I-V"
    saved = mi.MODULE_INFO.pop(key)
    try:
        info = mi.module_info(key, launcher.QUICK_CATALOG)
        assert info["what"].startswith("Current sweep at a fixed temperature")
        assert "Keithley 2400" in info["instruments"]
        assert info["inputs"] == ""
    finally:
        mi.MODULE_INFO[key] = saved


def test_quick_select_shows_the_same_inputs_line():
    source = inspect.getsource(launcher.PICALauncherV2._on_quick_protocol)
    assert "module_info(proto['key'])['inputs']" in source
    assert "You enter: " in source


def test_module_info_returns_a_copy():
    info = mi.module_info("K2400 I-V")
    info["what"] = "changed"
    assert mi.MODULE_INFO["K2400 I-V"]["what"] != "changed"


# ------------------------------------------------------- the index covers all
def test_every_launchable_program_is_in_the_search_index():
    """Measurement modules, diagnostics and every PICA Util: all searchable.

    The one launcher entry left out is "PICA Help", which opens the README
    rather than starting a program.
    """
    indexed = {e["key"] for e in INDEX}
    missing = sorted(set(SCRIPT_PATHS) - indexed - {"PICA Help"})
    assert not missing, f"launchable but not searchable: {missing}"


def test_every_diagnostic_tool_is_searchable_by_the_word():
    keys = set(_keys("diagnostics", limit=20))
    for _label, key in launcher.DIAGNOSTIC_TOOLS:
        assert key in keys, key


def test_every_pica_util_is_in_the_index():
    groups = launcher.PICALauncherV2._utils_groups(launcher.PICALauncherV2)
    named = {label for _title, tools in groups for label, _target in tools}
    indexed = {e["label"] for e in INDEX}
    assert named <= indexed, named - indexed


# ------------------------------------------------------------ display names
def test_every_program_has_a_distinct_full_name():
    names = [e["name"] for e in INDEX]
    assert len(names) == len(set(names)), "two programs share a name"


def test_a_measurement_name_leads_with_its_instruments():
    by_key = {e["key"]: e["name"] for e in INDEX}
    assert by_key["K2400 I-V"] == "Keithley 2400 — I-V Sweep"
    assert by_key["K6517B I-V"] == "Keithley 6517B — I-V Sweep"
    assert by_key["K2400_2182 I-V"] == "Keithley 2400 + 2182 — I-V Sweep"
    assert by_key["Delta Mode I-V Sweep"] == "Keithley 6221 + 2182 — Sweep Mode I-V"
    assert by_key["SR830 AC R-T (T_Sensing, CC34)"] == \
        "Keithley 6221 + SR830 lock-in — AC R vs. T (T Sensing, Cryocon 34)"


def test_a_name_says_which_thermometer_once():
    by_key = {e["key"]: e["name"] for e in INDEX}
    # The bare "(T Control)" gains its default controller.
    assert by_key["K2400 R-T"] == "Keithley 2400 — R vs. T (T Control, L350)"
    assert by_key["Pyroelectric Current"] == \
        "Keithley 6517B — PyroCurrent vs. T (L350)"
    # A temperature tool leads with the controller and does not repeat it.
    assert by_key["Lakeshore Temp Control"] == "Lakeshore 350 — Temperature Ramp"
    assert by_key["Lakeshore 340 Step Control (Advanced)"] == \
        "Lakeshore 340 — Step-wise Control (Advanced)"
    assert by_key["Lakeshore Sensor Curve Loader"] == \
        "Lakeshore 350 / 340 — Sensor Curve Loader"
    assert by_key["Cryocon Diagnostics"] == \
        "Cryo-con 34 — Diagnostics (read-only survey)"
    assert by_key["K197A Monitor"] == "Keithley 197A — Reading Monitor"
    for name in by_key.values():
        assert "(/)" not in name and "()" not in name and not name.endswith("…")


def test_no_full_name_is_too_long_for_a_row():
    assert max(len(e["name"]) for e in INDEX) <= 80


# ------------------------------------------------------------------ the index
def test_the_index_covers_every_catalogue_row_once():
    keys = [e["key"] for e in INDEX]
    assert len(keys) == len(set(keys))
    assert CATALOG_KEYS <= set(keys)
    assert {k for _l, k, _g in mi.UTILITY_TOOLS} <= set(keys)


def test_the_index_can_be_built_without_the_utilities():
    index = mi.build_search_index(launcher.CATALOG, launcher.QUICK_CATALOG,
                                  SCRIPT_PATHS)
    assert {e["key"] for e in index} == CATALOG_KEYS


def test_index_records_carry_what_the_popup_shows():
    rec = next(e for e in INDEX if e["key"] == "K2400 R-T")
    assert rec["label"] == "R vs. T (T Control)"
    assert rec["category"].startswith("Mid Resistance")
    assert rec["family"] == "control"
    assert rec["script"] == "RT_K2400_L350_T_Control_GUI.py"
    assert rec["snippet"] == mi.MODULE_INFO["K2400 R-T"]["what"]


# ----------------------------------------------------------------- the search
def test_empty_and_blank_queries_return_nothing():
    assert _search("") == []
    assert _search("   ") == []


def test_results_are_launchable_and_carry_a_score():
    for rec in _search("resistance", limit=50):
        assert rec["key"] in SCRIPT_PATHS
        assert rec["score"] > 0
        assert not any(k.startswith("_") for k in rec)


def test_a_module_name_finds_that_module_first():
    keys = _keys("delta")
    assert keys and all(k.startswith("Delta Mode") for k in keys[:6])


def test_an_instrument_and_a_protocol_narrow_to_that_pair():
    keys = _keys("k2400 rt")
    assert keys[0] == "K2400 R-T"
    assert all("2400" in k and "R-T" in k for k in keys[:6])


def test_the_alias_table_speaks_the_rack_shorthand():
    assert _keys("lcr temp sensing")[0].startswith("LCR Temp. Scan (T_Sensing")
    assert all("CC34" in k or "Cryocon" in k for k in _keys("cc34")[:5])
    assert _keys("ppms master")[0].startswith("PPMS Dielectric Master")


def test_a_misspelt_word_still_finds_the_modules():
    assert _keys("resistence")
    assert _keys("cryocon") and all(
        "CC34" in k or "Cryocon" in k for k in _keys("cryocon")[:5])
    assert _keys("dielectic")


def test_a_word_from_a_quick_select_description_is_searched_too():
    """No card says 'butterfly'; the Quick Select description of C-V does."""
    assert _keys("butterfly") == ["LCR C-V Measurement"]


def test_a_word_from_an_input_field_is_searched_too():
    """'safety cutoff' is a form field, named only in the card text."""
    keys = _keys("safety cutoff", limit=50)
    assert "K2400 R-T" in keys and "Pyroelectric Current" in keys


def test_initials_find_the_module_by_its_label():
    keys = _keys("dmrt")
    assert keys and all(k.startswith("Delta Mode R-T") for k in keys[:3])


def test_short_words_match_only_at_the_start_of_a_word():
    """'iv' must not find 'drivers', nor 'r t' find 'monitor t sensing'."""
    hits = _keys("iv", limit=30)
    assert len(hits) >= 6
    for key in hits:
        name = next(e["name"] for e in INDEX if e["key"] == key)
        assert "I-V" in name, name
    for not_iv in ("System and Driver Diagnostics", "SR830 AC Resistivity"):
        assert not_iv not in hits      # "drivers", "resistivity"
    top = _keys("rt", limit=6)
    assert all("R-T" in k for k in top), top


def test_hyphenated_letters_read_as_one_word():
    assert _keys("r-t", limit=6) == _keys("rt", limit=6)
    assert _keys("i-v", limit=6) == _keys("iv", limit=6)
    assert _keys("c-v")[0] == "LCR C-V Measurement"


def test_a_match_in_the_module_name_beats_one_in_its_category():
    top = _keys("pyro", limit=2)
    assert set(top) == {"Pyroelectric Current", "Pyroelectric Current (L340)"}


def test_every_word_must_match():
    assert _keys("delta zzqx") == []
    assert _keys("zzqx") == []


def test_utilities_are_found_by_name():
    keys = _keys("plotter")
    assert set(keys[:3]) == {"Plotter Utility", "PPMS Plotter Utility", "PE Plotter"}
    assert _keys("scpi")[0] == "SCPI Console"


def test_the_limit_is_honoured_and_best_first():
    res = _search("resistance", limit=4)
    assert len(res) == 4
    assert [r["score"] for r in res] == sorted((r["score"] for r in res), reverse=True)


def test_pyro_ranks_the_pyroelectric_programs_first():
    keys = _keys("pyro")
    assert set(keys[:3]) == {"Pyroelectric Current", "Pyroelectric Current (L340)",
                             "K6517B Polling (Bias)"}
    # The initials tier may add a weak match below them, never a plotter.
    assert not any("Plotter" in k for k in keys)


# ---------------------------------------------------------- launcher wiring
def _method_source(name):
    return inspect.getsource(getattr(launcher.PICALauncherV2, name))


def test_no_toolbar_carries_a_search_box_any_more():
    """The search is a Ctrl+F panel; neither toolbar builds a search field."""
    assert "search" not in _method_source("_build_toolbar").lower().replace(
        "research", "")
    assert not hasattr(launcher.PICALauncherV2, "_build_search_box")
    assert "Entry(" not in _method_source("_build_toolbar")


def test_ctrl_f_opens_the_search():
    source = _method_source("_build_menubar")
    assert '"<Control-f>"' in source and '"<Control-F>"' in source
    assert "_search_shortcut" in source


def test_the_tools_menu_offers_the_search_with_its_shortcut():
    source = _method_source("_build_menubar")
    assert 'label="Search Modules…", accelerator="Ctrl+F"' in source
    assert "self.open_search(w)" in source


def test_every_card_row_gets_a_hover_card():
    source = _method_source("_make_card")
    assert "_add_module_hover(button, key, label, cat['category']" in source


def test_the_hover_waits_before_it_opens():
    assert launcher.PICALauncherV2.HOVER_DELAY_MS >= 300
    assert "HOVER_DELAY_MS" in _method_source("_add_module_hover")


def test_the_hover_and_the_search_share_one_card_renderer():
    assert "_fill_module_info" in _method_source("_show_hover")
    assert "_fill_module_info" in _method_source("_search_refresh_card")


def test_closing_advanced_closes_its_popups():
    source = _method_source("_close_advanced")
    for call in ("_hide_hover", "_close_search", "_cancel_hover"):
        assert call in source


def test_the_launcher_uses_the_shared_index_and_search():
    assert "build_search_index(" in _method_source("__init__")
    assert "search_modules(" in _method_source("run_search")


# -------------------------------------------------------- real Tk (optional)
def _build_launcher():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    app = launcher.PICALauncherV2.__new__(launcher.PICALauncherV2)
    app.start_scan = lambda: None
    app._open_startup_status = lambda: None
    app._auto_launch_gpib_scanner = lambda: None
    launcher.PICALauncherV2.__init__(app, root)
    return root, app


@pytest.fixture
def tk_launcher():
    try:
        import tkinter as tk
        root, app = _build_launcher()
    except Exception as e:  # mocked tkinter, no display, incomplete Tcl
        pytest.skip(f"no usable Tk: {e}")
    try:
        root.update()
        yield root, app
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _labels_in(widget):
    import tkinter as tk
    out = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            out.append(child.cget("text"))
        out.extend(_labels_in(child))
    return out


def test_tk_ctrl_f_opens_results_and_enter_launches(tk_launcher):
    root, app = tk_launcher
    entry = app.open_search()
    root.update()
    assert app._search_win is not None and app._search_win.winfo_exists()
    entry.insert(0, "k2400 rt")
    results = app.run_search(entry)
    root.update()
    assert results and results[0]["key"] == "K2400 R-T"
    # Rows are the full names: instruments first, never a bare "I-V Sweep".
    assert app._search_list.get(0).strip() == results[0]["name"]
    assert results[0]["name"].startswith("Keithley 2400")
    # The card beside the list describes the highlighted result.
    assert "MEASURES" in _labels_in(app._search_card)
    launched = []
    app.launch_script = lambda key, argv=None: launched.append(key)
    app._search_move(entry, +1)
    app._search_launch()
    root.update()
    assert launched == [results[1]["key"]]
    assert app._search_win is None


def test_tk_no_match_shows_a_hint_and_escape_closes(tk_launcher):
    root, app = tk_launcher
    entry = app.open_search()
    entry.insert(0, "zzqx")
    assert app.run_search(entry) == []
    root.update()
    assert app._search_win is not None
    assert any("No module matches" in t for t in _labels_in(app._search_win))
    app._search_escape(entry)
    assert app._search_win is None


def test_tk_advanced_rows_open_a_hover_card(tk_launcher):
    import tkinter as tk
    from tkinter import ttk
    root, app = tk_launcher
    app.open_advanced()
    root.update()

    rows = []

    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button) and str(child.cget("style")) == "Mod.TButton":
                rows.append(child)
            collect(child)
    collect(app._adv_win)
    assert len(rows) == len(CATALOG_KEYS)

    app._show_hover(rows[0], "K2400 R-T", "R vs. T (T Control)",
                    "Mid Resistance", "control")
    root.update()
    win = app._hover_win
    assert win is not None and win.winfo_exists()
    texts = _labels_in(win)
    for caption in ("MEASURES", "INSTRUMENTS", "INPUT FIELDS", "SCRIPT", "T CONTROL"):
        assert caption in texts
    # The card heads with the full name, not the bare row label.
    assert "Keithley 2400 — R vs. T (T Control, L350)" in texts
    assert "RT_K2400_L350_T_Control_GUI.py" in texts
    assert mi.MODULE_INFO["K2400 R-T"]["what"] in texts
    # Wider than a tooltip, still well short of a screen. The width follows
    # the installed fonts (479 px on the GitHub runner, 424 px elsewhere),
    # so the upper bound is a sanity limit, not a pixel count.
    assert 300 <= win.winfo_reqwidth() <= app.INFO_WRAP + 250

    app._hide_hover()
    assert app._hover_win is None
    app._close_advanced()
    root.update()
    assert isinstance(root, tk.Tk)
