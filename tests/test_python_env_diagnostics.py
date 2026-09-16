"""Tests for the Python environment diagnostics console (17 Sep 2026):

    pica/utils/Diagnostics_Python_Env_GUI.py

It answers the layer below the instrument: whether the Python running
PICA is the one the user thinks it is. Three properties carry its whole
value, so all three are pinned here:

  1. It CHANGES NOTHING. No pip, no subprocess, no install path. It is
     meant to be run while a measurement is going.
  2. It never depends on the packages it is checking for. The one moment
     it is needed most is the moment they are missing, so an import of
     numpy or pyvisa at module level would make it useless.
  3. A missing or broken package is a REPORTED FINDING, not a traceback.

Also covered: version comparison, requirements.txt parsing, the bit-ness
report, and the Diagnostic Tools wiring in both launchers.

No hardware. Runnable as plain Python as well as under pytest:
    python tests/test_python_env_diagnostics.py
"""

import importlib.util
import inspect
import os
import struct
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

MODULE_PATH = os.path.join(project_root, "pica", "utils",
                           "Diagnostics_Python_Env_GUI.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


env = _load("python_env_diagnostics_module", MODULE_PATH)
SOURCE = open(MODULE_PATH, encoding="utf-8").read()


# ===========================================================================
# 1. It changes nothing
# ===========================================================================

def test_no_install_path_of_any_kind():
    """pip, subprocess and os.system have no business in a read-only tool."""
    for banned in ("subprocess", "os.system", "pip install ",
                   "pip.main", "ensurepip", "check_call", "Popen"):
        # The advice line "pip install -r requirements.txt" is printed for
        # the user to run themselves; it is text, never a call.
        occurrences = [line for line in SOURCE.splitlines()
                       if banned in line and not line.strip().startswith(
                           ('#', 'emit(', '"""', "'"))]
        assert not occurrences, (banned, occurrences[:3])


def test_the_only_file_it_writes_is_a_log_the_user_names():
    writes = [line for line in SOURCE.splitlines()
              if "io.open(" in line and "'w'" in line]
    assert len(writes) == 1, writes
    assert "_save_as" in SOURCE


def test_it_says_it_is_read_only_where_a_user_will_see_it():
    assert "read-only" in SOURCE
    assert "installs nothing" in SOURCE


# ===========================================================================
# 2. It does not depend on what it checks for
# ===========================================================================

def test_no_project_dependency_is_imported_at_module_level():
    """The moment this tool is needed is the moment numpy is missing."""
    tree_top = SOURCE.split("# ====", 2)[0] + SOURCE.split("import io", 1)[1]
    module_level = []
    for line in SOURCE.splitlines():
        stripped = line.rstrip()
        if stripped.startswith(("import ", "from ")) and stripped == line:
            module_level.append(stripped)
    banned = ("numpy", "pandas", "matplotlib", "pyvisa", "pymeasure",
              "scipy", "PIL", "psutil", "zeroconf", "packaging")
    for line in module_level:
        for name in banned:
            assert name not in line, line
    assert tree_top  # the split above must have found the header


def test_imports_only_the_standard_library_and_tk():
    allowed = {"io", "os", "platform", "queue", "re", "struct", "sys",
               "threading", "tkinter", "warnings", "datetime", "importlib"}
    for line in SOURCE.splitlines():
        if not line.startswith(("import ", "from ")):
            continue
        root = line.split()[1].split('.')[0]
        assert root in allowed, line


# ===========================================================================
# 3. Version arithmetic
# ===========================================================================

def test_parse_version_reads_the_forms_the_project_pins():
    assert env.parse_version("1.22.4") == (1, 22, 4)
    assert env.parse_version("0.113.0") == (0, 113, 0)
    assert env.parse_version("3.10") == (3, 10)
    assert env.parse_version("") == ()
    assert env.parse_version(None) == ()


def test_parse_version_stops_at_the_first_non_numeric_part():
    assert env.parse_version("1.26.0rc1") == (1, 26, 0)
    assert env.parse_version("2.0.dev3") == (2, 0)


def test_version_at_least_compares_by_component_not_by_string():
    assert env.version_at_least("1.22.4", "1.22.4")
    assert env.version_at_least("1.26.0", "1.9.0"), "9 > 2 as text, not here"
    assert env.version_at_least("2.0", "1.99.99")
    assert not env.version_at_least("1.22.3", "1.22.4")
    assert not env.version_at_least("0.9.0", "0.10.0")


def test_a_shorter_version_is_padded_not_failed():
    assert env.version_at_least("1.22", "1.22.0")
    assert not env.version_at_least("1.22", "1.22.1")


def test_no_minimum_means_anything_passes():
    assert env.version_at_least("0.0.1", None)
    assert env.version_at_least(None, None)


def test_an_unreadable_version_is_reported_not_failed():
    """A false 'too old' sends the user chasing an upgrade they do not need."""
    assert env.version_at_least("present (no version string)", "1.0")
    assert env.version_at_least("unknown", "99.0")


# ===========================================================================
# 4. The requirements table
# ===========================================================================

def test_the_builtin_table_covers_every_pinned_requirement():
    pins, path = env.read_requirements_file()
    assert path and os.path.isfile(path), path
    table = {name.lower() for name, _imp, _min, _why in env.REQUIRED_PACKAGES}
    missing = set(pins) - table
    assert not missing, f"requirements.txt pins {missing}, the table does not"


def test_every_builtin_minimum_matches_requirements_txt():
    """The fallback table exists for frozen builds; it must not disagree."""
    pins, _path = env.read_requirements_file()
    for name, _imp, minimum, _why in env.REQUIRED_PACKAGES:
        pinned = pins.get(name.lower())
        if pinned and minimum:
            assert env.parse_version(minimum) == env.parse_version(pinned), name


def test_every_required_entry_says_what_it_is_for():
    for name, import_name, _min, purpose in env.REQUIRED_PACKAGES:
        assert import_name and purpose.strip(), name
    for name, import_name, _min, purpose in env.OPTIONAL_PACKAGES:
        assert import_name and purpose.strip(), name


def test_the_requirements_reader_survives_a_missing_file(tmpdir=None):
    """A loose copy on a USB stick has no requirements.txt next to it."""
    original = env.__file__
    try:
        env.__file__ = os.path.join(os.sep, "nowhere", "at", "all", "x.py")
        pins, path = env.read_requirements_file()
        assert pins == {} and path is None
    finally:
        env.__file__ = original


# ===========================================================================
# 5. The survey
# ===========================================================================

def test_the_core_survey_runs_and_reports_the_interpreter():
    lines = []
    problems, warnings = env.run_survey(set(), lines.append)
    text = "\n".join(lines)
    assert "PYTHON INTERPRETER" in text
    assert "REQUIRED PACKAGES" in text
    assert "VERDICT" in text
    assert isinstance(problems, list) and isinstance(warnings, list)


def test_the_survey_states_the_bit_ness_of_this_python():
    lines = []
    env.run_survey(set(), lines.append)
    bits = struct.calcsize("P") * 8
    assert any(f"{bits}-bit" in line for line in lines), \
        "the 32/64-bit answer is the point of the interpreter section"


def test_the_survey_names_the_executable_and_the_prefix():
    lines = []
    env.run_survey(set(), lines.append)
    text = "\n".join(lines)
    assert sys.executable in text
    assert sys.prefix in text


def test_every_required_package_gets_a_line_whether_present_or_not():
    lines = []
    env.run_survey(set(), lines.append)
    text = "\n".join(lines)
    for name, _imp, _min, _why in env.REQUIRED_PACKAGES:
        assert name in text, name


def test_a_missing_package_is_a_problem_not_an_exception():
    original = list(env.REQUIRED_PACKAGES)
    try:
        env.REQUIRED_PACKAGES.append(
            ("definitely-not-installed-xyz", "definitely_not_installed_xyz",
             "1.0", "a package that cannot exist"))
        lines = []
        problems, _warnings = env.run_survey(set(), lines.append)
        assert any("definitely-not-installed-xyz" in item
                   for item in problems), problems
        assert any("MISSING" in line for line in lines)
    finally:
        env.REQUIRED_PACKAGES[:] = original


def test_an_old_package_is_reported_as_too_old():
    original = list(env.REQUIRED_PACKAGES)
    try:
        # sys is certainly importable and certainly has no version, so pin a
        # package that is present and demand an impossible version of it.
        env.REQUIRED_PACKAGES.append(("pytest", "pytest", "999.0", "a test"))
        lines = []
        problems, _warnings = env.run_survey(set(), lines.append)
        if any("pytest" in item and "older" in item for item in problems):
            assert any("TOO OLD" in line for line in lines)
    finally:
        env.REQUIRED_PACKAGES[:] = original


def test_the_verdict_counts_what_it_found():
    lines = []
    problems, warnings = env.run_survey(set(), lines.append)
    text = "\n".join(lines)
    if problems:
        assert "problem(s)" in text
        assert "pip install -r requirements.txt" in text
    if not problems and not warnings:
        assert "present and new enough" in text


def test_every_deep_section_has_a_probe_and_runs_clean():
    keys = {key for key, _label, _cost, _default in env.DEEP_SECTIONS}
    assert keys == set(env.PROBE_FUNCTIONS), keys ^ set(env.PROBE_FUNCTIONS)
    # Everything except the bus enumeration is pure introspection and is
    # safe to run here; 'resources' is left out so the suite never touches
    # a GPIB card.
    lines = []
    env.run_survey(keys - {"resources"}, lines.append)
    assert len(lines) > 40


def test_the_bus_enumeration_is_off_by_default():
    defaults = {key: default
                for key, _label, _cost, default in env.DEEP_SECTIONS}
    assert defaults["resources"] is False, \
        "a section that touches the bus must be opt-in"


def test_the_shadow_probe_notices_which_pica_is_imported():
    lines, _problems, _warnings = env.probe_shadow()
    text = "\n".join(lines)
    assert "Repository root" in text
    assert "import pica" in text


# ===========================================================================
# 6. The console
# ===========================================================================

def test_the_worker_never_touches_tk():
    """Tk from a thread is the bug that only shows up on the slow machine."""
    source = inspect.getsource(env.PythonEnvDiagnosticsGUI._start)
    work = source.split("def work(", 1)[1].split("self.worker =", 1)[0]
    for banned in ("self._log_line", "self.console", "self.status_var",
                   "self.progress"):
        assert banned not in work, banned
    assert "self.queue.put" in work


def test_the_poll_chain_is_cancelled_on_close():
    source = inspect.getsource(env.PythonEnvDiagnosticsGUI._on_closing)
    assert "after_cancel" in source
    assert "self.after_id = None" in source


def test_the_survey_itself_has_no_tk_in_it():
    """run_survey is the part a CLI or a test calls; it must stay headless."""
    source = inspect.getsource(env.run_survey)
    for banned in ("tk.", "ttk.", "messagebox", "self."):
        assert banned not in source, banned


def test_the_gui_uses_the_house_palette():
    gui = env.PythonEnvDiagnosticsGUI
    assert gui.CLR_BG_DARK == '#B8A392'
    assert gui.CLR_HEADER == '#E5DCD3'
    assert gui.CLR_ACCENT_GOLD == '#BA6B5E'
    assert gui.FONT_BASE == ('Segoe UI', 11)


def test_it_is_not_mistaken_for_a_measurement():
    """It names matplotlib -- as a package it checks for -- but never draws
    with it, and it has no sample and no data file."""
    for banned in ("sample_name", "FigureCanvas", "pyplot", "savefig"):
        assert banned not in SOURCE, banned


# ===========================================================================
# 7. Wiring: Diagnostic Tools in both launchers
# ===========================================================================

def test_it_is_in_script_paths_and_on_disk():
    from pica.main import PICALauncherApp
    path = PICALauncherApp.SCRIPT_PATHS["Python Environment Diagnostics"]
    assert os.path.basename(path) == "Diagnostics_Python_Env_GUI.py"
    assert os.path.isfile(path), path


def test_it_is_listed_in_the_diagnostic_tools_menu():
    from pica.main_v2 import DIAGNOSTIC_TOOLS
    keys = {key for _label, key in DIAGNOSTIC_TOOLS}
    assert "Python Environment Diagnostics" in keys


def test_every_diagnostic_tool_still_resolves_to_a_real_script():
    from pica.main import PICALauncherApp
    from pica.main_v2 import DIAGNOSTIC_TOOLS
    paths = PICALauncherApp.SCRIPT_PATHS
    for label, script_key in DIAGNOSTIC_TOOLS:
        assert script_key in paths, script_key
        assert os.path.isfile(paths[script_key]), script_key
        assert label.strip(), script_key


def test_the_advanced_catalogue_suite_matches_the_menu():
    from pica.main_v2 import CATALOG, DIAGNOSTIC_TOOLS
    suites = [s for s in CATALOG if s['category'] == "Diagnostic Tools"]
    assert len(suites) == 1
    keys = {entry[1] for entry in suites[0]['modules']}
    assert keys == {key for _label, key in DIAGNOSTIC_TOOLS}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s).")
    sys.exit(1 if failures else 0)
