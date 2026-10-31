"""Tests for the v2 launcher's wiring: menus, toolbar and module catalogue.

No Tk window is created. The File-menu handlers are exercised on a bare
instance with the few attributes they touch supplied by hand, and the parts
that only exist as widget construction are checked in the source.

Runnable as plain Python as well as under pytest.
"""

import ast
import importlib.util
import re
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")
V1_PATH = os.path.join(REPO_ROOT, "pica", "main.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load("pica_main_v2_wiring", V2_PATH)
V2_SOURCE = open(V2_PATH, encoding="utf-8").read()
V1_SOURCE = open(V1_PATH, encoding="utf-8").read()


class StubLauncher:
    """Just enough of PICALauncherV2 to drive its File-menu handlers."""

    SEQ_FILETYPES = launcher.PICALauncherV2.SEQ_FILETYPES
    PPMS_DATA_FILETYPES = launcher.PICALauncherV2.PPMS_DATA_FILETYPES
    DATA_FILETYPES = launcher.PICALauncherV2.DATA_FILETYPES
    SCRIPT_PATHS = launcher.PICALauncherV2.SCRIPT_PATHS

    _ask_one_file = launcher.PICALauncherV2._ask_one_file
    _ask_data_files = launcher.PICALauncherV2._ask_data_files
    open_sequence_file = launcher.PICALauncherV2.open_sequence_file
    open_ppms_data_as_plot = launcher.PICALauncherV2.open_ppms_data_as_plot
    open_data_as_graph = launcher.PICALauncherV2.open_data_as_graph

    def __init__(self):
        self._last_data_dir = REPO_ROOT
        # Advanced Options is open: the scanner that comes up with it is due.
        # (It is skipped if the window was closed before its timer fired.)
        self._adv_win = object()
        self.messages = []
        self.launched = []

    def log(self, message):
        self.messages.append(message)

    def launch_script(self, key, argv=None):
        self.launched.append((key, list(argv or [])))


class FakeDialog:
    """Stand-in for tkinter.filedialog with canned answers."""

    def __init__(self, single=None, multiple=()):
        self.single = single
        self.multiple = tuple(multiple)
        self.calls = []

    def askopenfilename(self, **kwargs):
        self.calls.append(("askopenfilename", kwargs))
        return self.single

    def askopenfilenames(self, **kwargs):
        self.calls.append(("askopenfilenames", kwargs))
        return self.multiple


class _PatchDialog:
    def __init__(self, dialog):
        self.dialog = dialog

    def __enter__(self):
        self._old = launcher.filedialog
        launcher.filedialog = self.dialog
        return self.dialog

    def __exit__(self, *exc):
        launcher.filedialog = self._old
        return False


# ------------------------------------------------------- module catalogue

def test_every_catalogue_entry_maps_to_a_known_script_key():
    paths = launcher.PICALauncherV2.SCRIPT_PATHS
    for category in launcher.CATALOG:
        for label, key, _family in category["modules"]:
            assert key in paths, f"{category['category']} / {label} -> {key}"


def test_every_catalogue_script_exists_on_disk():
    paths = launcher.PICALauncherV2.SCRIPT_PATHS
    for category in launcher.CATALOG:
        for label, key, _family in category["modules"]:
            target = os.path.abspath(paths[key])
            assert os.path.exists(target), f"{label}: {target}"


def test_every_tool_in_the_utils_popup_resolves():
    paths = launcher.PICALauncherV2.SCRIPT_PATHS
    groups = launcher.PICALauncherV2._utils_groups(launcher.PICALauncherV2)
    for _title, tools in groups:
        for label, target in tools:
            if callable(target):
                continue
            assert target in paths, label
            assert os.path.exists(os.path.abspath(paths[target])), label


def test_ctrl_u_opens_pica_utils():
    assert 'bind_all("<Control-u>", self.open_tools_popup)' in V2_SOURCE
    assert 'bind_all("<Control-U>", self.open_tools_popup)' in V2_SOURCE
    assert 'label="PICA Utils…", accelerator="Ctrl+U"' in V2_SOURCE
    # Opened from a key binding, so it must accept the event argument.
    launcher.PICALauncherV2.open_tools_popup.__code__.co_varnames.index("event")


def test_the_citation_matches_citation_cff():
    cff = open(os.path.join(os.path.dirname(V2_PATH), os.pardir, "CITATION.cff"),
               encoding="utf-8").read()
    app = launcher.PICALauncherV2
    assert f"doi: {app.CITE_DOI}" in cff
    assert f'title: "{app.CITE_TITLE}"' in cff
    assert f"version: {app.RELEASE_VERSION}" in cff
    assert f"date-released: {app.CITE_YEAR}-" in cff
    for family, given in app.CITE_AUTHORS:
        assert f'family-names: "{family}"' in cff
        assert f'given-names: "{given}"' in cff


def test_help_docs_are_not_handed_to_the_os():
    # A .md file often has no program to open it on Windows; the Help menu
    # shows them in the launcher instead.
    for name in ("MANUAL_FILE", "README_FILE", "CHANGELOG_FILE", "LICENSE_FILE"):
        assert f"_open_path(self.{name})" not in V2_SOURCE, name
    for key in ("manual", "readme", "changelog", "license"):
        assert f'self.open_doc("{key}")' in V2_SOURCE, key


def test_the_pressure_gauge_is_a_searchable_setting_not_a_tools_entry():
    assert 'label="Pressure Gauge (TPG 361)…"' not in V2_SOURCE
    from pica import module_info as mi
    index = mi.build_search_index(launcher.CATALOG, launcher.QUICK_CATALOG,
                                  launcher.PICALauncherV2.SCRIPT_PATHS,
                                  mi.UTILITY_TOOLS, mi.SETTINGS_TOOLS)
    for query in ("pressure", "tpg", "gauge address"):
        top = mi.search_modules(index, query, limit=5)
        assert any(r['key'] == "Pressure Gauge Setup" and r['setting'] for r in top), query


def test_the_newer_modules_are_all_in_the_catalogue():
    keys = {key for cat in launcher.CATALOG for _l, key, _f in cat["modules"]}
    for expected in ("Cryocon Direct Control", "Cryocon Temp Monitor",
                     "K197A Monitor", "SR830 Lock-in Comms",
                     "Alpha-AN Freq. Scan", "Alpha-AN Freq. Scan (32-bit)",
                     "AFG3022B Function Generator"):
        assert expected in keys, expected


def test_the_novocontrol_category_is_marked_experimental():
    for cat in launcher.CATALOG:
        if "Broadband Dielectric" in cat["category"]:
            assert cat.get("experimental") is True
            return
    raise AssertionError("no Broadband Dielectric category in the catalogue")


def _quick_impedance():
    return next(c for c in launcher.QUICK_CATALOG
                if c["category"] == "Impedance Spectroscopy")


def test_quick_impedance_is_three_analysers_by_frequency_range():
    names = [m["name"] for m in _quick_impedance()["modules"]]
    assert names == ["Broadband Dielectric Spectroscopy (3 µHz – 20 MHz)",
                     "High-Frequency Impedance (1 MHz – 3 GHz)",
                     "LCR Meter (20 Hz – 2 MHz)"]


def test_quick_impedance_protocols_resolve_and_the_hf_one_is_planned():
    paths = launcher.PICALauncherV2.SCRIPT_PATHS
    bds, hf, lcr = _quick_impedance()["modules"]
    assert hf["protocols"] == [] and "E4991B" in hf["desc"]
    assert "Experimental" in bds["desc"]
    assert all(p["key"].startswith("Alpha-AN") for p in bds["protocols"])
    assert "Novocontrol Alpha-AN" in bds["instruments"]
    assert "Keysight E4980A" in lcr["instruments"]
    for module in (bds, lcr):
        for proto in module["protocols"]:
            assert proto["key"] in paths, proto["key"]


def test_quick_select_dropdown_acts_like_the_combobox_it_replaced():
    """WrapDropdown: two-line wrapping box, same calls as ttk.Combobox.

    Built on a withdrawn root, so no window appears."""
    import tkinter as tk
    try:
        root = tk.Tk()
    except Exception as exc:
        try:
            import pytest
            pytest.skip(f"no usable Tk: {exc}")
        except ImportError:
            print(f"SKIP: no usable Tk: {exc}")
            return
    root.withdraw()
    try:
        colours = dict.fromkeys(("field", "text", "dim", "faint", "accent",
                                 "light", "border"), "#888888")
        var = tk.StringVar()
        dd = launcher.WrapDropdown(root, textvariable=var, state='disabled',
                                   colours=colours)
        # A never-mapped widget drops generated events, so record the call.
        picked = []
        dd.event_generate = lambda seq: picked.append((seq, dd.current()))
        assert dd.current() == -1
        assert dd._open() == "break" and dd._menu.index('end') is None

        names = [m["name"] for m in _quick_impedance()["modules"]]
        dd.config(state='readonly', values=names)
        assert dd.cget('state') == 'readonly'
        assert dd.cget('values') == tuple(names)
        assert dd.current(0) == 0 and var.get() == names[0]
        var.set('')
        assert dd.current() == -1

        dd._pick(2)
        assert picked == [("<<ComboboxSelected>>", 2)] and dd.get() == names[2]
        # The box is two text lines tall and wraps; nothing is truncated.
        assert int(dd._label.cget('height')) == 2
    finally:
        root.destroy()


# ----------------------------------------------------- File > Open Sequence

def test_open_sequence_hands_the_file_to_the_sequence_visualizer():
    app = StubLauncher()
    seq = os.path.join(REPO_ROOT, "README.md")     # any existing path
    with _PatchDialog(FakeDialog(single=seq)):
        app.open_sequence_file()

    assert len(app.launched) == 1
    key, argv = app.launched[0]
    assert key == "Sequence Visualizer"
    assert argv == [os.path.abspath(seq)]


def test_open_sequence_offers_seq_files_first():
    app = StubLauncher()
    with _PatchDialog(FakeDialog(single=None)) as dialog:
        app.open_sequence_file()
    kwargs = dialog.calls[0][1]
    assert kwargs["filetypes"][0][1] == "*.seq"


def test_cancelling_the_sequence_dialog_launches_nothing():
    app = StubLauncher()
    with _PatchDialog(FakeDialog(single="")):
        app.open_sequence_file()
    assert app.launched == []


def test_open_sequence_remembers_the_folder():
    app = StubLauncher()
    seq = os.path.join(REPO_ROOT, "README.md")
    with _PatchDialog(FakeDialog(single=seq)):
        app.open_sequence_file()
    assert app._last_data_dir == os.path.dirname(seq)


# ------------------------------------------------ File > Open PPMS as Plot

def test_open_ppms_data_hands_the_files_to_the_ppms_plotter():
    app = StubLauncher()
    files = (os.path.join(REPO_ROOT, "README.md"),
             os.path.join(REPO_ROOT, "LICENSE"))
    with _PatchDialog(FakeDialog(multiple=files)):
        app.open_ppms_data_as_plot()

    assert len(app.launched) == 1
    key, argv = app.launched[0]
    assert key == "PPMS Plotter Utility"
    assert argv == [os.path.abspath(p) for p in files]


def test_open_ppms_data_offers_dat_files_first():
    app = StubLauncher()
    with _PatchDialog(FakeDialog(multiple=())) as dialog:
        app.open_ppms_data_as_plot()
    kwargs = dialog.calls[0][1]
    assert kwargs["filetypes"][0][1] == "*.dat"


def test_cancelling_the_ppms_dialog_launches_nothing():
    app = StubLauncher()
    with _PatchDialog(FakeDialog(multiple=())):
        app.open_ppms_data_as_plot()
    assert app.launched == []


def test_generic_open_as_graph_still_goes_to_the_plotter_utility():
    """The PPMS entries must not have hijacked the generic one."""
    app = StubLauncher()
    files = (os.path.join(REPO_ROOT, "README.md"),)
    with _PatchDialog(FakeDialog(multiple=files)):
        app.open_data_as_graph()
    assert app.launched[0][0] == "Plotter Utility"


def test_the_three_open_as_plot_routes_target_three_different_tools():
    paths = launcher.PICALauncherV2.SCRIPT_PATHS
    targets = {paths["Plotter Utility"],
               paths["PPMS Plotter Utility"],
               paths["Sequence Visualizer"]}
    assert len(targets) == 3


# ------------------------------------------------------------ menu wiring

def test_the_file_menu_carries_both_new_entries():
    assert 'label="Open Sequence…"' in V2_SOURCE
    assert 'label="Open PPMS Data as Plot…"' in V2_SOURCE
    assert "command=self.open_sequence_file" in V2_SOURCE
    assert "command=self.open_ppms_data_as_plot" in V2_SOURCE


def test_the_new_entries_have_keyboard_accelerators():
    assert '<Control-q>' in V2_SOURCE and 'open_sequence_file' in V2_SOURCE
    assert '<Control-p>' in V2_SOURCE and 'open_ppms_data_as_plot' in V2_SOURCE


# --------------------------------------------------- v1 parity: the toolbar

def test_the_v1_icon_toolbar_is_present_in_v2():
    # Same two buttons as v1, drawn from the Segoe MDL2 Assets icon font
    # instead of v1's emoji (see test_v2_draws_no_emoji for why).
    for icon in ("📈", "📟"):
        assert icon in V1_SOURCE, f"{icon} missing from the v1 launcher"
    for name in ("plotter", "scanner"):
        assert f"text=self._icon('{name}'), width=3" in V2_SOURCE, \
            f"{name} toolbar button missing from the v2 launcher"


def test_every_v2_icon_has_a_bmp_fallback():
    # PUA icons need Segoe MDL2 Assets; without it the plainer Segoe UI
    # Symbol character is drawn. Neither may be above U+FFFF.
    icons = launcher.PICALauncherV2.ICONS
    for name, (icon, fallback) in icons.items():
        assert 0xE000 <= ord(icon) <= 0xF8FF, f"{name}: {icon!r} not an MDL2 icon"
        assert len(fallback) == 1 and ord(fallback) <= 0xFFFF, \
            f"{name}: fallback {fallback!r} is not a single BMP character"
    used = set(re.findall(r"self\._icon\('(\w+)'\)", V2_SOURCE))
    assert used <= set(icons), f"unknown icon names: {used - set(icons)}"


def test_v2_draws_no_emoji():
    # Tk 8.6 on Windows cannot find a character above U+FFFF in any font
    # and scans every installed font trying: ~1 s at startup on the lab PC
    # for the first such glyph. The launcher must not use one.
    emoji = [c for c in V2_SOURCE if ord(c) > 0xFFFF]
    assert not emoji, f"non-BMP characters in the v2 launcher: {set(emoji)}"


def test_the_toolbar_buttons_are_wired_to_the_same_two_utilities():
    assert hasattr(launcher.PICALauncherV2, "_build_toolbar")
    source = V2_SOURCE.split("def _build_toolbar", 1)[1].split("\n    def ", 1)[0]
    assert "launch_plotter_utility" in source
    assert "_launch_gpib_scanner" in source


def test_the_toolbar_buttons_carry_the_v1_tooltips():
    assert '"Plotter Utility"' in V2_SOURCE
    assert '"VISA/GPIB Scanner"' in V2_SOURCE
    assert hasattr(launcher.PICALauncherV2, "_add_tooltip")


def test_the_gpib_scanner_and_plotter_are_still_in_the_tools_menu():
    """The toolbar is an addition, not a replacement."""
    assert 'label="GPIB / VISA Scanner"' in V2_SOURCE
    assert 'label="Plotter Utility"' in V2_SOURCE


# ------------------------------- scanner auto-launch (with Advanced Options)

def test_the_scanner_auto_launches_with_advanced_options():
    assert hasattr(launcher.PICALauncherV2, "_auto_launch_gpib_scanner")
    # The delay is a drawing courtesy, not a contract: assert the
    # scheduling, not the number of milliseconds.
    assert re.search(
        r"self\.root\.after\(\d+, self\._auto_launch_gpib_scanner\)",
        V2_SOURCE)


def test_v1_also_auto_launches_so_the_behaviour_matches():
    assert "_auto_launch_gpib" in V1_SOURCE
    assert "self.root.after(500, self._auto_launch_gpib)" in V1_SOURCE


def test_auto_launch_never_raises_when_pyvisa_is_missing():
    """Startup must not be blocked by a missing dependency."""
    app = StubLauncher()
    app._auto_launch_gpib_scanner = \
        launcher.PICALauncherV2._auto_launch_gpib_scanner.__get__(app)
    old = launcher.PYVISA_AVAILABLE
    launcher.PYVISA_AVAILABLE = False
    try:
        app._auto_launch_gpib_scanner()
    finally:
        launcher.PYVISA_AVAILABLE = old
    assert any("pyvisa" in m for m in app.messages)


def test_auto_launch_reports_a_spawn_failure_instead_of_raising():
    app = StubLauncher()
    app._auto_launch_gpib_scanner = \
        launcher.PICALauncherV2._auto_launch_gpib_scanner.__get__(app)

    def boom():
        raise OSError("cannot spawn")

    old_flag, old_fn = launcher.PYVISA_AVAILABLE, launcher.launch_gpib_scanner
    launcher.PYVISA_AVAILABLE = True
    launcher.launch_gpib_scanner = boom
    try:
        app._auto_launch_gpib_scanner()
    finally:
        launcher.PYVISA_AVAILABLE = old_flag
        launcher.launch_gpib_scanner = old_fn
    assert any("cannot spawn" in m for m in app.messages)


# --------------------------------------------------------------- structure

def test_launch_script_accepts_and_forwards_argv():
    """The File-menu routes depend on argv reaching the child process."""
    tree = ast.parse(V2_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "launch_script":
            names = [a.arg for a in node.args.args]
            assert names == ["self", "script_key", "argv"], names
            return
    raise AssertionError("launch_script not found in the v2 launcher")


def test_run_script_process_puts_argv_into_sys_argv():
    """v1's runner (v2 spawns its copy in pica/spawn_target.py); argv has
    to survive it."""
    tree = ast.parse(V1_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_script_process":
            body = ast.dump(node)
            assert "sys" in body and "argv" in body
            return
    raise AssertionError("run_script_process not found in the v1 launcher")


def test_v2_spawns_the_tk_free_runner():
    """v2 must pickle pica.spawn_target.run_script_process, not pica.main's:
    the spawned child imports whatever module defines the target."""
    assert launcher.run_script_process.__module__ == "pica.spawn_target"


def test_spawn_target_imports_no_tkinter_or_pil():
    import subprocess
    code = ("import sys; import pica.spawn_target; "
            "bad = [m for m in ('tkinter', 'PIL', 'pica.main', 'pica.main_v2') "
            "if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_launch_path_gives_windows_the_pica_icon():
    """Launched modules, the utilities (v1 run_script_process) and both
    launchers set the PICA icon; the icon file must exist where it looks."""
    import pica.spawn_target as st
    assert os.path.exists(st.PICA_ICON), st.PICA_ICON
    assert "use_pica_icon()" in open(st.__file__, encoding="utf-8").read()
    assert V1_SOURCE.count("use_pica_icon()") >= 2      # run_script_process + main
    assert "use_pica_icon()" in V2_SOURCE


def test_spawn_target_runs_script_with_argv_in_its_own_folder(tmp_path=None):
    import subprocess
    import tempfile
    folder = str(tmp_path) if tmp_path else tempfile.mkdtemp()
    script = os.path.join(folder, "probe.py")
    with open(script, "w") as f:
        f.write("import os, sys\nprint(os.getcwd() == os.path.dirname("
                "os.path.abspath(__file__)), sys.argv[1:], __name__)\n")
    code = ("import sys; from pica.spawn_target import run_script_process; "
            "run_script_process(sys.argv[1], ['a.txt', 'b.txt'])")
    r = subprocess.run([sys.executable, "-c", code, script], cwd=REPO_ROOT,
                       capture_output=True, text=True)
    assert r.stdout.strip() == "True ['a.txt', 'b.txt'] __main__", r.stdout + r.stderr


def test_run_scripts_import_the_launcher_only_under_main_guard():
    """With 'spawn' every launched module re-runs the run_pica script as
    __mp_main__; a top-level launcher import made each one load it."""
    for script in ("run_pica.py", "run_pica_v2.py",
                   os.path.join("tools", "legacy_stuff", "run_pica_legacy.py")):
        tree = ast.parse(open(os.path.join(REPO_ROOT, script),
                              encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                mod = getattr(node, "module", None) or ""
                assert "pica" not in mod and not any("pica" in n for n in names), \
                    "%s imports %s at top level" % (script, mod or names)


def test_advanced_start_does_not_auto_open_the_scanner():
    """pica-gui / run_pica.py open Advanced Options alone, without the
    standalone scanner (Instrument Status already shows the bus)."""
    app = StubLauncher()
    app._adv_win = object()
    app._advanced_start = True
    started = []
    old = launcher.launch_gpib_scanner
    launcher.launch_gpib_scanner = lambda: started.append(1)
    try:
        launcher.PICALauncherV2._auto_launch_gpib_scanner(app)
    finally:
        launcher.launch_gpib_scanner = old
    assert started == []


def test_the_home_button_is_wired_to_the_main_window():
    assert '"Home (Quick Select)"' in V2_SOURCE
    assert "command=self._focus_main" in V2_SOURCE


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
