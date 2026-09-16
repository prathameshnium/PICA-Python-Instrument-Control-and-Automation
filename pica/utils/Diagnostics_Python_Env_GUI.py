#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PROGRAM:      Python Environment Diagnostics
 VERSION:      1.0
 DATE:         17 Sep 2026

 PURPOSE:
   Answers the question that comes up whenever a PICA module fails before it
   has touched an instrument: "is the Python underneath it the one I think it
   is?" It reports the interpreter (version, 32- or 64-bit, where it lives,
   whether it is a virtual environment), checks every package PICA depends on
   against the minimum version the project asks for, and looks for the two
   traps that have actually cost time in this lab:

     * a 32-bit / 64-bit mismatch between Python and the GPIB driver, which
       shows up as a VISA load failure and nothing else;
     * an installed copy of pica_suite in site-packages shadowing this
       repository, so edits appear to do nothing.

 READ-ONLY:
   This program installs nothing, upgrades nothing and writes nothing outside
   a log file you ask for by name. It imports the packages it reports on -- an
   import is how a version is read -- and nothing more. The one section that
   can touch hardware (listing VISA addresses) is off by default and, when
   enabled, only enumerates: it opens no instrument and sends no command, so
   it is safe beside a running measurement.

 DEPENDENCIES:
   Standard library and Tkinter only. Everything it reports on is imported
   defensively: a missing package is a finding, not a crash.
===============================================================================
"""

import io
import os
import platform
import queue
import re
import struct
import sys
import threading
import tkinter as tk
import warnings as warnings_module
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from importlib import metadata as importlib_metadata
except ImportError:                                            # pragma: no cover
    importlib_metadata = None


# ===============================================================================
# WHAT PICA NEEDS
# ===============================================================================
# The table is written out here rather than parsed from requirements.txt alone,
# because this program has to work from a frozen build and from a copy sitting
# on a USB stick, where requirements.txt is not next to it. When the file IS
# found, it wins -- it is the project's own answer -- and the table below is the
# fallback plus the source of the "why it matters" line.
#
#   (distribution name, import name, minimum version, what it is for)
REQUIRED_PACKAGES = [
    ("numpy", "numpy", "1.22.4",
     "every array of measured points"),
    ("pandas", "pandas", "1.4.2",
     "reading and writing the data files"),
    ("matplotlib", "matplotlib", "3.5.2",
     "the live plots in every measurement GUI"),
    ("PyVISA", "pyvisa", "1.12.0",
     "the instrument link itself -- nothing runs without it"),
    ("PyMeasure", "pymeasure", "0.10.0",
     "instrument drivers used by the Keithley modules"),
    ("pyvisa-py", "pyvisa_py", "0.5.3",
     "pure-Python VISA backend (fallback when NI-VISA is absent)"),
    ("scipy", "scipy", None,
     "fits and interpolation in the analysis utilities"),
    ("Pillow", "PIL", "10.3.0",
     "the logo and image handling in the launchers"),
    ("psutil", "psutil", "5.9.8",
     "the keep-awake and process checks in long unattended runs"),
    ("zeroconf", "zeroconf", "0.113.0",
     "finding LAN instruments (TPG 361, LXI) by name"),
]

# Not required. Their absence is worth a line, never a failure.
OPTIONAL_PACKAGES = [
    ("gpib-ctypes", "gpib_ctypes", None,
     "lets pyvisa-py talk to a NI GPIB card without NI-VISA"),
    ("pyserial", "serial", None,
     "RS-232 / USB-serial instruments (Pfeiffer TPG 361 over FTDI)"),
    ("pytest", "pytest", None,
     "running the test suite in tests/"),
]

# Deep sections, all optional, all read-only.
#   (key, label, cost, default on)
DEEP_SECTIONS = [
    ("tkinter", "Tk / Tcl version and what the GUIs will look like",
     "instant", True),
    ("visa", "VISA layer: which backend Python would actually use",
     "~1 s", True),
    ("shadow", "Whether an installed pica_suite is shadowing this repo",
     "instant", True),
    ("paths", "Full sys.path, in order",
     "instant", False),
    ("inventory", "Every installed distribution and its version",
     "~1 s", False),
    ("resources", "List VISA addresses (enumerate only -- opens nothing)",
     "~2 s", False),
]

SEPARATOR = "-" * 74


# ===============================================================================
# VERSION ARITHMETIC
# ===============================================================================
# Deliberately not packaging.Version: this program must run in an environment
# that may be missing its dependencies, which is the whole point of it, so it
# cannot depend on one to say so. The comparison handles the only form the
# project actually pins -- dotted numbers with an optional suffix.

def parse_version(text):
    """'1.22.4' -> (1, 22, 4). Non-numeric tails are dropped, not guessed."""
    if not text:
        return ()
    parts = []
    for chunk in str(text).split('.'):
        match = re.match(r'^(\d+)', chunk.strip())
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def version_at_least(found, minimum):
    """True when `found` >= `minimum`. Unparseable versions pass.

    A version this simple parser cannot read is reported as-is rather than
    failed: the user can see the number, and a false "too old" on an odd
    version string would send them chasing an upgrade they do not need.
    """
    if not minimum:
        return True
    got, want = parse_version(found), parse_version(minimum)
    if not got:
        return True
    length = max(len(got), len(want))
    got = got + (0,) * (length - len(got))
    want = want + (0,) * (length - len(want))
    return got >= want


def read_requirements_file():
    """Minimum versions from the project's own requirements.txt, if reachable.

    Looks beside this file, two and three levels up. Returns {} when the file
    is not there -- a frozen build or a loose copy of this script -- and the
    built-in table is then the answer.
    """
    here = os.path.abspath(os.path.dirname(__file__))
    candidates = [
        os.path.join(here, "requirements.txt"),
        os.path.join(here, "..", "requirements.txt"),
        os.path.join(here, "..", "..", "requirements.txt"),
    ]
    for path in candidates:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            continue
        pins = {}
        try:
            with io.open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.split('#')[0].strip()
                    if not line:
                        continue
                    match = re.match(r'^([A-Za-z0-9_.\-]+)\s*>=\s*([0-9.]+)',
                                     line)
                    if match:
                        pins[match.group(1).lower()] = match.group(2)
                    elif re.match(r'^[A-Za-z0-9_.\-]+$', line):
                        pins[line.lower()] = None
        except OSError:
            return {}, path
        return pins, path
    return {}, None


# ===============================================================================
# THE PROBES
# ===============================================================================
# Each probe returns (lines, problems, warnings) so the GUI can print a verdict
# without re-reading its own log. A "problem" is something that will stop a
# measurement; a "warning" is something worth knowing that will not.

def probe_interpreter():
    """The interpreter itself: version, bit-ness, where it came from."""
    lines = ["PYTHON INTERPRETER", SEPARATOR]
    problems, warnings = [], []

    version = platform.python_version()
    bits = struct.calcsize("P") * 8
    lines.append(f"  Version            {version} "
                 f"({platform.python_implementation()})")
    lines.append(f"  Build              {bits}-bit  "
                 f"[machine: {platform.machine() or 'unknown'}]")
    lines.append(f"  Executable         {sys.executable}")
    lines.append(f"  Compiler           {platform.python_compiler()}")

    if sys.version_info < (3, 10):
        problems.append(
            f"Python {version} is older than the 3.10 PICA asks for.")
    elif sys.version_info >= (3, 14):
        warnings.append(
            f"Python {version} is newer than anything PICA has been run on "
            "in this lab; if a package misbehaves, suspect this first.")

    if bits == 32:
        warnings.append(
            "This is 32-bit Python. It can only load a 32-bit VISA/GPIB "
            "driver. That is correct for the 32-bit GPIB scanner and wrong "
            "for a 64-bit NI-488.2 install.")
    else:
        lines.append("  GPIB driver        needs the 64-bit NI-488.2 / "
                     "Keysight IO libraries")

    # A virtual environment is not a problem, but which one is running is the
    # first thing to establish when a package is "installed" and missing.
    base = getattr(sys, "base_prefix", sys.prefix)
    in_venv = base != sys.prefix
    lines.append(f"  Environment        "
                 f"{'virtual environment' if in_venv else 'system Python'}")
    lines.append(f"  sys.prefix         {sys.prefix}")
    if in_venv:
        lines.append(f"  base prefix        {base}")
    if os.environ.get("CONDA_DEFAULT_ENV"):
        lines.append(f"  conda env          "
                     f"{os.environ['CONDA_DEFAULT_ENV']}")
    if getattr(sys, "frozen", False):
        lines.append("  Frozen build       yes (PyInstaller or similar)")

    lines.append("")
    lines.append("OPERATING SYSTEM")
    lines.append(SEPARATOR)
    lines.append(f"  System             {platform.system()} "
                 f"{platform.release()}")
    lines.append(f"  Detail             {platform.platform()}")
    os_bits = platform.architecture()[0]
    lines.append(f"  OS build           {os_bits}")
    if os_bits.startswith("64") and bits == 32:
        warnings.append(
            "64-bit Windows running 32-bit Python: both driver families can "
            "be installed side by side, so check which one VISA loaded.")
    lines.append("")
    return lines, problems, warnings


def probe_packages(pins, pins_path):
    """Every required package: present or not, version, old or not."""
    lines = ["REQUIRED PACKAGES", SEPARATOR]
    problems, warnings = [], []

    if pins_path:
        lines.append(f"  minimums from      {pins_path}")
    else:
        lines.append("  minimums from      this program's built-in table "
                     "(requirements.txt not found)")
    lines.append("")
    lines.append(f"  {'PACKAGE':<14}{'NEEDS':<12}{'FOUND':<14}RESULT")

    for dist, import_name, fallback_min, purpose in REQUIRED_PACKAGES:
        minimum = pins.get(dist.lower(), fallback_min) or fallback_min
        found, how = _find_version(dist, import_name)
        need_text = f">= {minimum}" if minimum else "any"
        if found is None:
            verdict = "MISSING"
            problems.append(f"{dist} is not installed - {purpose}.")
            found_text = "-"
        else:
            found_text = found
            if version_at_least(found, minimum):
                verdict = "ok"
            else:
                verdict = "TOO OLD"
                problems.append(
                    f"{dist} {found} is older than the {minimum} PICA asks "
                    f"for - {purpose}.")
        lines.append(f"  {dist:<14}{need_text:<12}{found_text:<14}{verdict}"
                     + (f"   [{how}]" if how and found else ""))

    lines.append("")
    lines.append("OPTIONAL PACKAGES")
    lines.append(SEPARATOR)
    for dist, import_name, _minimum, purpose in OPTIONAL_PACKAGES:
        found, _how = _find_version(dist, import_name)
        if found is None:
            lines.append(f"  {dist:<14}not installed   ({purpose})")
        else:
            lines.append(f"  {dist:<14}{found:<14}  ({purpose})")
    lines.append("")
    return lines, problems, warnings


def _find_version(dist, import_name):
    """Version of one package, by metadata first and import second.

    Metadata is asked first because it is cheap and does not run the
    package's own import side effects. The import is the fallback -- and the
    real test, since a distribution can be recorded as installed while its
    module fails to load.
    """
    if importlib_metadata is not None:
        try:
            return importlib_metadata.version(dist), "metadata"
        except Exception:
            pass
    try:
        module = __import__(import_name)
    except Exception:
        return None, None
    for attribute in ("__version__", "VERSION", "version"):
        value = getattr(module, attribute, None)
        if isinstance(value, str):
            return value, "import"
    return "present (no version string)", "import"


def probe_tkinter():
    """The GUI layer every PICA module is drawn with."""
    lines = ["TKINTER", SEPARATOR]
    problems, warnings = [], []
    try:
        lines.append(f"  Tk version         {tk.TkVersion}")
        lines.append(f"  Tcl version        {tk.TclVersion}")
        if float(tk.TkVersion) < 8.6:
            warnings.append(
                f"Tk {tk.TkVersion} is older than 8.6; the PICA windows use "
                "clam styling that only looks right on 8.6 and later.")
    except Exception as exc:
        problems.append(f"Tkinter could not report its version: {exc}")
    lines.append("")
    return lines, problems, warnings


def probe_visa():
    """Which VISA backend Python would use -- without touching the bus.

    Creating a ResourceManager loads the backend library. It does not scan,
    open or address anything, so this is safe beside a running experiment.
    Listing the addresses is a separate, opt-in section.
    """
    lines = ["VISA LAYER", SEPARATOR]
    problems, warnings = [], []
    try:
        import pyvisa
    except Exception as exc:
        problems.append(f"PyVISA will not import: {exc}")
        lines.append(f"  PyVISA             will not import: {exc}")
        lines.append("")
        return lines, problems, warnings

    lines.append(f"  PyVISA             {getattr(pyvisa, '__version__', '?')}")

    # Two questions, not one: which backend pyvisa picks when asked for
    # nothing in particular (that is the one every PICA module gets), and
    # whether the pure-Python fallback is there at all. A default that
    # resolves to '@py' means no NI-VISA was found -- which reads as
    # "working" right up to the moment something is on GPIB.
    caught = []
    default_library = None
    opened = False
    for spec, label in (("", "default backend"), ("@py", "pyvisa-py")):
        try:
            with warnings_module.catch_warnings(record=True) as captured:
                warnings_module.simplefilter("always")
                rm = pyvisa.ResourceManager(spec) if spec \
                    else pyvisa.ResourceManager()
                library = getattr(rm.visalib, 'library_path', '') or "(unnamed)"
                rm.close()
                caught.extend(str(w.message) for w in captured)
            lines.append(f"  {label:<18} loaded    {library}")
            opened = True
            if not spec:
                default_library = str(library)
        except Exception as exc:
            lines.append(f"  {label:<18} unavailable: "
                         f"{str(exc).splitlines()[0][:70]}")

    if not opened:
        problems.append(
            "No VISA backend could be loaded at all. On this machine that "
            "usually means the NI-488.2 / Keysight IO libraries are missing, "
            "or they are installed for the other bit-ness of Python.")
    elif default_library is not None:
        if default_library.strip().lower() in ("py", "@py", "(unnamed)"):
            lines.append("  Resolved to        pyvisa-py, the pure-Python "
                         "backend")
            warnings.append(
                "PyVISA fell back to pyvisa-py: no NI-VISA or Keysight IO "
                "library was found. Ethernet and USB-serial instruments still "
                "work; the GPIB card does not, unless gpib-ctypes finds the "
                "driver DLL. Check that NI-488.2 is installed for "
                f"{struct.calcsize('P') * 8}-bit Python.")
        else:
            lines.append("  Resolved to        a vendor VISA library "
                         "(NI / Keysight) -- GPIB is available")

    for message in caught:
        lines.append(f"  note               {message.splitlines()[0][:70]}")
    lines.append("")
    return lines, problems, warnings


def probe_shadow():
    """Is an installed pica_suite standing in front of this repository?

    This has bitten before: a launcher started as a script picks up the
    site-packages copy, and every edit in the working tree appears to do
    nothing while errors name modules that were renamed months ago.
    """
    lines = ["WHICH PICA IS IMPORTED", SEPARATOR]
    problems, warnings = [], []

    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".."))
    lines.append(f"  This file           {os.path.abspath(__file__)}")
    lines.append(f"  Repository root     {repo_root}")

    try:
        import pica
        imported = os.path.abspath(
            os.path.dirname(getattr(pica, '__file__', '') or ''))
        version = getattr(pica, '__version__', None)
        lines.append(f"  'import pica' gives {imported or 'no file path'}")
        if version:
            lines.append(f"  Its version         {version}")
        expected = os.path.join(repo_root, "pica")
        if imported and os.path.normcase(imported) != os.path.normcase(expected):
            warnings.append(
                "'import pica' resolves to " + imported + ", not the "
                "repository copy at " + expected + ". An installed pica_suite "
                "is shadowing this working tree; uninstall it, or run the "
                "launcher with -m from the repository root.")
    except Exception as exc:
        lines.append(f"  'import pica' fails  {exc}")
        warnings.append(
            "The pica package cannot be imported from here. Running a module "
            "as a loose script is fine; importing across modules is not.")
    lines.append("")
    return lines, problems, warnings


def probe_paths():
    """Where imports come from, in the order Python asks."""
    lines = ["SYS.PATH (in order)", SEPARATOR]
    for index, entry in enumerate(sys.path):
        lines.append(f"  [{index:2d}] {entry or '(current directory)'}")
    lines.append("")
    return lines, [], []


def probe_inventory():
    """Everything installed, for the times the fault is a package you forgot."""
    lines = ["INSTALLED DISTRIBUTIONS", SEPARATOR]
    if importlib_metadata is None:
        lines.append("  importlib.metadata is unavailable on this Python.")
        lines.append("")
        return lines, [], []
    try:
        seen = {}
        for dist in importlib_metadata.distributions():
            name = (dist.metadata.get('Name') or '').strip()
            if name:
                seen[name] = dist.version
        for name in sorted(seen, key=str.lower):
            lines.append(f"  {name:<32}{seen[name]}")
        lines.append("")
        lines.append(f"  {len(seen)} distributions.")
    except Exception as exc:
        lines.append(f"  Inventory failed: {exc}")
    lines.append("")
    return lines, [], []


def probe_resources():
    """Enumerate VISA addresses. Opens nothing, sends nothing."""
    lines = ["VISA ADDRESSES", SEPARATOR]
    problems, warnings = [], []
    try:
        import pyvisa
        rm = pyvisa.ResourceManager()
        resources = list(rm.list_resources())
        rm.close()
    except Exception as exc:
        lines.append(f"  Enumeration failed: {exc}")
        lines.append("")
        return lines, problems, warnings
    if not resources:
        lines.append("  No VISA resources found.")
        warnings.append(
            "The bus enumerated with nothing on it. That is a driver or "
            "cabling question, not a Python one.")
    for resource in resources:
        lines.append(f"  {resource}")
    lines.append("")
    lines.append("  Addresses only -- no instrument was opened and no "
                 "command was sent.")
    lines.append("")
    return lines, problems, warnings


PROBE_FUNCTIONS = {
    "tkinter": probe_tkinter,
    "visa": probe_visa,
    "shadow": probe_shadow,
    "paths": probe_paths,
    "inventory": probe_inventory,
    "resources": probe_resources,
}


def run_survey(selected, emit):
    """The whole check, as a sequence of emitted lines. No Tk in here.

    `selected` is the set of deep-section keys to include; `emit` takes one
    line. Kept free of the GUI so the survey can be run and asserted on from
    a test, and so a future CLI needs no second copy of it.
    """
    problems, warnings = [], []

    emit("Python Environment Diagnostics")
    emit(datetime.now().strftime("Run %d %b %Y  %H:%M:%S"))
    emit("")

    pins, pins_path = read_requirements_file()

    stages = [probe_interpreter, lambda: probe_packages(pins, pins_path)]
    for key, _label, _cost, _default in DEEP_SECTIONS:
        if key in selected:
            stages.append(PROBE_FUNCTIONS[key])

    for stage in stages:
        lines, stage_problems, stage_warnings = stage()
        for line in lines:
            emit(line)
        problems.extend(stage_problems)
        warnings.extend(stage_warnings)

    emit("VERDICT")
    emit(SEPARATOR)
    if not problems and not warnings:
        emit("  Everything PICA needs is present and new enough, and nothing")
        emit("  about this interpreter looks unusual.")
    if problems:
        emit(f"  {len(problems)} problem(s) that will stop a measurement:")
        for item in problems:
            emit(f"    * {item}")
    if warnings:
        emit(f"  {len(warnings)} thing(s) worth knowing:")
        for item in warnings:
            emit(f"    - {item}")
    if problems:
        emit("")
        emit("  To install what is missing, from the repository root:")
        emit("    pip install -r requirements.txt")
    emit("")
    return problems, warnings


# ===============================================================================
# THE CONSOLE
# ===============================================================================

class PythonEnvDiagnosticsGUI:
    """Pick what to check, run, read, save.

    The same shape as the Cryocon 34 diagnostics console, because they are
    opened for the same reason and land in the same Diagnostic Tools list.
    """

    PROGRAM_NAME = "Python Environment Diagnostics"
    PROGRAM_VERSION = "1.0"
    POLL_MS = 60

    # House palette, identical to the other PICA modules.
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#F4EFEA'
    CLR_STATUS_OK = '#6B8E4E'
    CLR_STATUS_BAD = '#BA6B5E'

    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION} "
                        "(read-only)")
        self.root.geometry("1020x720")
        self.root.minsize(860, 540)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.queue = queue.Queue()
        self.worker = None
        self.after_id = None
        self.lines = []
        self.deep_vars = {}

        self.setup_styles()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self._log_line(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self._log_line("")
        self._log_line("Read-only. This program installs nothing and upgrades")
        self._log_line("nothing; it reports what is already here. Safe to run")
        self._log_line("while a measurement is going.")
        self._log_line("")
        self._log_line("Press Run Check.")

    # -- styles --

    def setup_styles(self):
        """The shared PICA look: clam, warm panels on the darker backing."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('Backing.TLabel', background=self.CLR_BG_DARK)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Connect.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Connect.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor=self.CLR_ACCENT_GOLD)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.map('TCheckbutton',
                  background=[('active', self.CLR_FRAME_BG)])
        style.configure('TProgressbar', background=self.CLR_ACCENT_GREEN,
                        troughcolor=self.CLR_INPUT_BG,
                        bordercolor=self.CLR_ACCENT_GOLD)

    # -- layout --

    def _build(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(header, text="Python Environment Diagnostics",
                  style='Header.TLabel',
                  font=('Segoe UI', self.FONT_BASE[1] + 4, 'bold'),
                  foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)
        ttk.Label(header, text="read-only", style='Header.TLabel',
                  foreground=self.CLR_STATUS_OK).pack(side='right', padx=20)

        head = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        head.pack(fill='x')
        ttk.Label(
            head, style='Backing.TLabel',
            text=("Checks the Python underneath PICA: the interpreter's "
                  "version and whether it is 32- or 64-bit, and every "
                  "package PICA needs against the version the project asks "
                  "for. Nothing is installed, upgraded or changed."),
            wraplength=960, justify='left').pack(anchor='w')

        summary = ttk.LabelFrame(self.root, text="This interpreter",
                                 padding=(10, 6))
        summary.pack(fill='x', padx=12, pady=(6, 4))
        bits = struct.calcsize("P") * 8
        ttk.Label(
            summary,
            text=(f"Python {platform.python_version()}   ·   {bits}-bit   ·   "
                  f"{platform.system()} {platform.release()}"),
            font=self.FONT_TITLE).pack(anchor='w')
        ttk.Label(summary, text=sys.executable,
                  font=self.FONT_CONSOLE).pack(anchor='w', pady=(2, 0))

        deep = ttk.LabelFrame(
            self.root,
            text="Extra checks (the interpreter and the package table always "
                 "run)",
            padding=(10, 6))
        deep.pack(fill='x', padx=12, pady=4)
        for column, (key, label, cost, default) in enumerate(DEEP_SECTIONS):
            var = tk.BooleanVar(value=default)
            self.deep_vars[key] = var
            ttk.Checkbutton(deep, text=f"{label}  ({cost})",
                            variable=var).grid(
                row=column // 2, column=column % 2, sticky='w',
                padx=(0, 20), pady=1)

        bar = ttk.Frame(self.root, padding=(12, 4))
        bar.pack(fill='x')
        self.run_btn = ttk.Button(bar, text="Run Check",
                                  style='Connect.TButton', command=self._start)
        self.run_btn.pack(side='left')
        self.save_btn = ttk.Button(bar, text="Save Log As...",
                                   command=self._save_as, state='disabled')
        self.save_btn.pack(side='left', padx=(6, 0))
        self.copy_btn = ttk.Button(bar, text="Copy All", command=self._copy)
        self.copy_btn.pack(side='left', padx=(6, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var,
                  style='Backing.TLabel').pack(side='right')

        prog = ttk.Frame(self.root, padding=(12, 0))
        prog.pack(fill='x')
        self.progress = ttk.Progressbar(prog, mode='determinate')
        self.progress.pack(fill='x')

        self.console = scrolledtext.ScrolledText(
            self.root, wrap='none', font=self.FONT_CONSOLE,
            bg=self.CLR_CONSOLE_BG, fg=self.CLR_TEXT_DARK,
            insertbackground=self.CLR_TEXT_DARK,
            relief='flat', highlightthickness=1,
            highlightbackground=self.CLR_ACCENT_GOLD)
        self.console.pack(fill='both', expand=True, padx=12, pady=(6, 12))
        self.console.config(state='disabled')

    # -- console --

    def _log_line(self, text):
        self.lines.append(text)
        self.console.config(state='normal')
        self.console.insert('end', text + "\n")
        self.console.see('end')
        self.console.config(state='disabled')

    # -- the run --

    def _start(self):
        """Run the survey on a worker thread; the queue carries it back.

        Tk is touched only from the main thread. Importing a dozen packages
        takes long enough on a cold cache to freeze a window, which is the
        whole reason this is not done inline.
        """
        if self.worker is not None and self.worker.is_alive():
            return
        selected = {key for key, var in self.deep_vars.items() if var.get()}
        self.run_btn.config(state='disabled')
        self.save_btn.config(state='disabled')
        self.status_var.set("Checking...")
        self.progress.config(mode='indeterminate')
        self.progress.start(40)

        self.console.config(state='normal')
        self.console.delete('1.0', 'end')
        self.console.config(state='disabled')
        self.lines = []

        def work():
            try:
                problems, warnings = run_survey(selected, self.queue.put)
                self.queue.put(("__done__", len(problems), len(warnings)))
            except Exception as exc:                        # pragma: no cover
                self.queue.put(f"The check itself failed: {exc}")
                self.queue.put(("__done__", 1, 0))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.after_id = self.root.after(self.POLL_MS, self._drain)

    def _drain(self):
        """Move finished lines onto the console. One tk.after chain only."""
        self.after_id = None
        try:
            while True:
                item = self.queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__done__":
                    self._finish(item[1], item[2])
                    return
                self._log_line(item)
        except queue.Empty:
            pass
        self.after_id = self.root.after(self.POLL_MS, self._drain)

    def _finish(self, problems, warnings):
        self.progress.stop()
        self.progress.config(mode='determinate', value=0)
        self.run_btn.config(state='normal')
        self.save_btn.config(state='normal')
        if problems:
            self.status_var.set(f"{problems} problem(s) found.")
        elif warnings:
            self.status_var.set(f"No problems; {warnings} note(s).")
        else:
            self.status_var.set("All clear.")

    # -- output --

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save the diagnostics log",
            defaultextension=".txt",
            initialfile=datetime.now().strftime(
                "python_env_diagnostics_%Y%m%d_%H%M%S.txt"),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with io.open(path, 'w', encoding='utf-8') as handle:
                handle.write("\n".join(self.lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self.status_var.set(f"Saved to {os.path.basename(path)}")

    def _copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(self.lines))
        self.status_var.set("Log copied to the clipboard.")

    def _on_closing(self):
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        self.root.destroy()


def main():
    root = tk.Tk()
    PythonEnvDiagnosticsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
