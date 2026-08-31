'''
===============================================================================
 PROGRAM:      PICA Launcher (v2)

 PURPOSE:      A modernised dashboard for launching PICA measurement scripts.
               The main window is the Quick Select view: category ->
               sub-category -> protocol, three dropdowns and a plain-language
               description of each choice, showing only the instruments that
               the selection actually needs. Everything PICA can launch lives
               in the Advanced Options window (Tools menu, or Ctrl+Shift+A),
               which keeps the full card grid and the full instrument list.
               Both windows carry the instrument status strip along the
               bottom edge.

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
    ("Lakeshore 350",   ["MODEL350", "MODEL 350", "LSCI"]),
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


def scan_instruments(skip_addresses=None):
    """Perform a one-shot, read-only VISA scan.

    Runs in a worker thread -- must not touch any Tk object. Returns a plain
    dict the GUI can apply on the main thread.

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
        'novocontrol': None,
        'protect': set(),
        'temperature': None,
        'temp_units': 'K',
        'temp_source': None,
        'timestamp': datetime.now().strftime("%H:%M:%S"),
    }
    if not PYVISA_AVAILABLE:
        result['error'] = "PyVISA not installed"
        return result

    rm = None
    try:
        rm = pyvisa.ResourceManager()
        resources = list(rm.list_resources())
    except Exception as e:
        result['error'] = f"VISA backend error: {e}"
        return result

    result['resources'] = resources
    lakeshore_resource = None
    cryocon_resource = None

    try:
        for res in resources:
            addr = _gpib_address_of(res)
            if addr is not None and addr in protected:
                result['skipped'].append(res)   # protected: never opened
                continue
            if not _is_probeable(res):
                result['skipped'].append(res)   # serial and friends
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

            for name, subs in KNOWN_INSTRUMENTS:
                if any(_idn_matches(head, s) for s in subs):
                    result['detected'][name] = res
                    if name == "Lakeshore 350":
                        lakeshore_resource = res
                    elif name == "Cryocon 34":
                        cryocon_resource = res

        # One temperature snapshot: the Lakeshore if present, otherwise the
        # Cryocon. Both are single read-only queries; nothing is configured.
        if lakeshore_resource:
            try:
                inst = rm.open_resource(lakeshore_resource)
                inst.timeout = TEMP_TIMEOUT_MS
                temp = inst.query("KRDG? A").strip()
                result['temperature'] = float(temp)
                result['temp_source'] = "Lakeshore 350 · Input A"
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
        'instruments': "K6517B",
        'modules': [
            ("PyroCurrent vs. T", "Pyroelectric Current", None),
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
        'category': "Temperature Utilities",
        'type': "Control Utility",
        'instruments': "Lakeshore 350 · Cryocon 34",
        'modules': [
            ("Temperature Ramp (L350)", "Lakeshore Temp Control", "control"),
            ("Step-wise Control (Basic, L350)", "Lakeshore Step Control", "control"),
            ("Step-wise Control (Advanced, L350)", "Lakeshore Step Control (Advanced)", "control"),
            ("Direct Control (L350)", "Lakeshore Direct Control", "control"),
            ("Temperature Monitor (L350)", "Lakeshore Temp Monitor", "sensing"),
            ("Direct Control (Cryocon 34)", "Cryocon Direct Control", "control"),
            ("Temperature Monitor (Cryocon 34)", "Cryocon Temp Monitor", "sensing"),
            ("Sensor Curve Loader (Cryocon 34)", "Cryocon Sensor Curve Loader", "control"),
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
        ],
    },
]



# -----------------------------------------------------------------------------
#  Quick Select catalogue (the main screen)
# -----------------------------------------------------------------------------
# The main window is the "Quick Select" view: it is what a new PICA user meets
# first, so it asks three plain questions -- what kind of measurement, over
# what range, which protocol -- instead of showing every module at once. The
# full CATALOG above stays behind Tools > Advanced Options (Ctrl+Shift+A).
#
# Structure:
#   category -> sub-categories -> protocols
# where a protocol is one launchable script. Each level carries its own short
# description, shown stacked at the bottom of the screen as the user narrows
# down, and each sub-category names the instruments that matter to it -- the
# status strip then shows only those chips.
#
# Deliberate differences from CATALOG:
#   * "Ultra Low Resistance" and "Low Resistance" are two entries that resolve
#     to the SAME delta-mode scripts. They are split because a user thinks in
#     terms of the sample, not the instrument pair: 10 nOhm of contact
#     resistance and a 10 mOhm film feel like different measurements.
#   * Where a Cryo-con 34 twin of a script exists, Quick Select lists ONLY the
#     Cryo-con one for the LCR meter (E4980A) and the electrometer (K6517B):
#     those two benches now run on the CC34. The Lakeshore variants are still
#     one keystroke away in Advanced Options. Pyroelectric has no CC34 twin
#     yet, so its Lakeshore module is the one listed.
#   * Temperature utilities, the Novocontrol Alpha-AN and the bench multimeter
#     are Advanced-only: they are not measurements a newcomer starts from.
QUICK_CATALOG = [
    {
        'category': "DC Resistance",
        'desc': "Force a steady current (or voltage) through the sample and "
                "measure the voltage (or current) it develops. Everything here "
                "is a two- or four-probe DC measurement; the sub-categories "
                "differ only in the resistance range the hardware can resolve.",
        'subcategories': [
            {
                'name': "Ultra Low Resistance (10 nOhm - 1 uOhm)",
                'desc': "Delta mode: the K6221 reverses the current at every "
                        "point and the K2182 nanovoltmeter averages the two "
                        "readings, so thermoelectric EMFs cancel and nanovolt "
                        "signals survive. Use it for contacts, metallic films "
                        "and superconducting transitions.",
                'instruments': ["Keithley 6221", "Keithley 2182",
                                "Lakeshore 350", "Cryocon 34"],
                'protocols': [
                    {'label': "I-V Sweep (Delta Mode)",
                     'key': "Delta Mode I-V Sweep", 'family': None,
                     'desc': "Sweep the delta-mode current and record V(I) at "
                             "one fixed temperature -- the check you run before "
                             "any R vs. T, to confirm the contacts are ohmic."},
                    {'label': "R vs. T - PICA drives the temperature",
                     'key': "Delta Mode R-T", 'family': 'control',
                     'desc': "PICA sets each Lakeshore 350 setpoint, waits for "
                             "it to settle, then measures. Use when the "
                             "cryostat is yours to command."},
                    {'label': "R vs. T - temperature read only (Cryocon 34)",
                     'key': "Delta Mode R-T (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "Something else ramps the temperature (a PPMS "
                             "sequence, a manual dewar warm-up) and PICA only "
                             "reads the Cryo-con 34 while it measures."},
                    {'label': "R vs. T - temperature read only (Lakeshore 350)",
                     'key': "Delta Mode R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "The same passive scan, reading the Lakeshore 350 "
                             "instead of the Cryo-con 34."},
                ],
            },
            {
                'name': "Low Resistance (above 1 uOhm)",
                # Same scripts as the entry above -- see the note at the head
                # of QUICK_CATALOG for why the range is split in two.
                'desc': "The same K6221 + K2182 delta-mode pair, used where "
                        "the sample sits comfortably above a microohm. Current "
                        "reversal is still worth having: it removes the drift "
                        "and thermal offsets a single-polarity reading hides.",
                'instruments': ["Keithley 6221", "Keithley 2182",
                                "Lakeshore 350", "Cryocon 34"],
                'protocols': [
                    {'label': "I-V Sweep (Delta Mode)",
                     'key': "Delta Mode I-V Sweep", 'family': None,
                     'desc': "Sweep the delta-mode current and record V(I) at "
                             "one fixed temperature."},
                    {'label': "R vs. T - PICA drives the temperature",
                     'key': "Delta Mode R-T", 'family': 'control',
                     'desc': "PICA sets each Lakeshore 350 setpoint, waits for "
                             "it to settle, then measures."},
                    {'label': "R vs. T - temperature read only (Cryocon 34)",
                     'key': "Delta Mode R-T (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "Passive scan: an external ramp moves the "
                             "temperature, PICA reads the Cryo-con 34."},
                    {'label': "R vs. T - temperature read only (Lakeshore 350)",
                     'key': "Delta Mode R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "The same passive scan against the "
                             "Lakeshore 350."},
                ],
            },
            {
                'name': "Resistance with High Precision (1 uOhm - 100 MOhm)",
                'desc': "The K2400 sources the current and a dedicated K2182 "
                        "nanovoltmeter reads the sample voltage. Two "
                        "instruments instead of one, in exchange for a voltage "
                        "resolution the K2400's own ADC cannot reach.",
                'instruments': ["Keithley 2400", "Keithley 2182",
                                "Lakeshore 350", "Cryocon 34"],
                'protocols': [
                    {'label': "I-V Sweep", 'key': "K2400_2182 I-V",
                     'family': None,
                     'desc': "Current sweep at fixed temperature, with the "
                             "voltage read by the K2182."},
                    {'label': "R vs. T - PICA drives the temperature",
                     'key': "K2400_2182 R-T", 'family': 'control',
                     'desc': "Setpoint-by-setpoint scan with PICA in charge of "
                             "the Lakeshore 350."},
                    {'label': "R vs. T - temperature read only (Cryocon 34)",
                     'key': "K2400_2182 R-T (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "Passive scan alongside an external ramp, reading "
                             "the Cryo-con 34."},
                    {'label': "R vs. T - temperature read only (Lakeshore 350)",
                     'key': "K2400_2182 R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "The same passive scan against the "
                             "Lakeshore 350."},
                ],
            },
            {
                'name': "Normal Resistance (100 uOhm - 200 MOhm)",
                'desc': "One K2400 SourceMeter both sources the current and "
                        "measures the voltage. The simplest wiring in PICA, "
                        "and the right default for an ordinary sample.",
                'instruments': ["Keithley 2400", "Lakeshore 350",
                                "Cryocon 34"],
                'protocols': [
                    {'label': "I-V Sweep", 'key': "K2400 I-V", 'family': None,
                     'desc': "Source-measure current sweep at one "
                             "temperature."},
                    {'label': "R vs. T - PICA drives the temperature",
                     'key': "K2400 R-T", 'family': 'control',
                     'desc': "PICA walks the Lakeshore 350 through the "
                             "temperature list and measures at each point."},
                    {'label': "R vs. T - temperature read only (Cryocon 34)",
                     'key': "K2400 R-T (T_Sensing, CC34)", 'family': 'sensing',
                     'desc': "Passive scan against an external ramp, reading "
                             "the Cryo-con 34."},
                    {'label': "R vs. T - temperature read only (Lakeshore 350)",
                     'key': "K2400 R-T (T_Sensing)", 'family': 'sensing',
                     'desc': "The same passive scan against the "
                             "Lakeshore 350."},
                ],
            },
            {
                'name': "High Resistance (1 Ohm - 10 POhm)",
                'desc': "The K6517B electrometer applies a voltage and "
                        "measures the sub-picoamp current that flows. This is "
                        "the range for insulators, ceramics and dielectric "
                        "films, where every other instrument reads open "
                        "circuit.",
                # The electrometer bench runs on the Cryo-con 34; the
                # Lakeshore variants of these scripts are in Advanced Options.
                'instruments': ["Keithley 6517B", "Cryocon 34"],
                'protocols': [
                    {'label': "I-V Sweep", 'key': "K6517B I-V", 'family': None,
                     'desc': "Voltage sweep with electrometer current "
                             "measurement, at one fixed temperature."},
                    {'label': "R vs. T - temperature read only (Cryocon 34)",
                     'key': "K6517B R-T (T_Sensing, CC34)", 'family': 'sensing',
                     'desc': "Passive high-resistance scan: an external ramp "
                             "moves the temperature, PICA reads the Cryo-con "
                             "34 and logs R(T)."},
                ],
            },
        ],
    },
    {
        'category': "AC Resistance",
        'desc': "Drive the sample with a small alternating current and recover "
                "the voltage at that same frequency with a lock-in amplifier. "
                "Everything outside the reference frequency -- drift, 1/f "
                "noise, mains pickup -- is rejected, so far smaller signals "
                "are measurable than DC allows.",
        'subcategories': [
            {
                'name': "Lock-in AC Resistivity (K6221 + SR830)",
                'desc': "The K6221 supplies the AC current and the SR830 DSP "
                        "lock-in recovers the in-phase voltage. Experimental: "
                        "the pairing works, but has not yet been through a "
                        "full measurement campaign.",
                'instruments': ["Keithley 6221", "SR830 Lock-in"],
                'protocols': [
                    {'label': "Comms and Control",
                     'key': "SR830 Lock-in Comms", 'family': None,
                     'desc': "Talk to the SR830 directly: set sensitivity, "
                             "time constant, phase and reference, and watch X, "
                             "Y, R and theta live. Start here to get the "
                             "lock-in locked."},
                    {'label': "AC Resistivity (4-probe)",
                     'key': "SR830 AC Resistivity", 'family': None,
                     'desc': "Four-probe AC resistivity: the K6221 sources the "
                             "AC current, the SR830 reads the voltage drop, "
                             "and PICA logs R against time."},
                ],
            },
        ],
    },
    {
        'category': "Impedance Spectroscopy",
        'desc': "Apply a small AC voltage over a range of frequencies and "
                "measure the complex impedance. From it come capacitance, "
                "dielectric permittivity and loss -- the standard way to "
                "separate bulk, grain-boundary and electrode responses in a "
                "dielectric.",
        'subcategories': [
            {
                'name': "Frequency / Bias Sweep at fixed temperature",
                'desc': "Keysight E4980A precision LCR meter, 20 Hz - 2 MHz. "
                        "The temperature is held still (or simply not "
                        "controlled) while frequency or DC bias is swept.",
                'instruments': ["Keysight E4980A"],
                'protocols': [
                    {'label': "C-V Measurement", 'key': "LCR C-V Measurement",
                     'family': None,
                     'desc': "Capacitance against DC bias at a fixed frequency "
                             "-- ferroelectric butterfly loops, depletion "
                             "profiling, tunability."},
                    {'label': "Dielectric Frequency Scan",
                     'key': "LCR Frequency Scan", 'family': None,
                     'desc': "Sweep frequency across the E4980A range at one "
                             "temperature and record the permittivity and "
                             "loss."},
                ],
            },
            {
                'name': "Temperature-dependent Dielectric (Cryocon 34)",
                # The LCR bench runs on the CC34; the L350 twins of these
                # three scripts remain available in Advanced Options.
                'desc': "The same LCR meter, run while the temperature moves. "
                        "PICA reads the Cryo-con 34 and stamps every "
                        "dielectric point with the temperature it was actually "
                        "taken at, rather than the one that was requested.",
                'instruments': ["Keysight E4980A", "Cryocon 34"],
                'protocols': [
                    {'label': "Dielectric Temperature Scan",
                     'key': "LCR Temp. Scan (T_Sensing, CC34)",
                     'family': 'sensing',
                     'desc': "Continuous permittivity against temperature at a "
                             "fixed frequency list, while an external ramp "
                             "warms or cools the sample. The hardened passive "
                             "scan: it reconnects by itself and flushes every "
                             "point to disk."},
                    {'label': "Temperature-step Frequency Scan (PPMS)",
                     'key': "PPMS Sync Freq. Scan (CC34)", 'family': 'sensing',
                     'desc': "Waits for the PPMS to hold a plateau, runs a "
                             "full frequency sweep there, then waits for the "
                             "next plateau. Gives a clean grid in frequency "
                             "and temperature."},
                    {'label': "Dielectric Master - T-scan + F-scan (PPMS)",
                     'key': "PPMS Dielectric Master (CC34)", 'family': 'master',
                     'desc': "The full protocol in one run: temperature scans "
                             "and frequency scans interleaved to a written "
                             "sequence, with field tags and per-run cooldowns. "
                             "Use it for an overnight PPMS campaign."},
                ],
            },
        ],
    },
    {
        'category': "Pyroelectric",
        'desc': "Measure the tiny current a polarised sample releases as its "
                "temperature changes. Integrating that current against time "
                "gives the released charge, and from it the remanent "
                "polarisation and any depolarisation (TSDC) peaks.",
        'subcategories': [
            {
                'name': "Pyroelectric / TSDC Current",
                # No Cryo-con twin of these scripts exists yet, so the
                # Lakeshore module is the one Quick Select shows.
                'desc': "The K6517B electrometer reads the sample current at "
                        "zero applied bias while the temperature ramps. Poling "
                        "is done first, with the same instrument's voltage "
                        "source.",
                'instruments': ["Keithley 6517B", "Lakeshore 350"],
                'protocols': [
                    {'label': "Pyroelectric Current vs. T",
                     'key': "Pyroelectric Current", 'family': None,
                     'desc': "Log the depolarisation current against "
                             "temperature during the ramp -- the measurement "
                             "itself."},
                    {'label': "Poling / Voltage Polling (Bias)",
                     'key': "K6517B Polling (Bias)", 'family': None,
                     'desc': "Hold a poling voltage on the sample and watch the "
                             "leakage current settle. Run this before the "
                             "pyroelectric scan."},
                ],
            },
        ],
    },
]


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
    # The status strip runs against the type scale rather than with it: it is a
    # glanceable band, not reading matter, and at the v2 base size it ate three
    # rows of window height. Its own smaller scale keeps it to one compact band.
    FONT_DOT = ('Segoe UI', FONT_SIZE_BASE + 1)              # status dot glyph
    FONT_STRIP = ('Segoe UI', FONT_SIZE_BASE - 4)            # chip / caption text
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
    RAIL_WIDTH = 300
    # Card grid reflows with the window: a maximised 1920 px screen fits three
    # columns, a restored window two, a narrow one a single column.
    MAX_CARD_COLS = 3
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
        # The VISA/GPIB scanner opens by itself at startup, as in v1: the
        # first thing anyone needs is the list of what is actually on the bus
        # with its real addresses. It runs in its own process and does its own
        # scan, so it does not interfere with the status strip's.
        self.root.after(900, self._auto_launch_gpib_scanner)

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

        style.configure('TCombobox', fieldbackground=self.CLR_PANEL2,
                        background=self.CLR_PANEL2, foreground=self.CLR_TEXT,
                        arrowcolor=self.CLR_TEXT_DIM, padding=4)
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
        tools_menu.add_separator()
        tools_menu.add_command(label="PICA Utils…", command=self.open_tools_popup)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        view_menu = tk.Menu(menubar, tearoff=0, font=self.FONT_MENU)
        view_menu.add_command(label="Advanced Options…", accelerator="Ctrl+Shift+A",
                              command=self.open_advanced)
        view_menu.add_command(label="Main Window", command=self._focus_main)
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Show Console", variable=self._make_console_var())
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

    def _make_console_var(self):
        self._console_visible = tk.BooleanVar(value=True)
        self._console_visible.trace_add('write', lambda *a: self._toggle_console())
        return self._console_visible

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
    # FUTURE (pressure): a pressure tile belongs immediately to the right of
    # the temperature tile -- same StripCap / StripStat / StripDim triple,
    # filled from the same one-shot scan result -- once a gauge is on the bus.
    # Nothing else in the strip has to change to make room for it.
    def _all_chip_names(self):
        """Every instrument chip, in reference-table order."""
        return [n for n, _ in KNOWN_INSTRUMENTS] + NEVER_PROBED_INSTRUMENTS

    def _build_statusbar(self):
        """The Quick Select strip; its chips follow the current selection."""
        self.quick_strip = self._build_status_strip(self.root,
                                                    self._all_chip_names())

    def _build_status_strip(self, parent, chip_names):
        """Build one bottom status band and register it for scan updates."""
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

        # Reload and the legend are packed to the right edge before the elastic
        # chip grid, so pack reserves their width instead of letting the chips
        # squeeze them out.
        strip['reload_btn'] = ttk.Button(inner, text="⟳ Reload", style='Aux.TButton',
                                         command=self.start_scan)
        strip['reload_btn'].pack(side='right', padx=(12, 0))

        # Colour legend. Without it a grey dot reads as "broken" rather than
        # "switched off", which is the question people actually ask of the
        # strip. Protected instruments get a hollow ring, not a filled dot:
        # PICA has no reading for them at all, so they are neither on nor off.
        legend = tk.Frame(inner, bg=self.CLR_PANEL)
        legend.pack(side='right', padx=(12, 0))
        ttk.Label(legend, text="DOT KEY", style='StripCap.TLabel').pack(anchor='w')
        keyrow = tk.Frame(legend, bg=self.CLR_PANEL)
        keyrow.pack(anchor='w')
        for glyph, colour, text in (("●", self.CLR_LIVE, "on"),
                                    ("●", self.CLR_TEXT_FAINT, "off"),
                                    ("○", self.CLR_TEXT_FAINT, "not probed")):
            item = tk.Frame(keyrow, bg=self.CLR_PANEL)
            item.pack(side='left', padx=(0, 8))
            tk.Label(item, text=glyph, bg=self.CLR_PANEL, fg=colour,
                     font=self.FONT_STRIP).pack(side='left', padx=(0, 3))
            tk.Label(item, text=text, bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                     font=self.FONT_STRIP).pack(side='left')

        chip_tile = tk.Frame(inner, bg=self.CLR_PANEL)
        chip_tile.pack(side='left', fill='x', expand=True)
        ttk.Label(chip_tile, text="INSTRUMENTS", style='StripCap.TLabel').pack(anchor='w')
        strip['chips_frame'] = tk.Frame(chip_tile, bg=self.CLR_PANEL)
        strip['chips_frame'].pack(anchor='w', fill='x')

        self._strips.append(strip)
        self._set_strip_chips(strip, chip_names)
        return strip

    def _set_strip_chips(self, strip, chip_names):
        """Rebuild one strip's chips, then repaint them from the last scan."""
        frame = strip['chips_frame']
        for w in frame.winfo_children():
            w.destroy()
        strip['chips'] = {}
        strip['names'] = list(chip_names)
        # Four natural-width columns: the full list lands in three rows, which
        # is the height the temperature tile already sets, and a filtered
        # handful stays on one. Six columns fitted the count but not the
        # width -- the last chips ran under the dot key at the right edge.
        cols = 4
        for i, name in enumerate(chip_names):
            chip = tk.Frame(frame, bg=self.CLR_PANEL)
            chip.grid(row=i // cols, column=i % cols, sticky='w', padx=(0, 10))
            manual = name in NEVER_PROBED_INSTRUMENTS
            dot = tk.Label(chip, text="○" if manual else "●", bg=self.CLR_PANEL,
                           fg=self.CLR_TEXT_FAINT, font=self.FONT_DOT)
            dot.pack(side='left', padx=(0, 4))
            lbl = tk.Label(chip, text=f"{name} (prot.)" if manual else name,
                           bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                           font=self.FONT_STRIP)
            lbl.pack(side='left')
            strip['chips'][name] = (dot, lbl, manual)
        self._render_strip(strip)

    def _render_strip(self, strip):
        """Paint the most recent scan result into one strip. Safe with none."""
        r = self._last_scan
        if r is None:
            return
        if not r['available'] or r['error']:
            msg = r['error'] or "PyVISA not installed"
            strip['temp_value'].config(text="—")
            strip['temp_sub'].config(text=msg)
            strip['link_value'].config(text="—")
            strip['link_sub'].config(text=msg)
            return

        detected = r['detected']
        n_found = sum(1 for v in detected.values() if v)
        n_known = len(KNOWN_INSTRUMENTS)
        strip['link_value'].config(
            text=str(n_found), foreground=self.CLR_OK if n_found else self.CLR_TEXT)
        strip['link_sub'].config(text=f"of {n_known} known · scan {r['timestamp']}")

        if r['temperature'] is not None:
            units = r.get('temp_units', 'K')
            strip['temp_value'].config(text=f"{r['temperature']:.2f} {units}")
            strip['temp_sub'].config(text=f"{r['temp_source']} · as of {r['timestamp']}")
        else:
            strip['temp_value'].config(text="—")
            strip['temp_sub'].config(text="no Lakeshore / Cryocon reading")

        for name, (dot, lbl, manual) in strip['chips'].items():
            if manual:
                # Protected: PICA never speaks to it, so it has no state to
                # report. The ring stays hollow and grey whatever the scan says.
                continue
            if detected.get(name):
                dot.config(fg=self.CLR_LIVE)
                lbl.config(fg=self.CLR_TEXT)
            else:
                dot.config(fg=self.CLR_TEXT_FAINT)
                lbl.config(fg=self.CLR_TEXT_DIM)

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
                    if widgets and not widgets[2]:
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

        # Console (packed from the bottom up, so the links sit just above it)
        self.console_frame = tk.Frame(pad, bg=self.CLR_PANEL)
        self.console_frame.pack(side='bottom', fill='both', expand=True)

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

        ttk.Label(self.console_frame, text="CONSOLE", style='Faint.TLabel').pack(anchor='w', pady=(0, 4))
        self.console_widget = scrolledtext.ScrolledText(
            self.console_frame, state='disabled', bg=self.CLR_PANEL2, fg=self.CLR_TEXT_DIM,
            font=self.FONT_MONO, wrap='word', bd=0, relief='flat', height=10, width=1)
        self.console_widget.pack(fill='both', expand=True)

    def _load_logo(self):
        """Load the institute logo into the rail canvas (deferred, like v1)."""
        if not (PIL_AVAILABLE and os.path.exists(self.LOGO_FILE)):
            self.log("Logo not loaded: PIL unavailable or file missing.")
            return
        try:
            img = Image.open(self.LOGO_FILE)
            img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
            self.logo_image = ImageTk.PhotoImage(img)  # keep a reference
            self.logo_canvas.create_image(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2,
                                          image=self.logo_image)
        except Exception as e:
            self.log(f"ERROR: Failed to load logo. {e}")

    # ---------------------------------------------------- Browse (card grid)
    def _build_browse(self, parent):
        container = tk.Frame(parent, bg=self.CLR_APP)
        container.pack(fill='both', expand=True)

        # Legend
        legend = tk.Frame(container, bg=self.CLR_APP)
        legend.pack(fill='x', padx=12, pady=(10, 4))
        for text, color in [("T Sensing", self.CLR_FAMILY_SENSING),
                            ("T Control", self.CLR_FAMILY_CONTROL),
                            ("Master Sequence", self.CLR_FAMILY_MASTER)]:
            item = tk.Frame(legend, bg=self.CLR_APP)
            item.pack(side='left', padx=(0, 16))
            tk.Frame(item, bg=color, width=10, height=10).pack(side='left', padx=(0, 5))
            tk.Label(item, text=text, bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                     font=self.FONT_SMALL).pack(side='left')

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
        canvas.pack(side='left', fill='both', expand=True, padx=(12, 0), pady=(0, 12))
        scrollbar.pack(side='right', fill='y', pady=(0, 12))

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
    # actually thinks in: what kind of measurement, over what range, which
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
                 text="Pick a category, narrow the range, then choose a protocol. "
                      "Every module PICA ships is in Advanced Options — Ctrl+Shift+A.",
                 bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_SMALL).pack(anchor='w', pady=(2, 18))

        grid = tk.Frame(inner, bg=self.CLR_PANEL)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1, 2), weight=1, uniform='q')

        self.cat_var = tk.StringVar()
        self.sub_var = tk.StringVar()
        self.proto_var = tk.StringVar()

        self.cat_combo = self._combo_column(
            grid, 0, "1 · Category", self.cat_var,
            [c['category'] for c in QUICK_CATALOG], 'readonly',
            self._on_quick_category)
        self.sub_combo = self._combo_column(
            grid, 1, "2 · Sub-category", self.sub_var, [], 'disabled',
            self._on_quick_sub)
        self.proto_combo = self._combo_column(
            grid, 2, "3 · Protocol", self.proto_var, [], 'disabled',
            self._on_quick_protocol)

        # Description stack: one block per level, filled in as the user
        # narrows down and greyed out until then.
        desc = tk.Frame(inner, bg=self.CLR_PANEL)
        desc.pack(fill='both', expand=True, pady=(20, 18))
        self._desc_blocks = {
            'category': self._desc_block(desc, "CATEGORY"),
            'sub': self._desc_block(desc, "SUB-CATEGORY"),
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
        self.data_btn = ttk.Button(actions, text="≡ Data", style='Aux.TButton',
                                   state='disabled', command=self._data_selected)
        self.data_btn.pack(side='left', padx=(8, 0))
        self.plot_btn = ttk.Button(actions, text="📈 Plot", style='Aux.TButton',
                                   state='disabled',
                                   command=lambda: launch_plotter_utility())
        self.plot_btn.pack(side='left', padx=(8, 0))

        self._selected_key = None

    def _combo_column(self, parent, column, caption, var, values, state, handler):
        """One labelled dropdown in the three-across selector row."""
        col = tk.Frame(parent, bg=self.CLR_PANEL)
        col.grid(row=0, column=column, sticky='ew',
                 padx=(0 if column == 0 else 7, 0 if column == 2 else 7))
        tk.Label(col, text=caption, bg=self.CLR_PANEL, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_LABEL).pack(anchor='w', pady=(0, 4))
        combo = ttk.Combobox(col, textvariable=var, state=state, values=values)
        combo.pack(fill='x')
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
        self._set_desc('sub', None, None)
        self._set_desc('protocol', None, None)

    def _on_quick_category(self, _event=None):
        idx = self.cat_combo.current()
        if idx < 0:
            return
        cat = QUICK_CATALOG[idx]
        self.sub_combo.config(state='readonly',
                              values=[s['name'] for s in cat['subcategories']])
        self.sub_var.set('')
        self.proto_var.set('')
        self.proto_combo.config(state='disabled', values=[])
        self._reset_descriptions()
        self._set_desc('category', cat['category'], cat['desc'])
        self._set_quick_actions(False)
        self._update_quick_chips(cat)
        # A category with a single sub-category is not a choice, so make it
        # for the user rather than asking them to click through it.
        if len(cat['subcategories']) == 1:
            self.sub_combo.current(0)
            self._on_quick_sub()

    def _on_quick_sub(self, _event=None):
        cat_idx, sub_idx = self.cat_combo.current(), self.sub_combo.current()
        if cat_idx < 0 or sub_idx < 0:
            return
        cat = QUICK_CATALOG[cat_idx]
        subcat = cat['subcategories'][sub_idx]
        self.proto_combo.config(state='readonly',
                                values=[p['label'] for p in subcat['protocols']])
        self.proto_var.set('')
        self._set_desc('sub', subcat['name'], subcat['desc'])
        self._set_desc('protocol', None, None)
        self._set_quick_actions(False)
        self._update_quick_chips(cat, subcat)

    def _on_quick_protocol(self, _event=None):
        cat_idx, sub_idx = self.cat_combo.current(), self.sub_combo.current()
        proto_idx = self.proto_combo.current()
        if cat_idx < 0 or sub_idx < 0 or proto_idx < 0:
            return
        subcat = QUICK_CATALOG[cat_idx]['subcategories'][sub_idx]
        proto = subcat['protocols'][proto_idx]
        self._selected_key = proto['key']
        tag = {'control': "  ·  PICA controls the temperature",
               'sensing': "  ·  PICA only reads the temperature",
               'master': "  ·  master sequence"}.get(proto['family'], "")
        self._set_desc('protocol', proto['label'] + tag, proto['desc'])
        self._set_quick_actions(True)

    def _update_quick_chips(self, cat, subcat=None):
        """Show only the instruments the current selection can use.

        A category with no sub-category chosen yet shows the union of all of
        its sub-categories, so the strip never goes empty mid-choice.

        FUTURE (pressure): when a pressure gauge joins KNOWN_INSTRUMENTS it is
        filtered here like any other chip -- name it in the sub-category's
        'instruments' list and nothing else has to change.
        """
        if subcat is not None:
            wanted = set(subcat['instruments'])
        else:
            wanted = set()
            for s in cat['subcategories']:
                wanted |= set(s['instruments'])
        # Keep the reference-table order so chips do not jump around between
        # selections.
        names = [n for n in self._all_chip_names() if n in wanted]
        self._set_strip_chips(self.quick_strip, names)

    def _set_quick_actions(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for b in (self.launch_btn, self.folder_btn, self.data_btn, self.plot_btn):
            b.config(state=state)
        if not enabled:
            self._selected_key = None

    def _launch_selected(self):
        if self._selected_key:
            self.launch_script(self._selected_key)

    def _folder_selected(self):
        if self._selected_key:
            self.open_script_folder(self._selected_key)

    def _data_selected(self):
        if not self._selected_key:
            return
        script_path = self.SCRIPT_PATHS.get(self._selected_key)
        start_dir = os.path.dirname(os.path.abspath(script_path)) if script_path else os.getcwd()
        path = filedialog.askopenfilename(
            title="Open data file",
            initialdir=start_dir,
            filetypes=[("Data files", "*.txt *.csv *.dat"), ("All files", "*.*")])
        if path:
            self._open_path(path)

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
        win.protocol("WM_DELETE_WINDOW", self._close_advanced)
        self._adv_win = win

        head = tk.Frame(win, bg=self.CLR_APP)
        head.pack(fill='x', padx=18, pady=(14, 0))
        tk.Label(head, text="Advanced Options", bg=self.CLR_APP,
                 fg=self.CLR_ACCENT, font=self.FONT_TITLE).pack(anchor='w')
        tk.Label(head,
                 text="Every module PICA ships, grouped by measurement range and "
                      "instrument. Quick Select in the main window is the guided route.",
                 bg=self.CLR_APP, fg=self.CLR_TEXT_DIM,
                 font=self.FONT_SMALL).pack(anchor='w')

        # Strip first: packed to the bottom edge before the card grid claims
        # the rest of the height.
        self._adv_strip = self._build_status_strip(win, self._all_chip_names())
        self._build_browse(win)
        self.log("Advanced Options opened.")

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
            strip['reload_btn'].config(state='disabled', text="⟳ Scanning…")
            strip['link_sub'].config(text="scanning the VISA bus…")
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
        self._live_chips = {name for name, res in detected.items() if res}
        self._refresh_strips()

        n_found = len(self._live_chips)
        n_known = len(KNOWN_INSTRUMENTS)
        skipped = r.get('skipped') or []
        tail = f", {len(skipped)} not probed" if skipped else ""
        self.log(f"Scan complete — {n_found}/{n_known} instruments detected "
                 f"across {len(r['resources'])} VISA resource(s){tail}.")

    def _refresh_strips(self):
        """Render the last scan result into every strip that is alive."""
        for strip in list(self._strips):
            try:
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
        """Open the VISA/GPIB scanner at startup (v1 behaviour).

        Startup must never be blocked by a missing dependency or a spawn
        failure, so this reports to the console and gives up rather than
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
    def _toggle_console(self):
        if not hasattr(self, 'console_frame'):
            return
        if self._console_visible.get():
            self.console_frame.pack(side='bottom', fill='both', expand=True)
        else:
            self.console_frame.pack_forget()

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
        if getattr(self, 'console_widget', None):
            ts = datetime.now().strftime("%H:%M:%S")
            self.console_widget.config(state='normal')
            self.console_widget.insert('end', f"[{ts}] {message}\n")
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')


def main():
    root = tk.Tk()
    PICALauncherV2(root)
    root.mainloop()


if __name__ == '__main__':
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main()
