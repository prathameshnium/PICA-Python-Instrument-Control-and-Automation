"""Tests for the file-preload path into the two PPMS viewer tools.

The v2 launcher's File > Open Sequence and File > Open PPMS Data as Plot pass
the chosen paths to the child process on its command line. That only works if
three things line up: the launcher forwards argv, the runner puts argv into
sys.argv, and each tool reads sys.argv and loads without going back to a file
dialog. All three are checked here, plus the loader entry points themselves.

No Tk window is created: the tools' module-level __main__ blocks are read as
source, and the loader methods are driven on bare instances.

Runnable as plain Python as well as under pytest.
"""

import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

SEQ_PATH = os.path.join(REPO_ROOT, "pica", "PPMS", "PPMS_SeqVisualizer_GUI.py")
PLOT_PATH = os.path.join(REPO_ROOT, "pica", "PPMS", "PPMS_Plotter_GUI.py")
UTIL_PATH = os.path.join(REPO_ROOT, "pica", "utils", "PlotterUtil_GUI.py")

SEQ_SOURCE = open(SEQ_PATH, encoding="utf-8").read()
PLOT_SOURCE = open(PLOT_PATH, encoding="utf-8").read()
UTIL_SOURCE = open(UTIL_PATH, encoding="utf-8").read()

TOOLS = (("SeqVisualizer", SEQ_SOURCE), ("PPMSPlotter", PLOT_SOURCE),
         ("PlotterUtil", UTIL_SOURCE))


def _function_names(source):
    tree = ast.parse(source)
    return {node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


# ------------------------------------------------------- argv is consumed

def test_every_viewer_reads_its_command_line():
    for label, source in TOOLS:
        assert "sys.argv[1:]" in source, label


def test_every_viewer_imports_sys():
    """sys.argv[1:] in a module that never imports sys is a NameError."""
    for label, source in TOOLS:
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "sys" in imported, label


def test_every_viewer_filters_out_paths_that_do_not_exist():
    """A stale path on the command line must not raise at startup."""
    for label, source in TOOLS:
        assert "os.path.exists(p)" in source, label


def test_the_preload_is_deferred_until_the_window_exists():
    """Loading before mainloop would draw into a window that is not up yet."""
    for label, source in TOOLS:
        tail = source.split("__main__", 1)[1]
        assert "root.after(" in tail, label


def test_each_viewer_preloads_through_a_named_loader_not_a_dialog():
    assert "app.load_file(" in SEQ_SOURCE
    assert "app.add_files(" in PLOT_SOURCE
    assert "app.add_files(" in UTIL_SOURCE


# ------------------------------------------------------ the loader methods

def test_the_sequence_visualizer_exposes_a_loader():
    assert "load_file" in _function_names(SEQ_SOURCE)


def test_the_ppms_plotter_exposes_a_loader():
    assert "add_files" in _function_names(PLOT_SOURCE)


def test_the_sequence_loader_sets_the_path_and_redraws():
    """load_file must do everything browse_file did apart from the dialog."""
    tree = ast.parse(SEQ_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "load_file":
            body = ast.unparse(node)
            assert "self.filepath" in body
            assert "self.file_var.set" in body
            assert "parse_and_plot" in body
            return
    raise AssertionError("load_file not found in the Sequence Visualizer")


def test_browse_file_now_goes_through_the_same_loader():
    """Two code paths for the same job is how one of them rots."""
    tree = ast.parse(SEQ_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "browse_file":
            assert "self.load_file(" in ast.unparse(node)
            return
    raise AssertionError("browse_file not found in the Sequence Visualizer")


def test_the_ppms_loader_caches_plots_and_picks_an_active_file():
    tree = ast.parse(PLOT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "add_files":
            body = ast.unparse(node)
            assert "_load_file" in body
            assert "_add_file_to_ui" in body
            assert "_set_active_file" in body
            assert "plot_data" in body
            return
    raise AssertionError("add_files not found in the PPMS Plotter")


def test_browse_files_now_goes_through_the_same_loader():
    tree = ast.parse(PLOT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "browse_files":
            assert "self.add_files(" in ast.unparse(node)
            return
    raise AssertionError("browse_files not found in the PPMS Plotter")


def test_the_ppms_loader_survives_a_file_it_cannot_read():
    """One bad file must not take the whole preload down."""
    tree = ast.parse(PLOT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_file":
            body = ast.unparse(node)
            assert "except" in body
            assert "return False" in body
            return
    raise AssertionError("_load_file not found in the PPMS Plotter")


# --------------------------------------------- behaviour of the PPMS loader

class _StubPlotter:
    """Drives add_files without Tk, matplotlib or a real data file."""

    def __init__(self, loadable=()):
        self.file_data_cache = {}
        self.active_filepath = None
        self.added_to_ui = []
        self.plotted = 0
        self.loadable = set(loadable)

    def _load_file(self, filepath):
        if filepath not in self.loadable:
            return False
        self.file_data_cache[filepath] = {"meta": {}, "data": {}, "segments": []}
        return True

    def _add_file_to_ui(self, filepath):
        self.added_to_ui.append(filepath)

    def _set_active_file(self, filepath):
        self.active_filepath = filepath

    def plot_data(self):
        self.plotted += 1


def _bind_add_files():
    """Compile the real add_files and bind it to the stub."""
    tree = ast.parse(PLOT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "add_files":
            namespace = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         "<add_files>", "exec"), namespace)
            return namespace["add_files"]
    raise AssertionError("add_files not found in the PPMS Plotter")


def test_add_files_loads_plots_and_selects_the_first_readable_file():
    add_files = _bind_add_files()
    app = _StubPlotter(loadable={"a.dat", "b.dat"})
    add_files(app, ["a.dat", "b.dat"])
    assert app.added_to_ui == ["a.dat", "b.dat"]
    assert app.active_filepath == "a.dat"
    assert app.plotted == 1


def test_add_files_skips_a_file_it_cannot_read_and_still_plots():
    add_files = _bind_add_files()
    app = _StubPlotter(loadable={"good.dat"})
    add_files(app, ["bad.dat", "good.dat"])
    assert app.added_to_ui == ["good.dat"]
    assert app.active_filepath == "good.dat"
    assert app.plotted == 1


def test_add_files_does_not_reload_a_file_already_in_the_cache():
    add_files = _bind_add_files()
    app = _StubPlotter(loadable={"a.dat"})
    add_files(app, ["a.dat"])
    add_files(app, ["a.dat"])
    assert app.added_to_ui == ["a.dat"], "the same file was added twice"
    assert app.plotted == 2


def test_add_files_leaves_the_active_file_alone_on_a_second_call():
    add_files = _bind_add_files()
    app = _StubPlotter(loadable={"a.dat", "b.dat"})
    add_files(app, ["a.dat"])
    add_files(app, ["b.dat"])
    assert app.active_filepath == "a.dat"


def test_add_files_with_nothing_readable_still_refreshes_the_plot():
    add_files = _bind_add_files()
    app = _StubPlotter(loadable=set())
    add_files(app, ["nope.dat"])
    assert app.added_to_ui == []
    assert app.active_filepath is None
    assert app.plotted == 1


# ------------------------------------------------- the three tools differ

def test_the_three_viewers_are_three_separate_programs():
    """The launcher routes .seq, QD .dat and generic data to three tools."""
    assert len({SEQ_PATH, PLOT_PATH, UTIL_PATH}) == 3
    for path in (SEQ_PATH, PLOT_PATH, UTIL_PATH):
        assert os.path.exists(path), path


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
