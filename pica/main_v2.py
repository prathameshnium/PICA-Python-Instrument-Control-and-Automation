'''
===============================================================================
 PROGRAM:      PICA Launcher (v2)

 PURPOSE:      A modernised dashboard for launching PICA measurement scripts.
               The main window is the Quick Select view: category -> module
               -> protocol, three dropdowns and a plain-language description
               of each choice. Everything PICA can launch lives in the
               Advanced Options window (Tools menu, or Ctrl+Shift+A), which
               keeps the full card grid and the whole instrument list.
               Both windows carry a status strip along the bottom edge: a
               temperature reading, a pressure placeholder, and -- on Quick
               Select -- a button that opens the Instrument Status window,
               where the per-instrument lights and the raw VISA/GPIB scan
               table live.

 STATUS SAFETY (important):
               The launcher talks to the instruments ONLY at startup and when
               the user presses "Reload" on the status strip. It NEVER polls in
               the background, so once a measurement is launched the launcher
               stays off the GPIB bus. A running measurement must never be
               disturbed -- Reload therefore warns and asks for confirmation if
               any measurement launched from this window is still alive.

               The Novocontrol Alpha-AN is spoken to AT MOST ONCE, ever. The
               scan identifies instruments by the content of their *IDN? reply
               rather than by address, so a re-addressed instrument is still
               found; the first time an Alpha answers, its GPIB address is
               written to launcher_protected_gpib.json and every later scan
               skips it outright. Pre-seed that file (or SKIP_GPIB_ADDRESSES)
               to protect it before the first scan.

               Serial (ASRL) resources are never probed at all -- see
               PROBE_RESOURCE_PREFIXES.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre
===============================================================================
'''
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font, scrolledtext, Toplevel
import json
import os
import re
import sys
import platform
import subprocess
import webbrowser
import threading
import multiprocessing
from multiprocessing import Process
from datetime import datetime

# Run this file directly (python pica/main_v2.py) and sys.path[0] is the pica
# folder, not the repo root, so "import pica.main" can bind to an older copy
# installed in site-packages instead of the one sitting next to this file. A
# launcher that half-loads a stale twin reports things like "Unknown module
# key" for modules that plainly exist, so put the repo root first.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reuse the proven helpers from the legacy launcher so the launch path and the
# resolved script locations stay identical between v1 and v2.
from pica.main import (
    resource_path,
    run_script_process,
    launch_gpib_scanner,
    launch_plotter_utility,
    PICALauncherApp,
)

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# -----------------------------------------------------------------------------
#  Instrument identification (read-only, startup / reload only)
# -----------------------------------------------------------------------------
# Each entry: friendly name -> substrings that identify it in a *IDN? reply.
#
# Identification is by *IDN? CONTENT, never by GPIB address: an instrument that
# has been re-addressed since the reference table was written is still found,
# and two instruments that swap addresses cannot be confused for one another.
# A pattern is either a substring, or a tuple of substrings that must ALL be
# present. Patterns are matched against the identity head (see _idn_head), not
# the whole reply, so a serial number that happens to contain "2400" or "34"
# cannot masquerade as a model number.
KNOWN_INSTRUMENTS = [
    # Matched on the model, not on the "LSCI" maker token: every Lake Shore
    # controller answers LSCI, and a 340 on the same rack was being lit up
    # as a 350. Each model gets a chip of its own.
    ("Lakeshore 350",   ["MODEL350", "MODEL 350"]),
    ("Lakeshore 340",   ["MODEL340", "MODEL 340"]),
    # A Cryo-con answers either IEEE-488.2 style ("Cryocon,34,204683,3.18A")
    # or as free text ("Cryocon Model 34 Rev 3.18A"), and both spellings of
    # the maker name are in circulation. Matching maker AND model as separate
    # tokens covers every one of those; a single contiguous "CRYOCON MODEL 34"
    # matched none of the comma-separated forms.
    ("Cryocon 34",      [("CRYOCON", "34"), ("CRYO-CON", "34")]),
    ("Keithley 2400",   ["MODEL 2400"]),
    ("Keithley 6221",   ["MODEL 6221"]),
    ("Keithley 2182",   ["MODEL 2182"]),
    ("Keithley 6517B",  ["MODEL 6517"]),
    ("Keysight E4980A", ["E4980"]),
    ("SR830 Lock-in",   ["SR830"]),
    ("Tektronix AFG3022B", ["AFG3022"]),
    # The 197A has no *IDN?: the 1973A/1972A card simply hands back whatever
    # reading is pending, e.g. "OOHM+9.99999E+9" (overflow, ohms) as seen on
    # the 29 Aug 2026 scan at GPIB1::20. The 4-character function prefix is
    # the identity -- any of these substrings means a 197-dialect DMM, and
    # none of them appear in an IEEE-488.2 vendor/model field.
    ("Keithley 197A",   ["DCV+", "ACV+", "DCA+", "ACA+", "OHM+"]),
]


def _idn_head(idn):
    """The vendor+model part of a *IDN? reply, upper-cased.

    An IEEE-488.2 reply is <vendor>,<model>,<serial>,<firmware>, so the first
    two fields are the identity and everything after is a serial number that
    must not be pattern-matched -- a serial containing "34" would otherwise
    read as a Model 34. Instruments that answer free text have no commas and
    so no serial field to confuse, and are matched whole.
    """
    parts = idn.upper().split(",")
    return ",".join(parts[:2]) if len(parts) > 2 else idn.upper()


def _idn_short_name(idn):
    """A chip label for an instrument that is not in the reference table.

    An IEEE-488.2 reply is <vendor>,<model>,<serial>,<firmware>, so vendor and
    model make a readable name and the serial is dropped -- two identical
    instruments would otherwise appear as two unrelated strangers. Free-text
    repliers have no fields to take, so the reply itself is trimmed to
    something that fits a chip.
    """
    parts = [f.strip() for f in idn.split(",") if f.strip()]
    name = " ".join(parts[:2]) if len(parts) > 2 else idn.strip()
    name = " ".join(name.split())
    return name[:28] if len(name) > 28 else name


def _idn_matches(head, pattern):
    """True if one KNOWN_INSTRUMENTS pattern matches an identity head."""
    if isinstance(pattern, (tuple, list)):
        return all(part.upper() in head for part in pattern)
    return pattern.upper() in head

# A reply carrying any of these is a Novocontrol mainframe. It is recorded,
# never talked to again, and written into the protected-address file so that
# no later scan -- in this session or a future one -- probes it at all.
NOVOCONTROL_IDN_MARKERS = ("NOVOCONTROL", "ALPHA-AN", "ALPHA-A", "BDS")
NOVOCONTROL_CHIP = "Novocontrol Alpha-AN"

# Instruments that must never be auto-probed. Each gets a permanently greyed
# "(protected)" chip on the status strip: the launcher never sends them
# anything, so it never has a state to report.
NEVER_PROBED_INSTRUMENTS = [NOVOCONTROL_CHIP]

# GPIB primary addresses that must be skipped during the scan even if VISA
# lists them. Anything here is never opened and never written to. This is the
# in-source override; PROTECTED_ADDRESS_FILE below is the persistent one the
# launcher maintains itself.
SKIP_GPIB_ADDRESSES = set()

# Persistent record of addresses that must not be probed. The launcher writes
# the Novocontrol Alpha's address here the first (and only) time it identifies
# it, so from then on the Alpha is skipped outright rather than being sent a
# *IDN? it did not ask for. Hand-edit it to protect anything else:
#     {"skip_addresses": [5, 20]}
PROTECTED_ADDRESS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "launcher_protected_gpib.json")

# VISA resource kinds that are safe to probe. ASRL (serial) is deliberately
# excluded: on a Windows rack ASRL1 is as likely to be a UPS, a Bluetooth port
# or a modem as an instrument, and a *IDN? at one of those either blocks for
# the whole timeout or wedges the port. Serial resources are still listed in
# the resource count -- they are just not spoken to.
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")

# Short per-resource *IDN? timeout so a non-responding address does not stall
# the whole scan. The Lakeshore temperature read gets a little longer.
IDN_TIMEOUT_MS = 900
TEMP_TIMEOUT_MS = 1500


def _gpib_address_of(resource):
    """Return the integer GPIB primary address of a VISA resource, or None.

    Handles the full form (GPIB0::12::INSTR), the bare form (GPIB0::12) and
    a secondary address (GPIB0::12::7::INSTR, whose primary is still 12).
    """
    m = re.search(r'GPIB\d+::(\d+)(?:::|$)', resource.upper())
    return int(m.group(1)) if m else None


def _is_probeable(resource):
    """True if this resource kind may be sent a *IDN?.

    See PROBE_RESOURCE_PREFIXES for why serial is left alone.
    """
    return resource.upper().startswith(PROBE_RESOURCE_PREFIXES)


def load_protected_addresses(path=None):
    """Read the persistent protected-address list. Never raises.

    A missing, empty or corrupt file simply means "nothing extra protected".
    A launcher that refused to start because a cache file was malformed would
    be worse than one that falls back to the in-source skip list.
    """
    path = path or PROTECTED_ADDRESS_FILE
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return set()
    addresses = data.get('skip_addresses') if isinstance(data, dict) else None
    if not isinstance(addresses, list):
        return set()
    out = set()
    for item in addresses:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def save_protected_addresses(addresses, path=None):
    """Persist the protected-address list. Returns True on success.

    Never raises: an installed copy may sit in a read-only folder, and losing
    the persistence is not a reason to fail a scan -- the in-memory skip set
    still holds for the rest of the session.
    """
    path = path or PROTECTED_ADDRESS_FILE
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({
                "_comment": "GPIB primary addresses the PICA launcher must "
                            "never probe. The Novocontrol Alpha is added "
                            "automatically the first time it is identified.",
                "skip_addresses": sorted(int(a) for a in addresses),
            }, fh, indent=2)
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
#  Pressure gauge (Pfeiffer TPG 361 SingleGauge)
# -----------------------------------------------------------------------------
# The TPG 361 has no GPIB. It reaches the PC over USB (an FTDI virtual COM
# port, so an ASRL resource) or over Ethernet (TCP port 8000, fixed, so a
# TCPIP0::<ip>::8000::SOCKET resource). Neither can be found by the bus
# scan: it does not answer *IDN?, ASRL is the one resource kind PICA refuses
# to probe blind (see PROBE_RESOURCE_PREFIXES, because an unannounced write
# to an unknown COM port is how you wedge a UPS), and a socket resource is
# never enumerated by VISA at all.
#
# So the gauge is OPT-IN. Nothing here runs until somebody names its address
# in Tools > Pressure Gauge, and from then on the scan speaks to that one
# resource and no other -- and never sends it a *IDN?, even if it does show
# up in list_resources(). Both status strips already carry the PRESSURE
# tile; this is what finally fills it.
PRESSURE_GAUGE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "launcher_pressure_gauge.json")

# UNI reply -> unit name. Read rather than assumed: the tile must never label
# a Torr reading "mbar".
GAUGE_UNITS = {0: "mbar", 1: "Torr", 2: "Pa", 3: "micron", 4: "hPa", 5: "Volt"}

# PR1 status codes. Only 0 is a number worth showing; the rest are states the
# tile reports in words instead of printing a meaningless pressure.
GAUGE_STATUS = {
    0: "Measurement data okay",
    1: "Underrange",
    2: "Overrange",
    3: "Sensor error",
    4: "Sensor off",
    5: "No sensor",
    6: "Identification error",
}

GAUGE_ACK = '\x06'
GAUGE_NAK = '\x15'
GAUGE_ENQ = b'\x05'
GAUGE_TIMEOUT_MS = 1500
GAUGE_TCP_PORT = 8000            # fixed on the TPG 36x Ethernet interface
PFEIFFER_OUI = "00-A0-41"        # first three octets of every Pfeiffer MAC


def normalise_gauge_resource(text):
    """Whatever was typed -> a VISA resource string.

        COM5 / com5 / 5 / ASRL5 / ASRL5::INSTR  -> ASRL5::INSTR
        192.168.1.50 / 192.168.1.50:8000        -> TCPIP0::192.168.1.50::8000::SOCKET
        any other full VISA string              -> unchanged
        ""                                      -> ""
    """
    t = (text or "").strip()
    if not t:
        return ""
    up = t.upper()
    if "::" in up:
        if up.startswith("ASRL"):
            return up if up.endswith("::INSTR") else up + "::INSTR"
        return t
    m = re.fullmatch(r'(?:ASRL|COM)?\s*(\d+)', up)
    if m:
        return f"ASRL{int(m.group(1))}::INSTR"
    m = re.fullmatch(r'([A-Za-z0-9.\-]+)(?::(\d+))?', t)
    if m:
        port = int(m.group(2)) if m.group(2) else GAUGE_TCP_PORT
        return f"TCPIP0::{m.group(1)}::{port}::SOCKET"
    return t


def is_network_gauge(resource):
    """True for an Ethernet (TCPIP) gauge resource, False for a COM port."""
    return (resource or "").strip().upper().startswith("TCPIP")


def pfeiffer_hosts_from_arp(arp_text):
    """IPv4 addresses in an `arp -a` listing whose MAC carries the Pfeiffer
    OUI -- the one way a LAN device gives itself away without being spoken
    to. Handles both 00-a0-41-.. and 00:a0:41:.. forms."""
    oui = PFEIFFER_OUI.lower().replace("-", "")
    hosts = []
    for line in (arp_text or "").splitlines():
        m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3})\D+'
                      r'((?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})', line)
        if not m:
            continue
        mac = re.sub(r'[-:]', '', m.group(2)).lower()
        if mac.startswith(oui) and m.group(1) not in hosts:
            hosts.append(m.group(1))
    return hosts


def load_pressure_gauge(path=None):
    """Read the configured gauge, or None if there is not one. Never raises.

    A missing or corrupt file simply means "no gauge" -- the tile says so and
    the scan skips the whole business.
    """
    path = path or PRESSURE_GAUGE_FILE
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    resource = data.get('resource')
    if not isinstance(resource, str) or not resource.strip():
        return None
    try:
        baud = int(data.get('baud', 9600))
    except (TypeError, ValueError):
        baud = 9600
    return {'resource': normalise_gauge_resource(resource), 'baud': baud}


def save_pressure_gauge(resource, baud=9600, path=None):
    """Persist (or, with resource None, forget) the gauge. Returns True on
    success. Never raises: an installed copy may sit in a read-only folder,
    and losing the setting is not a reason to fail."""
    path = path or PRESSURE_GAUGE_FILE
    resource = normalise_gauge_resource(resource)
    try:
        if not resource:
            if os.path.exists(path):
                os.remove(path)
            return True
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({
                "_comment": "Address of the Pfeiffer TPG 361 pressure gauge: "
                            "ASRLn::INSTR for its USB (FTDI) COM port, or "
                            "TCPIP0::<ip>::8000::SOCKET for Ethernet. This "
                            "is the ONLY serial/socket resource the PICA "
                            "launcher ever speaks to, and it is never sent "
                            "a *IDN?. Delete this file to switch the "
                            "pressure tile off again.",
                "resource": resource,
                "baud": int(baud),
            }, fh, indent=2)
        return True
    except Exception:
        return False


def gauge_port_choices(resources):
    """Annotate ASRL resources with the port's friendly name where possible.

    "ASRL3::INSTR" tells somebody who already knows which COM the gauge is
    on. "ASRL3::INSTR — USB Serial Port (COM3)" tells everybody else, which
    is the whole difficulty with a serial instrument: nothing identifies
    itself until you speak to it, and speaking to the wrong one is exactly
    what PICA refuses to do.

    pyserial is an optional import -- it ships with pyvisa-py but is not a
    declared PICA dependency, so its absence costs the labels and nothing
    else.
    """
    try:
        from serial.tools import list_ports
        descriptions = {p.device.upper(): p.description for p in list_ports.comports()}
    except Exception:
        return list(resources)

    out = []
    for res in resources:
        m = re.search(r'ASRL(?:COM)?(\d+)', res.upper())
        desc = descriptions.get(f"COM{m.group(1)}") if m else None
        out.append(f"{res} — {desc}" if desc else res)
    return out


def gauge_resource_from_choice(text):
    """The bare VISA resource from a (possibly annotated) dropdown choice."""
    return (text or "").split("—")[0].strip()


def _gauge_query(inst, mnemonic):
    """One Pfeiffer mnemonic / ACK / ENQ / data exchange.

    The TPG 26x/36x protocol is three steps and the ENQ carries NO
    terminator -- write() would append CRLF and the controller would sit
    there waiting instead of answering.
    """
    inst.write(mnemonic)
    acknowledged = False
    for _ in range(3):
        reply = inst.read()
        if GAUGE_ACK in reply:
            acknowledged = True
            break
        if GAUGE_NAK in reply:
            raise IOError(f"gauge rejected '{mnemonic}'")
    if not acknowledged:
        raise IOError(f"no ACK for '{mnemonic}'")
    inst.write_raw(GAUGE_ENQ)
    return inst.read().strip()


def read_pressure_gauge(rm, gauge):
    """Read one pressure from the configured gauge.

    Returns a dict with 'pressure', 'units', 'source' and 'error'; any of the
    first three may be None. Read-only: UNI and PR1 are both queries, so a
    scan can never disturb a gauge that is guarding somebody's pump-down.
    """
    out = {'pressure': None, 'units': None, 'source': None, 'error': None}
    inst = None
    try:
        inst = rm.open_resource(gauge['resource'])
        inst.timeout = GAUGE_TIMEOUT_MS
        if not is_network_gauge(gauge['resource']):
            try:
                inst.baud_rate = gauge['baud']
                inst.data_bits = 8
                inst.parity = pyvisa.constants.Parity.none
                inst.stop_bits = pyvisa.constants.StopBits.one
            except Exception:
                pass                  # backend without full serial control
        inst.write_termination = '\r\n'
        inst.read_termination = '\r\n'

        try:
            unit_code = int(_gauge_query(inst, 'UNI'))
        except Exception:
            unit_code = 0             # label falls back to mbar, reading stands
        out['units'] = GAUGE_UNITS.get(unit_code, f"unit-{unit_code}")

        status_str, value_str = (_gauge_query(inst, 'PR1').split(',') + [''])[:2]
        status = int(status_str)
        if status == 0:
            out['pressure'] = float(value_str)
            out['source'] = f"Pfeiffer TPG 361 · {gauge['resource']}"
        else:
            # An underrange at the end of a pump-down is normal, not a fault,
            # so the state is reported in words rather than as an error.
            out['error'] = GAUGE_STATUS.get(status, f"status {status}")
    except Exception as e:
        out['error'] = str(e) or e.__class__.__name__
    finally:
        if inst is not None:
            try:
                inst.close()
            except Exception:
                pass
    return out


def scan_instruments(skip_addresses=None, gauge=None):
    """Perform a one-shot, read-only VISA scan.

    Runs in a worker thread -- must not touch any Tk object. Returns a plain
    dict the GUI can apply on the main thread.

    Every resource that is not protected and not excluded by kind IS probed,
    whether or not it is in KNOWN_INSTRUMENTS: a reply matching nothing in the
    table comes back under 'unknown' and is shown as a new instrument rather
    than discarded.

    Never probes an address listed in SKIP_GPIB_ADDRESSES, in the persistent
    protected-address file, or in the caller-supplied 'skip_addresses'. If a
    Novocontrol mainframe is nonetheless identified (because its address was
    not yet known), its address comes back in 'protect' so the caller can
    record it and never come back.
    """
    protected = set(SKIP_GPIB_ADDRESSES)
    protected |= load_protected_addresses()
    protected |= set(skip_addresses or ())

    result = {
        'available': PYVISA_AVAILABLE,
        'error': None,
        'resources': [],
        'skipped': [],
        'detected': {name: None for name, _ in KNOWN_INSTRUMENTS},
        # Every *IDN? reply this scan collected, resource -> reply (None when
        # the address was opened but stayed silent). Nothing extra is sent to
        # get it: the identification pass already has these in hand, and the
        # Instrument Status window shows them rather than re-probing the bus.
        'idns': {},
        # Why each skipped resource was passed over, resource -> reason. The
        # Instrument Status table prints it rather than a bare "not probed",
        # which would read as a failure when every one of them is deliberate.
        'skip_reason': {},
        # Instruments that answered but match nothing in KNOWN_INSTRUMENTS,
        # display name -> resource. They are shown as chips of their own
        # rather than being dropped: an unrecognised reply means the rack has
        # something the reference table has not been told about yet, which is
        # exactly the thing worth seeing.
        'unknown': {},
        'novocontrol': None,
        'protect': set(),
        'temperature': None,
        'temp_units': 'K',
        'temp_source': None,
        # Pressure comes from the opt-in serial gauge, not from the bus scan.
        'pressure': None,
        'pressure_units': None,
        'pressure_source': None,
        'pressure_error': None,
        'timestamp': datetime.now().strftime("%H:%M:%S"),
    }
    if not PYVISA_AVAILABLE:
        result['error'] = "PyVISA not installed"
        return result

    gauge = load_pressure_gauge() if gauge is None else gauge

    rm = None
    try:
        rm = pyvisa.ResourceManager()
        resources = list(rm.list_resources())
    except Exception as e:
        result['error'] = f"VISA backend error: {e}"
        return result

    result['resources'] = resources
    lakeshore_resource = None
    lakeshore_name = "Lakeshore 350"
    cryocon_resource = None

    try:
        for res in resources:
            addr = _gpib_address_of(res)
            if addr is not None and addr in protected:
                result['skipped'].append(res)   # protected: never opened
                result['skip_reason'][res] = "protected — never probed"
                continue
            if gauge and res.strip().upper() == gauge['resource'].upper():
                # The gauge speaks Pfeiffer mnemonics, not SCPI. It is read
                # below with its own protocol and must never see a *IDN?,
                # even if VISA has it registered as a TCPIP/USB resource.
                result['skipped'].append(res)
                result['skip_reason'][res] = "pressure gauge — read by its own protocol"
                continue
            if not _is_probeable(res):
                result['skipped'].append(res)   # serial: see the gauge policy
                result['skip_reason'][res] = (
                    "serial port — only the configured pressure gauge is read")
                continue

            idn = None
            inst = None
            try:
                inst = rm.open_resource(res)
                inst.timeout = IDN_TIMEOUT_MS
                idn = inst.query("*IDN?").strip()
            except Exception:
                idn = None
            finally:
                # 'inst' stays None when open_resource itself failed, so this
                # can never close a stale handle from an earlier iteration.
                if inst is not None:
                    try:
                        inst.close()
                    except Exception:
                        pass

            result['idns'][res] = idn
            if not idn:
                continue

            up = idn.upper()
            head = _idn_head(idn)

            # Novocontrol first: identifying it is the last thing this
            # launcher ever does to it. The address is handed back so the
            # caller can protect it permanently. This one is matched against
            # the WHOLE reply, not the identity head -- a false negative here
            # means probing it again, so it errs toward matching.
            if any(marker in up for marker in NOVOCONTROL_IDN_MARKERS):
                result['novocontrol'] = res
                if addr is not None:
                    result['protect'].add(addr)
                continue

            matched = False
            for name, subs in KNOWN_INSTRUMENTS:
                if any(_idn_matches(head, s) for s in subs):
                    matched = True
                    result['detected'][name] = res
                    if name == "Lakeshore 350":
                        lakeshore_resource = res
                        lakeshore_name = name
                    elif name == "Lakeshore 340" and not lakeshore_resource:
                        # Same KRDG? query; a 350 on the bus takes priority.
                        lakeshore_resource = res
                        lakeshore_name = name
                    elif name == "Cryocon 34":
                        cryocon_resource = res
            if not matched:
                result['unknown'][_idn_short_name(idn)] = res

        # One temperature snapshot: the Lakeshore if present, otherwise the
        # Cryocon. Both are single read-only queries; nothing is configured.
        if lakeshore_resource:
            try:
                inst = rm.open_resource(lakeshore_resource)
                inst.timeout = TEMP_TIMEOUT_MS
                temp = inst.query("KRDG? A").strip()
                result['temperature'] = float(temp)
                result['temp_source'] = f"{lakeshore_name} · Input A"
                inst.close()
            except Exception:
                result['temperature'] = None

        if result['temperature'] is None and cryocon_resource:
            try:
                inst = rm.open_resource(cryocon_resource)
                inst.timeout = TEMP_TIMEOUT_MS
                # INPUT? reports in the channel's own display units, so the
                # units are read too rather than assumed to be Kelvin.
                temp = inst.query("INPUT? A").strip()
                units = inst.query("INPUT A:UNITS?").strip().upper()
                result['temperature'] = float(temp)
                result['temp_units'] = units[:1] or 'K'
                result['temp_source'] = "Cryocon 34 · Input A"
                inst.close()
            except Exception:
                result['temperature'] = None

        # Pressure snapshot, only if a gauge has been configured. Its port is
        # in result['skipped'] like every other serial resource -- the scan
        # still does not probe it, it reads the one it was told about.
        if gauge:
            reading = read_pressure_gauge(rm, gauge)
            result['pressure'] = reading['pressure']
            result['pressure_units'] = reading['units']
            result['pressure_source'] = reading['source']
            result['pressure_error'] = reading['error']
    finally:
        try:
            rm.close()
        except Exception:
            pass

    return result


# -----------------------------------------------------------------------------
#  Measurement catalogue (mirrors the module suites; maps to SCRIPT_PATHS keys)
# -----------------------------------------------------------------------------
# Each module: (label, script_key, family) where family is 'control' | 'sensing'
# | 'master' | None. script_key must exist in PICALauncherApp.SCRIPT_PATHS.
CATALOG = [
    {
        'category': "Low Resistance (10 nΩ – 100 MΩ)",
        'type': "Current Driven",
        'instruments': "K6221 · K2182 · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("Sweep Mode I-V", "Delta Mode I-V Sweep", None),
            ("Delta Mode R vs. T (T Control)", "Delta Mode R-T", "control"),
            ("Delta Mode R vs. T (T Sensing, L350)", "Delta Mode R-T (T_Sensing)", "sensing"),
            ("Delta Mode R vs. T (T Sensing, Cryocon 34)", "Delta Mode R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Mid Resistance (100 µΩ – 200 MΩ)",
        'type': "Current Driven",
        'instruments': "K2400 · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("I-V Sweep", "K2400 I-V", None),
            ("R vs. T (T Control)", "K2400 R-T", "control"),
            ("R vs. T (T Sensing, L350)", "K2400 R-T (T_Sensing)", "sensing"),
            ("R vs. T (T Sensing, Cryocon 34)", "K2400 R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Mid Resistance (Precision, 1 µΩ – 100 MΩ)",
        'type': "Current Driven",
        'instruments': "K2400 · K2182 · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("I-V Sweep", "K2400_2182 I-V", None),
            ("R vs. T (T Control)", "K2400_2182 R-T", "control"),
            ("R vs. T (T Sensing, L350)", "K2400_2182 R-T (T_Sensing)", "sensing"),
            ("R vs. T (T Sensing, Cryocon 34)", "K2400_2182 R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "High Resistance (1 Ω – 10 PΩ)",
        'type': "Voltage Driven",
        'instruments': "K6517B · Lakeshore 350 or Cryocon 34",
        'modules': [
            ("I-V Sweep", "K6517B I-V", None),
            ("R vs. T (T Control)", "K6517B R-T", "control"),
            ("R vs. T (T Sensing, L350)", "K6517B R-T (T_Sensing)", "sensing"),
            ("R vs. T (T Sensing, Cryocon 34)", "K6517B R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Pyroelectric Measurement",
        'type': "Current Sensing",
        'instruments': "K6517B · Lakeshore 350",
        'modules': [
            # T Control: the script sets the Lakeshore 350 start point and
            # drives it with SETP/RAMP to the end temperature while it reads
            # the current. It was listed here as untyped, which put no family
            # strip on the row and left the Lakeshore off the card.
            ("PyroCurrent vs. T", "Pyroelectric Current", "control"),
            ("Voltage Polling (Bias)", "K6517B Polling (Bias)", None),
        ],
    },
    {
        'category': "Bench Multimeter Logging",
        'type': "Passive Logging",
        'instruments': "Keithley 197A · Model 1973A / 1972A IEEE-488 card",
        'modules': [
            ("197A Reading Monitor", "K197A Monitor", "sensing"),
        ],
    },
    {
        'category': "Vacuum Gauge Logging",
        'type': "Passive Logging",
        # USB / Ethernet, not GPIB: the TPG 361 never shows up in the launcher's bus
        # scan, so the module asks for the COM port itself.
        'instruments': "Pfeiffer TPG 361 SingleGauge · USB (FTDI COM) or Ethernet",
        'modules': [
            ("Pressure vs. Time", "TPG361 Pressure Log", "sensing"),
        ],
    },
    {
        'category': "Signal Generation",
        'type': "Waveform Source",
        # A source, not a measurement: this module sets the drive another
        # module measures against, so it carries no data file of its own.
        'instruments': "Tektronix AFG 3022B (2 ch, 25 MHz)",
        'modules': [
            ("Function Generator Direct Control", "AFG3022B Function Generator",
             "control"),
        ],
    },
    {
        'category': "Temperature Utilities",
        'type': "Control Utility",
        'instruments': "Lakeshore 350 · Cryocon 34",
        'modules': [
            ("Temperature Ramp (L350)", "Lakeshore Temp Control", "control"),
            ("Step-wise Control (Basic, L350)", "Lakeshore Step Control", "control"),
            ("Step-wise Control (Advanced, L350)", "Lakeshore Step Control (Advanced)", "control"),
            ("Direct Control (L350)", "Lakeshore Direct Control", "control"),
            ("Temperature Monitor (L350)", "Lakeshore Temp Monitor", "sensing"),
            # The two viewers are read-only: they ask the instrument what
            # curves it holds and can send nothing but queries.
            ("Sensor Curve Loader (L340 / L350)",
             "Lakeshore Sensor Curve Loader", "control"),
            ("Sensor Curve Viewer (L350, read only)", "Lakeshore Sensor Curve Viewer", "sensing"),
            ("Direct Control (Cryocon 34)", "Cryocon Direct Control", "control"),
            ("Temperature Monitor (Cryocon 34)", "Cryocon Temp Monitor", "sensing"),
            ("Sensor Curve Loader (Cryocon 34)", "Cryocon Sensor Curve Loader", "control"),
            ("Sensor Curve Viewer (Cryocon 34, read only)", "Cryocon Sensor Curve Viewer", "sensing"),
        ],
    },
    {
        'category': "Impedance Spectroscopy",
        'type': "Voltage Driven",
        'instruments': "Keysight E4980A",
        'modules': [
            ("C-V Measurement", "LCR C-V Measurement", None),
            ("Dielectric Frequency Scan", "LCR Frequency Scan", None),
            ("Temp. Step Freq. Scan (T Control)", "LCR Temp. Step Freq. Scan (T_Control)", "control"),
            ("Temp. Step Freq. Scan (PPMS, T Sensing, L350)", "PPMS Sync Freq. Scan", "sensing"),
            ("Temp. Step Freq. Scan (PPMS, T Sensing, Cryocon 34)", "PPMS Sync Freq. Scan (CC34)", "sensing"),
            ("Dielectric Master Tscan+Fscan (PPMS, L350)", "PPMS Dielectric Master", "master"),
            ("Dielectric Master Tscan+Fscan (PPMS, Cryocon 34)", "PPMS Dielectric Master (CC34)", "master"),
            ("Dielectric Temp. Scan (T Control)", "LCR Temp. Scan (T_Control)", "control"),
            ("Dielectric Temp. Scan (T Sensing, L350)", "LCR Temp. Scan (T_Sensing)", "sensing"),
            ("Dielectric Temp. Scan (T Sensing, Cryocon 34)", "LCR Temp. Scan (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "Broadband Dielectric Spectroscopy",
        'type': "Voltage Driven",
        'instruments': "Novocontrol Alpha-AN · ZG4",
        'experimental': True,
        'modules': [
            ("Frequency Scan (fixed T)", "Alpha-AN Freq. Scan", None),
            ("Frequency Scan (fixed T, 32-bit GPIB)",
             "Alpha-AN Freq. Scan (32-bit)", None),
        ],
    },
    {
        'category': "Lock-in Amplifier",
        'type': "AC Current Driven",
        'instruments': "Keithley 6221 · SRS SR830 DSP Lock-in",
        'experimental': True,
        'modules': [
            ("Comms and Control", "SR830 Lock-in Comms", None),
            ("AC Resistivity (4-probe)", "SR830 AC Resistivity", None),
            ("AC I-V Sweep", "SR830 AC I-V", None),
            ("AC Frequency Scan", "SR830 AC Freq. Scan", None),
            ("AC R vs. T (T Control)", "SR830 AC R-T", "control"),
            ("AC R vs. T (T Sensing, L350)", "SR830 AC R-T (T_Sensing)", "sensing"),
            ("AC R vs. T (T Sensing, Cryocon 34)", "SR830 AC R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
    {
        'category': "AC Transport without a Lock-in",
        'type': "AC Current Driven",
        'instruments': "Keithley 6221 · Keithley 197A (AC volts)",
        # No reference and no phase: R is a magnitude and an upper bound.
        # Kept apart from the lock-in suite so the two are never confused.
        'experimental': True,
        'modules': [
            ("AC I-V Sweep", "K197A AC I-V", None),
            ("AC Frequency Scan", "K197A AC Freq. Scan", None),
            ("AC R vs. T (T Control)", "K197A AC R-T", "control"),
            ("AC R vs. T (T Sensing, L350)", "K197A AC R-T (T_Sensing)", "sensing"),
            ("AC R vs. T (T Sensing, Cryocon 34)", "K197A AC R-T (T_Sensing, CC34)", "sensing"),
        ],
    },
]



# -----------------------------------------------------------------------------
#  Quick Select catalogue (the main screen)
# -----------------------------------------------------------------------------
# The main window is the "Quick Select" view: it is what a new PICA user meets
# first, so it asks three plain questions -- what kind of measurement, which
# module, which protocol -- instead of showing every script at once. The full
# CATALOG above stays behind Tools > Advanced Options (Ctrl+Shift+A).
#
# Structure:
#   category -> modules -> protocols
# where a protocol is one launchable script. Each level carries its own short
# description, shown stacked at the bottom of the screen as the user narrows
# down, and each module names the instruments that matter to it -- the status
# strip then shows only those chips.
#
# Deliberate differences from CATALOG:
#   * "Ultra Low Resistance" and "Low Resistance" are two entries that resolve
#     to the SAME delta-mode scripts. They are split because a user thinks in
#     terms of the sample, not the instrument pair: 10 nOhm of contact
#     resistance and a 10 mOhm film feel like different measurements.
#   * Cryo-con 34 protocols are listed for the LCR meter (E4980A), the
#     electrometer (K6517B) and the two AC benches, next to the Lakeshore 350
#     ones. The delta-mode and Keithley 2400 modules are Lakeshore 350 only in
#     Quick Select; their Cryo-con twins still exist and are in Advanced
#     Options. Pyroelectric has no CC34 twin written yet.
#   * PICA is the software. The rack it drives is the ITMS (Integrated
#     Transport Measurement System, formerly ATMS), so descriptions say "the
#     module" or "the ITMS rack" where they mean the hardware, never "PICA".
#   * Temperature utilities, the Novocontrol Alpha-AN, the bench multimeter,
#     the pressure log and the function generator are Advanced-only: they are
#     not measurements a newcomer starts from. The Alpha-AN broadband scans
#     are the ones to add here first, once the module has been through a full
#     campaign; the temperature utilities stay in Advanced by choice.
#
# Instrument names are written out in full in every description -- "Keithley
# 2400", not "K2400". A newcomer has not yet learned to read the shorthand,
# and this screen is the one place in PICA that assumes nothing.
QUICK_CATALOG = [
    {
        'category': "DC Resistance",
        'desc': "A steady current is passed through the sample and the "
                "voltage across it is measured, in a two- or four-wire "
                "arrangement. The modules below use different instrument "
                "pairs for different resistance ranges; pick the one whose "
                "range covers the sample.",
        'modules': [
            {
                'name': "Ultra Low Resistance (10 nΩ – 1 µΩ)",
                'desc': "Keithley 6221 current source with a Keithley 2182 "
                        "nanovoltmeter in delta mode. The current is reversed "
                        "at every point and the two readings are averaged, "
                        "which cancels the thermal EMFs in the leads and "
                        "contacts. For superconductors, metallic films and "
                        "contact resistance.",
                'instruments': ["Keithley 6221", "Keithley 2182",
                                "Lakeshore 350"],
                'protocols': [
                    {'label': "Delta Mode I-V Sweep",
                     'key': "Delta Mode I-V Sweep", 'family': None,
                     'desc': "Current sweep at a fixed temperature, recording "
                             "V against I. Run this first to check that the "
                             "contacts are ohmic."},
                    {'label': "R vs. T (T Control, Lakeshore 350)",
                     'key': "Delta Mode R-T", 'family': 'control',
                     'desc': "Resistance against temperature. The module sets "
                             "each Lakeshore 350 setpoint, waits for it to "
                             "settle, then takes the reading."},
                    {'label': "R vs. T (T Sensing, Lakeshore 350)",
                     'key': "Delta Mode R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "Resistance against temperature while the PPMS, "
                             "or a manual dewar warm-up, drives the "
                             "temperature. The Lakeshore 350 is only read."},
                ],
            },
            {
                'name': "Low Resistance (above 1 µΩ)",
                # Same scripts as the entry above -- see the note at the head
                # of QUICK_CATALOG for why the range is split in two.
                'desc': "The same Keithley 6221 and Keithley 2182 delta-mode "
                        "pair, for samples above a microohm. Current reversal "
                        "still removes thermal offsets and drift that a "
                        "single-polarity reading would include.",
                'instruments': ["Keithley 6221", "Keithley 2182",
                                "Lakeshore 350"],
                'protocols': [
                    {'label': "Delta Mode I-V Sweep",
                     'key': "Delta Mode I-V Sweep", 'family': None,
                     'desc': "Current sweep at a fixed temperature, recording "
                             "V against I."},
                    {'label': "R vs. T (T Control, Lakeshore 350)",
                     'key': "Delta Mode R-T", 'family': 'control',
                     'desc': "Resistance against temperature. The module sets "
                             "each Lakeshore 350 setpoint and waits for it to "
                             "settle before reading."},
                    {'label': "R vs. T (T Sensing, Lakeshore 350)",
                     'key': "Delta Mode R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "Resistance against temperature while an "
                             "external system drives the temperature. The "
                             "Lakeshore 350 is only read."},
                ],
            },
            {
                'name': "Resistance, High Precision (1 µΩ – 100 MΩ)",
                'desc': "Keithley 2400 SourceMeter as the current source and "
                        "a Keithley 2182 nanovoltmeter to read the sample "
                        "voltage, in a true four-wire arrangement. Better "
                        "voltage resolution than the Keithley 2400 alone, for "
                        "picking out small features such as phase "
                        "transitions.",
                'instruments': ["Keithley 2400", "Keithley 2182",
                                "Lakeshore 350"],
                'protocols': [
                    {'label': "I-V Sweep", 'key': "K2400_2182 I-V",
                     'family': None,
                     'desc': "Current sweep at a fixed temperature, with the "
                             "voltage read by the Keithley 2182."},
                    {'label': "R vs. T (T Control, Lakeshore 350)",
                     'key': "K2400_2182 R-T", 'family': 'control',
                     'desc': "Resistance against temperature, setpoint by "
                             "setpoint, with the module driving the "
                             "Lakeshore 350."},
                    {'label': "R vs. T (T Sensing, Lakeshore 350)",
                     'key': "K2400_2182 R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "Resistance against temperature while an "
                             "external system drives the temperature. The "
                             "Lakeshore 350 is only read."},
                ],
            },
            {
                'name': "Normal Resistance (100 µΩ – 200 MΩ)",
                'desc': "A single Keithley 2400 SourceMeter sources the "
                        "current and measures the voltage. The simplest "
                        "wiring on the ITMS rack, and the usual choice for "
                        "semiconductors, oxides and general transport.",
                'instruments': ["Keithley 2400", "Lakeshore 350"],
                'protocols': [
                    {'label': "I-V Sweep", 'key': "K2400 I-V", 'family': None,
                     'desc': "Current sweep at a fixed temperature: linear "
                             "sweeps, hysteresis loops or a custom current "
                             "list."},
                    {'label': "R vs. T (T Control, Lakeshore 350)",
                     'key': "K2400 R-T", 'family': 'control',
                     'desc': "Resistance against temperature, setpoint by "
                             "setpoint, with the module driving the "
                             "Lakeshore 350."},
                    {'label': "R vs. T (T Sensing, Lakeshore 350)",
                     'key': "K2400 R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "Resistance against temperature while an "
                             "external system drives the temperature. The "
                             "Lakeshore 350 is only read."},
                ],
            },
            {
                'name': "High Resistance (1 Ω – 10 PΩ)",
                'desc': "Keithley 6517B electrometer. A voltage is applied "
                        "and the leakage current, down to the pA and fA "
                        "range, is measured. For insulators, ceramics, "
                        "polymers and dielectric films. The module allows a "
                        "settling delay so the current reaches steady state "
                        "before it is recorded.",
                # Electrometer bench: both temperature controllers are offered.
                'instruments': ["Keithley 6517B", "Lakeshore 350",
                                "Cryocon 34"],
                'protocols': [
                    {'label': "I-V Sweep", 'key': "K6517B I-V", 'family': None,
                     'desc': "Voltage sweep at a fixed temperature, with the "
                             "current read by the electrometer."},
                    {'label': "R vs. T (T Control, Lakeshore 350)",
                     'key': "K6517B R-T", 'family': 'control',
                     'desc': "Resistance against temperature. The module sets "
                             "each Lakeshore 350 setpoint and waits for it to "
                             "settle before the reading is taken."},
                    {'label': "R vs. T (T Sensing, Lakeshore 350)",
                     'key': "K6517B R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "Resistance against temperature while an "
                             "external system drives the temperature. The "
                             "Lakeshore 350 is only read."},
                    {'label': "R vs. T (T Sensing, Cryocon 34)",
                     'key': "K6517B R-T (T_Sensing, CC34)", 'family': 'sensing',
                     'desc': "The same passive scan, with the Cryo-con 34 as "
                             "the thermometer."},
                ],
            },
        ],
    },
    {
        'category': "AC Resistance",
        'desc': "A small alternating current is passed through the sample "
                "and the voltage at the drive frequency is measured. With a "
                "lock-in amplifier, drift, 1/f noise and mains pickup are "
                "rejected, so much smaller signals can be resolved than with "
                "DC. Both modules here are experimental.",
        'modules': [
            {
                'name': "Lock-in AC Resistivity (4-probe)",
                'desc': "Keithley 6221 as the AC current source and a "
                        "Stanford Research SR830 lock-in amplifier to read "
                        "the in-phase voltage. The 6221 must supply the "
                        "reference to the SR830 (Trigger Link line 3 to REF "
                        "IN) or the readings are meaningless. Experimental: "
                        "the pairing works but has not been through a full "
                        "measurement campaign.",
                # The AC modules use the Lakeshore 350 for T Control and
                # either thermometer for T Sensing; both chips must be listed
                # or the R vs. T protocols show no thermometer at all.
                'instruments': ["Keithley 6221", "SR830 Lock-in",
                                "Lakeshore 350", "Cryocon 34"],
                'protocols': [
                    {'label': "SR830 Comms and Control",
                     'key': "SR830 Lock-in Comms", 'family': None,
                     'desc': "Direct control of the SR830: sensitivity, time "
                             "constant, phase and reference, with X, Y, R "
                             "and theta shown live. Use it to get the lock-in "
                             "locked before a measurement."},
                    {'label': "AC Resistivity, 4-probe (SR830)",
                     'key': "SR830 AC Resistivity", 'family': None,
                     'desc': "Four-probe AC resistance against time at a "
                             "fixed current and frequency."},
                    {'label': "AC I-V Sweep", 'key': "SR830 AC I-V",
                     'family': None,
                     'desc': "Current amplitude sweep at a fixed frequency. "
                             "A straight line through the origin means an "
                             "ohmic contact, and shows which current to use "
                             "for the temperature scans."},
                    {'label': "AC Frequency Scan",
                     'key': "SR830 AC Freq. Scan", 'family': None,
                     'desc': "Frequency sweep at a fixed current. A flat R(f) "
                             "is a plain resistance; a roll-off points to "
                             "cable capacitance or contact impedance."},
                    {'label': "R vs. T (T Control)", 'key': "SR830 AC R-T",
                     'family': 'control',
                     'desc': "AC resistance against temperature along a "
                             "Lakeshore 350 ramp that the module drives. The "
                             "heater is switched off on every exit path."},
                    {'label': "R vs. T (T Sensing)",
                     'key': "SR830 AC R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "AC resistance against temperature while an "
                             "external system drives the temperature. The "
                             "Lakeshore 350 is only read."},
                    {'label': "R vs. T (T Sensing, Cryocon 34)",
                     'key': "SR830 AC R-T (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "The same passive scan, with the Cryo-con 34 as "
                             "the thermometer."},
                ],
            },
            {
                'name': "DMM AC Resistance (no lock-in)",
                'desc': "Keithley 6221 as the AC current source and a "
                        "Keithley 197A bench multimeter on AC volts. There "
                        "is no reference and no phase, so the result is a "
                        "magnitude and an upper bound: noise inside the "
                        "meter's passband adds to it. Use it only when the "
                        "voltage is well above the meter's noise floor and "
                        "no lock-in is free. The 197A needs its 1973A/1972A "
                        "interface card, and its command table is not yet "
                        "verified against the manual.",
                'instruments': ["Keithley 6221", "Keithley 197A",
                                "Lakeshore 350", "Cryocon 34"],
                'protocols': [
                    {'label': "AC I-V Sweep", 'key': "K197A AC I-V",
                     'family': None,
                     'desc': "Current amplitude sweep at a fixed frequency. "
                             "Start at a current that gives a voltage the "
                             "197A can resolve."},
                    {'label': "AC Frequency Scan",
                     'key': "K197A AC Freq. Scan", 'family': None,
                     'desc': "Frequency sweep at a fixed current. A roll-off "
                             "at the top of the range is usually the meter's "
                             "own passband, not the sample."},
                    {'label': "R vs. T (T Control)", 'key': "K197A AC R-T",
                     'family': 'control',
                     'desc': "AC resistance against temperature along a "
                             "Lakeshore 350 ramp that the module drives."},
                    {'label': "R vs. T (T Sensing)",
                     'key': "K197A AC R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "AC resistance against temperature while an "
                             "external system drives the temperature. The "
                             "Lakeshore 350 is only read."},
                    {'label': "R vs. T (T Sensing, Cryocon 34)",
                     'key': "K197A AC R-T (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "The same passive scan, with the Cryo-con 34 as "
                             "the thermometer."},
                ],
            },
        ],
    },
    {
        'category': "Impedance Spectroscopy",
        'desc': "A small AC voltage is applied over a range of frequencies "
                "and the complex impedance is measured. Capacitance, "
                "dielectric permittivity and loss tangent follow from it. "
                "Used for C-V analysis, magnetocapacitance and dielectric "
                "anomalies at phase transitions.",
        'modules': [
            {
                'name': "Frequency / Bias Sweep (fixed T)",
                'desc': "Keysight E4980A precision LCR meter, 20 Hz to "
                        "2 MHz. The temperature is held, or simply not "
                        "controlled, while the frequency or the DC bias is "
                        "swept.",
                'instruments': ["Keysight E4980A"],
                'protocols': [
                    {'label': "C-V Measurement", 'key': "LCR C-V Measurement",
                     'family': None,
                     'desc': "Capacitance against DC bias at a fixed "
                             "frequency, for ferroelectric butterfly loops, "
                             "depletion profiling and tunability."},
                    {'label': "Dielectric Frequency Scan",
                     'key': "LCR Frequency Scan", 'family': None,
                     'desc': "Frequency sweep at one temperature, recording "
                             "capacitance, permittivity and loss."},
                ],
            },
            {
                'name': "Dielectric Temperature Scan",
                # LCR bench: both temperature controllers are offered.
                'desc': "Keysight E4980A read continuously while the "
                        "temperature changes. Every point is stamped with the "
                        "temperature it was actually taken at, not the one "
                        "that was requested.",
                'instruments': ["Keysight E4980A", "Lakeshore 350",
                                "Cryocon 34"],
                'protocols': [
                    {'label': "Dielectric Temp. Scan (T Control)",
                     'key': "LCR Temp. Scan (T_Control)", 'family': 'control',
                     'desc': "Setpoint by setpoint: the dielectric response "
                             "is measured once each Lakeshore 350 setpoint "
                             "has settled."},
                    {'label': "Dielectric Temp. Scan (T Sensing, L350)",
                     'key': "LCR Temp. Scan (T_Sensing)", 'family': 'sensing',
                     'desc': "Permittivity against temperature while the "
                             "PPMS or another external ramp warms or cools "
                             "the sample. The Lakeshore 350 is only read."},
                    {'label': "Dielectric Temp. Scan (T Sensing, CC34)",
                     'key': "LCR Temp. Scan (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "The same passive scan with the Cryo-con 34 as "
                             "the thermometer. This is the hardened version: "
                             "it reconnects by itself and writes every point "
                             "to disk as it goes."},
                ],
            },
            {
                'name': "Frequency Scan vs. Temperature",
                'desc': "A full frequency sweep at each of a series of "
                        "temperatures. Either the module holds each setpoint "
                        "itself on the Lakeshore 350, or it waits for the "
                        "plateaus of a Quantum Design PPMS sequence and never "
                        "commands the temperature.",
                'instruments': ["Keysight E4980A", "Lakeshore 350",
                                "Cryocon 34"],
                'protocols': [
                    {'label': "Temp. Step Freq. Scan (T Control)",
                     'key': "LCR Temp. Step Freq. Scan (T_Control)",
                     'family': 'control',
                     'desc': "Each Lakeshore 350 setpoint is held while a "
                             "full frequency sweep is run, then the next "
                             "setpoint is taken. Stand-alone, no PPMS "
                             "involved."},
                    {'label': "PPMS Sync Freq. Scan (T Sensing, L350)",
                     'key': "PPMS Sync Freq. Scan", 'family': 'sensing',
                     'desc': "Waits for the PPMS to hold a plateau, runs a "
                             "full frequency sweep there, then waits for the "
                             "next one. Gives a clean grid in frequency and "
                             "temperature."},
                    {'label': "PPMS Sync Freq. Scan (T Sensing, CC34)",
                     'key': "PPMS Sync Freq. Scan (CC34)", 'family': 'sensing',
                     'desc': "The same plateau-synchronised frequency scan "
                             "with the Cryo-con 34 as the thermometer."},
                    {'label': "PPMS Dielectric Master (L350)",
                     'key': "PPMS Dielectric Master", 'family': 'master',
                     'desc': "Temperature scans and frequency scans in one "
                             "run, following a written sequence, with field "
                             "tags and per-run cooldowns. For an overnight "
                             "PPMS campaign."},
                    {'label': "PPMS Dielectric Master (CC34)",
                     'key': "PPMS Dielectric Master (CC34)", 'family': 'master',
                     'desc': "The same master sequence with the Cryo-con 34 "
                             "as the thermometer."},
                ],
            },
        ],
    },
    {
        'category': "Pyroelectric",
        'desc': "The current a poled sample releases as its temperature "
                "changes, measured down to the fA range. Integrating it "
                "gives the released charge, and from that the remanent "
                "polarisation and the depolarisation (TSDC) peaks that mark "
                "a ferroelectric transition or Curie temperature.",
        'modules': [
            {
                'name': "Pyroelectric / TSDC Current",
                # The electrometer bench may use the Cryo-con 34, but no CC34
                # twin of these two scripts is written yet.
                'desc': "Keithley 6517B electrometer reading the sample "
                        "current at zero bias while the temperature ramps. "
                        "Pole the sample first with the same instrument's "
                        "voltage source. Proper shielding matters at these "
                        "currents.",
                'instruments': ["Keithley 6517B", "Lakeshore 350"],
                'protocols': [
                    # T Control, not untyped: the script sets the Lakeshore
                    # 350 start point and drives it with SETP/RAMP to the end
                    # temperature while it reads the current.
                    {'label': "Pyroelectric Current vs. T",
                     'key': "Pyroelectric Current", 'family': 'control',
                     'desc': "The measurement itself: depolarisation current "
                             "against temperature during a linear ramp that "
                             "the module drives on the Lakeshore 350."},
                    {'label': "Voltage Polling (Bias / Poling)",
                     'key': "K6517B Polling (Bias)", 'family': None,
                     'desc': "Hold a poling voltage on the sample and watch "
                             "the leakage current settle. Run this before the "
                             "pyroelectric scan."},
                ],
            },
        ],
    },
]

# The two temperature controllers, which are what distinguishes one protocol
# from the next within a module: an I-V sweep uses neither, a T Control scan
# drives the Lakeshore, a T Sensing scan reads whichever one its script names.
TEMPERATURE_CHIPS = ("Lakeshore 350", "Cryocon 34")


def _protocol_instruments(module, proto):
    """The instruments one protocol actually talks to.

    Derived from the module's list rather than written out thirty times: the
    module names the hardware, and which thermometer (if any) is in play is
    already stated unambiguously by the protocol itself -- 'CC34' in the
    script key for the Cryo-con variants, and T Control scripts being
    Lakeshore throughout. Deriving it keeps this in step with the script keys
    by construction; a hand-written list would drift the first time a protocol
    was renamed.
    """
    names = list(module['instruments'])
    if not proto['family']:
        # No temperature dimension at all -- an I-V or fixed-T sweep.
        return [n for n in names if n not in TEMPERATURE_CHIPS]
    key = proto['key'].upper()
    cryocon = "CC34" in key or "CRYOCON" in key
    drop = "Lakeshore 350" if cryocon else "Cryocon 34"
    return [n for n in names if n != drop]


def _run_legacy_launcher():
    """Target for a spawned process: run the classic v1 launcher."""
    from pica.main import main as legacy_main
    legacy_main()


class PICALauncherV2:
    PROGRAM_VERSION = "2.1.0"

    # --- Palette: matches the v1 launcher (warm sand / cream / terracotta) ---
    CLR_APP = '#B8A392'          # tan window background (v1 CLR_BG_DARK)
    CLR_PANEL = '#E5DCD3'        # cream panels / cards / rail (v1 CLR_FRAME_BG)
    CLR_PANEL2 = '#F1EBE4'       # lighter cream for input fields / console
    CLR_ACCENT = '#BA6B5E'       # terracotta accent (v1 CLR_ACCENT_GOLD)
    CLR_ACCENT_SOFT = '#EAD9D2'  # pale terracotta tint for soft fills
    CLR_HOVER = '#8B3A2F'        # deep maroon hover (v1 CLR_HOVER)
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DIM = '#6B5F54'
    CLR_TEXT_FAINT = '#8A7C6E'
    CLR_TEXT_LIGHT = '#FFFFFF'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_BORDER = '#C4B2A0'
    CLR_BORDER_STRONG = '#B8A392'
    CLR_OK = '#5B7A3F'           # olive green = connected (semantic status)
    # Status dots need to read as "alive" from across the lab bench, which the
    # muted olive above cannot do at dot size. The pair below is the blink:
    # bright on-phase, dimmer green off-phase (never grey, so an idle blink is
    # still obviously "connected" rather than "lost").
    CLR_LIVE = '#12D64B'         # vivid green = instrument answering now
    CLR_LIVE_DIM = '#3E8B4A'     # off-phase of the blink
    CLR_WARN = '#8B3A2F'         # deep maroon = experimental (matches v1)
    CLR_FAMILY_SENSING = '#D8C0A8'   # pale sand   (v1)
    CLR_FAMILY_CONTROL = '#B04A38'   # terracotta  (v1)
    CLR_FAMILY_MASTER = '#4A3222'    # espresso    (v1)

    # --- Type scale: anchored on the same base size as the v1 launcher ---
    # v2 runs two points larger than v1: the module rows, tab labels and menus
    # are read from a standing position at the rig, not from a desk chair.
    FONT_SIZE_BASE = PICALauncherApp.FONT_SIZE_BASE + 2      # 12 -> 14
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SMALL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_LABEL = ('Segoe UI', FONT_SIZE_BASE - 3, 'bold')
    FONT_WORDMARK = ('Segoe UI', FONT_SIZE_BASE + 10, 'bold')  # v1 FONT_TITLE
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')    # v1 FONT_INSTITUTE
    FONT_CARD = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')     # v1 FONT_SUBTITLE
    FONT_INSTITUTE = ('Segoe UI', FONT_SIZE_BASE + 4, 'bold')
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE)                 # v1 FONT_INFO
    FONT_INFO_BOLD = ('Segoe UI', FONT_SIZE_BASE, 'bold')
    FONT_STAT = ('Consolas', FONT_SIZE_BASE + 4, 'bold')
    FONT_MONO = ('Consolas', 11)                             # v1 FONT_CONSOLE
    FONT_MENU = ('Segoe UI', FONT_SIZE_BASE)                 # menu / sub-menu items
    # Quick Select dropdowns and their popup lists (entry + listbox share it).
    FONT_COMBO = ('Segoe UI', FONT_SIZE_BASE + 2)
    # The status strip runs against the type scale rather than with it: it is a
    # glanceable band, not reading matter, and at the v2 base size it ate three
    # rows of window height. Its own smaller scale keeps it to one compact band.
    FONT_DOT = ('Segoe UI', FONT_SIZE_BASE + 1)              # status dot glyph
    FONT_STRIP = ('Segoe UI', FONT_SIZE_BASE - 4)            # chip / caption text
    FONT_STRIP_BOLD = ('Segoe UI', FONT_SIZE_BASE - 4, 'bold')  # a marked chip
    # The chips that answer "have I got the hardware for this, and is it on?"
    # run well above the strip's own caption scale: text a size under the body
    # copy, and a dot big enough to catch the eye across the bench while it
    # blinks. Only the strips that carry the narrowed selection use them.
    FONT_CHIP = ('Segoe UI', FONT_SIZE_BASE - 1)
    FONT_CHIP_BOLD = ('Segoe UI', FONT_SIZE_BASE - 1, 'bold')
    FONT_CHIP_DOT = ('Segoe UI', FONT_SIZE_BASE + 4)
    FONT_STRIP_CAP = ('Segoe UI', FONT_SIZE_BASE - 5, 'bold')  # tile headings
    FONT_STRIP_STAT = ('Consolas', FONT_SIZE_BASE, 'bold')   # tile big numbers
    # Browse cards keep the v1 sizes. The base bump was for the menus and the
    # chrome; inside a card it only forced titles onto a second line and
    # pushed the instrument line off the right edge.
    FONT_CARD_TITLE = ('Segoe UI', FONT_SIZE_BASE - 1, 'bold')   # 13
    FONT_MOD = ('Segoe UI', FONT_SIZE_BASE - 2)                  # 12, v1 row size
    FONT_CARD_META = ('Consolas', FONT_SIZE_BASE - 5)            # 9, instrument line

    SCRIPT_PATHS = PICALauncherApp.SCRIPT_PATHS
    LOGO_FILE = resource_path("assets/LOGO/UGC_DAE_CSR_NBG.jpeg")
    LOGO_SIZE = PICALauncherApp.LOGO_SIZE                    # 140, as in v1
    ADV_LOGO_SIZE = 64          # header badge in the Advanced Options window
    RAIL_WIDTH = 300
    # Card grid reflows with the window: a maximised 1920 px screen fits four
    # columns, a 1440 px one three, a restored window two, a narrow one a
    # single column. The fourth column is what keeps the taller cards off the
    # scrollbar on a wide monitor -- with eleven cards, going from three
    # columns to four takes a column from four cards deep to three.
    MAX_CARD_COLS = 4
    CARD_MIN_WIDTH = 380
    MANUAL_FILE = resource_path("docs/User_Manual.md")
    README_FILE = resource_path("README.md")
    CHANGELOG_FILE = resource_path("CHANGELOG.md")
    LICENSE_FILE = resource_path("LICENSE")
    REPO_URL = PICALauncherApp.REPO_URL

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher — v{self.PROGRAM_VERSION}")
        self.root.geometry("1280x820")
        self.root.configure(bg=self.CLR_APP)
        try:
            self.root.state('zoomed')
        except tk.TclError:
            pass

        # Instrument-status state (updated only on scan)
        self._scanning = False
        # Browse-grid layout state, set before the first <Configure> fires.
        self._browse_cols = 2
        self._browse_width = 900
        self._rendering = False
        # Folder the File menu's pickers open in (last one the user visited).
        self._last_data_dir = os.getcwd()
        # Every status strip alive right now (Quick Select, and Advanced
        # Options while it is open). One scan result is rendered into all
        # of them; a strip built later catches up from _last_scan.
        self._strips = []
        self._last_scan = None
        self._adv_win = None
        self._status_win = None
        # Instruments the current Quick Select choice needs. The strip no
        # longer shows chips, so this is what tints them in the Instrument
        # Status window instead of filtering a list nobody is looking at.
        self._needed_instruments = set()
        # Every line the launcher has logged, and every console view currently
        # showing them. The log outlives any one view: a console opened after
        # the fact still shows what happened at startup, which is exactly when
        # the interesting lines are written.
        self._log_lines = []
        self._consoles = []
        self._console_win = None
        # The Instrument Status window is shown once, shortly after launch.
        # Later scans must not re-open it.
        self._startup_status_shown = False
        # Names whose dot is currently blinking (detected on the last scan).
        self._live_chips = set()
        self._blink_on = True
        self.launched_processes = []
        # Addresses this session has learned must not be probed again. Seeded
        # from the persistent file so a Novocontrol identified on a previous
        # run is skipped from the very first scan of this one.
        self._session_protected = load_protected_addresses()

        self._setup_styles()
        self._build_menubar()
        self._build_statusbar()
        self._build_body()

        self.log(f"PICA Launcher v{self.PROGRAM_VERSION} initialized.")
        # First (and only automatic) instrument scan, shortly after the UI draws.
        self.root.after(400, self.start_scan)
        self._blink_tick()
        # The Instrument Status window comes up right after the launcher
        # draws, on its own timer rather than off the back of the scan: the
        # scan can end early (no PyVISA, a VISA backend error) and those are
        # the cases where the window has the most to say. It fills in when the
        # scan lands a moment later.
        #
        # The standalone VISA/GPIB scanner is NOT opened here: it repeats, in
        # a second process and a second pass over the bus, what that window
        # already shows. Advanced Options opens it, and it is in the Tools
        # menu and on the toolbar.
        self.root.after(600, self._open_startup_status)

    def _open_startup_status(self):
        """Show the Instrument Status window once, just after startup."""
        if self._startup_status_shown:
            return
        self._startup_status_shown = True
        self.open_instrument_status()

    # ---------------------------------------------------------------- styling
    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_APP, foreground=self.CLR_TEXT,
                        font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_APP)
        style.configure('Panel.TFrame', background=self.CLR_PANEL)
        style.configure('Card.TFrame', background=self.CLR_PANEL)
        style.configure('TLabel', background=self.CLR_APP, foreground=self.CLR_TEXT)
        style.configure('Panel.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT)
        style.configure('Dim.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT_DIM,
                        font=self.FONT_SMALL)
        style.configure('Faint.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT_FAINT,
                        font=self.FONT_LABEL)
        style.configure('Stat.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_STAT)
        # Status-strip variants: same colours, tighter type.
        style.configure('StripCap.TLabel', background=self.CLR_PANEL,
                        foreground=self.CLR_TEXT_FAINT, font=self.FONT_STRIP_CAP)
        style.configure('StripDim.TLabel', background=self.CLR_PANEL,
                        foreground=self.CLR_TEXT_DIM, font=self.FONT_STRIP)
        style.configure('StripStat.TLabel', background=self.CLR_PANEL,
                        foreground=self.CLR_TEXT, font=self.FONT_STRIP_STAT)
        style.configure('CardTitle.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_CARD_TITLE)
        style.configure('Mono.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT_DIM,
                        font=self.FONT_MONO)
        # Rail identity lines (institute, credits) read at full base size --
        # Dim.TLabel is the 10 pt style used for status-strip captions.
        style.configure('Info.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_INFO)
        style.configure('Institute.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_INSTITUTE)
        style.configure('InfoBold.TLabel', background=self.CLR_PANEL, foreground=self.CLR_TEXT,
                        font=self.FONT_INFO_BOLD)

        # Primary launch button (accent fill)
        style.configure('Launch.TButton', font=('Segoe UI', self.FONT_SIZE_BASE, 'bold'),
                        foreground=self.CLR_TEXT_LIGHT, background=self.CLR_ACCENT,
                        borderwidth=0, focusthickness=0, focuscolor='none', padding=(12, 8))
        style.map('Launch.TButton', background=[('active', self.CLR_HOVER),
                                                ('disabled', self.CLR_BORDER_STRONG)])

        # Module row button (flat, left-aligned; maroon hover like v1)
        style.configure('Mod.TButton', font=self.FONT_MOD, anchor='w',
                        foreground=self.CLR_TEXT, background=self.CLR_PANEL,
                        borderwidth=0, focusthickness=0, focuscolor='none', padding=(8, 6))
        style.map('Mod.TButton', background=[('active', self.CLR_HOVER)],
                  foreground=[('active', self.CLR_TEXT_LIGHT)])

        # Auxiliary / secondary button (outlined)
        style.configure('Aux.TButton', font=self.FONT_SMALL, foreground=self.CLR_TEXT_DIM,
                        background=self.CLR_PANEL, borderwidth=1, padding=(10, 7))
        style.map('Aux.TButton', foreground=[('active', self.CLR_ACCENT)],
                  background=[('active', self.CLR_ACCENT_SOFT)])

        # Icon button for the top-right toolbar (v1 'Icon.TButton')
        style.configure('Icon.TButton', font=('Segoe UI', self.FONT_SIZE_BASE),
                        foreground=self.CLR_TEXT, background=self.CLR_PANEL,
                        borderwidth=0, focusthickness=0, focuscolor='none',
                        padding=(4, 2))
        style.map('Icon.TButton', background=[('active', self.CLR_ACCENT_SOFT)])

        style.configure('TNotebook', background=self.CLR_APP, borderwidth=0)
        style.configure('TNotebook.Tab', font=('Segoe UI', self.FONT_SIZE_BASE - 1, 'bold'),
                        padding=(16, 8), background=self.CLR_APP, foreground=self.CLR_TEXT)
        style.map('TNotebook.Tab',
                  background=[('selected', self.CLR_APP)],
                  foreground=[('selected', self.CLR_ACCENT)])

        # The three Quick Select dropdowns are read (and clicked) from a
        # standing position at the rack, so they run a size above the base
        # scale with room around the text. The popup is a plain Tk listbox
        # rather than a ttk widget: it only takes a font and colours through
        # the option database, and only for widgets created afterwards.
        style.configure('TCombobox', fieldbackground=self.CLR_PANEL2,
                        background=self.CLR_PANEL2, foreground=self.CLR_TEXT,
                        arrowcolor=self.CLR_TEXT_DIM, padding=8,
                        font=self.FONT_COMBO)
        style.map('TCombobox',
                  fieldbackground=[('readonly', self.CLR_PANEL2)],
                  foreground=[('readonly', self.CLR_TEXT)])
        self.root.option_add('*TCombobox*Listbox.font', self.FONT_COMBO)
        self.root.option_add('*TCombobox*Listbox.background', self.CLR_PANEL2)
        self.root.option_add('*TCombobox*Listbox.foreground', self.CLR_TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', self.CLR_ACCENT)
        self.root.option_add('*TCombobox*Listbox.selectForeground', self.CLR_TEXT_LIGHT)
        style.configure('Vertical.TScrollbar', troughcolor=self.CLR_APP,
                        background=self.CLR_BORDER_STRONG, arrowcolor=self.CLR_TEXT_DIM,
                        borderwidth=0)

    # --------------------------------------------------------------- menu bar
    def _build_menubar(self):
        menubar = tk.Menu(self.root, font=self.FONT_MENU)

        # Classic File menu: opening things comes first, then the app commands.
        file_menu = tk.Menu(menubar, tearoff=0, font=self.FONT_MENU)
        file_menu.add_command(label="Open Data File…", accelerator="Ctrl+O",
                              command=self.open_data_file)
        file_menu.add_command(label="Open Data as Graph…", accelerator="Ctrl+G",
                              command=self.open_data_as_graph)
        file_menu.add_command(label="Open Data in Text Editor…",
                              command=self.open_data_in_editor)
        file_menu.add_separator()
        # PPMS files go to the tool that understands their format: a .seq to
        # the Sequence Visualizer, a QD .dat to the PPMS Plotter. The generic
        # "Open Data as Graph" above still routes to the Plotter Utility.
        file_menu.add_command(label="Open Sequence…", accelerator="Ctrl+Q",
                              command=self.open_sequence_file)
        file_menu.add_command(label="Open PPMS Data as Plot…",
                              accelerator="Ctrl+P",
                              command=self.open_ppms_data_as_plot)
        file_menu.add_separator()
        file_menu.add_command(label="Open Folder…", accelerator="Ctrl+Shift+O",
                              command=self.open_folder)
        file_menu.add_command(label="Open PICA Folder", command=self.open_pica_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Reload Instrument Status", command=self.start_scan)
        file_menu.add_command(label="Refresh Module List", command=self._rebuild_browse)
        file_menu.add_separator()
        file_menu.add_command(label="Open Legacy Launcher (v1)", command=self.open_legacy_launcher)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", accelerator="Alt+F4", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        self.root.bind_all("<Control-o>", lambda _e: self.open_data_file())
        self.root.bind_all("<Control-g>", lambda _e: self.open_data_as_graph())
        self.root.bind_all("<Control-O>", lambda _e: self.open_folder())
        self.root.bind_all("<Control-q>", lambda _e: self.open_sequence_file())
        self.root.bind_all("<Control-p>",
                           lambda _e: self.open_ppms_data_as_plot())
        # Ctrl+Shift+A: the expert door. Tk reports the shifted letter as an
        # upper-case keysym, so the binding is on "A", not "a".
        self.root.bind_all("<Control-Shift-KeyPress-A>",
                           lambda _e: self.open_advanced())

        tools_menu = tk.Menu(menubar, tearoff=0, font=self.FONT_MENU)
        # Advanced Options sits at the top of Tools: it is the one entry an
        # experienced user reaches for, and it has the accelerator to match.
        tools_menu.add_command(label="Advanced Options…", accelerator="Ctrl+Shift+A",
                               command=self.open_advanced)
        tools_menu.add_separator()
        tools_menu.add_command(label="GPIB / VISA Scanner", command=self._launch_gpib_scanner)
        tools_menu.add_command(label="GPIB Scanner (32-bit, no VISA)",
                               command=lambda: self.launch_script("GPIB Scanner (32-bit)"))
        tools_menu.add_command(label="SCPI Console", command=lambda: self.launch_script("SCPI Console"))
        tools_menu.add_command(label="Plotter Utility", command=launch_plotter_utility)
        tools_menu.add_command(label="Pressure Gauge (TPG 361)…",
                               command=self.open_pressure_gauge_dialog)
        tools_menu.add_separator()
        tools_menu.add_command(label="PICA Utils…", command=self.open_tools_popup)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        view_menu = tk.Menu(menubar, tearoff=0, font=self.FONT_MENU)
        # Advanced Options is deliberately not repeated here: it stays in
        # the Tools menu and on Ctrl+Shift+A, out of a newcomer's way.
        view_menu.add_command(label="Main Window", command=self._focus_main)
        view_menu.add_separator()
        view_menu.add_command(label="Console", command=self.open_console)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0, font=self.FONT_MENU)
        help_menu.add_command(label="User Manual", command=lambda: self._open_path(self.MANUAL_FILE))
        help_menu.add_command(label="README", command=lambda: self._open_path(self.README_FILE))
        help_menu.add_command(label="Change Log", command=lambda: self._open_path(self.CHANGELOG_FILE))
        help_menu.add_separator()
        help_menu.add_command(label="View Project on GitHub", command=lambda: webbrowser.open(self.REPO_URL))
        help_menu.add_command(label="About PICA", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    # ------------------------------------------------------------ status strip
    # The strip is the bottom band of a PICA window: a temperature snapshot,
    # the VISA link count, a colour legend and one chip per instrument. It
    # sits at the BOTTOM rather than the top because the eye starts at the
    # chooser above it -- the strip is reference material, not a heading.
    #
    # Up to two strips are alive at once (Quick Select and Advanced Options),
    # so each one registers itself in self._strips and every scan result is
    # rendered into all of them. Quick Select rebuilds its chips whenever the
    # selection changes, so it only ever shows the instruments that the chosen
    # measurement actually needs; Advanced Options always shows all of them.
    #
    # The pressure tile sits immediately to the right of the temperature
    # tile, same StripCap / StripStat / StripDim triple, filled from the same
    # one-shot scan result. Its gauge is serial and opt-in, so unlike every
    # other reading it stays blank until somebody names the port in
    # Tools > Pressure Gauge -- see PRESSURE_GAUGE_FILE.
    def _all_chip_names(self):
        """Every instrument chip, in reference-table order."""
        return [n for n, _ in KNOWN_INSTRUMENTS] + NEVER_PROBED_INSTRUMENTS

    def _build_statusbar(self):
        """The Quick Select strip: readings and a button, no chips."""
        self.quick_strip = self._build_status_strip(self.root, None,
                                                    console_button=True,
                                                    selection_chips=True)

    def _build_status_strip(self, parent, chip_names, console_button=False,
                            console=False, selection_chips=False):
        """Build one bottom status band and register it for scan updates.

        `chip_names` is the list of instruments to show as chips, or None for
        a strip that carries the Instrument Status button instead. Quick
        Select uses the button: a newcomer reading that screen is choosing a
        measurement, not auditing the bus, and eleven chips under the question
        answered a question nobody had asked yet. Advanced Options keeps the
        chips, because that is where the bus is what you came to look at.
        """
        bar = tk.Frame(parent, bg=self.CLR_PANEL, highlightthickness=1,
                       highlightbackground=self.CLR_BORDER)
        bar.pack(side='bottom', fill='x')
        inner = tk.Frame(bar, bg=self.CLR_PANEL)
        inner.pack(fill='x', padx=10, pady=5)
        strip = {'bar': bar, 'chips': {}, 'names': []}

        # Temperature snapshot tile. Fixed width: the value grows from "—" to
        # "300.00 K" on every scan and an elastic tile would shove the rest of
        # the strip sideways each time.
        temp_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        temp_tile.pack(side='left', padx=(0, 12))
        ttk.Label(temp_tile, text="TEMPERATURE", style='StripCap.TLabel').pack(anchor='w')
        strip['temp_value'] = ttk.Label(temp_tile, text="—", style='StripStat.TLabel',
                                        width=10, anchor='w')
        strip['temp_value'].pack(anchor='w', fill='x')
        strip['temp_sub'] = ttk.Label(temp_tile, text="not scanned yet",
                                      style='StripDim.TLabel')
        strip['temp_sub'].pack(anchor='w')

        tk.Frame(inner, bg=self.CLR_BORDER, width=1).pack(side='left', fill='y', padx=(0, 12))

        # Pressure tile, fed by the opt-in Pfeiffer TPG 361 on a serial
        # port. scan_instruments reads it in the same one-shot pass that
        # reads the temperature and _render_strip fills these two labels
        # from result['pressure'] / result['pressure_units']. With no gauge
        # configured the tile stays dashed -- it is drawn either way so the
        # strip does not change shape when one is added.
        pres_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        pres_tile.pack(side='left', padx=(0, 12))
        ttk.Label(pres_tile, text="PRESSURE", style='StripCap.TLabel').pack(anchor='w')
        strip['pres_value'] = ttk.Label(pres_tile, text="—", style='StripStat.TLabel',
                                        width=10, anchor='w')
        strip['pres_value'].pack(anchor='w', fill='x')
        strip['pres_sub'] = ttk.Label(pres_tile, text="no gauge configured",
                                      style='StripDim.TLabel')
        strip['pres_sub'].pack(anchor='w')

        tk.Frame(inner, bg=self.CLR_BORDER, width=1).pack(side='left', fill='y', padx=(0, 12))

        # Reload is packed to the right edge before anything elastic, so pack
        # reserves its width instead of letting the rest squeeze it out.
        strip['reload_btn'] = ttk.Button(inner, text="⟳ Reload", style='Aux.TButton',
                                         command=self.start_scan)
        strip['reload_btn'].pack(side='right', padx=(12, 0))

        if chip_names is None:
            strip['status_btn'] = ttk.Button(
                inner, text="⚡ Instrument Status", style='Aux.TButton',
                command=self.open_instrument_status)
            strip['status_btn'].pack(side='right', padx=(12, 0))
            if console_button:
                # Quick Select reaches the console through a button rather
                # than a panel: the log matters when something has gone wrong,
                # and until then it is a wall of text on the screen a newcomer
                # is trying to read. Advanced Options has one open inline.
                ttk.Button(inner, text="🗒 Console", style='Aux.TButton',
                           command=self.open_console).pack(side='right',
                                                           padx=(12, 0))
            if console:
                # The console rides in the strip itself, in the gap between
                # the readings and the buttons. That space was empty on the
                # Advanced strip, and a console block above the strip took a
                # slice of height from the card grid to say the same thing.
                console_tile = tk.Frame(inner, bg=self.CLR_PANEL)
                console_tile.pack(side='left', fill='both', expand=True,
                                  padx=(0, 12))
                ttk.Label(console_tile, text="CONSOLE",
                          style='StripCap.TLabel').pack(anchor='w')
                box = tk.Frame(console_tile, bg=self.CLR_PANEL,
                               highlightthickness=1,
                               highlightbackground=self.CLR_BORDER)
                box.pack(fill='both', expand=True)
                self._make_console(box, height=3).pack(fill='both', expand=True,
                                                       padx=1, pady=1)

            if selection_chips:
                # The instruments the current selection needs, with their
                # lights. They belong here beside the temperature and the
                # pressure -- this is the bus reading, and the strip is where
                # the rack's state is read. Only the window that HAS a
                # selection builds this: on Advanced Options it was an empty
                # frame, and an empty frame packed to expand still takes its
                # share of the width from whatever else wanted it.
                chip_tile = tk.Frame(inner, bg=self.CLR_PANEL)
                chip_tile.pack(side='left', fill='x', expand=True)
                strip['caption'] = ttk.Label(chip_tile, text="",
                                             style='StripCap.TLabel')
                strip['caption'].pack(anchor='w')
                strip['chips_frame'] = tk.Frame(chip_tile, bg=self.CLR_PANEL)
                strip['chips_frame'].pack(anchor='w', fill='x')
                strip['cols'] = 4
                strip['big'] = True

            self._strips.append(strip)
            self._render_strip(strip)
            self._mark_strip_scanning(strip)
            return strip

        # GPIB link tile
        link_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        link_tile.pack(side='left', padx=(0, 12))
        ttk.Label(link_tile, text="GPIB / VISA", style='StripCap.TLabel').pack(anchor='w')
        strip['link_value'] = ttk.Label(link_tile, text="—", style='StripStat.TLabel',
                                        width=4, anchor='w')
        strip['link_value'].pack(anchor='w', fill='x')
        strip['link_sub'] = ttk.Label(link_tile, text="press Reload to scan",
                                      style='StripDim.TLabel')
        strip['link_sub'].pack(anchor='w')

        tk.Frame(inner, bg=self.CLR_BORDER, width=1).pack(side='left', fill='y', padx=(0, 12))

        # The dot key rides on the INSTRUMENTS caption line instead of taking
        # a column at the right edge. That returns the full strip width to
        # the chips, which is what lets them sit six-up in two rows instead
        # of four-up in three -- the strip is a third shorter for it.
        chip_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        chip_tile.pack(side='left', fill='x', expand=True)
        caption_row = tk.Frame(chip_tile, bg=self.CLR_PANEL)
        caption_row.pack(anchor='w', fill='x')
        ttk.Label(caption_row, text="INSTRUMENTS",
                  style='StripCap.TLabel').pack(side='left')
        self._build_dot_key(caption_row, inline=True).pack(side='left', padx=(14, 0))
        strip['chips_frame'] = tk.Frame(chip_tile, bg=self.CLR_PANEL)
        strip['chips_frame'].pack(anchor='w', fill='x')

        self._strips.append(strip)
        self._set_strip_chips(strip, chip_names)
        self._mark_strip_scanning(strip)
        return strip

    def _build_dot_key(self, parent, inline=False):
        """The colour legend for the instrument dots.

        Without it a grey dot reads as "broken" rather than "switched off",
        which is the question people actually ask of the strip. Protected
        instruments get a hollow ring, not a filled dot: PICA has no reading
        for them at all, so they are neither on nor off -- the word is
        "protected", matching the "(prot.)" the chip itself carries.

        `inline` drops the DOT KEY caption and lays the three items out in a
        single row, for sitting beside the INSTRUMENTS caption rather than in
        a column of its own.
        """
        legend = tk.Frame(parent, bg=self.CLR_PANEL)
        if not inline:
            ttk.Label(legend, text="DOT KEY", style='StripCap.TLabel').pack(anchor='w')
        keyrow = tk.Frame(legend, bg=self.CLR_PANEL)
        keyrow.pack(anchor='w')
        for glyph, colour, text in (("●", self.CLR_LIVE, "on"),
                                    ("●", self.CLR_TEXT_FAINT, "off"),
                                    ("○", self.CLR_TEXT_FAINT, "protected")):
            item = tk.Frame(keyrow, bg=self.CLR_PANEL)
            item.pack(side='left', padx=(0, 8))
            tk.Label(item, text=glyph, bg=self.CLR_PANEL, fg=colour,
                     font=self.FONT_STRIP).pack(side='left', padx=(0, 3))
            tk.Label(item, text=text, bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                     font=self.FONT_STRIP).pack(side='left')
        return legend

    def _chip_names_with_unknown(self):
        """Every known instrument, plus anything new the last scan found."""
        names = self._all_chip_names()
        unknown = sorted((self._last_scan or {}).get('unknown') or {})
        return names + [n for n in unknown if n not in names]

    def _mark_strip_scanning(self, strip):
        """Tell a strip built mid-scan that a scan is already in flight.

        start_scan can only speak to the strips that existed when it ran, and
        the startup scan begins before the Instrument Status window is built.
        Without this the new window comes up saying "press Reload to scan"
        while the scan it is waiting for is already running -- which reads as
        a window that never refreshes, rather than one that is about to.
        """
        if not self._scanning:
            return
        if strip.get('reload_btn'):
            strip['reload_btn'].config(state='disabled', text="⟳ Scanning…")
        if strip.get('link_sub'):
            strip['link_sub'].config(text="scanning the VISA bus…")
        if strip.get('temp_sub') and self._last_scan is None:
            strip['temp_sub'].config(text="scanning the VISA bus…")
        if strip.get('raw_note'):
            strip['raw_note'].config(text="waiting for the scan to finish…")

    def _set_strip_chips(self, strip, chip_names, render=True):
        """Rebuild one strip's chips, then repaint them from the last scan.

        A chip is in one of three modes: a known instrument (its dot follows
        the scan), a protected one (hollow ring, never probed), or one this
        scan found that the reference table does not list (always lit, marked
        "new" -- it answered, so it is certainly there).
        """
        frame = strip['chips_frame']
        for w in frame.winfo_children():
            w.destroy()
        strip['chips'] = {}
        strip['names'] = list(chip_names)
        known = set(self._all_chip_names())
        # Only the Instrument Status window marks what the current Quick
        # Select choice needs; a general list of the bus should not.
        needed = self._needed_instruments if strip.get('mark_needed') else set()
        # Six natural-width columns: the full list lands in two rows, which
        # is roughly the height the temperature tile already sets, and a
        # filtered handful stays on one. Six used to overrun the dot key at
        # the right edge; the key now sits on the caption line, so the whole
        # strip width belongs to the chips and six fits.
        cols = strip.get('cols', 6)
        if strip.get('big'):
            # The selection chips run at reading size, so how many fit on a
            # line depends on the window. Measure rather than guess: a fixed
            # count either wrapped a list that had room to stay on one line,
            # or ran the last chip off the edge of a narrow window. Width is 1
            # until the frame has been mapped, which is what the fallback is
            # for -- the next selection change lays it out for real.
            available = strip['chips_frame'].winfo_width()
            if available > 1:
                cols = max(2, min(len(chip_names) or 1, available // 150))
        for i, name in enumerate(chip_names):
            chip = tk.Frame(frame, bg=self.CLR_PANEL)
            chip.grid(row=i // cols, column=i % cols, sticky='w', padx=(0, 10))
            # The mark is an accent bar and bold text, not a background tint:
            # _render_strip repaints the label colour on every scan, and a
            # tint pale enough to sit under this palette does not read at all.
            # The bar needs an explicit height -- a childless frame packed
            # with fill='y' asks for none and collapses to nothing.
            marked = name in needed
            if marked:
                tk.Frame(chip, bg=self.CLR_ACCENT, width=4, height=14).pack(
                    side='left', fill='y', padx=(0, 5))
            if name in NEVER_PROBED_INSTRUMENTS:
                mode, glyph, colour = 'protected', "○", self.CLR_TEXT_FAINT
                text, fg = f"{name} (prot.)", self.CLR_TEXT_DIM
            elif name not in known:
                mode, glyph, colour = 'unknown', "●", self.CLR_LIVE
                text, fg = f"{name} (new)", self.CLR_TEXT
            else:
                mode, glyph, colour = None, "●", self.CLR_TEXT_FAINT
                text, fg = name, self.CLR_TEXT_DIM
            big = strip.get('big')
            dot = tk.Label(chip, text=glyph, bg=self.CLR_PANEL, fg=colour,
                           font=self.FONT_CHIP_DOT if big else self.FONT_DOT)
            dot.pack(side='left', padx=(0, 5))
            if big:
                label_font = self.FONT_CHIP_BOLD if marked else self.FONT_CHIP
            else:
                label_font = self.FONT_STRIP_BOLD if marked else self.FONT_STRIP
            lbl = tk.Label(chip, text=text, bg=self.CLR_PANEL, fg=fg,
                           font=label_font)
            lbl.pack(side='left')
            strip['chips'][name] = (dot, lbl, mode)
        if render:
            self._render_strip(strip)

    def _render_strip(self, strip):
        """Paint the most recent scan result into one strip or window.

        Every part is optional: a Quick Select strip has no chips and no link
        tile, and the Instrument Status window has no temperature tile.
        """
        r = self._last_scan
        if r is None:
            return
        if not r['available'] or r['error']:
            msg = r['error'] or "PyVISA not installed"
            for key in ('temp_value', 'pres_value', 'link_value'):
                if strip.get(key):
                    strip[key].config(text="—")
            for key in ('temp_sub', 'link_sub'):
                if strip.get(key):
                    strip[key].config(text=msg)
            if strip.get('status_btn'):
                strip['status_btn'].config(text="⚡ Instrument Status")
            if strip.get('raw'):
                self._schedule_raw(strip)
            return

        detected = r['detected']
        n_found = sum(1 for v in detected.values() if v)
        n_known = len(KNOWN_INSTRUMENTS)

        n_new = len(r.get('unknown') or {})
        new_tail = f" · +{n_new} new" if n_new else ""
        if strip.get('link_value'):
            strip['link_value'].config(
                text=str(n_found), foreground=self.CLR_OK if n_found else self.CLR_TEXT)
            strip['link_sub'].config(
                text=f"of {n_known} known{new_tail} · scan {r['timestamp']}")
        if strip.get('status_btn'):
            # The button carries the headline the chips used to: how much of
            # the rack answered, without any of it on screen.
            strip['status_btn'].config(
                text=f"⚡ Instrument Status   {n_found}/{n_known} on the bus{new_tail}")

        if strip.get('temp_value'):
            if r['temperature'] is not None:
                units = r.get('temp_units', 'K')
                strip['temp_value'].config(text=f"{r['temperature']:.2f} {units}")
                strip['temp_sub'].config(text=f"{r['temp_source']} · as of {r['timestamp']}")
            else:
                strip['temp_value'].config(text="—")
                strip['temp_sub'].config(text="no Lakeshore / Cryocon reading")

        if strip.get('pres_value'):
            if r.get('pressure') is not None:
                # Vacuum spans decades, so the tile is always in exponent
                # form -- "1.0E-06 mbar" reads at a glance where 0.000001
                # does not.
                units = r.get('pressure_units') or "mbar"
                strip['pres_value'].config(text=f"{r['pressure']:.1E} {units}")
                strip['pres_sub'].config(
                    text=f"{r['pressure_source']} · as of {r['timestamp']}")
            elif r.get('pressure_error'):
                # An underrange at the bottom of a pump-down is a state, not
                # a failure, so whatever the gauge said goes in the sub-line
                # verbatim rather than being flattened to "error".
                strip['pres_value'].config(text="—")
                strip['pres_sub'].config(text=f"{r['pressure_error']}")
            else:
                strip['pres_value'].config(text="—")
                strip['pres_sub'].config(text="no gauge configured")

        for name, (dot, lbl, mode) in strip['chips'].items():
            if mode == 'protected':
                # PICA never speaks to it, so it has no state to report. The
                # ring stays hollow and grey whatever the scan says.
                continue
            if mode == 'unknown' or detected.get(name):
                dot.config(fg=self.CLR_LIVE)
                lbl.config(fg=self.CLR_TEXT)
            else:
                dot.config(fg=self.CLR_TEXT_FAINT)
                lbl.config(fg=self.CLR_TEXT_DIM)

        if strip.get('raw'):
            self._schedule_raw(strip)

    def _blink_tick(self):
        """Pulse the dots of the instruments that answered the last scan.

        Only live chips blink -- absent and protected instruments keep their
        static colour, so motion in the strip always means "this one is on the
        bus right now". Rescheduled unconditionally; a destroyed root simply
        stops the chain when Tk raises on the next config.
        """
        self._blink_on = not self._blink_on
        colour = self.CLR_LIVE if self._blink_on else self.CLR_LIVE_DIM
        try:
            for strip in self._strips:
                for name in self._live_chips:
                    widgets = strip['chips'].get(name)
                    if widgets and widgets[2] != 'protected':
                        widgets[0].config(fg=colour)
            self.root.after(650, self._blink_tick)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------- body
    def _build_body(self):
        body = ttk.Frame(self.root)
        body.pack(side='top', fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_rail(body)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky='nsew', padx=(0, 12), pady=12)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_toolbar(right)
        # The main screen IS the Quick Select view. It carries no tab and no
        # name of its own -- it stopped being one view among several when
        # Browse All Modules became the Advanced Options window (Ctrl+Shift+A).
        self._build_quick(right)

    def _focus_main(self):
        """Bring the main (Quick Select) window back to the front."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _build_toolbar(self, parent):
        """Top-right icon buttons, carried over from the v1 launcher.

        v1 put the VISA/GPIB scanner and the Plotter Utility one click away
        instead of two menu levels down, and they are the two utilities that
        get used at the start and the end of every session. Same icons, same
        order, same tooltips, so muscle memory transfers.
        """
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky='e', pady=(0, 4))

        plotter_button = ttk.Button(toolbar, text="📈", width=3,
                                    style='Icon.TButton',
                                    command=launch_plotter_utility)
        plotter_button.pack(side='right', padx=(2, 0))
        self._add_tooltip(plotter_button, "Plotter Utility")

        gpib_button = ttk.Button(toolbar, text="📟", width=3,
                                 style='Icon.TButton',
                                 command=self._launch_gpib_scanner)
        gpib_button.pack(side='right', padx=(0, 2))
        self._add_tooltip(gpib_button, "VISA/GPIB Scanner")

    def _add_tooltip(self, widget, text):
        """Hover tooltip, same behaviour as the v1 launcher's."""
        tip = {'win': None}

        def show(_event):
            if tip['win'] is not None:
                return
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            win = Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(win, text=text, bg=self.CLR_PANEL2, fg=self.CLR_TEXT,
                     font=self.FONT_SMALL, bd=1, relief='solid',
                     padx=6, pady=2).pack()
            tip['win'] = win

        def hide(_event):
            if tip['win'] is not None:
                tip['win'].destroy()
                tip['win'] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)
        # A tooltip left parented to a destroyed button would linger on top of
        # every other window, so it is torn down with the button itself.
        widget.bind("<Destroy>", hide)

    def _build_rail(self, parent):
        # Rail is wide enough for the 140 px logo plus the v1-scale title type.
        rail = tk.Frame(parent, bg=self.CLR_PANEL, width=self.RAIL_WIDTH,
                        highlightthickness=1, highlightbackground=self.CLR_BORDER)
        rail.grid(row=0, column=0, sticky='nsew', padx=12, pady=12)
        # The rail's children are packed, so pack_propagate is what pins the
        # width -- grid_propagate does nothing here and the rail was growing to
        # whatever the console asked for (~600 px), eating the module area.
        rail.pack_propagate(False)

        pad = tk.Frame(rail, bg=self.CLR_PANEL)
        pad.pack(fill='both', expand=True, padx=16, pady=16)

        # UGC-DAE CSR logo (loaded after the window draws, like the v1 launcher)
        self.logo_image = None
        self.logo_canvas = tk.Canvas(pad, width=self.LOGO_SIZE, height=self.LOGO_SIZE,
                                     bg=self.CLR_PANEL, highlightthickness=0)
        self.logo_canvas.pack(anchor='w', pady=(0, 12))
        self.root.after(60, self._load_logo)

        # Wraps to the rail width rather than at hardcoded newlines, so the
        # institute name can carry a larger size without overflowing.
        ttk.Label(pad, text="UGC-DAE Consortium for Scientific Research, Mumbai Centre",
                  style='Institute.TLabel', justify='left',
                  wraplength=self.RAIL_WIDTH - 32).pack(anchor='w', pady=(0, 10))
        # Wordmark carries the largest type (v1 title size); the expansion sits
        # under it as a subtitle -- the acronym is what the eye lands on first.
        ttk.Label(pad, text="PICA", background=self.CLR_PANEL,
                  foreground=self.CLR_ACCENT, font=self.FONT_WORDMARK).pack(anchor='w')
        ttk.Label(pad, text="Python Instrument\nControl & Automation",
                  style='CardTitle.TLabel', justify='left').pack(anchor='w', pady=(0, 12))
        ttk.Label(pad, text="Prathamesh Deshmukh", style='InfoBold.TLabel').pack(anchor='w')
        ttk.Label(pad, text="Development", style='Dim.TLabel').pack(anchor='w', pady=(0, 6))
        ttk.Label(pad, text="Dr. Sudip Mukherjee", style='InfoBold.TLabel').pack(anchor='w')
        ttk.Label(pad, text="Vision & Guidance", style='Dim.TLabel').pack(anchor='w', pady=(0, 12))

        tk.Frame(pad, bg=self.CLR_BORDER, height=1).pack(fill='x', pady=8)

        links = tk.Frame(pad, bg=self.CLR_PANEL)
        links.pack(side='bottom', fill='x', pady=(6, 10))
        ttk.Label(links, text=f"Version {self.PROGRAM_VERSION}",
                  style='Faint.TLabel').pack(anchor='w', pady=(0, 4))
        link_font = font.Font(family='Segoe UI', size=self.FONT_SIZE_BASE - 2, underline=True)
        for text, action in [("Change Log", lambda: self._open_path(self.CHANGELOG_FILE)),
                             ("User Manual", lambda: self._open_path(self.MANUAL_FILE)),
                             ("GitHub", lambda: webbrowser.open(self.REPO_URL))]:
            lnk = tk.Label(links, text=text, bg=self.CLR_PANEL, fg=self.CLR_ACCENT,
                           font=link_font, cursor="hand2")
            lnk.pack(side='left', padx=(0, 12))
            lnk.bind("<Button-1>", lambda _e, a=action: a())


    def _logo_photo(self, size):
        """Institute logo scaled to fit a size x size box, or None.

        Shared by the rail (140 px) and the Advanced Options header (a
        smaller badge). The caller keeps the returned PhotoImage alive.
        """
        if not (PIL_AVAILABLE and os.path.exists(self.LOGO_FILE)):
            self.log("Logo not loaded: PIL unavailable or file missing.")
            return None
        try:
            img = Image.open(self.LOGO_FILE)
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception as e:
            self.log(f"ERROR: Failed to load logo. {e}")
            return None

    def _load_logo(self):
        """Load the institute logo into the rail canvas (deferred, like v1)."""
        photo = self._logo_photo(self.LOGO_SIZE)
        if photo is None:
            return
        self.logo_image = photo  # keep a reference
        self.logo_canvas.create_image(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                                      image=self.logo_image)

    # ---------------------------------------------------- Browse (card grid)
    def _build_family_legend(self, parent):
        """Key to the coloured strip down the left of each module row.

        It rides in the Advanced header rather than on a row of its own: it is
        three words, and a full-width band for them cost the card grid a line
        of cards on a laptop screen.
        """
        legend = tk.Frame(parent, bg=self.CLR_APP)
        for text, color in [("T Sensing", self.CLR_FAMILY_SENSING),
                            ("T Control", self.CLR_FAMILY_CONTROL),
                            ("Master Sequence", self.CLR_FAMILY_MASTER)]:
            item = tk.Frame(legend, bg=self.CLR_APP)
            item.pack(side='left', padx=(16, 0))
            tk.Frame(item, bg=color, width=10, height=10).pack(side='left', padx=(0, 5))
            tk.Label(item, text=text, bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                     font=self.FONT_SMALL).pack(side='left')
        return legend

    def _build_browse(self, parent):
        container = tk.Frame(parent, bg=self.CLR_APP)
        container.pack(fill='both', expand=True)

        # Scrollable canvas
        canvas = tk.Canvas(container, bg=self.CLR_APP, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.browse_frame = tk.Frame(canvas, bg=self.CLR_APP)
        self.browse_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=self.browse_frame, anchor="nw")

        def _on_canvas_resize(event):
            canvas.itemconfig(window_id, width=event.width)
            self._reflow_cards(event.width)
        canvas.bind("<Configure>", _on_canvas_resize)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left', fill='both', expand=True, padx=(12, 0), pady=(6, 8))
        scrollbar.pack(side='right', fill='y', pady=(6, 8))

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        container.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        container.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        self._render_cards()

    def _reflow_cards(self, width):
        """Re-lay the card grid if the available width changed the column count."""
        if self._rendering or not self._browse_alive():
            return
        cols = max(1, min(self.MAX_CARD_COLS, width // self.CARD_MIN_WIDTH))
        self._browse_width = width
        if cols != self._browse_cols:
            self._browse_cols = cols
            self._render_cards()

    def _browse_alive(self):
        """True while the Advanced Options card grid exists."""
        frame = getattr(self, 'browse_frame', None)
        try:
            return frame is not None and frame.winfo_exists()
        except tk.TclError:
            return False

    def _render_cards(self):
        # Guard: the masonry pass calls update_idletasks, which can dispatch the
        # canvas <Configure> and re-enter this method mid-build. The grid also
        # only exists while Advanced Options is open.
        if self._rendering or not self._browse_alive():
            return
        self._rendering = True
        try:
            self._render_cards_inner()
        finally:
            self._rendering = False
        # A resize that arrived while we were building is applied now.
        self._reflow_cards(self._browse_width)

    def _render_cards_inner(self):
        for w in self.browse_frame.winfo_children():
            w.destroy()
        cols = self._browse_cols
        for c in range(self.MAX_CARD_COLS):
            self.browse_frame.columnconfigure(
                c, weight=1 if c < cols else 0, uniform='cols' if c < cols else '')
        # Titles wrap inside their own column, not at a fixed 2-column width.
        wrap = max(180, self._browse_width // cols - 90)

        # Masonry layout. A grid row is as tall as its tallest card, so a
        # 1-module card next to a 7-module one left ~200 px of dead space under
        # it. Independent columns let each card sit right under the previous
        # one; every card goes to whichever column is currently shortest.
        columns = []
        for c in range(cols):
            col = tk.Frame(self.browse_frame, bg=self.CLR_APP)
            col.grid(row=0, column=c, sticky='new', padx=6)
            columns.append(col)
        for cat in CATALOG:
            self.browse_frame.update_idletasks()   # settle heights before choosing
            target = min(columns, key=lambda f: f.winfo_reqheight())
            self._make_card(target, cat, wrap).pack(fill='x', pady=(0, 12))

    def _rebuild_browse(self):
        if not self._browse_alive():
            self.log("Module list is rebuilt when Advanced Options opens.")
            return
        self._render_cards()
        self.log("Module list refreshed.")

    def _make_card(self, parent, cat, wrap=320):
        card = tk.Frame(parent, bg=self.CLR_PANEL, highlightthickness=1,
                        highlightbackground=self.CLR_BORDER)
        head = tk.Frame(card, bg=self.CLR_PANEL)
        head.pack(fill='x', padx=12, pady=(10, 6))

        top = tk.Frame(head, bg=self.CLR_PANEL)
        top.pack(fill='x')
        tk.Label(top, text=cat['category'], bg=self.CLR_PANEL, fg=self.CLR_TEXT,
                 font=self.FONT_CARD_TITLE, anchor='w', justify='left',
                 wraplength=wrap).pack(side='left', anchor='w')
        first_key = cat['modules'][0][1]
        ttk.Button(top, text="📁", style='Aux.TButton', width=3,
                   command=lambda k=first_key: self.open_script_folder(k)).pack(side='right')

        meta = tk.Frame(head, bg=self.CLR_PANEL)
        meta.pack(fill='x', pady=(6, 0))
        badge = tk.Label(meta, text=cat['type'].upper(), bg=self.CLR_ACCENT,
                         fg=self.CLR_TEXT_DARK, font=self.FONT_LABEL, padx=6, pady=2)
        badge.pack(side='left')
        tk.Label(meta, text=cat['instruments'], bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_CARD_META).pack(side='left', padx=(8, 0))
        if cat.get('experimental'):
            tk.Label(meta, text="EXPERIMENTAL", bg=self.CLR_WARN, fg=self.CLR_TEXT_LIGHT,
                     font=self.FONT_LABEL, padx=6, pady=2).pack(side='left', padx=(8, 0))

        tk.Frame(card, bg=self.CLR_BORDER, height=1).pack(fill='x', padx=0, pady=(6, 0))

        body = tk.Frame(card, bg=self.CLR_PANEL)
        body.pack(fill='x', padx=6, pady=6)
        family_colors = {'sensing': self.CLR_FAMILY_SENSING,
                         'control': self.CLR_FAMILY_CONTROL,
                         'master': self.CLR_FAMILY_MASTER}
        for label, key, family in cat['modules']:
            # No pady: consecutive rows butt together so the family strips form
            # one continuous bar instead of a dashed column of 4 px stubs.
            row = tk.Frame(body, bg=self.CLR_PANEL)
            row.pack(fill='x')
            strip = tk.Frame(row, bg=family_colors.get(family, self.CLR_PANEL2), width=5)
            strip.pack(side='left', fill='y')
            ttk.Button(row, text=label, style='Mod.TButton',
                       command=lambda k=key: self.launch_script(k)).pack(
                side='left', fill='x', expand=True)
        return card

    # ------------------------------------------------ Quick Select (main screen)
    # Three questions, asked in plain language and in the order a person
    # actually thinks in: what kind of measurement, which module, which
    # protocol. Each answer explains itself underneath, and the status strip
    # narrows to the instruments that answer needs.
    def _build_quick(self, parent):
        outer = tk.Frame(parent, bg=self.CLR_APP)
        outer.grid(row=1, column=0, sticky='nsew')

        card = tk.Frame(outer, bg=self.CLR_PANEL, highlightthickness=1,
                        highlightbackground=self.CLR_BORDER)
        card.pack(fill='both', expand=True)
        inner = tk.Frame(card, bg=self.CLR_PANEL)
        inner.pack(fill='both', expand=True, padx=26, pady=22)

        tk.Label(inner, text="What do you want to measure?", bg=self.CLR_PANEL,
                 fg=self.CLR_TEXT, font=self.FONT_TITLE).pack(anchor='w')
        tk.Label(inner,
                 text="Choose a category, a module and a protocol, then launch. "
                      "Each protocol is one PICA program for the ITMS "
                      "(Integrated Transport Measurement System) hardware.",
                 bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_SMALL).pack(anchor='w', pady=(2, 18))

        grid = tk.Frame(inner, bg=self.CLR_PANEL)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1, 2), weight=1, uniform='q')

        self.cat_var = tk.StringVar()
        self.mod_var = tk.StringVar()
        self.proto_var = tk.StringVar()

        self.cat_combo = self._combo_column(
            grid, 0, "1 · Category", self.cat_var,
            [c['category'] for c in QUICK_CATALOG], 'readonly',
            self._on_quick_category)
        self.mod_combo = self._combo_column(
            grid, 1, "2 · Module", self.mod_var, [], 'disabled',
            self._on_quick_module)
        self.proto_combo = self._combo_column(
            grid, 2, "3 · Protocol", self.proto_var, [], 'disabled',
            self._on_quick_protocol)

        # Description stack: one block per level, filled in as the user
        # narrows down and greyed out until then.
        desc = tk.Frame(inner, bg=self.CLR_PANEL)
        desc.pack(fill='both', expand=True, pady=(20, 18))
        self._desc_blocks = {
            'category': self._desc_block(desc, "CATEGORY"),
            'module': self._desc_block(desc, "MODULE"),
            'protocol': self._desc_block(desc, "PROTOCOL"),
        }
        # Body text wraps to whatever width the window currently gives it.
        desc.bind("<Configure>", self._rewrap_descriptions)

        self._reset_descriptions()

        actions = tk.Frame(inner, bg=self.CLR_PANEL)
        actions.pack(fill='x', side='bottom')
        self.launch_btn = ttk.Button(actions, text="▶  Launch Protocol",
                                     style='Launch.TButton', state='disabled',
                                     command=self._launch_selected)
        self.launch_btn.pack(side='left', fill='x', expand=True)
        self.folder_btn = ttk.Button(actions, text="📁 Folder", style='Aux.TButton',
                                     state='disabled', command=self._folder_selected)
        self.folder_btn.pack(side='left', padx=(8, 0))

        self._selected_key = None

    def _combo_column(self, parent, column, caption, var, values, state, handler):
        """One labelled dropdown in the three-across selector row."""
        col = tk.Frame(parent, bg=self.CLR_PANEL)
        col.grid(row=0, column=column, sticky='ew',
                 padx=(0 if column == 0 else 7, 0 if column == 2 else 7))
        tk.Label(col, text=caption, bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_LABEL).pack(anchor='w', pady=(0, 4))
        combo = ttk.Combobox(col, textvariable=var, state=state,
                             values=values, height=14, font=self.FONT_COMBO)
        combo.pack(fill='x', ipady=3)
        combo.bind("<<ComboboxSelected>>", handler)
        return combo

    def _desc_block(self, parent, caption):
        """A caption / title / body triple in the description stack."""
        block = tk.Frame(parent, bg=self.CLR_PANEL)
        block.pack(fill='x', anchor='w', pady=(0, 10))
        head = tk.Frame(block, bg=self.CLR_PANEL)
        head.pack(fill='x')
        tk.Label(head, text=caption, bg=self.CLR_PANEL, fg=self.CLR_TEXT_FAINT,
                 font=self.FONT_LABEL, width=14, anchor='w').pack(side='left')
        title = tk.Label(head, text="—", bg=self.CLR_PANEL, fg=self.CLR_TEXT,
                         font=self.FONT_INFO_BOLD, anchor='w', justify='left')
        title.pack(side='left', fill='x', expand=True)
        body = tk.Label(block, text="", bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                        font=self.FONT_SMALL, anchor='w', justify='left',
                        wraplength=700)
        body.pack(fill='x', padx=(14 * 8, 0))
        return {'title': title, 'body': body}

    def _rewrap_descriptions(self, event):
        for block in self._desc_blocks.values():
            block['body'].config(wraplength=max(280, event.width - 130))

    def _set_desc(self, level, title, text):
        block = self._desc_blocks[level]
        block['title'].config(text=title or "—",
                              fg=self.CLR_TEXT if title else self.CLR_TEXT_FAINT)
        block['body'].config(text=text or "")

    def _reset_descriptions(self):
        self._set_desc('category', None, None)
        self._set_desc('module', None, None)
        self._set_desc('protocol', None, None)
        row = getattr(self, 'quick_strip', None)
        if row is not None and row.get('caption') is not None:
            self._set_strip_chips(row, [])
            row['caption'].config(text="")

    def _on_quick_category(self, _event=None):
        idx = self.cat_combo.current()
        if idx < 0:
            return
        cat = QUICK_CATALOG[idx]
        self.mod_combo.config(state='readonly',
                              values=[m['name'] for m in cat['modules']])
        self.mod_var.set('')
        self.proto_var.set('')
        self.proto_combo.config(state='disabled', values=[])
        self._reset_descriptions()
        self._set_desc('category', cat['category'], cat['desc'])
        self._set_quick_actions(False)
        self._update_needed_instruments(cat)
        # A category with a single module is not a choice, so make it for the
        # user rather than asking them to click through it.
        if len(cat['modules']) == 1:
            self.mod_combo.current(0)
            self._on_quick_module()

    def _on_quick_module(self, _event=None):
        cat_idx, mod_idx = self.cat_combo.current(), self.mod_combo.current()
        if cat_idx < 0 or mod_idx < 0:
            return
        cat = QUICK_CATALOG[cat_idx]
        module = cat['modules'][mod_idx]
        self.proto_combo.config(state='readonly',
                                values=[p['label'] for p in module['protocols']])
        self.proto_var.set('')
        self._set_desc('module', module['name'], module['desc'])
        self._set_desc('protocol', None, None)
        self._set_quick_actions(False)
        self._update_needed_instruments(cat, module)

    def _on_quick_protocol(self, _event=None):
        cat_idx, mod_idx = self.cat_combo.current(), self.mod_combo.current()
        proto_idx = self.proto_combo.current()
        if cat_idx < 0 or mod_idx < 0 or proto_idx < 0:
            return
        module = QUICK_CATALOG[cat_idx]['modules'][mod_idx]
        proto = module['protocols'][proto_idx]
        self._selected_key = proto['key']
        tag = {'control': "  ·  T Control",
               'sensing': "  ·  T Sensing",
               'master': "  ·  Master Sequence"}.get(proto['family'], "")
        self._set_desc('protocol', proto['label'] + tag, proto['desc'])
        self._update_needed_instruments(QUICK_CATALOG[cat_idx], module, proto)
        self._set_quick_actions(True)

    def _update_needed_instruments(self, cat, module=None, proto=None):
        """Record which instruments the current selection needs.

        The set narrows one step at a time: a category counts everything its
        modules can use, a module counts its own list, and a protocol counts
        only what that script talks to. Nothing is ever empty mid-choice, so
        the row never blinks out between clicks.

        The row under the Quick Select descriptions shows the set with each
        instrument's light; the Instrument Status window marks the same names.

        FUTURE (pressure): a pressure gauge is picked up here like any other
        instrument -- name it in the module's 'instruments' list and nothing
        else has to change.
        """
        if proto is not None and module is not None:
            self._needed_instruments = set(_protocol_instruments(module, proto))
        elif module is not None:
            self._needed_instruments = set(module['instruments'])
        else:
            self._needed_instruments = set()
            for m in cat['modules']:
                self._needed_instruments |= set(m['instruments'])
        # Keep the reference-table order so chips do not jump around as the
        # selection narrows.
        names = [n for n in self._all_chip_names()
                 if n in self._needed_instruments]
        self._set_strip_chips(self.quick_strip, names)
        self.quick_strip['caption'].config(
            text="INSTRUMENTS FOR THIS PROTOCOL" if names else "")
        # Repaint the marks if the status window happens to be open behind us.
        strip = getattr(self, '_status_strip', None)
        if strip is not None:
            self._set_strip_chips(strip, self._chip_names_with_unknown())

    def _set_quick_actions(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for b in (self.launch_btn, self.folder_btn):
            b.config(state=state)
        if not enabled:
            self._selected_key = None

    def _launch_selected(self):
        """Launch the chosen protocol and repaint the status panels.

        No window is opened here. The Instrument Status window comes up once,
        when the launcher starts; putting it in front of the user again on
        every single measurement launch only buried the module they had just
        asked for.

        The bottom panel of every open window is repainted -- NOT rescanned. A
        module that has just been launched is opening its own connections
        right now, and the launcher going back on the bus to confirm what it
        already knows is exactly the interference the status design exists to
        avoid. Reload is there for a deliberate rescan.
        """
        if not self._selected_key:
            return
        self.launch_script(self._selected_key)
        self._refresh_strips()

    def _folder_selected(self):
        if self._selected_key:
            self.open_script_folder(self._selected_key)

    # ----------------------------------------------- Instrument Status window
    # The chips, the dot key and the raw VISA/GPIB scan in one place, opened
    # from the button on the Quick Select strip. This is the VISA scanner the
    # launcher itself runs: the same read-only pass that lights the dots also
    # fills the table, so opening this window costs the bus nothing. The
    # standalone SCPI scanner is unchanged and one button away.
    def open_instrument_status(self):
        """Open the Instrument Status window, or raise the one already open."""
        if self._status_win is not None and self._status_win.winfo_exists():
            self._status_win.deiconify()
            self._status_win.lift()
            self._status_win.focus_force()
            return

        win = Toplevel(self.root)
        win.title("PICA — Instrument Status")
        win.configure(bg=self.CLR_APP)
        win.geometry("760x560")
        win.protocol("WM_DELETE_WINDOW", self._close_instrument_status)
        self._status_win = win

        strip = {'chips': {}, 'names': []}

        head = tk.Frame(win, bg=self.CLR_APP)
        head.pack(fill='x', padx=18, pady=(14, 6))
        tk.Label(head, text="Instrument Status", bg=self.CLR_APP,
                 fg=self.CLR_ACCENT, font=self.FONT_TITLE).pack(side='left')
        strip['reload_btn'] = ttk.Button(head, text="⟳ Reload", style='Aux.TButton',
                                         command=self.start_scan)
        strip['reload_btn'].pack(side='right')
        ttk.Button(head, text="📟 Full VISA / GPIB Scanner", style='Aux.TButton',
                   command=self._launch_gpib_scanner).pack(side='right', padx=(0, 8))

        tk.Label(win, text="One read-only pass over the bus, taken at startup and "
                           "whenever you press Reload. Every address that answers is "
                           "listed, whether or not PICA knows it. Nothing is polled "
                           "in the background, so a running measurement is never "
                           "disturbed.",
                 bg=self.CLR_APP, fg=self.CLR_TEXT_DIM, font=self.FONT_SMALL,
                 justify='left', wraplength=820).pack(anchor='w', padx=18, pady=(0, 10))

        # --- known instruments, with their lights -------------------------
        panel = tk.Frame(win, bg=self.CLR_PANEL, highlightthickness=1,
                         highlightbackground=self.CLR_BORDER)
        panel.pack(fill='x', padx=18)
        pad = tk.Frame(panel, bg=self.CLR_PANEL)
        pad.pack(fill='x', padx=14, pady=12)

        cap = tk.Frame(pad, bg=self.CLR_PANEL)
        cap.pack(fill='x')
        ttk.Label(cap, text="KNOWN INSTRUMENTS", style='StripCap.TLabel').pack(side='left')
        strip['link_value'] = ttk.Label(cap, text="—", style='StripStat.TLabel',
                                        width=4, anchor='e')
        strip['link_value'].pack(side='right')
        strip['link_sub'] = ttk.Label(cap, text="press Reload to scan",
                                      style='StripDim.TLabel')
        strip['link_sub'].pack(side='right', padx=(0, 8))

        strip['chips_frame'] = tk.Frame(pad, bg=self.CLR_PANEL)
        strip['chips_frame'].pack(fill='x', pady=(8, 8))

        keys = tk.Frame(pad, bg=self.CLR_PANEL)
        keys.pack(fill='x')
        self._build_dot_key(keys).pack(side='left')
        if self._needed_instruments:
            tint = tk.Frame(keys, bg=self.CLR_PANEL)
            tint.pack(side='left', padx=(24, 0))
            ttk.Label(tint, text="HIGHLIGHT", style='StripCap.TLabel').pack(anchor='w')
            row = tk.Frame(tint, bg=self.CLR_PANEL)
            row.pack(anchor='w')
            tk.Frame(row, bg=self.CLR_ACCENT, width=4, height=14).pack(
                side='left', padx=(0, 5))
            tk.Label(row, text="needed by your Quick Select choice",
                     bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                     font=self.FONT_STRIP_BOLD).pack(side='left')

        # --- raw scan table ------------------------------------------------
        tk.Label(win, text="VISA / GPIB SCAN", bg=self.CLR_APP,
                 fg=self.CLR_TEXT_FAINT, font=self.FONT_LABEL).pack(
            anchor='w', padx=18, pady=(14, 4))

        table_wrap = tk.Frame(win, bg=self.CLR_PANEL, highlightthickness=1,
                              highlightbackground=self.CLR_BORDER)
        table_wrap.pack(fill='both', expand=True, padx=18, pady=(0, 8))
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)
        strip['raw'] = tk.Text(
            table_wrap, state='disabled', bg=self.CLR_PANEL2, fg=self.CLR_TEXT,
            font=self.FONT_MONO, wrap='none', bd=0, relief='flat', height=12)
        strip['raw'].grid(row=0, column=0, sticky='nsew', padx=1, pady=1)
        vbar = ttk.Scrollbar(table_wrap, orient='vertical',
                             command=strip['raw'].yview)
        vbar.grid(row=0, column=1, sticky='ns')
        hbar = ttk.Scrollbar(table_wrap, orient='horizontal',
                             command=strip['raw'].xview)
        hbar.grid(row=1, column=0, sticky='ew')
        strip['raw'].config(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        strip['raw_note'] = tk.Label(
            win, text="", bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
            font=self.FONT_SMALL, anchor='w', justify='left', wraplength=820)
        strip['raw_note'].pack(fill='x', padx=18, pady=(0, 14))

        strip['auto_names'] = True
        strip['mark_needed'] = True
        self._strips.append(strip)
        self._status_strip = strip
        self._set_strip_chips(strip, self._chip_names_with_unknown())
        self._mark_strip_scanning(strip)
        self.log("Instrument Status window opened.")

    def _close_instrument_status(self):
        strip = getattr(self, '_status_strip', None)
        if strip in self._strips:
            self._strips.remove(strip)
        self._status_strip = None
        if self._status_win is not None:
            self._status_win.destroy()
        self._status_win = None

    def _schedule_raw(self, strip):
        """Fill the raw scan table half a second after the lights settle.

        The identification pass comes first and the table second, in that
        order and with a visible beat between them, so it is obvious which
        answer came from where -- the same half second on which the
        standalone scanner is started at launch.
        """
        strip['raw_note'].config(text="Reading the bus listing…")
        self.root.after(500, lambda: self._render_raw(strip))

    def _render_raw(self, strip):
        """Render the resource-by-resource listing of the last scan.

        Everything shown here was collected by the scan that lit the dots. No
        address is opened a second time to build this table.
        """
        widget = strip.get('raw')
        r = self._last_scan
        if widget is None or r is None:
            return
        try:
            widget.config(state='normal')
            widget.delete('1.0', 'end')
            if not r['available'] or r['error']:
                widget.insert('end', (r['error'] or "PyVISA not installed") + "\n")
            else:
                by_resource = {res: name for name, res in r['detected'].items() if res}
                by_resource.update({res: f"{name} (new)"
                                    for name, res in (r.get('unknown') or {}).items()})
                if r.get('novocontrol'):
                    by_resource[r['novocontrol']] = NOVOCONTROL_CHIP
                skipped = set(r.get('skipped') or ())
                widget.insert('end', f"{'RESOURCE':<28}{'IDENTIFIED AS':<36}REPLY\n")
                widget.insert('end', "-" * 108 + "\n")
                for res in r['resources']:
                    if res in skipped:
                        name = "—"
                        reply = r.get('skip_reason', {}).get(res, "not probed")
                    else:
                        name = by_resource.get(res, "—")
                        reply = r.get('idns', {}).get(res) or "no reply"
                    widget.insert('end', f"{res:<28}{name:<36}{reply}\n")
                if not r['resources']:
                    widget.insert('end', "No VISA resources found.\n")
            widget.config(state='disabled')
            n_skipped = len(r.get('skipped') or ())
            strip['raw_note'].config(
                text=f"{len(r['resources'])} VISA resource(s), {n_skipped} not probed · "
                     f"scan {r['timestamp']}. Use the full VISA / GPIB Scanner above "
                     f"for the address guide and for sending SCPI by hand.")
        except tk.TclError:
            pass

    # ------------------------------------------------- Advanced Options window
    # This was the "Browse All Modules" tab. It is now a window of its own,
    # opened from Tools / View or with Ctrl+Shift+A, and it is unfiltered on
    # purpose: the full card grid with every module and its range exactly as
    # before, and every instrument chip on the strip whether or not the module
    # in front of you uses it. Quick Select is the guided route for a new user;
    # this is the one for someone who already knows the module name.
    def open_advanced(self):
        if self._adv_win is not None and self._adv_win.winfo_exists():
            self._adv_win.deiconify()
            self._adv_win.lift()
            self._adv_win.focus_force()
            return

        win = Toplevel(self.root)
        win.title("PICA — Advanced Options")
        win.configure(bg=self.CLR_APP)
        win.geometry("1400x900")
        # Opens maximised, like the main launcher. This is the window whose
        # whole job is fitting as many module cards on screen at once as it
        # can, and a fixed 1400x900 frame on a 1080p screen threw away a third
        # of the height it could have used -- the geometry above is only what
        # it restores to.
        try:
            win.state('zoomed')
        except tk.TclError:
            pass
        win.protocol("WM_DELETE_WINDOW", self._close_advanced)
        self._adv_win = win

        # Letterhead-style band, as on the main window's rail: institute logo
        # with the institute name and the programme's full name stacked beside
        # it, the window title and family legend on the right, and a rule
        # under the whole band. One accent headline only -- the window title;
        # the branding is set in the body colours so the two do not compete.
        head = tk.Frame(win, bg=self.CLR_APP)
        head.pack(fill='x', padx=18, pady=(12, 0))

        brand = tk.Frame(head, bg=self.CLR_APP)
        brand.pack(side='left', fill='y')
        self._adv_logo_image = self._logo_photo(self.ADV_LOGO_SIZE)
        if self._adv_logo_image is not None:
            tk.Label(brand, image=self._adv_logo_image, bg=self.CLR_APP
                     ).pack(side='left', padx=(0, 14))
        words = tk.Frame(brand, bg=self.CLR_APP)
        words.pack(side='left', fill='y')
        # Two-line stack centred on the logo: inner frame packed with
        # expand=True so the lines sit in the middle of the logo's height.
        lines = tk.Frame(words, bg=self.CLR_APP)
        lines.pack(expand=True)
        tk.Label(lines, text="UGC-DAE Consortium for Scientific Research",
                 bg=self.CLR_APP, fg=self.CLR_TEXT,
                 font=self.FONT_CARD).pack(anchor='w')
        tk.Label(lines, text="Mumbai Centre  |  PICA — Python Instrument "
                             "Control & Automation",
                 bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_BASE).pack(anchor='w')

        title_blk = tk.Frame(head, bg=self.CLR_APP)
        title_blk.pack(side='right', fill='y')
        tk.Label(title_blk, text="Advanced Options", bg=self.CLR_APP,
                 fg=self.CLR_ACCENT, font=self.FONT_TITLE).pack(anchor='e')
        self._build_family_legend(title_blk).pack(anchor='e', pady=(2, 0))

        tk.Frame(win, bg=self.CLR_BORDER, height=1).pack(fill='x', padx=18,
                                                         pady=(10, 6))
        tk.Label(win,
                 text="Every module PICA ships, grouped by measurement range and "
                      "instrument. Quick Select in the main window is the guided route.",
                 bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_SMALL).pack(anchor='w', padx=18)

        # Strip first: packed to the bottom edge before the card grid claims
        # the rest of the height. It is the compact strip -- readings and the
        # Instrument Status button. Three rows of chips plus their caption and
        # dot key stood nearly 80 px tall along the bottom of the one window
        # whose whole job is showing as many cards at once as will fit, and
        # the window they open holds the same list with more room for it.
        # Console, always open and inside the strip: this is the expert
        # window, and the log is what you read when a module refuses to
        # launch. It fills the gap the strip had between its readings and its
        # buttons, so it costs the card grid nothing.
        # The button opens the full console window; the strip console is a
        # three-line tail of the same log, for reading in place.
        self._adv_strip = self._build_status_strip(win, None, console=True,
                                                   console_button=True)
        self._build_browse(win)
        self.log("Advanced Options opened.")
        # The standalone scanner comes up with this window by default: this is
        # the window for someone who came to look at the rack, and the
        # scanner's address guide is what they reach for. It runs its own pass
        # over the bus, so it follows a moment later rather than starting
        # while the window is still drawing.
        self.root.after(400, self._auto_launch_gpib_scanner)

    def _close_advanced(self):
        """Tear down the Advanced window and un-register its status strip."""
        strip = getattr(self, '_adv_strip', None)
        if strip in self._strips:
            self._strips.remove(strip)
        self._adv_strip = None
        # The card grid lives in this window; drop the reference so a stray
        # refresh cannot reach into a destroyed widget tree.
        self.browse_frame = None
        if self._adv_win is not None:
            self._adv_win.destroy()
        self._adv_win = None

    # ------------------------------------------------------- scan lifecycle
    def start_scan(self):
        """Trigger a one-shot instrument scan (startup or manual reload)."""
        if self._scanning:
            return
        # Guard: never scan the bus while a launched measurement is still running.
        self.launched_processes = [p for p in self.launched_processes if p.is_alive()]
        if self.launched_processes:
            proceed = messagebox.askyesno(
                "Measurement in progress",
                "A measurement launched from this window is still running.\n\n"
                "Reloading the instrument status sends commands over the GPIB bus "
                "and can disturb a running measurement.\n\n"
                "Reload anyway?",
                icon='warning', default='no')
            if not proceed:
                self.log("Reload cancelled — measurement still running.")
                return

        self._scanning = True
        for strip in self._strips:
            if strip.get('reload_btn'):
                strip['reload_btn'].config(state='disabled', text="⟳ Scanning…")
            if strip.get('link_sub'):
                strip['link_sub'].config(text="scanning the VISA bus…")
            # The Quick Select strip has no GPIB tile to put that line in, so
            # the temperature tile carries it -- but only until there is a
            # reading worth keeping on screen.
            elif strip.get('temp_sub') and self._last_scan is None:
                strip['temp_sub'].config(text="scanning the VISA bus…")
        self.log("Scanning instruments (startup/reload only)…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        results = scan_instruments(skip_addresses=self._session_protected)
        # Marshal back to the Tk thread.
        self.root.after(0, lambda: self._apply_scan(results))

    def _apply_scan(self, r):
        self._scanning = False
        self._last_scan = r
        for strip in self._strips:
            if strip.get('reload_btn'):
                strip['reload_btn'].config(state='normal', text="⟳ Reload")

        if not r['available']:
            self._refresh_strips()
            self.log("PyVISA not available — instrument status unavailable.")
            return
        if r['error']:
            self._refresh_strips()
            self.log(f"Scan error: {r['error']}")
            return

        # A Novocontrol mainframe answered this scan, so its address was not
        # yet in the protected list. Record it -- in memory and on disk -- so
        # this launcher never sends it anything again.
        self._record_protected(r)

        # Which chips blink is strip-independent, so it is decided once here
        # and each strip picks up whichever of those names it happens to show.
        detected = r['detected']
        self._live_chips = ({name for name, res in detected.items() if res}
                            | set(r.get('unknown') or {}))
        self._refresh_strips()

        # Anything that answered but is not in KNOWN_INSTRUMENTS is worth
        # saying out loud once per scan: it is either a new instrument on the
        # rack or one whose reply has drifted from its reference pattern.
        for name, res in sorted((r.get('unknown') or {}).items()):
            self.log(f"New instrument at {res}: {name} — not in the known list.")

        n_found = len(self._live_chips)
        n_known = len(KNOWN_INSTRUMENTS)
        # No "N not probed" tail here: on the Quick Select console it reads
        # like a warning about instruments PICA failed to reach, when it only
        # ever counts serial ports and protected addresses that are skipped
        # by design. The Instrument Status window still spells it out per
        # resource, which is where somebody auditing the bus is looking.
        self.log(f"Scan complete — {n_found}/{n_known} instruments detected "
                 f"across {len(r['resources'])} VISA resource(s).")

        # The standalone scanner runs its own pass over the bus, so it is
        # started only once the launcher has finished its own -- half a second
        # after, the same beat the raw table appears on.


    def _refresh_strips(self):
        """Render the last scan result into every strip that is alive.

        A strip flagged 'auto_names' shows the whole instrument list, so it is
        rebuilt first: that is how an instrument the reference table has never
        heard of gets a chip without anyone editing the table.
        """
        for strip in list(self._strips):
            try:
                if strip.get('auto_names'):
                    self._set_strip_chips(strip, self._chip_names_with_unknown(),
                                          render=False)
                self._render_strip(strip)
            except tk.TclError:
                self._strips.remove(strip)

    def _record_protected(self, r):
        """Learn the Novocontrol's address from a scan and never probe it again.

        The Alpha answers *IDN? like any IEEE-488.2 device, so it can be
        identified once -- but it uses a command-ack protocol of its own and
        unsolicited bus traffic during a measurement degrades point accuracy.
        One identification, then permanent silence.
        """
        new = set(r.get('protect') or ())
        if r.get('novocontrol'):
            for strip in self._strips:
                chip = strip['chips'].get(NOVOCONTROL_CHIP)
                if chip:
                    chip[1].config(text=f"{NOVOCONTROL_CHIP} (protected)",
                                   fg=self.CLR_TEXT)
            self.log(f"Novocontrol mainframe identified at "
                     f"{r['novocontrol']} — it will not be probed again.")
        new -= self._session_protected
        if not new:
            return
        self._session_protected |= new
        listed = ", ".join(str(a) for a in sorted(new))
        if save_protected_addresses(self._session_protected):
            self.log(f"GPIB address(es) {listed} added to the protected list "
                     f"({os.path.basename(PROTECTED_ADDRESS_FILE)}).")
        else:
            self.log(f"GPIB address(es) {listed} protected for this session "
                     f"(could not write {PROTECTED_ADDRESS_FILE}).")

    # ----------------------------------------------------------- launching
    def launch_script(self, script_key, argv=None):
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path:
            self.log(f"ERROR: Unknown module key '{script_key}'.")
            messagebox.showerror("Not Found", f"Module '{script_key}' is not defined.")
            return
        abs_path = os.path.abspath(script_path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Script not found: {abs_path}")
            messagebox.showerror("File Not Found", f"Script not found:\n\n{abs_path}")
            return
        try:
            proc = Process(target=run_script_process, args=(abs_path, argv or []))
            proc.start()
            self.launched_processes.append(proc)
            self.log(f"Launched '{os.path.basename(abs_path)}' in a new process.")
        except Exception as e:
            self.log(f"ERROR: Failed to launch. {e}")
            messagebox.showerror("Launch Error", f"Could not launch the module:\n\n{e}")

    # -------------------------------------------------------- File menu actions
    DATA_FILETYPES = [("Data files", "*.txt *.csv *.dat"),
                      ("Text files", "*.txt"),
                      ("CSV files", "*.csv"),
                      ("All files", "*.*")]

    def _ask_data_files(self, title, multiple=False):
        """Common data-file picker; remembers the last folder used."""
        ask = filedialog.askopenfilenames if multiple else filedialog.askopenfilename
        chosen = ask(title=title, initialdir=self._last_data_dir,
                     filetypes=self.DATA_FILETYPES)
        if not chosen:
            return [] if multiple else None
        first = chosen[0] if multiple else chosen
        self._last_data_dir = os.path.dirname(first)
        return list(chosen) if multiple else chosen

    def open_data_file(self):
        """Open a data file in whatever application Windows associates with it."""
        path = self._ask_data_files("Open data file")
        if path:
            self.log(f"Opening '{os.path.basename(path)}'.")
            self._open_path(path)

    def open_data_in_editor(self):
        """Open a data file in Notepad (a plain text editor on other platforms)."""
        path = self._ask_data_files("Open data file in text editor")
        if not path:
            return
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["notepad.exe", os.path.abspath(path)])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", "-e", os.path.abspath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.abspath(path)])
            self.log(f"Opened '{os.path.basename(path)}' in a text editor.")
        except Exception as e:
            self.log(f"ERROR: Could not open the text editor. {e}")
            messagebox.showerror("Error", f"Could not open a text editor:\n\n{e}")

    def open_data_as_graph(self):
        """Plot one or more data files by handing them to the Plotter Utility."""
        paths = self._ask_data_files("Open data as graph", multiple=True)
        if not paths:
            return
        self.log(f"Plotting {len(paths)} file(s) in the Plotter Utility.")
        self.launch_script("Plotter Utility", argv=[os.path.abspath(p) for p in paths])

    SEQ_FILETYPES = [("PPMS sequence files", "*.seq"),
                     ("Sequence-like text", "*.seq *.txt *.dat"),
                     ("All files", "*.*")]
    PPMS_DATA_FILETYPES = [("Quantum Design data", "*.dat"),
                           ("Data files", "*.dat *.txt *.csv"),
                           ("All files", "*.*")]

    def _ask_one_file(self, title, filetypes):
        """Single-file picker that remembers the folder the user was last in."""
        chosen = filedialog.askopenfilename(
            title=title, initialdir=self._last_data_dir, filetypes=filetypes)
        if chosen:
            self._last_data_dir = os.path.dirname(chosen)
        return chosen

    def open_sequence_file(self):
        """Open a PPMS .seq file in the Sequence Visualizer."""
        path = self._ask_one_file("Open PPMS sequence file", self.SEQ_FILETYPES)
        if not path:
            return
        self.log(f"Opening '{os.path.basename(path)}' in the "
                 f"Sequence Visualizer.")
        self.launch_script("Sequence Visualizer", argv=[os.path.abspath(path)])

    def open_ppms_data_as_plot(self):
        """Plot Quantum Design PPMS/VSM .dat files in the PPMS Plotter."""
        paths = filedialog.askopenfilenames(
            title="Open PPMS data as plot", initialdir=self._last_data_dir,
            filetypes=self.PPMS_DATA_FILETYPES)
        if not paths:
            return
        self._last_data_dir = os.path.dirname(paths[0])
        self.log(f"Plotting {len(paths)} PPMS file(s) in the PPMS Plotter.")
        self.launch_script("PPMS Plotter Utility",
                           argv=[os.path.abspath(p) for p in paths])

    def open_pressure_gauge_dialog(self):
        """Name the address of the Pfeiffer TPG 361, or forget it again.

        This is the only place PICA is ever told about a serial or socket
        instrument. The dialog offers the ASRL resources VISA can see but
        opens none of them: picking one from the list is a choice, not a
        probe, and the first thing ever sent down that port is the gauge's
        own UNI at the next scan. An IP address (Ethernet, TCP 8000) or a
        COM port VISA does not enumerate can be typed in by hand. "Find on
        LAN" reads the ARP table for Pfeiffer MACs and offers those IPs --
        it sends nothing to anything.
        """
        current = load_pressure_gauge() or {}

        win = tk.Toplevel(self.root)
        win.title("PICA — Pressure Gauge")
        win.configure(bg=self.CLR_APP)
        win.transient(self.root)
        win.resizable(False, False)

        pad = tk.Frame(win, bg=self.CLR_APP)
        pad.pack(fill='both', expand=True, padx=18, pady=16)

        ttk.Label(pad, text="Pfeiffer TPG 361 SingleGauge",
                  style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(pad, style='Dim.TLabel', justify='left',
                  text="USB or Ethernet — the gauge is never on GPIB, so it\n"
                       "cannot be found by a bus scan. Name its address and the\n"
                       "status strip reads the pressure on every scan; leave it\n"
                       "empty and PICA never touches a serial port at all.\n"
                       "USB: pick the FTDI 'USB Serial Port' below.\n"
                       "Ethernet: type its IP (TCP port 8000 is implied), or\n"
                       "press Find on LAN.").pack(
            anchor='w', pady=(2, 12))

        ttk.Label(pad, text="ADDRESS — COM port, IP address or VISA resource",
                  style='Faint.TLabel').pack(anchor='w')
        port_var = tk.StringVar(value=current.get('resource', ""))
        port_cb = ttk.Combobox(pad, textvariable=port_var, height=8,
                               font=self.FONT_COMBO, width=34)
        port_cb.pack(anchor='w', fill='x', ipady=3, pady=(2, 4))

        def _find_on_lan():
            try:
                out = subprocess.run(["arp", "-a"], capture_output=True,
                                     text=True, timeout=10).stdout
            except Exception as e:
                self.log(f"Could not read the ARP table: {e}")
                return
            hosts = pfeiffer_hosts_from_arp(out)
            if not hosts:
                self.log("No Pfeiffer MAC (00-A0-41) in the ARP table. Ping "
                         "the gauge once, read the IP off its front panel, or "
                         "use Find on LAN in the pressure-log module, which "
                         "can sweep the subnet.")
                return
            choices = [f"TCPIP0::{ip}::{GAUGE_TCP_PORT}::SOCKET" for ip in hosts]
            port_cb['values'] = list(choices) + list(port_cb['values'])
            port_var.set(choices[0])
            self.log(f"Pfeiffer device(s) on the LAN: {', '.join(hosts)}. "
                     "Save to read the gauge on the next scan.")

        ttk.Button(pad, text="Find on LAN (ARP table, sends nothing)",
                   style='Aux.TButton', command=_find_on_lan).pack(
            anchor='w', pady=(0, 10))
        try:
            rm = pyvisa.ResourceManager()
            try:
                found = [r for r in rm.list_resources()
                         if r.upper().startswith("ASRL")]
            finally:
                rm.close()
            port_cb['values'] = gauge_port_choices(found)
            if not found:
                # Said out loud rather than left as an empty dropdown: an
                # empty list here means the cable or its USB-serial driver,
                # not a PICA problem, and there is nothing to type either.
                ttk.Label(pad, style='Dim.TLabel', justify='left',
                          text="No serial ports on this computer. On USB, plug\n"
                               "the TPG 361 in (or install the FTDI VCP driver)\n"
                               "and reopen this window. On Ethernet, type its\n"
                               "IP address above.").pack(anchor='w', pady=(0, 10))
                self.log("No serial ports found — type an IP address for "
                         "Ethernet, or plug in the USB cable.")
        except Exception as e:
            self.log(f"Could not list serial resources: {e}")

        ttk.Label(pad, text="BAUD RATE (USB / serial only, ignored over Ethernet)",
                  style='Faint.TLabel').pack(anchor='w')
        baud_var = tk.StringVar(value=str(current.get('baud', 9600)))
        baud_cb = ttk.Combobox(pad, textvariable=baud_var, state='readonly',
                               values=("9600", "19200", "38400", "57600", "115200"),
                               font=self.FONT_COMBO, width=34)
        baud_cb.pack(anchor='w', fill='x', ipady=3, pady=(2, 16))

        row = tk.Frame(pad, bg=self.CLR_APP)
        row.pack(fill='x')

        def _save():
            resource = normalise_gauge_resource(
                gauge_resource_from_choice(port_var.get()))
            if not resource:
                self.log("No address given — pressure gauge left unset.")
                win.destroy()
                return
            if not save_pressure_gauge(resource, baud_var.get()):
                self.log("WARNING: could not save the gauge setting to disk. "
                         "It holds for this session only.")
            how = ("over Ethernet" if is_network_gauge(resource)
                   else f"@ {baud_var.get()} baud")
            self.log(f"Pressure gauge set to {resource} {how}. "
                     "It is read on the next scan.")
            win.destroy()
            self.start_scan()

        def _clear():
            save_pressure_gauge(None)
            self.log("Pressure gauge cleared. No serial port or socket will be read.")
            win.destroy()
            self.start_scan()

        ttk.Button(row, text="Save", style='Launch.TButton',
                   command=_save).pack(side='left')
        ttk.Button(row, text="Clear", style='Aux.TButton',
                   command=_clear).pack(side='left', padx=(8, 0))
        ttk.Button(row, text="Cancel", style='Aux.TButton',
                   command=win.destroy).pack(side='right')

    def open_folder(self):
        """Open any folder in the system file browser."""
        folder = filedialog.askdirectory(title="Open folder",
                                         initialdir=self._last_data_dir)
        if folder:
            self._last_data_dir = folder
            self._open_path(folder)

    def open_pica_folder(self):
        """Open the PICA installation folder itself."""
        self._open_path(os.path.dirname(os.path.abspath(self.README_FILE)))

    def open_script_folder(self, script_key):
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path:
            return
        folder = os.path.dirname(os.path.abspath(script_path))
        if os.path.exists(folder):
            self._open_path(folder)
        else:
            messagebox.showwarning("Not Found", f"Folder not found:\n\n{folder}")

    def open_legacy_launcher(self):
        try:
            Process(target=_run_legacy_launcher).start()
            self.log("Opened the legacy (v1) launcher.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the legacy launcher:\n\n{e}")

    def _launch_gpib_scanner(self):
        if not PYVISA_AVAILABLE:
            messagebox.showerror("Dependency Missing",
                                 "The 'pyvisa' library is required.\n\npip install pyvisa pyvisa-py")
            return
        launch_gpib_scanner()

    def _auto_launch_gpib_scanner(self):
        """Open the standalone VISA/GPIB scanner with Advanced Options.

        Opening a window must never be blocked by a missing dependency or a
        spawn failure, so this reports to the console and gives up rather than
        raising a dialog the way the menu command does.
        """
        if not PYVISA_AVAILABLE:
            self.log("VISA/GPIB scanner not auto-opened: pyvisa is not "
                     "installed.")
            return
        self.log("Auto-launching the VISA/GPIB scanner…")
        try:
            launch_gpib_scanner()
        except Exception as e:
            self.log(f"ERROR: could not auto-open the VISA/GPIB scanner. {e}")

    # -------------------------------------------------------- tools popup
    # Every standalone utility PICA ships, grouped by what it is used for.
    # An entry is (button label, module key) or (button label, callable) where a
    # tool needs the dedicated launcher with its own dependency check.
    def _utils_groups(self):
        return [
            ("Plotting", [("Plotter Utility", launch_plotter_utility),
                          ("PPMS Plotter Utility", "PPMS Plotter Utility"),
                          ("P-E Plotter", "PE Plotter")]),
            ("PPMS Utilities", [("Sequence Visualizer", "Sequence Visualizer"),
                                ("PPMS Time Estimator", "PPMS Time Estimator"),
                                ("MD Ratio Calculator", "MD Ratio Calculator")]),
            ("Communication", [("GPIB / VISA Scanner", self._launch_gpib_scanner),
                               ("GPIB Scanner (32-bit)", "GPIB Scanner (32-bit)"),
                               ("SCPI Console", "SCPI Console")]),
            ("Calculators", [("Quick Calc", "Quick Calc"),
                             ("Time Utility", "Time Utility"),
                             ("Unit Converter", "Unit Converter")]),
        ]

    def open_tools_popup(self):
        win = Toplevel(self.root)
        win.title("PICA Utils")
        win.configure(bg=self.CLR_APP)
        win.transient(self.root)
        win.resizable(False, False)

        tk.Label(win, text="PICA Utils", bg=self.CLR_APP, fg=self.CLR_ACCENT,
                 font=self.FONT_CARD).pack(anchor='w', padx=20, pady=(16, 2))
        tk.Label(win, text="Standalone utilities bundled with PICA", bg=self.CLR_APP,
                 fg=self.CLR_TEXT_DIM, font=self.FONT_SMALL).pack(anchor='w', padx=20, pady=(0, 12))

        body = tk.Frame(win, bg=self.CLR_APP)
        body.pack(fill='both', expand=True, padx=20, pady=(0, 16))
        groups = self._utils_groups()
        for i, (title, tools) in enumerate(groups):
            grp = tk.LabelFrame(body, text=title, bg=self.CLR_PANEL, fg=self.CLR_TEXT,
                                font=self.FONT_LABEL, bd=1, relief='solid')
            # 'nsew' + a weighted row: paired groups stretch to equal height, so
            # the shorter one no longer leaves a hole under it.
            grp.grid(row=i // 2, column=i % 2, sticky='nsew', padx=5, pady=5)
            for label, target in tools:
                cmd = target if callable(target) else (lambda k=target: self.launch_script(k))
                ttk.Button(grp, text=label, style='Aux.TButton',
                           command=cmd).pack(fill='x', padx=8, pady=2)
        body.columnconfigure((0, 1), weight=1, uniform='t')
        for r in range((len(groups) + 1) // 2):
            body.rowconfigure(r, weight=1)

        ttk.Button(win, text="Close", style='Aux.TButton',
                   command=win.destroy).pack(fill='x', padx=20, pady=(0, 16))

    def show_about(self):
        messagebox.showinfo(
            "About PICA",
            f"PICA — Python Instrument Control & Automation\n"
            f"Launcher v{self.PROGRAM_VERSION}\n\n"
            "UGC-DAE Consortium for Scientific Research, Mumbai Centre\n\n"
            "Development: Prathamesh Deshmukh\n"
            "Vision & Guidance: Dr. Sudip Mukherjee\n\n"
            "Licensed under the MIT License.")

    # ------------------------------------------------------------- helpers
    def _open_path(self, path):
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Path not found: {abs_path}")
            messagebox.showwarning("Not Found", f"Path does not exist:\n\n{abs_path}")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(abs_path)
            elif platform.system() == "Darwin":
                subprocess.run(['open', abs_path], check=True)
            else:
                subprocess.run(['xdg-open', abs_path], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open:\n{path}\n\n{e}")

    def log(self, message):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        self._log_lines.append(line)
        for widget in list(self._consoles):
            try:
                widget.config(state='normal')
                widget.insert('end', line)
                widget.see('end')
                widget.config(state='disabled')
            except tk.TclError:
                # Its window was closed; the line is still in the buffer.
                self._consoles.remove(widget)

    def _make_console(self, parent, height=10):
        """Build a console view, fill it from the log, and keep it fed."""
        widget = scrolledtext.ScrolledText(
            parent, state='disabled', bg=self.CLR_PANEL2, fg=self.CLR_TEXT_DIM,
            font=self.FONT_MONO, wrap='word', bd=0, relief='flat',
            height=height, width=1)
        widget.config(state='normal')
        widget.insert('end', "".join(self._log_lines))
        widget.see('end')
        widget.config(state='disabled')
        self._consoles.append(widget)
        return widget

    def open_console(self):
        """Show the console in a window of its own (Quick Select route)."""
        if self._console_win is not None and self._console_win.winfo_exists():
            self._console_win.deiconify()
            self._console_win.lift()
            self._console_win.focus_force()
            return
        win = Toplevel(self.root)
        win.title("PICA — Console")
        win.configure(bg=self.CLR_APP)
        win.geometry("760x420")
        win.protocol("WM_DELETE_WINDOW", self._close_console)
        self._console_win = win

        head = tk.Frame(win, bg=self.CLR_APP)
        head.pack(fill='x', padx=16, pady=(12, 6))
        tk.Label(head, text="Console", bg=self.CLR_APP, fg=self.CLR_ACCENT,
                 font=self.FONT_TITLE).pack(side='left')
        tk.Label(head, text="Everything this launcher has done, oldest first.",
                 bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_SMALL).pack(side='left', padx=(12, 0))

        body = tk.Frame(win, bg=self.CLR_PANEL, highlightthickness=1,
                        highlightbackground=self.CLR_BORDER)
        body.pack(fill='both', expand=True, padx=16, pady=(0, 14))
        self._make_console(body, height=18).pack(fill='both', expand=True,
                                                 padx=1, pady=1)

    def _close_console(self):
        if self._console_win is not None:
            self._console_win.destroy()
        self._console_win = None


def main():
    root = tk.Tk()
    PICALauncherV2(root)
    root.mainloop()


if __name__ == '__main__':
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main()
