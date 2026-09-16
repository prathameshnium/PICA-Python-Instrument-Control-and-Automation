#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PROGRAM:      System and Driver Diagnostics
 VERSION:      1.0
 DATE:         17 Sep 2026

 PURPOSE:
   Describes the machine PICA is running on and, above all, the instrument
   control software installed on it: NI-488.2, NI-VISA, the Keysight IO
   Libraries, IVI. For each one it reports the version the installer
   registered AND the driver DLL actually sitting on disk -- its path, its
   size, its date and whether it is a 32- or 64-bit binary.

   That last point is the reason this program exists. A 64-bit Python cannot
   load a 32-bit GPIB driver and a 32-bit Python cannot load a 64-bit one,
   and neither failure says so: VISA simply reports that no backend could be
   opened, or falls back to pyvisa-py and quietly loses the GPIB card. Seeing
   both bit-nesses side by side answers in one line what the error messages
   never do.

 READ-ONLY:
   Registry keys are opened for reading only. Files are read, never written
   (apart from a log you ask for by name). No installer, no repair, no
   service is started or stopped, and nothing is sent to any instrument --
   this program does not open the bus at all. Safe beside a running
   measurement.

 SCOPE:
   Windows is where the driver questions live, so the driver sections are
   Windows-only and say so plainly on anything else. The machine and Python
   sections work everywhere.

 DEPENDENCIES:
   Standard library and Tkinter only. psutil, if it happens to be installed,
   is used for a memory figure and skipped silently when it is not.
===============================================================================
"""

import ctypes
import io
import os
import platform
import queue
import shutil
import socket
import struct
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

IS_WINDOWS = os.name == 'nt'

if IS_WINDOWS:
    try:
        import winreg
    except ImportError:                                        # pragma: no cover
        winreg = None
else:                                                          # pragma: no cover
    winreg = None


# ===============================================================================
# WHAT WE LOOK FOR
# ===============================================================================
# The driver DLLs that matter, in the order a question about them arises.
# gpib-32.dll is the NI-488.2 entry point every GPIB program on Windows ends
# up at, whatever its own name for it; ni4882.dll is the modern one behind it.
# visa32.dll is the VISA entry point and is 32- OR 64-bit despite the name --
# a historical wart that has confused more than one afternoon, so it is
# spelled out in the report rather than assumed.
DRIVER_DLLS = [
    ("gpib-32.dll", "NI-488.2 entry point (all GPIB traffic goes through it)"),
    ("ni4882.dll", "NI-488.2 core library"),
    ("visa32.dll", "VISA entry point -- 32- or 64-bit despite the name"),
    ("visa64.dll", "VISA, explicitly 64-bit"),
    ("nivisa32.dll", "NI-VISA implementation"),
    ("ktvisa32.dll", "Keysight VISA implementation"),
    ("agvisa32.dll", "Agilent VISA (older Keysight installs)"),
    ("ivi.dll", "IVI shared components"),
]

# Registry homes the vendors register their versions under. Each entry is
# (hive path, value name, label); a missing key is an answer, not an error.
VERSION_KEYS = [
    (r"SOFTWARE\National Instruments\NI-488.2\CurrentVersion",
     None, "NI-488.2"),
    (r"SOFTWARE\WOW6432Node\National Instruments\NI-488.2\CurrentVersion",
     None, "NI-488.2 (32-bit view)"),
    (r"SOFTWARE\National Instruments\NI-VISA\CurrentVersion",
     None, "NI-VISA"),
    (r"SOFTWARE\WOW6432Node\National Instruments\NI-VISA\CurrentVersion",
     None, "NI-VISA (32-bit view)"),
    (r"SOFTWARE\Keysight Technologies\IO Libraries Suite",
     "Version", "Keysight IO Libraries Suite"),
    (r"SOFTWARE\WOW6432Node\Keysight Technologies\IO Libraries Suite",
     "Version", "Keysight IO Libraries (32-bit view)"),
    (r"SOFTWARE\Agilent\IO Libraries Suite", "Version",
     "Agilent IO Libraries Suite"),
]

# Names in the Windows uninstall list worth reporting. Matched case-folded as
# substrings, because vendors rename their own products between releases.
SOFTWARE_PATTERNS = [
    "ni-488", "ni-visa", "ni-daq", "ni system", "ni package",
    "measurement & automation", "labview",
    "io libraries", "keysight", "agilent", "ivi", "visa",
    "gpib", "ftdi", "prologix", "tektronix", "keithley",
]

# Environment variables that decide which library gets loaded. PATH is
# reported separately, entry by entry, because the winner is whichever
# vendor's folder comes first.
ENV_OF_INTEREST = [
    "VXIPNPPATH", "VXIPNPPATH64", "VISA_LIB", "PYVISA_LIBRARY",
    "NIEXTCCOMPILERSUPP", "IVIROOTDIR", "IVIROOTDIR32", "IVIROOTDIR64",
    "PYTHONPATH", "PYTHONHOME",
]

DEEP_SECTIONS = [
    ("registry", "Versions the installers registered", "instant", True),
    ("dlls", "Driver DLLs on disk: path, date, 32/64-bit", "~1 s", True),
    ("loadable", "Whether THIS Python can load each driver DLL", "~2 s", True),
    ("software", "Instrument-control software in the uninstall list",
     "~3 s", False),
    ("environment", "Environment variables and PATH, entry by entry",
     "instant", False),
    ("storage", "Disk space where data is written", "instant", False),
]

SEPARATOR = "-" * 74

# Machine values from the PE header, which is how a DLL's bit-ness is read
# without loading it. Reading four bytes of a file cannot fail in a way that
# matters and cannot disturb a driver that is in use.
PE_MACHINES = {
    0x014c: "32-bit (x86)",
    0x8664: "64-bit (x64)",
    0xAA64: "64-bit (ARM64)",
    0x01c4: "32-bit (ARM)",
}


# ===============================================================================
# SMALL READ-ONLY HELPERS
# ===============================================================================
# Everything here opens something for reading and closes it. There is no
# write path anywhere in this file.

def registry_value(path, name=None, hive=None):
    """One registry value, or None. A missing key is a normal answer."""
    if not IS_WINDOWS or winreg is None:
        return None
    hive = hive if hive is not None else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, name or "")
            return value
    except OSError:
        return None


def registry_subkeys(path, hive=None):
    """The subkey names under one key, or [] when it is not there."""
    if not IS_WINDOWS or winreg is None:
        return []
    hive = hive if hive is not None else winreg.HKEY_LOCAL_MACHINE
    names = []
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return []
    return names


def pe_machine(path):
    """32- or 64-bit, read straight out of the PE header.

    Cheaper and safer than loading the library: a DLL that cannot be loaded
    by this Python still has to be identified, and that is exactly the case
    the user is here to diagnose.
    """
    try:
        with open(path, 'rb') as handle:
            if handle.read(2) != b'MZ':
                return "not a Windows binary"
            handle.seek(0x3C)
            offset = struct.unpack('<I', handle.read(4))[0]
            handle.seek(offset)
            if handle.read(4) != b'PE\0\0':
                return "unreadable PE header"
            machine = struct.unpack('<H', handle.read(2))[0]
    except OSError as exc:
        return f"unreadable ({exc.strerror or exc})"
    return PE_MACHINES.get(machine, f"unknown machine 0x{machine:04X}")


def file_version(path):
    """The vendor's own version string for a DLL, when Windows can read it."""
    if not IS_WINDOWS:
        return None
    try:
        version_dll = ctypes.windll.version
        size = version_dll.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path),
                                                   None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0,
                                               size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version_dll.VerQueryValueW(buffer, ctypes.c_wchar_p("\\"),
                                          ctypes.byref(pointer),
                                          ctypes.byref(length)):
            return None
        # VS_FIXEDFILEINFO: the two DWORDs at offsets 8 and 12 are the file
        # version, most significant half first.
        raw = ctypes.string_at(pointer, length.value)
        most, least = struct.unpack_from('<II', raw, 8)
        return (f"{most >> 16}.{most & 0xFFFF}."
                f"{least >> 16}.{least & 0xFFFF}")
    except Exception:
        return None


def search_folders():
    """Where a driver DLL could be, most authoritative first."""
    folders = []
    if IS_WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        folders += [os.path.join(root, "System32"),
                    os.path.join(root, "SysWOW64")]
    folders += [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                if entry.strip()]
    seen, unique = set(), []
    for folder in folders:
        key = os.path.normcase(os.path.abspath(folder)) if folder else ""
        if key and key not in seen:
            seen.add(key)
            unique.append(folder)
    return unique


def find_dll(name):
    """Every copy of one DLL that is on the search path, in order."""
    hits = []
    for folder in search_folders():
        candidate = os.path.join(folder, name)
        try:
            if os.path.isfile(candidate):
                hits.append(candidate)
        except OSError:
            continue
    return hits


def human_bytes(count):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return f"{count:,.1f} {unit}" if unit != "B" else f"{count} B"
        count /= 1024.0
    return f"{count:.1f} TB"                                   # pragma: no cover


# ===============================================================================
# THE PROBES
# ===============================================================================
# Each returns (lines, problems, warnings). A problem stops a measurement; a
# warning is worth knowing and does not.

def probe_machine():
    """The computer itself."""
    lines = ["THIS MACHINE", SEPARATOR]
    problems, warnings = [], []

    lines.append(f"  Host name          {socket.gethostname()}")
    lines.append(f"  System             {platform.system()} "
                 f"{platform.release()}")
    lines.append(f"  Detail             {platform.platform()}")
    lines.append(f"  OS build           {platform.architecture()[0]}")
    lines.append(f"  Processor          "
                 f"{platform.processor() or platform.machine()}")
    lines.append(f"  Logical cores      {os.cpu_count()}")

    total = _physical_memory()
    if total:
        lines.append(f"  Physical memory    {human_bytes(total)}")

    bits = struct.calcsize("P") * 8
    lines.append(f"  Python             {platform.python_version()}  "
                 f"({bits}-bit)")
    lines.append(f"  Python executable  {sys.executable}")
    if bits == 32 and platform.architecture()[0].startswith("64"):
        warnings.append(
            "32-bit Python on 64-bit Windows: the driver sections below tell "
            "you whether the GPIB library installed here matches.")

    if IS_WINDOWS:
        try:
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            lines.append(f"  Running as admin   {'yes' if admin else 'no'}")
        except Exception:
            pass
    lines.append("")
    return lines, problems, warnings


def _physical_memory():
    """Total RAM, by whichever route is available. None when neither is."""
    try:
        import psutil
        return psutil.virtual_memory().total
    except Exception:
        pass
    if IS_WINDOWS:
        try:
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(
                    ctypes.byref(status)):
                return status.ullTotalPhys
        except Exception:
            return None
    return None


def probe_registry():
    """What each vendor's installer recorded about itself."""
    lines = ["INSTALLED VERSIONS (from the registry)", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only. Nothing to read on this system.")
        lines.append("")
        return lines, problems, warnings

    found_any = False
    for path, name, label in VERSION_KEYS:
        value = registry_value(path, name)
        if value:
            found_any = True
            lines.append(f"  {label:<34}{value}")
    if not found_any:
        lines.append("  No NI-488.2, NI-VISA or Keysight IO version key "
                     "was found.")
        warnings.append(
            "No vendor VISA/GPIB package registered a version on this "
            "machine. If this is the measurement PC, GPIB will not work "
            "until NI-488.2 is installed; on a laptop used for analysis, "
            "this is expected.")

    # The NI tree is worth showing even when the version keys are absent:
    # a partial install (Package Manager but no NI-488.2) looks like nothing
    # at all from the keys above, and looks like this from here.
    for path, label in ((r"SOFTWARE\National Instruments", "National "
                         "Instruments (64-bit view)"),
                        (r"SOFTWARE\WOW6432Node\National Instruments",
                         "National Instruments (32-bit view)"),
                        (r"SOFTWARE\Keysight Technologies", "Keysight"),
                        (r"SOFTWARE\WOW6432Node\Keysight Technologies",
                         "Keysight (32-bit view)")):
        children = registry_subkeys(path)
        if children:
            lines.append("")
            lines.append(f"  {label}:")
            for child in sorted(children):
                lines.append(f"    {child}")
    lines.append("")
    return lines, problems, warnings


def probe_dlls():
    """Every driver DLL on the search path, with its bit-ness."""
    lines = ["DRIVER LIBRARIES ON DISK", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS:
        lines.append("  Windows only. On this system VISA is provided by "
                     "the shared libraries pyvisa finds for itself.")
        lines.append("")
        return lines, problems, warnings

    python_bits = struct.calcsize("P") * 8
    lines.append(f"  This Python is {python_bits}-bit, so it can only load a "
                 f"{python_bits}-bit library.")
    lines.append("")

    gpib_usable = False
    visa_usable = False
    for name, purpose in DRIVER_DLLS:
        hits = find_dll(name)
        if not hits:
            lines.append(f"  {name:<16}not found        ({purpose})")
            continue
        for path in hits:
            machine = pe_machine(path)
            version = file_version(path)
            try:
                stat = os.stat(path)
                when = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%d %b %Y")
                size = human_bytes(stat.st_size)
            except OSError:
                when, size = "?", "?"
            lines.append(f"  {name:<16}{machine}")
            lines.append(f"  {'':<16}{path}")
            lines.append(f"  {'':<16}{size}, {when}"
                         + (f", version {version}" if version else ""))
            matches = str(python_bits) in machine
            if matches and name in ("gpib-32.dll", "ni4882.dll"):
                gpib_usable = True
            if matches and name in ("visa32.dll", "visa64.dll",
                                    "nivisa32.dll", "ktvisa32.dll"):
                visa_usable = True
        lines.append("")

    if not any(find_dll(name) for name, _ in DRIVER_DLLS):
        warnings.append(
            "No VISA or GPIB driver library is on this machine at all. "
            "pyvisa-py can still reach Ethernet and USB-serial instruments; "
            "a GPIB card cannot be used.")
    else:
        if not gpib_usable:
            warnings.append(
                f"No {python_bits}-bit NI-488.2 library was found. A GPIB "
                f"card will not be reachable from this {python_bits}-bit "
                "Python, even if the card and its driver are installed for "
                "the other bit-ness.")
        if not visa_usable:
            warnings.append(
                f"No {python_bits}-bit VISA library was found, so pyvisa "
                "will fall back to its pure-Python backend.")
    lines.append("")
    return lines, problems, warnings


def probe_loadable():
    """Can THIS interpreter actually load the libraries it found?

    Loading a library is not talking to an instrument: no board is opened,
    no address is addressed and no command is sent. It is the one test that
    settles a bit-ness argument, because the loader either accepts the file
    or refuses it by name.
    """
    lines = ["WHAT THIS PYTHON CAN LOAD", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    for name, _purpose in DRIVER_DLLS:
        hits = find_dll(name)
        if not hits:
            continue
        path = hits[0]
        try:
            ctypes.WinDLL(path)
            lines.append(f"  {name:<16}loads")
        except OSError as exc:
            detail = str(exc).splitlines()[0][:60]
            lines.append(f"  {name:<16}REFUSED   {detail}")
            if "not a valid Win32" in str(exc) or "%1 is not" in str(exc):
                warnings.append(
                    f"{name} exists but is the wrong bit-ness for this "
                    "Python. That is the mismatch this program is for: "
                    "install the driver for "
                    f"{struct.calcsize('P') * 8}-bit, or run PICA on the "
                    "other Python.")
    if len(lines) == 2:
        lines.append("  Nothing to load: no driver library was found.")
    lines.append("")
    lines.append("  No board was opened and no command was sent -- loading a")
    lines.append("  library only asks Windows whether the file fits.")
    lines.append("")
    return lines, problems, warnings


def probe_software():
    """Instrument-control software, as Windows lists it for uninstall."""
    lines = ["INSTRUMENT-CONTROL SOFTWARE INSTALLED", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    roots = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "64-bit"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
         "32-bit"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "per-user"),
    ]
    found = []
    for hive, path, view in roots:
        for child in registry_subkeys(path, hive=hive):
            name = registry_value(f"{path}\\{child}", "DisplayName",
                                  hive=hive)
            if not name:
                continue
            folded = name.lower()
            if not any(pattern in folded for pattern in SOFTWARE_PATTERNS):
                continue
            version = registry_value(f"{path}\\{child}", "DisplayVersion",
                                     hive=hive) or ""
            found.append((name, str(version), view))

    if not found:
        lines.append("  Nothing matching a VISA, GPIB or instrument vendor "
                     "was found in the uninstall list.")
    for name, version, view in sorted(set(found)):
        lines.append(f"  {name[:46]:<48}{version:<16}[{view}]")
    lines.append("")
    lines.append(f"  {len(set(found))} entr(ies).")
    lines.append("")
    return lines, problems, warnings


def probe_environment():
    """The variables and the PATH order that decide which library wins."""
    lines = ["ENVIRONMENT", SEPARATOR]
    for name in ENV_OF_INTEREST:
        value = os.environ.get(name)
        lines.append(f"  {name:<20}{value if value else '(not set)'}")
    lines.append("")
    lines.append("  PATH, in the order Windows searches it. The first folder")
    lines.append("  holding a VISA or GPIB DLL is the one that gets loaded:")
    interesting = ("visa", "ni", "gpib", "keysight", "agilent", "ivi",
                   "national")
    for index, entry in enumerate(os.environ.get("PATH", "").split(os.pathsep)):
        if not entry.strip():
            continue
        folded = entry.lower()
        mark = "  <--" if any(word in folded for word in interesting) else ""
        lines.append(f"    [{index:2d}] {entry}{mark}")
    lines.append("")
    return lines, [], []


def probe_storage():
    """Room where the data goes. A run that fills the disk is a lost run."""
    lines = ["STORAGE", SEPARATOR]
    problems, warnings = [], []
    # The current drive's root, the system drive, the home folder and
    # wherever PICA itself is sitting -- a run writes to the last of those
    # and people forget it is not on C.
    targets = [os.path.abspath(os.sep),
               os.environ.get("SystemDrive", "") + os.sep if IS_WINDOWS
               else os.sep,
               os.path.expanduser("~"),
               os.path.abspath(os.path.dirname(__file__))]
    seen = set()
    for target in targets:
        key = os.path.normcase(target)
        if key in seen:
            continue
        seen.add(key)
        try:
            usage = shutil.disk_usage(target)
        except OSError as exc:
            lines.append(f"  {target:<44}unreadable ({exc.strerror or exc})")
            continue
        free_fraction = usage.free / usage.total if usage.total else 0
        lines.append(f"  {target[:42]:<44}{human_bytes(usage.free)} free "
                     f"of {human_bytes(usage.total)}")
        if usage.free < 512 * 1024 * 1024:
            problems.append(
                f"Less than 512 MB free on {target}. A long unattended run "
                "writes continuously and will stop when the disk fills.")
        elif free_fraction < 0.05:
            warnings.append(f"{target} is more than 95% full.")
    lines.append("")
    return lines, problems, warnings


PROBE_FUNCTIONS = {
    "dlls": probe_dlls,
    "registry": probe_registry,
    "loadable": probe_loadable,
    "software": probe_software,
    "environment": probe_environment,
    "storage": probe_storage,
}


def run_survey(selected, emit):
    """The whole survey, as emitted lines. No Tk in here.

    Kept headless so a test can assert on it and a future CLI needs no
    second copy of the logic.
    """
    problems, warnings = [], []

    emit("System and Driver Diagnostics")
    emit(datetime.now().strftime("Run %d %b %Y  %H:%M:%S"))
    emit("")

    stages = [probe_machine]
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
        emit("  Nothing about this machine or its instrument-control")
        emit("  software looks wrong from here.")
    if problems:
        emit(f"  {len(problems)} problem(s):")
        for item in problems:
            emit(f"    * {item}")
    if warnings:
        emit(f"  {len(warnings)} thing(s) worth knowing:")
        for item in warnings:
            emit(f"    - {item}")
    emit("")
    return problems, warnings


# ===============================================================================
# THE CONSOLE
# ===============================================================================

class SystemInfoDiagnosticsGUI:
    """Pick what to look at, run, read, save.

    The same shape and the same palette as the other PICA diagnostics
    consoles: they are opened for the same reason and sit in the same list.
    """

    PROGRAM_NAME = "System and Driver Diagnostics"
    PROGRAM_VERSION = "1.0"
    POLL_MS = 60

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
        self._log_line("Read-only. Registry keys and files are read, never")
        self._log_line("written; no installer or service is touched, and no")
        self._log_line("instrument is contacted. Safe during a measurement.")
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
        style.map('TCheckbutton', background=[('active', self.CLR_FRAME_BG)])
        style.configure('TProgressbar', background=self.CLR_ACCENT_GREEN,
                        troughcolor=self.CLR_INPUT_BG,
                        bordercolor=self.CLR_ACCENT_GOLD)

    # -- layout --

    def _build(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(header, text="System and Driver Diagnostics",
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
            text=("What this computer has installed for talking to "
                  "instruments: NI-488.2, NI-VISA, the Keysight IO "
                  "libraries, and the driver DLLs themselves -- where they "
                  "are, how old they are, and whether they are 32- or "
                  "64-bit. Nothing is installed, changed or contacted."),
            wraplength=960, justify='left').pack(anchor='w')

        summary = ttk.LabelFrame(self.root, text="This machine",
                                 padding=(10, 6))
        summary.pack(fill='x', padx=12, pady=(6, 4))
        bits = struct.calcsize("P") * 8
        ttk.Label(
            summary,
            text=(f"{socket.gethostname()}   ·   {platform.system()} "
                  f"{platform.release()} ({platform.architecture()[0]})"
                  f"   ·   Python {platform.python_version()} {bits}-bit"),
            font=self.FONT_TITLE).pack(anchor='w')
        ttk.Label(summary,
                  text=f"A {bits}-bit Python can only load a {bits}-bit "
                       "GPIB or VISA driver.",
                  font=self.FONT_CONSOLE).pack(anchor='w', pady=(2, 0))

        deep = ttk.LabelFrame(
            self.root, text="Sections (the machine summary always runs)",
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

        Tk is touched from the main thread only. Walking the uninstall list
        and stat-ing every folder on PATH is slow enough on a cold cache to
        freeze a window.
        """
        if self.worker is not None and self.worker.is_alive():
            return
        selected = {key for key, var in self.deep_vars.items() if var.get()}
        self.run_btn.config(state='disabled')
        self.save_btn.config(state='disabled')
        self.status_var.set("Looking...")
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
                "system_diagnostics_%Y%m%d_%H%M%S.txt"),
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
    SystemInfoDiagnosticsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
