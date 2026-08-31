"""Tests for the v2 launcher's wiring: menus, toolbar and module catalogue.

No Tk window is created. The File-menu handlers are exercised on a bare
instance with the few attributes they touch supplied by hand, and the parts
that only exist as widget construction are checked in the source.

Runnable as plain Python as well as under pytest.
"""

import ast
import importlib.util
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
    for icon in ("📈", "📟"):
        assert icon in V1_SOURCE, f"{icon} missing from the v1 launcher"
        assert icon in V2_SOURCE, f"{icon} missing from the v2 launcher"


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


# ------------------------------------------- v1 parity: scanner auto-launch

def test_the_scanner_auto_launches_at_startup():
    assert hasattr(launcher.PICALauncherV2, "_auto_launch_gpib_scanner")
    assert "self.root.after(900, self._auto_launch_gpib_scanner)" in V2_SOURCE


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
    """v1's runner is what both launchers spawn; argv has to survive it."""
    tree = ast.parse(V1_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_script_process":
            body = ast.dump(node)
            assert "sys" in body and "argv" in body
            return
    raise AssertionError("run_script_process not found in the v1 launcher")


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
