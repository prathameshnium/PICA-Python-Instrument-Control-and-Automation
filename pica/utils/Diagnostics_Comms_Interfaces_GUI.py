#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PROGRAM:      Communication Interface Diagnostics
 VERSION:      1.0
 DATE:         17 Sep 2026

 PURPOSE:
   Answers "what is this computer actually connected to?" one layer below the
   instruments: the GPIB boards (PCI/PCIe cards and USB-GPIB adapters), the
   serial and USB-serial ports, the USB controllers and which of them are USB
   2 or USB 3, the network interfaces LAN instruments would be found on, and
   the VISA addresses that all of this adds up to.

   It is the companion to the Instrument Status window, and deliberately
   stops where that one starts. This program finds the INTERFACES; the
   Instrument Status window and the Full VISA / GPIB Scanner ask the
   instruments who they are. When a scan finds nothing, the question is
   whether there is a bus at all -- and that question is answered here,
   without putting a single byte on the wire.

 READ-ONLY, AND QUIETER THAN THAT:
   No instrument is opened. No *IDN? is sent. No serial port is opened --
   opening one toggles DTR and can reset the device on the other end, which
   is precisely what you do not want beside a running experiment. Windows is
   asked what hardware it has, through registry keys opened for reading, and
   VISA is asked to enumerate addresses, which puts no traffic on GPIB.

 DEPENDENCIES:
   Standard library and Tkinter only. pyvisa and psutil are used when they
   are installed and reported as absent when they are not.
===============================================================================
"""

import io
import os
import platform
import queue
import socket
import struct
import sys
import threading
import tkinter as tk
import warnings as warnings_module
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
# WHO MAKES WHAT
# ===============================================================================
# USB and PCI vendor IDs worth naming when they turn up. Anything not in here
# is still listed -- an unrecognised adapter is a finding, not a non-event --
# it just does not get a friendly name.
USB_VENDORS = {
    "3923": "National Instruments (GPIB-USB-HS, USB-232, DAQ)",
    "0957": "Keysight / Agilent (82357 GPIB-USB, USB instruments)",
    "0403": "FTDI (USB-serial: Pfeiffer TPG 361, Prologix, adapters)",
    "05E6": "Keithley",
    "0699": "Tektronix",
    "0B21": "Yokogawa",
    "1AB1": "Rigol",
    "164E": "Prologix / miscellaneous serial bridges",
    "067B": "Prolific (USB-serial adapter)",
    "10C4": "Silicon Labs (CP210x USB-serial)",
    "1A86": "QinHeng (CH340 USB-serial)",
}

PCI_VENDORS = {
    "1093": "National Instruments (PCI/PCIe-GPIB, DAQ)",
    "15BC": "Agilent / Keysight",
    "10E8": "AMCC (older GPIB cards)",
    "12E2": "Computer Boards / MCC",
}

# Services Windows registers for a GPIB board. Their presence means a driver
# is installed for a card; their state is not asked for, because asking would
# mean touching the service control manager.
GPIB_SERVICES = [
    ("NIPALK", "NI Platform Abstraction Layer (under NI-488.2 / NI-VISA)"),
    ("nipxirmk", "NI PXI resource manager"),
    ("NiViPxiK", "NI-VISA PXI"),
    ("GPIB", "generic GPIB driver service"),
    ("ni488k", "NI-488.2 kernel driver"),
    ("NIUSB488K", "NI GPIB-USB driver"),
    ("ktgpib", "Keysight GPIB"),
    ("agtgpib", "Agilent GPIB"),
]

# Bus prefixes VISA uses, in the order a lab thinks about them.
VISA_BUS_NAMES = [
    ("GPIB", "GPIB card or USB-GPIB adapter"),
    ("USB", "USBTMC instrument (direct USB, not a serial adapter)"),
    ("TCPIP", "LAN / LXI instrument"),
    ("ASRL", "serial port (real RS-232 or a USB-serial adapter)"),
    ("PXI", "PXI chassis"),
    ("VXI", "VXI chassis"),
]

DEEP_SECTIONS = [
    ("visa", "VISA layer: which backend, and every address it enumerates",
     "~2 s", True),
    ("gpib", "GPIB boards and adapters Windows knows about", "~1 s", True),
    ("serial", "Serial and USB-serial ports (never opened)", "instant", True),
    ("usb", "USB devices and whether the controllers are USB 2 or 3",
     "~2 s", False),
    ("network", "Network interfaces LAN instruments live on", "~1 s", False),
]

SEPARATOR = "-" * 74


# ===============================================================================
# SMALL READ-ONLY HELPERS
# ===============================================================================

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


def registry_values(path, hive=None):
    """Every (name, value) under one key. Used for the serial port map."""
    if not IS_WINDOWS or winreg is None:
        return []
    hive = hive if hive is not None else winreg.HKEY_LOCAL_MACHINE
    pairs = []
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                pairs.append((name, value))
                index += 1
    except OSError:
        return []
    return pairs


def readable_name(value):
    """The human half of a Windows device name, or '' when there isn't one.

    Windows stores these two ways. A plain string ("HP Wide Vision HD
    Camera") is already the answer. An indirect one starts with '@' and
    names a resource inside an .inf or a .sys, with the resolved text after
    the first semicolon -- and sometimes more semicolon-separated junk after
    THAT, which is why this splits on the first and not the last. Taking the
    last is how a USB controller ends up reported as "(AMD,3.10,1.10)".
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith('@'):
        if ';' not in text:
            return ""
        text = text.split(';', 1)[1].strip()
        # A leftover %1-style placeholder means Windows would have filled it
        # in from the driver at display time. Nothing here can, and a name
        # with holes in it reads worse than the plainer DeviceDesc the
        # caller falls back to, so this one is declined.
        if '%' in text:
            return ""
    return text


def enum_devices(bus):
    """Devices under HKLM\\SYSTEM\\...\\Enum\\<bus>, as (id, description).

    This is how Windows records what is plugged in. Reading it neither opens
    the device nor disturbs its driver, which is the whole reason it is used
    here rather than an enumeration API that touches the hardware.
    """
    root = r"SYSTEM\CurrentControlSet\Enum" + "\\" + bus
    devices = []
    for hardware_id in registry_subkeys(root):
        for instance in registry_subkeys(f"{root}\\{hardware_id}"):
            path = f"{root}\\{hardware_id}\\{instance}"
            description = (readable_name(registry_value(path, "FriendlyName"))
                           or readable_name(registry_value(path,
                                                           "DeviceDesc")))
            service = registry_value(path, "Service") or ""
            devices.append((hardware_id, instance, description, service))
    return devices


# Driver services a USB-serial adapter binds to. Matching the SERVICE is
# what distinguishes a real adapter from a "USB Composite Device", whose
# description contains the letters "com" and fooled a substring test.
SERIAL_SERVICES = {"usbser", "ftdibus", "ftdiport", "silabser", "silabenm",
                   "ch341ser", "ch343ser", "ser2pl", "usbcdcacm", "wceusbsh"}


def is_usb_serial(description, service):
    """True for a USB-serial adapter, false for anything merely USB.

    Two independent signals: the driver service, and a description that
    names a COM port or says "serial" as a word. "USB Composite Device"
    satisfies neither, which is the point.
    """
    if str(service).strip().lower() in SERIAL_SERVICES:
        return True
    text = str(description).lower()
    if "(com" in text or "com port" in text:
        return True
    return any(word in text.split() or word in text
               for word in ("serial", "ftdi", "cp210", "ch340", "prolific"))


def vendor_of(hardware_id, table):
    """Friendly maker name for a VID_xxxx / VEN_xxxx hardware id."""
    upper = hardware_id.upper()
    for marker in ("VID_", "VEN_"):
        if marker in upper:
            code = upper.split(marker, 1)[1][:4]
            return table.get(code), code
    return None, None


# ===============================================================================
# THE PROBES
# ===============================================================================

def probe_summary():
    """One paragraph: what this machine could talk to, before the detail."""
    lines = ["THIS MACHINE", SEPARATOR]
    bits = struct.calcsize("P") * 8
    lines.append(f"  Host name          {socket.gethostname()}")
    lines.append(f"  System             {platform.system()} "
                 f"{platform.release()} ({platform.architecture()[0]})")
    lines.append(f"  Python             {platform.python_version()} "
                 f"({bits}-bit)")
    lines.append("")
    lines.append("  Nothing below opens an instrument, opens a serial port")
    lines.append("  or sends *IDN?. Identification is the Instrument Status")
    lines.append("  window's job; this program only finds the interfaces.")
    lines.append("")
    return lines, [], []


def probe_visa():
    """Which backend VISA resolves to, and every address it enumerates.

    Enumeration is not traffic: VISA lists what its drivers have configured
    and, on GPIB, what the board reports. No address is opened and no command
    is sent, so this is safe beside a running measurement -- and safe beside
    the Novocontrol Alpha, which must never be probed automatically.
    """
    lines = ["VISA LAYER", SEPARATOR]
    problems, warnings = [], []
    try:
        import pyvisa
    except Exception as exc:
        lines.append(f"  PyVISA will not import: {exc}")
        problems.append("PyVISA is not available, so nothing can be reached "
                        "through VISA at all.")
        lines.append("")
        return lines, problems, warnings

    lines.append(f"  PyVISA             {getattr(pyvisa, '__version__', '?')}")
    library = None
    resources = []
    caught = []
    try:
        with warnings_module.catch_warnings(record=True) as captured:
            warnings_module.simplefilter("always")
            rm = pyvisa.ResourceManager()
            library = str(getattr(rm.visalib, 'library_path', '') or "(unnamed)")
            resources = list(rm.list_resources())
            rm.close()
            # ResourceWarnings about sockets pyvisa-py left open are noise
            # from the enumeration itself, not findings about this machine.
            caught = [str(item.message) for item in captured
                      if not issubclass(item.category, ResourceWarning)
                      and "unclosed" not in str(item.message).lower()]
    except Exception as exc:
        lines.append(f"  Backend            will not open: "
                     f"{str(exc).splitlines()[0][:60]}")
        problems.append("No VISA backend could be opened.")
        lines.append("")
        return lines, problems, warnings

    lines.append(f"  Backend library    {library}")
    if library.strip().lower() in ("py", "@py", "(unnamed)"):
        lines.append("  Resolved to        pyvisa-py (pure Python)")
        warnings.append(
            "VISA resolved to pyvisa-py, so no vendor library is installed. "
            "LAN and USB-serial instruments still work; a GPIB card does not "
            "unless gpib-ctypes can find the driver.")
    else:
        lines.append("  Resolved to        a vendor library (NI / Keysight)")
    for message in caught:
        lines.append(f"  note               {message.splitlines()[0][:66]}")

    lines.append("")
    lines.append("  ADDRESSES VISA ENUMERATES")
    if not resources:
        lines.append("    none")
        warnings.append(
            "VISA enumerated no addresses. Either nothing is connected, or "
            "the interface itself is missing -- the sections below say "
            "which.")
    else:
        # Grouped by bus, because "eleven addresses" and "eleven addresses,
        # all of them serial" are different situations.
        by_bus = {}
        for resource in resources:
            prefix = resource.split("::", 1)[0]
            family = "".join(ch for ch in prefix if not ch.isdigit()) or prefix
            by_bus.setdefault(family.upper(), []).append(resource)
        for family, purpose in VISA_BUS_NAMES:
            entries = by_bus.pop(family, [])
            if entries:
                lines.append(f"    {family}  -- {purpose}")
                for resource in sorted(entries):
                    lines.append(f"      {resource}")
        for family in sorted(by_bus):
            lines.append(f"    {family}")
            for resource in sorted(by_bus[family]):
                lines.append(f"      {resource}")
        lines.append("")
        lines.append(f"    {len(resources)} address(es). An address means an "
                     "interface answered,")
        lines.append("    not that an instrument is switched on at it.")
    lines.append("")
    return lines, problems, warnings


def probe_gpib():
    """GPIB cards, USB-GPIB adapters and the drivers behind them."""
    lines = ["GPIB INTERFACES", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    found = 0

    lines.append("  PCI / PCIe cards:")
    for hardware_id, instance, description, service in enum_devices("PCI"):
        vendor, code = vendor_of(hardware_id, PCI_VENDORS)
        text = f"{description} {hardware_id}".lower()
        if not vendor and "gpib" not in text:
            continue
        if vendor and "gpib" not in text and code != "1093":
            continue
        found += 1
        lines.append(f"    {description or hardware_id}")
        lines.append(f"      {hardware_id}")
        if vendor:
            lines.append(f"      vendor  {vendor}")
        if service:
            lines.append(f"      driver  {service}")
    if not found:
        lines.append("    none found")

    usb_found = 0
    lines.append("")
    lines.append("  USB-GPIB adapters:")
    for hardware_id, instance, description, service in enum_devices("USB"):
        vendor, code = vendor_of(hardware_id, USB_VENDORS)
        text = f"{description} {service}".lower()
        is_gpib = "gpib" in text or (code in ("3923", "0957")
                                     and "gpib" in text)
        if not is_gpib:
            continue
        usb_found += 1
        found += 1
        lines.append(f"    {description or hardware_id}")
        lines.append(f"      {hardware_id}")
        if vendor:
            lines.append(f"      vendor  {vendor}")
        if service:
            lines.append(f"      driver  {service}")
    if not usb_found:
        lines.append("    none found")

    lines.append("")
    lines.append("  Drivers registered for GPIB hardware:")
    services = 0
    for name, purpose in GPIB_SERVICES:
        path = r"SYSTEM\CurrentControlSet\Services" + "\\" + name
        if registry_subkeys(path) or registry_value(path, "ImagePath") \
                or registry_value(path, "DisplayName"):
            services += 1
            display = registry_value(path, "DisplayName") or purpose
            lines.append(f"    {name:<12}{display}")
    if not services:
        lines.append("    none registered")

    if not found and not services:
        warnings.append(
            "No GPIB card, no USB-GPIB adapter and no GPIB driver was found "
            "on this machine. Every GPIB instrument in PICA is unreachable "
            "from here; that is expected on an analysis laptop and a fault "
            "on the measurement PC.")
    elif not found and services:
        warnings.append(
            "A GPIB driver is installed but no GPIB hardware is present. "
            "Either the card is not seated / the adapter is unplugged, or "
            "this machine keeps the driver for a card it no longer has.")
    lines.append("")
    return lines, problems, warnings


def probe_serial():
    """Serial ports, from the map Windows keeps. Nothing is opened.

    Opening a COM port asserts DTR, and a device on the other end may reset
    when it sees that. A diagnostics program that reboots the pressure gauge
    it was asked about is not a diagnostics program, so this section reads
    the device map and stops.
    """
    lines = ["SERIAL PORTS", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    ports = registry_values(r"HARDWARE\DEVICEMAP\SERIALCOMM")
    if not ports:
        lines.append("  No serial ports. (The key Windows lists them under")
        lines.append("  does not exist, which is what an entirely "
                     "serial-less")
        lines.append("  machine looks like.)")
    for device, name in sorted(ports, key=lambda pair: str(pair[1])):
        lines.append(f"  {str(name):<8}{device}")

    # A USB-serial adapter is a serial port AND a USB device; naming the
    # maker is what tells an FTDI cable (the TPG 361's) from a CH340.
    lines.append("")
    lines.append("  USB-serial adapters present:")
    adapters = 0
    for hardware_id, instance, description, service in enum_devices("USB"):
        vendor, code = vendor_of(hardware_id, USB_VENDORS)
        if not is_usb_serial(description, service):
            continue
        adapters += 1
        lines.append(f"    {description or hardware_id}")
        lines.append(f"      {hardware_id}"
                     + (f"   [{vendor}]" if vendor else ""))
    if not adapters:
        lines.append("    none found")
    lines.append("")
    lines.append("  No port was opened: opening one asserts DTR and can "
                 "reset")
    lines.append("  the instrument on the other end.")
    lines.append("")
    return lines, problems, warnings


def probe_usb():
    """USB devices, and whether the controllers on this machine are 2 or 3.

    USB 2 versus USB 3 matters for exactly one reason in this lab: a
    GPIB-USB-HS is a USB 2 device, and plugging one into a hub behind a
    flaky USB 3 controller is a known way to get intermittent timeouts that
    look like instrument faults.
    """
    lines = ["USB", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    lines.append("  Host controllers:")
    controllers = 0
    for hardware_id, instance, description, service in enum_devices("PCI"):
        text = f"{description} {service}".lower()
        if "usb" not in text:
            continue
        controllers += 1
        if "xhci" in text or "usb 3" in text or "3.0" in text or "3.1" in text:
            generation = "USB 3 (xHCI)"
        elif "ehci" in text or "usb 2" in text or "2.0" in text:
            generation = "USB 2 (EHCI)"
        elif "ohci" in text or "uhci" in text:
            generation = "USB 1.1"
        else:
            generation = "generation not stated"
        lines.append(f"    {generation:<24}{description or hardware_id}")
    if not controllers:
        lines.append("    none found")

    lines.append("")
    lines.append("  Devices of interest (instrument makers and serial "
                 "bridges):")
    interesting = 0
    total = 0
    for hardware_id, instance, description, service in enum_devices("USB"):
        total += 1
        vendor, code = vendor_of(hardware_id, USB_VENDORS)
        if not vendor:
            continue
        interesting += 1
        lines.append(f"    {description or hardware_id}")
        lines.append(f"      {hardware_id}   [{vendor}]")
        if service:
            lines.append(f"      driver  {service}")
    if not interesting:
        lines.append("    none -- no instrument-maker USB device is "
                     "plugged in")
    lines.append("")
    lines.append(f"  {total} USB device record(s) in total, {interesting} of "
                 "them from a maker")
    lines.append("  PICA knows. Windows keeps a record for anything ever "
                 "plugged in,")
    lines.append("  so a listed device is not necessarily connected right "
                 "now.")
    lines.append("")
    return lines, problems, warnings


def probe_network():
    """The interfaces a LAN instrument would be reachable on."""
    lines = ["NETWORK", SEPARATOR]
    problems, warnings = [], []
    lines.append(f"  Host name          {socket.gethostname()}")

    addresses = []
    try:
        import psutil
        for name, entries in psutil.net_if_addrs().items():
            for entry in entries:
                if entry.family == socket.AF_INET:
                    addresses.append((name, entry.address, entry.netmask))
    except Exception:
        # Without psutil the host's own resolution is the best available
        # answer, and it is enough to say which subnet to look on.
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None,
                                           socket.AF_INET):
                addresses.append(("(from host name)", info[4][0], None))
        except OSError:
            pass

    if not addresses:
        lines.append("  No IPv4 address could be determined.")
    for name, address, netmask in sorted(set(addresses)):
        suffix = f"  mask {netmask}" if netmask else ""
        lines.append(f"  {name[:30]:<32}{address}{suffix}")

    lines.append("")
    lines.append("  A LAN instrument is reached as TCPIP0::<address>::"
                 "<port>::SOCKET")
    lines.append("  and has to be on one of the subnets above. Nothing was")
    lines.append("  pinged, scanned or connected to.")
    lines.append("")
    return lines, problems, warnings


PROBE_FUNCTIONS = {
    "visa": probe_visa,
    "gpib": probe_gpib,
    "serial": probe_serial,
    "usb": probe_usb,
    "network": probe_network,
}


def run_survey(selected, emit):
    """The whole survey, as emitted lines. No Tk in here."""
    problems, warnings = [], []

    emit("Communication Interface Diagnostics")
    emit(datetime.now().strftime("Run %d %b %Y  %H:%M:%S"))
    emit("")

    stages = [probe_summary]
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
        emit("  Nothing wrong in the sections that ran.")
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

class CommsInterfaceDiagnosticsGUI:
    """Pick what to look at, run, read, save."""

    PROGRAM_NAME = "Communication Interface Diagnostics"
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
        self._log_line("Read-only, and quieter than that: no instrument is")
        self._log_line("opened, no *IDN? is sent and no serial port is")
        self._log_line("opened. Safe during a measurement.")
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
        ttk.Label(header, text="Communication Interface Diagnostics",
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
            text=("What this computer is connected to: GPIB cards and "
                  "USB-GPIB adapters, serial and USB-serial ports, USB "
                  "controllers and their generation, network interfaces, "
                  "and the VISA addresses they add up to. No instrument is "
                  "opened and no *IDN? is sent -- that is the Instrument "
                  "Status window's job."),
            wraplength=960, justify='left').pack(anchor='w')

        deep = ttk.LabelFrame(
            self.root, text="Sections (the machine summary always runs)",
            padding=(10, 6))
        deep.pack(fill='x', padx=12, pady=(6, 4))
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

        Walking the device tree takes seconds on a machine with a lot of USB
        history, and VISA enumeration blocks on the driver. Neither belongs
        on the Tk thread.
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
                "comms_diagnostics_%Y%m%d_%H%M%S.txt"),
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
    CommsInterfaceDiagnosticsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
