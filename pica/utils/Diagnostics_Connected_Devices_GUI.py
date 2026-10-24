#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PROGRAM:      Connected Devices and Drivers
 VERSION:      1.0
 DATE:         24 Oct 2026

 PURPOSE:
   Answers "I plugged it in and nothing can see it -- why?" for any USB or
   LAN peripheral: a vacuum gauge on an FTDI cable, a USB-GPIB adapter, a
   USBTMC instrument, a LAN controller.

   The Communication Interface Diagnostics program lists the INTERFACES
   this machine has. This one goes one step further down and asks Windows
   about each DEVICE: is it connected right now, does it have a driver,
   is that driver running, and if not, what error code did Windows give
   it. That is the difference between the three ways "nothing shows up"
   happens:

     1. Windows never saw a device at all  -> cable, port or power.
     2. Windows saw it but has no driver   -> install the driver.
     3. The driver runs but made no port   -> a driver setting.

   It also lists every COM port with the device that owns it, the
   third-party driver packages installed on this machine (FTDI, NI,
   Keysight, Prolific, ...), and the LAN neighbours Windows already knows
   about. Pressing Run Check a second time after plugging something in
   prints exactly which devices appeared and disappeared in between.

 READ-ONLY, AND QUIETER THAN THAT:
   Nothing is opened, sent, installed, enabled, disabled or rescanned.
     - Device state comes from the Plug and Play manager's query calls
       (cfgmgr32: list, locate, get-status). None of them touches the
       hardware; the calls that DO (rescan, enable, disable, remove) are
       never imported, and a test fails if one appears in this file.
     - Driver details come from registry keys opened for reading, and from
       reading the .inf text files Windows keeps in its INF folder.
     - LAN neighbours come from the ARP cache Windows already holds. No
       ping, no scan, no connection: not one packet leaves this machine.
     - No serial port is opened (opening one asserts DTR and can reset the
       device on the other end) and no VISA address is opened.
   It is safe to run beside a live measurement and beside the Novocontrol
   Alpha.

 DEPENDENCIES:
   Standard library and Tkinter only. ctypes is used to ask Windows'
   own query calls (cfgmgr32, iphlpapi) what the registry alone cannot
   say: whether a device is connected NOW, and its error code. psutil is
   used for network adapter names when it is installed.
===============================================================================
"""

import io
import os
import platform
import queue
import socket
import struct
import threading
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import filedialog, messagebox, scrolledtext, ttk

IS_WINDOWS = os.name == 'nt'

if IS_WINDOWS:
    try:
        import winreg
    except ImportError:                                        # pragma: no cover
        winreg = None
    import ctypes
    from ctypes import wintypes
else:                                                          # pragma: no cover
    winreg = None
    ctypes = None
    wintypes = None


# ===============================================================================
# WHAT WINDOWS' NUMBERS MEAN
# ===============================================================================
# Device Manager's problem codes, in the words a person needs: what it means
# and what to do. Only the ones a plugged-in device can actually show.
PROBLEM_CODES = {
    1: "not configured -- reinstall the driver",
    3: "driver may be corrupted, or the PC is out of memory",
    10: "device cannot start -- often a driver/firmware mismatch; "
        "unplug, replug, then reinstall the driver",
    12: "not enough free resources for this device",
    14: "needs a Windows restart to work",
    18: "drivers must be reinstalled",
    19: "its registry entry is damaged -- reinstall the driver",
    21: "Windows is removing this device",
    22: "device is DISABLED in Device Manager",
    24: "not present, not working, or missing some drivers",
    28: "NO DRIVER INSTALLED for this device",
    29: "disabled by the firmware/BIOS",
    31: "driver failed to load for this device",
    32: "its driver service is disabled",
    37: "the driver reported a failure while starting",
    39: "driver is corrupted or missing",
    41: "driver loaded but cannot find the hardware",
    43: "the device reported a fault (for USB: often the cable, the port "
        "or the device's own power -- try another cable and port)",
    47: "prepared for safe removal -- unplug and replug it",
    48: "driver blocked from starting (known compatibility problem)",
    52: "driver signature could not be verified",
}

# USB vendor IDs worth naming. Anything else is still listed, just without
# a friendly maker.
USB_VENDORS = {
    "3923": "National Instruments (GPIB-USB-HS, USB-232, DAQ)",
    "0957": "Keysight / Agilent (82357 GPIB-USB, USB instruments)",
    "2A8D": "Keysight (newer USB instruments)",
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

# Words that mark a driver package as one a lab PC cares about.
INTERESTING_PROVIDERS = ("ftdi", "future technology", "national instruments",
                         "keysight", "agilent", "prolific", "silicon lab",
                         "wch", "qinheng", "pfeiffer", "tektronix",
                         "keithley", "rigol", "yokogawa", "ines", "prologix")

# MAC address prefixes (IEEE OUIs) of makers whose instruments sit on a lab
# LAN. Pfeiffer's is the one confirmed on this lab's gauge.
INSTRUMENT_OUIS = {
    "00A041": "Pfeiffer Vacuum (e.g. TPG 361 gauge, TCP port 8000)",
    "00802F": "National Instruments",
    "0030D3": "Agilent / Keysight",
}

# Driver services that make a COM port. Matching the SERVICE is what keeps
# "USB Composite Device" (which contains the letters "com") out.
SERIAL_SERVICES = {"usbser", "ftdibus", "ftdiport", "silabser", "silabenm",
                   "ch341ser", "ch343ser", "ser2pl", "usbcdcacm", "wceusbsh",
                   "serial"}

DEEP_SECTIONS = [
    ("attention", "Devices Windows reports a problem with (any kind)",
     "~1 s", True),
    ("usb", "USB devices connected now, with their drivers", "~1 s", True),
    ("ports", "Serial (COM) ports and the device that owns each",
     "instant", True),
    ("history", "Instrument USB devices seen before but not connected now",
     "~1 s", True),
    ("drivers", "Third-party driver packages installed (FTDI, NI, ...)",
     "~2 s", True),
    ("network", "Network adapters and LAN neighbours Windows already knows",
     "instant", True),
]

SEPARATOR = "-" * 74
ENUM_ROOT = r"SYSTEM\CurrentControlSet\Enum"
CLASS_ROOT = r"SYSTEM\CurrentControlSet\Control\Class"
SERVICES_ROOT = r"SYSTEM\CurrentControlSet\Services"

# cfgmgr32 constants (cfgmgr32.h).
CR_SUCCESS = 0x00
CR_BUFFER_SMALL = 0x1A
CM_GETIDLIST_FILTER_PRESENT = 0x100
DN_DRIVER_LOADED = 0x00000002
DN_STARTED = 0x00000008
DN_HAS_PROBLEM = 0x00000400

# iphlpapi return codes.
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_DATA = 232
ARP_TYPES = {1: "other", 2: "invalid", 3: "dynamic", 4: "static"}


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


def registry_changed(path, hive=None):
    """When Windows last wrote to a key, as a local datetime, or None.

    For a device record this is roughly the last time it was plugged in or
    had its driver changed -- a hint, not a timestamp to quote.
    """
    if not IS_WINDOWS or winreg is None:
        return None
    hive = hive if hive is not None else winreg.HKEY_LOCAL_MACHINE
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            stamp = winreg.QueryInfoKey(key)[2]
    except OSError:
        return None
    return filetime_to_local(stamp)


def filetime_to_local(stamp):
    """A Windows FILETIME (100 ns ticks since 1601, UTC) as local time."""
    if not stamp:
        return None
    try:
        moment = datetime(1601, 1, 1, tzinfo=timezone.utc) \
            + timedelta(microseconds=int(stamp) // 10)
        return moment.astimezone().replace(tzinfo=None)
    except (OverflowError, ValueError, OSError):
        return None


def readable_name(value):
    """The human half of a Windows device name, or '' when there isn't one.

    Windows stores these as plain text or as '@file.inf,%id%;Text'. The
    text after the FIRST semicolon is the name; a leftover %1 placeholder
    means only Windows can fill it in, so it is declined.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith('@'):
        if ';' not in text:
            return ""
        text = text.split(';', 1)[1].strip()
        if '%' in text:
            return ""
    return text


def vid_pid(instance_id):
    """('0403', '6001') from a USB or FTDIBUS instance id, else (None, None).

    USB writes VID_0403&PID_6001; the FTDI bus writes VID_0403+PID_6001.
    """
    upper = instance_id.upper()
    vid = pid = None
    if "VID_" in upper:
        vid = upper.split("VID_", 1)[1][:4]
    if "PID_" in upper:
        pid = upper.split("PID_", 1)[1][:4]
    return vid, pid


def problem_text(code):
    """Plain words for a Device Manager problem code."""
    return PROBLEM_CODES.get(code, "Windows reports a problem with it")


def state_text(status, problem):
    """One phrase: is it working, and if not, why not."""
    if status is None:
        return "not connected now"
    if status & DN_HAS_PROBLEM:
        return f"PROBLEM (code {problem}): {problem_text(problem)}"
    if status & DN_STARTED:
        return "working (driver running)"
    if status & DN_DRIVER_LOADED:
        return "driver loaded but not started"
    return "connected, no driver running"


def resolve_driver_file(image_path):
    """A service's ImagePath as a real file path, or ''."""
    text = str(image_path or "").strip().strip('"')
    if not text:
        return ""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    lowered = text.lower()
    if lowered.startswith("\\systemroot\\"):
        text = os.path.join(system_root, text[len("\\systemroot\\"):])
    elif lowered.startswith("system32\\"):
        text = os.path.join(system_root, text)
    elif text.startswith("\\??\\"):
        text = text[4:]
    return text


def is_serial_owner(record):
    """True when the device record is one that makes a COM port."""
    return (str(record.get("service", "")).lower() in SERIAL_SERVICES
            or str(record.get("class", "")).lower() == "ports"
            or bool(record.get("port")))


def mac_oui(mac):
    """'00A041' from '00-a0-41-12-34-56' (or with colons), else ''."""
    digits = "".join(ch for ch in str(mac).upper() if ch in "0123456789ABCDEF")
    return digits[:6] if len(digits) >= 6 else ""


# ===============================================================================
# ASKING WINDOWS (query calls only)
# ===============================================================================

def _cfgmgr():
    """cfgmgr32 with argument types set, or None off Windows.

    Only the three query calls are declared. The ones that act on hardware
    (rescan, enable, disable, remove, setup) are deliberately absent.
    """
    if not IS_WINDOWS or ctypes is None:
        return None
    try:
        lib = ctypes.WinDLL("cfgmgr32")
    except OSError:
        return None
    ulong = wintypes.ULONG
    lib.CM_Get_Device_ID_List_SizeW.argtypes = [
        ctypes.POINTER(ulong), wintypes.LPCWSTR, ulong]
    lib.CM_Get_Device_ID_List_SizeW.restype = wintypes.DWORD
    lib.CM_Get_Device_ID_ListW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_wchar), ulong, ulong]
    lib.CM_Get_Device_ID_ListW.restype = wintypes.DWORD
    lib.CM_Locate_DevNodeW.argtypes = [
        ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, ulong]
    lib.CM_Locate_DevNodeW.restype = wintypes.DWORD
    lib.CM_Get_DevNode_Status.argtypes = [
        ctypes.POINTER(ulong), ctypes.POINTER(ulong), wintypes.DWORD, ulong]
    lib.CM_Get_DevNode_Status.restype = wintypes.DWORD
    return lib


def present_device_ids(lib):
    """Every device instance Windows says is connected now, or None."""
    if lib is None:
        return None
    size = wintypes.ULONG(0)
    # The list can grow between the two calls (a device arriving); asking
    # again is the documented answer to CR_BUFFER_SMALL.
    for _attempt in range(4):
        if lib.CM_Get_Device_ID_List_SizeW(
                ctypes.byref(size), None,
                CM_GETIDLIST_FILTER_PRESENT) != CR_SUCCESS:
            return None
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        result = lib.CM_Get_Device_ID_ListW(None, buffer, size.value,
                                            CM_GETIDLIST_FILTER_PRESENT)
        if result == CR_SUCCESS:
            return [item for item in buffer[:size.value].split("\0") if item]
        if result != CR_BUFFER_SMALL:
            return None
    return None


def device_status(lib, instance_id):
    """(status flags, problem code) for a connected device, else None."""
    if lib is None:
        return None
    devinst = wintypes.DWORD(0)
    # Flag 0 = CM_LOCATE_DEVNODE_NORMAL: find it only if it is present.
    if lib.CM_Locate_DevNodeW(ctypes.byref(devinst), instance_id,
                              0) != CR_SUCCESS:
        return None
    status = wintypes.ULONG(0)
    problem = wintypes.ULONG(0)
    if lib.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem),
                                 devinst.value, 0) != CR_SUCCESS:
        return None
    return status.value, problem.value


def device_record(instance_id):
    """Everything the registry says about one device instance."""
    path = ENUM_ROOT + "\\" + instance_id
    name = (readable_name(registry_value(path, "FriendlyName"))
            or readable_name(registry_value(path, "DeviceDesc")))
    record = {
        "id": instance_id,
        "name": name,
        "service": registry_value(path, "Service") or "",
        "class": registry_value(path, "Class") or "",
        "maker": readable_name(registry_value(path, "Mfg")),
        "location": registry_value(path, "LocationInformation") or "",
        "port": registry_value(path + r"\Device Parameters",
                               "PortName") or "",
        "changed": registry_changed(path),
        "status": None,
        "problem": 0,
    }
    driver_key = registry_value(path, "Driver")
    if driver_key:
        class_path = CLASS_ROOT + "\\" + str(driver_key)
        record["driver_provider"] = registry_value(class_path,
                                                   "ProviderName") or ""
        record["driver_version"] = registry_value(class_path,
                                                  "DriverVersion") or ""
        record["driver_date"] = registry_value(class_path, "DriverDate") or ""
        record["driver_inf"] = registry_value(class_path, "InfPath") or ""
    if record["service"]:
        image = registry_value(SERVICES_ROOT + "\\" + record["service"],
                               "ImagePath")
        record["driver_file"] = resolve_driver_file(image)
    return record


def gather_devices():
    """{instance id: record} for every connected device, or None.

    Built once per run and shared by the sections, so the machine is asked
    once and every section describes the same moment.
    """
    lib = _cfgmgr()
    ids = present_device_ids(lib)
    if ids is None:
        return None
    devices = {}
    for instance_id in ids:
        record = device_record(instance_id)
        state = device_status(lib, instance_id)
        if state is not None:
            record["status"], record["problem"] = state
        else:
            # Listed as present a moment ago and gone now: unplugged
            # during the run. Kept, marked as such.
            record["status"] = None
        devices[instance_id] = record
    return devices


def arp_cache():
    """[(interface, ip, mac, kind)] from Windows' ARP cache, or None.

    GetIpNetTable copies the table Windows already holds. It does not send
    an ARP request or anything else.
    """
    if not IS_WINDOWS or ctypes is None:
        return None
    try:
        lib = ctypes.WinDLL("iphlpapi")
    except OSError:
        return None
    lib.GetIpNetTable.argtypes = [ctypes.c_void_p,
                                  ctypes.POINTER(wintypes.ULONG),
                                  wintypes.BOOL]
    lib.GetIpNetTable.restype = wintypes.DWORD
    size = wintypes.ULONG(0)
    result = lib.GetIpNetTable(None, ctypes.byref(size), True)
    if result == ERROR_NO_DATA:
        return []
    if result != ERROR_INSUFFICIENT_BUFFER:
        return None
    for _attempt in range(4):
        buffer = ctypes.create_string_buffer(size.value)
        result = lib.GetIpNetTable(buffer, ctypes.byref(size), True)
        if result == 0:
            return parse_arp_table(buffer.raw)
        if result != ERROR_INSUFFICIENT_BUFFER:
            return None
    return None


def parse_arp_table(raw):
    """Rows of a MIB_IPNETTABLE: a DWORD count, then 24-byte rows."""
    if len(raw) < 4:
        return []
    count = struct.unpack_from("<I", raw, 0)[0]
    rows = []
    for index in range(count):
        offset = 4 + index * 24
        if offset + 24 > len(raw):
            break
        interface, length, phys, address, kind = struct.unpack_from(
            "<II8sII", raw, offset)
        mac = "-".join(f"{byte:02X}" for byte in phys[:min(length, 8)])
        ip = socket.inet_ntoa(struct.pack("<I", address))
        rows.append((interface, ip, mac, ARP_TYPES.get(kind, str(kind))))
    return rows


def is_neighbour(ip, mac, kind):
    """False for broadcast, multicast and empty entries -- not devices."""
    if kind == "invalid" or not mac:
        return False
    if mac.replace("-", "") in ("", "000000000000", "FFFFFFFFFFFF"):
        return False
    try:
        first = int(ip.split(".", 1)[0])
    except ValueError:
        return False
    return not (224 <= first <= 239 or ip == "255.255.255.255"
                or ip.endswith(".255"))


def strip_inf_comment(line):
    """An .inf line without its ';' comment, minding quoted text."""
    quoted = False
    for position, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ';' and not quoted:
            return line[:position]
    return line


def parse_inf(text):
    """{'class', 'provider', 'version', 'catalog'} from an .inf's text.

    Providers are usually written as %TOKEN% and defined in [Strings] at
    the bottom of the file; this resolves them so "FTDI" reads as FTDI.
    """
    section = ""
    version = {}
    strings = {}
    for raw in text.splitlines():
        line = strip_inf_comment(raw).strip()
        if not line:
            continue
        if line.startswith('[') and ']' in line:
            section = line[1:line.index(']')].strip().lower()
            continue
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().lower()
        value = value.strip().strip('"').strip()
        if section == "version":
            version[key] = value
        elif section == "strings" or (section.startswith("strings.")
                                      and key not in strings):
            strings.setdefault(key, value)

    def resolve(value):
        """Swap every %token% for its [Strings] text; unknown ones stay."""
        parts = str(value or "").split('%')
        for index in range(1, len(parts) - 1, 2):
            token = parts[index]
            parts[index] = strings.get(token.lower(), f"%{token}%")
        return "".join(parts)

    driver_ver = resolve(version.get("driverver", ""))
    date, _, number = driver_ver.partition(",")
    return {
        "class": resolve(version.get("class", "")),
        "provider": resolve(version.get("provider", "")),
        "date": date.strip(),
        "version": number.strip(),
        "catalog": version.get("catalogfile", ""),
    }


def read_text_file(path, limit=4 * 1024 * 1024):
    """A text file's contents (UTF-16 or ANSI), or '' when unreadable."""
    try:
        with open(path, 'rb') as handle:
            data = handle.read(limit)
    except OSError:
        return ""
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', errors='replace')
    if data[:3] == b'\xef\xbb\xbf':
        return data[3:].decode('utf-8', errors='replace')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def third_party_packages():
    """[(file, parsed)] for every oem*.inf Windows has installed."""
    folder = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "INF")
    try:
        names = sorted(name for name in os.listdir(folder)
                       if name.lower().startswith("oem")
                       and name.lower().endswith(".inf"))
    except OSError:
        return []
    packages = []
    for name in names:
        text = read_text_file(os.path.join(folder, name))
        if text:
            packages.append((name, parse_inf(text)))
    return packages


def is_interesting_package(parsed):
    text = f"{parsed.get('provider', '')} {parsed.get('catalog', '')}".lower()
    return any(word in text for word in INTERESTING_PROVIDERS)


# ===============================================================================
# THE SECTIONS
# ===============================================================================

def describe_device(record, lines, indent="    "):
    """The block of lines one device gets, shared by every section."""
    vid, pid = vid_pid(record["id"])
    lines.append(f"{indent}{record['name'] or '(no name)'}")
    lines.append(f"{indent}  state     {state_text(record['status'], record['problem'])}")
    if vid:
        maker = USB_VENDORS.get(vid, "(maker not in PICA's list)")
        lines.append(f"{indent}  ids       VID {vid}  PID {pid or '?'}"
                     f"   {maker}")
    elif record.get("maker"):
        lines.append(f"{indent}  maker     {record['maker']}")
    lines.append(f"{indent}  instance  {record['id']}")
    if record.get("port"):
        number = "".join(ch for ch in record["port"] if ch.isdigit())
        visa = f"   ->  VISA ASRL{number}::INSTR" if number else ""
        lines.append(f"{indent}  port      {record['port']}{visa}")
    if record.get("service") or record.get("driver_provider"):
        parts = [record.get("service") or "(no service)"]
        for key in ("driver_provider", "driver_version", "driver_date",
                    "driver_inf"):
            if record.get(key):
                parts.append(str(record[key]))
        lines.append(f"{indent}  driver    {'  '.join(parts)}")
    else:
        lines.append(f"{indent}  driver    none assigned")
    if record.get("driver_file"):
        exists = "present" if os.path.isfile(record["driver_file"]) \
            else "FILE MISSING"
        lines.append(f"{indent}  file      {record['driver_file']} "
                     f"({exists})")
    if record.get("location"):
        lines.append(f"{indent}  where     {record['location']}")


def probe_summary(ctx):
    """Who is asking, and the promise of what will not be done."""
    lines = ["THIS MACHINE", SEPARATOR]
    bits = struct.calcsize("P") * 8
    lines.append(f"  Host name          {socket.gethostname()}")
    lines.append(f"  System             {platform.system()} "
                 f"{platform.release()} ({platform.architecture()[0]})")
    lines.append(f"  Python             {platform.python_version()} "
                 f"({bits}-bit)")
    devices = ctx.get("devices")
    if devices is None:
        lines.append("  Devices            Windows only -- the device "
                     "sections below are skipped.")
    else:
        lines.append(f"  Devices connected  {len(devices)} (every kind, "
                     "including built-in ones)")
    lines.append("")
    lines.append("  Nothing below opens, sends, installs, enables, disables")
    lines.append("  or rescans anything. Windows is only asked what it "
                 "already knows.")
    lines.append("")
    return lines, [], []


def probe_changes(ctx):
    """What appeared or vanished since the previous Run Check."""
    lines = ["CHANGES SINCE THE PREVIOUS RUN", SEPARATOR]
    problems, warnings = [], []
    devices = ctx.get("devices")
    previous = ctx.get("previous")
    if devices is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings
    if previous is None:
        lines.append("  This is the first run in this window. To find a "
                     "device that")
        lines.append("  \"does not show up\": unplug it, press Run Check, "
                     "plug it in,")
        lines.append("  wait ten seconds and press Run Check again. This "
                     "section will")
        lines.append("  then list exactly what Windows saw arrive.")
        lines.append("")
        return lines, problems, warnings

    appeared, vanished = snapshot_diff(previous, snapshot_of(devices))
    if not appeared and not vanished:
        lines.append("  Nothing appeared or disappeared since the previous "
                     "run.")
        lines.append("  If you plugged something in between the runs, "
                     "Windows did not")
        lines.append("  see it at all: suspect the cable (some USB cables "
                     "carry power")
        lines.append("  only), the USB port, or the device's USB port being "
                     "off or")
        lines.append("  in another mode. A missing driver would still show "
                     "up here.")
        warnings.append("Nothing appeared since the previous run. If a "
                        "device was plugged in between the two runs, Windows "
                        "never detected it: a cable, port or device-side "
                        "problem, not a driver.")
    if appeared:
        lines.append(f"  Appeared ({len(appeared)}):")
        for instance_id in appeared:
            describe_device(devices[instance_id], lines)
        lines.append("")
    if vanished:
        lines.append(f"  Disappeared ({len(vanished)}):")
        for instance_id in vanished:
            lines.append(f"    {previous[instance_id] or '(no name)'}")
            lines.append(f"      instance  {instance_id}")
    lines.append("")
    return lines, problems, warnings


def probe_attention(ctx):
    """Every connected device with a Device Manager problem code."""
    lines = ["DEVICES WITH A PROBLEM", SEPARATOR]
    problems, warnings = [], []
    devices = ctx.get("devices")
    if devices is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings
    flagged = [record for record in devices.values()
               if record["status"] is not None
               and record["status"] & DN_HAS_PROBLEM]
    if not flagged:
        lines.append("  None. Every connected device has a working driver.")
    for record in sorted(flagged, key=lambda item: item["id"]):
        describe_device(record, lines)
        lines.append("")
        name = record["name"] or record["id"]
        message = (f"{name}: code {record['problem']}, "
                   f"{problem_text(record['problem'])}.")
        if record["problem"] == 22:
            warnings.append(message)
        else:
            problems.append(message)
    lines.append("")
    return lines, problems, warnings


def probe_usb(ctx):
    """USB devices connected right now, each with its driver."""
    lines = ["USB DEVICES CONNECTED NOW", SEPARATOR]
    problems, warnings = [], []
    devices = ctx.get("devices")
    if devices is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    usb = [record for record in devices.values()
           if record["id"].upper().startswith(("USB\\", "FTDIBUS\\"))]
    hubs = [record for record in usb
            if "hub" in record["name"].lower()
            or record["service"].lower() in ("usbhub", "usbhub3")]
    shown = [record for record in usb if record not in hubs]
    for record in sorted(shown, key=lambda item: item["id"]):
        describe_device(record, lines)
        lines.append("")
    lines.append(f"  {len(shown)} USB device(s) listed, plus {len(hubs)} "
                 "hub(s) and root hub(s) not shown.")
    lines.append("")

    # FTDI cables (the TPG 361's) fail in a particular way: the bus driver
    # runs, but the virtual COM port layer is switched off, so no COMn ever
    # appears and every VISA tool looks straight past it.
    ftdi = [record for record in usb
            if record["id"].upper().startswith("USB\\")
            and vid_pid(record["id"])[0] == "0403"]
    ftdi_ports = [record for record in devices.values()
                  if record["id"].upper().startswith("FTDIBUS\\")
                  and record.get("port")]
    for record in ftdi:
        if record["status"] is not None and record["status"] & DN_STARTED \
                and record["service"].lower() == "ftdibus" and not ftdi_ports:
            warnings.append(
                f"{record['name'] or 'An FTDI device'} is running on the "
                "FTDI bus driver but no COM port was created. In Device "
                "Manager, open it, Advanced tab, tick 'Load VCP', then "
                "unplug and replug it.")
    return lines, problems, warnings


def probe_ports(ctx):
    """Every COM port: the live map, and the device behind each name."""
    lines = ["SERIAL (COM) PORTS", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings

    live = registry_values(r"HARDWARE\DEVICEMAP\SERIALCOMM")
    devices = ctx.get("devices") or {}
    owners = {}
    for record in devices.values():
        if record.get("port"):
            owners.setdefault(str(record["port"]).upper(), record)

    if not live:
        lines.append("  No COM port exists on this machine right now.")
    for _kernel_name, port in sorted(live, key=lambda pair: str(pair[1])):
        port = str(port)
        number = "".join(ch for ch in port if ch.isdigit())
        owner = owners.get(port.upper())
        lines.append(f"  {port:<7} VISA ASRL{number}::INSTR")
        if owner:
            describe_device(owner, lines, indent="      ")
        lines.append("")

    lines.append("  No port was opened: opening one asserts DTR and can "
                 "reset the")
    lines.append("  instrument on the other end.")
    lines.append("")
    lines.append("  Worth knowing: the PICA launcher's bus scan never probes "
                 "serial")
    lines.append("  ports, so a gauge on COMn will not appear there even when "
                 "it")
    lines.append("  works. Type the port (e.g. COM5) into the instrument's "
                 "own module.")
    lines.append("  VISA tools (Keysight Connection Expert, NI MAX) only see "
                 "it once")
    lines.append("  the COM port exists.")
    lines.append("")
    return lines, problems, warnings


def probe_history(ctx):
    """Instrument-maker USB devices Windows has a record of, not plugged in.

    A record here proves the device HAS been seen on this PC before, and
    with which driver -- useful when it worked once and does not now.
    """
    lines = ["INSTRUMENT USB DEVICES SEEN BEFORE, NOT CONNECTED NOW", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS or winreg is None:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings
    devices = ctx.get("devices") or {}
    present = {instance_id.upper() for instance_id in devices}
    absent = []
    for bus in ("USB", "FTDIBUS"):
        root = ENUM_ROOT + "\\" + bus
        for hardware_id in registry_subkeys(root):
            vid, _pid = vid_pid(hardware_id)
            if vid not in USB_VENDORS:
                continue
            for instance in registry_subkeys(root + "\\" + hardware_id):
                instance_id = f"{bus}\\{hardware_id}\\{instance}"
                if instance_id.upper() in present:
                    continue
                absent.append(device_record(instance_id))
    if not absent:
        lines.append("  None.")
    epoch = datetime(1900, 1, 1)
    for record in sorted(absent, key=lambda item: item["changed"] or epoch,
                         reverse=True):
        describe_device(record, lines)
        if record["changed"]:
            lines.append(f"      last record change  "
                         f"{record['changed']:%d %b %Y %H:%M}")
        lines.append("")
    lines.append("")
    return lines, problems, warnings


def probe_drivers(ctx):
    """Third-party driver packages Windows has installed."""
    lines = ["THIRD-PARTY DRIVER PACKAGES", SEPARATOR]
    problems, warnings = [], []
    if not IS_WINDOWS:
        lines.append("  Windows only.")
        lines.append("")
        return lines, problems, warnings
    packages = third_party_packages()
    ctx["packages"] = packages
    if not packages:
        lines.append("  None found (or the INF folder could not be read).")
    interesting = [(name, parsed) for name, parsed in packages
                   if is_interesting_package(parsed)]
    lines.append("  Lab-relevant (instrument makers, USB-serial chips):")
    if not interesting:
        lines.append("    none")
    for name, parsed in interesting:
        lines.append(f"    {name:<13}{parsed['provider'][:28]:<30}"
                     f"{parsed['class'][:20]:<22}{parsed['version']}  "
                     f"{parsed['date']}")
    lines.append("")
    lines.append("  Everything else:")
    for name, parsed in packages:
        if (name, parsed) in interesting:
            continue
        lines.append(f"    {name:<13}{parsed['provider'][:28]:<30}"
                     f"{parsed['class'][:20]:<22}{parsed['version']}  "
                     f"{parsed['date']}")
    lines.append("")

    devices = ctx.get("devices") or {}
    has_ftdi_package = any(
        "ftdi" in f"{parsed['provider']} {parsed['catalog']}".lower()
        or "future technology" in parsed["provider"].lower()
        for _name, parsed in packages)
    for record in devices.values():
        if vid_pid(record["id"])[0] == "0403" and record["status"] is not None \
                and record["status"] & DN_HAS_PROBLEM \
                and record["problem"] == 28 and not has_ftdi_package:
            problems.append(
                "An FTDI device is connected but no FTDI driver package is "
                "installed. Install the FTDI VCP driver (setup executable "
                "from ftdichip.com, 'VCP Drivers'), then replug the cable.")
            break
    return lines, problems, warnings


def probe_network(ctx):
    """Adapters, and the LAN neighbours already in the ARP cache."""
    lines = ["NETWORK", SEPARATOR]
    problems, warnings = [], []
    adapters = []
    try:
        import psutil
        stats = psutil.net_if_stats()
        for name, entries in psutil.net_if_addrs().items():
            up = getattr(stats.get(name), "isup", None)
            mac = ""
            ipv4 = []
            for entry in entries:
                if entry.family == socket.AF_INET:
                    ipv4.append((entry.address, entry.netmask))
                elif entry.family == getattr(psutil, "AF_LINK", None):
                    mac = entry.address
            adapters.append((name, up, mac, ipv4))
    except Exception:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None,
                                           socket.AF_INET):
                adapters.append(("(from host name)", None, "",
                                 [(info[4][0], None)]))
        except OSError:
            pass

    lines.append("  Adapters:")
    if not adapters:
        lines.append("    none could be read")
    for name, up, mac, ipv4 in sorted(adapters, key=lambda item: item[0]):
        state = {True: "up", False: "DOWN (cable unplugged?)",
                 None: ""}[up]
        lines.append(f"    {name[:40]:<42}{state}")
        if mac:
            lines.append(f"      MAC   {mac}")
        for address, netmask in ipv4:
            mask = f"  mask {netmask}" if netmask else ""
            lines.append(f"      IPv4  {address}{mask}")
            if address.startswith("169.254.") and up:
                lines.append("            ^ self-assigned: no DHCP server "
                             "answered on this adapter")
                warnings.append(
                    f"'{name}' has a self-assigned 169.254 address. For an "
                    "instrument on a direct cable, give the PC and the "
                    "instrument fixed addresses on the same subnet "
                    "(e.g. 192.168.1.10 and 192.168.1.50, mask 255.255.255.0).")
    lines.append("")

    rows = arp_cache()
    lines.append("  LAN neighbours Windows already knows (ARP cache):")
    if rows is None:
        lines.append("    could not be read on this machine")
    else:
        neighbours = [row for row in rows if is_neighbour(row[1], row[2],
                                                          row[3])]
        if not neighbours:
            lines.append("    none")
        for interface, ip, mac, kind in neighbours:
            maker = INSTRUMENT_OUIS.get(mac_oui(mac), "")
            flag = f"   <- {maker}" if maker else ""
            lines.append(f"    {ip:<17}{mac:<19}{kind:<9}if {interface}{flag}")
            if maker:
                warnings.append(f"A {maker.split(' (')[0]} device is on the "
                                f"LAN at {ip} (MAC {mac}).")
    lines.append("")
    lines.append("  Nothing was pinged, scanned or connected to. A device "
                 "only shows")
    lines.append("  here once this PC has talked to it; an instrument that "
                 "is on the")
    lines.append("  LAN but never contacted will not be listed.")
    lines.append("")
    return lines, problems, warnings


PROBE_FUNCTIONS = {
    "attention": probe_attention,
    "usb": probe_usb,
    "ports": probe_ports,
    "history": probe_history,
    "drivers": probe_drivers,
    "network": probe_network,
}


def snapshot_of(devices):
    """{instance id: name} -- what the next run compares itself against."""
    if devices is None:
        return None
    return {instance_id: record["name"]
            for instance_id, record in devices.items()}


def snapshot_diff(previous, current):
    """(appeared, vanished) instance ids, each sorted."""
    previous = previous or {}
    current = current or {}
    appeared = sorted(set(current) - set(previous))
    vanished = sorted(set(previous) - set(current))
    return appeared, vanished


def run_survey(selected, emit, previous=None):
    """The whole survey, as emitted lines. No Tk in here.

    Returns (problems, warnings, snapshot); the snapshot is handed back in
    as `previous` on the next run so it can say what changed.
    """
    problems, warnings = [], []

    emit("Connected Devices and Drivers")
    emit(datetime.now().strftime("Run %d %b %Y  %H:%M:%S"))
    emit("")

    devices = gather_devices()
    ctx = {"devices": devices, "previous": previous}

    stages = [probe_summary, probe_changes]
    for key, _label, _cost, _default in DEEP_SECTIONS:
        if key in selected:
            stages.append(PROBE_FUNCTIONS[key])

    for stage in stages:
        lines, stage_problems, stage_warnings = stage(ctx)
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
    return problems, warnings, snapshot_of(devices)


# ===============================================================================
# THE WINDOW
# ===============================================================================

class ConnectedDevicesDiagnosticsGUI:
    """Pick what to look at, run, read, save -- and run again to compare."""

    PROGRAM_NAME = "Connected Devices and Drivers"
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
    FONT_HEADLINE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION} "
                        "(read-only)")
        self.root.geometry("1060x760")
        self.root.minsize(880, 560)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.queue = queue.Queue()
        self.worker = None
        self.after_id = None
        self.lines = []
        self.deep_vars = {}
        self.previous = None

        self.setup_styles()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self._log_line(f"{self.PROGRAM_NAME} v{self.PROGRAM_VERSION}")
        self._log_line("")
        self._log_line("Read-only: nothing is opened, sent, installed, "
                       "enabled, disabled")
        self._log_line("or rescanned. Safe during a measurement.")
        self._log_line("")
        self._log_line("Tip: unplug the device, press Run Check, plug it in, "
                       "wait ten")
        self._log_line("seconds, press Run Check again -- the log then says "
                       "what arrived.")

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
        ttk.Label(header, text=self.PROGRAM_NAME,
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
            text=("For a device that \"does not show up\": is Windows seeing "
                  "it at all, does it have a driver, is the driver running, "
                  "and did it get a COM port? Also lists installed "
                  "third-party drivers and the LAN neighbours this PC "
                  "already knows. Run it twice -- before and after plugging "
                  "in -- to see exactly what arrived."),
            wraplength=1000, justify='left').pack(anchor='w')

        deep = ttk.LabelFrame(
            self.root, text="Sections (machine summary and changes always "
                            "run)",
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

        # The answer in one sentence, above the detail.
        self.headline_var = tk.StringVar(value="")
        self.headline = ttk.Label(self.root, textvariable=self.headline_var,
                                  style='Backing.TLabel',
                                  font=self.FONT_HEADLINE,
                                  wraplength=1020, justify='left')
        self.headline.pack(fill='x', padx=12, pady=(4, 0))

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
        """Run the survey on a worker thread; the queue carries it back."""
        if self.worker is not None and self.worker.is_alive():
            return
        selected = {key for key, var in self.deep_vars.items() if var.get()}
        previous = self.previous
        self.run_btn.config(state='disabled')
        self.save_btn.config(state='disabled')
        self.status_var.set("Looking...")
        self.headline_var.set("")
        self.progress.config(mode='indeterminate')
        self.progress.start(40)

        self.console.config(state='normal')
        self.console.delete('1.0', 'end')
        self.console.config(state='disabled')
        self.lines = []

        def work():
            try:
                problems, warnings, snapshot = run_survey(
                    selected, self.queue.put, previous)
                self.queue.put(("__done__", problems, warnings, snapshot))
            except Exception as exc:                        # pragma: no cover
                self.queue.put(f"The check itself failed: {exc}")
                self.queue.put(("__done__", [f"The check failed: {exc}"], [],
                                previous))

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
                    self._finish(item[1], item[2], item[3])
                    return
                self._log_line(item)
        except queue.Empty:
            pass
        self.after_id = self.root.after(self.POLL_MS, self._drain)

    def _finish(self, problems, warnings, snapshot):
        self.progress.stop()
        self.progress.config(mode='determinate', value=0)
        self.run_btn.config(state='normal')
        self.save_btn.config(state='normal')
        if snapshot is not None:
            self.previous = snapshot
        if problems:
            self.status_var.set(f"{len(problems)} problem(s) found.")
            self.headline.configure(foreground=self.CLR_STATUS_BAD)
            self.headline_var.set(problems[0])
        elif warnings:
            self.status_var.set(f"No problems; {len(warnings)} note(s).")
            self.headline.configure(foreground=self.CLR_FG_LIGHT)
            self.headline_var.set(warnings[0])
        else:
            self.status_var.set("All clear.")
            self.headline.configure(foreground=self.CLR_STATUS_OK)
            self.headline_var.set("Every connected device has a working "
                                  "driver.")

    # -- output --

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save the diagnostics log",
            defaultextension=".txt",
            initialfile=datetime.now().strftime(
                "connected_devices_%Y%m%d_%H%M%S.txt"),
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
    ConnectedDevicesDiagnosticsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
