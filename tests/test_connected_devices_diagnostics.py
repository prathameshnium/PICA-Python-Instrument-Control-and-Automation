"""Tests for Connected Devices and Drivers (24 Oct 2026):

    pica/utils/Diagnostics_Connected_Devices_GUI.py

It answers "I plugged it in and nothing can see it": per device, is it
connected now, does it have a driver, what Device Manager error code, and
which COM port. It must stay safe to run on the measurement PC beside a
live experiment, so the promises that matter most are pinned by name:
no shelling out, registry read-only, only the cfgmgr32 QUERY calls (never
rescan / enable / disable / remove), no serial port opened, no VISA
session, and not one packet sent on the LAN.

No hardware. Runnable as plain Python as well as under pytest:
    python tests/test_connected_devices_diagnostics.py
"""

import importlib.util
import inspect
import os
import socket
import struct
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

DEVICES_PATH = os.path.join(project_root, "pica", "utils",
                            "Diagnostics_Connected_Devices_GUI.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


devdiag = _load("pica_connected_devices_diagnostics_module", DEVICES_PATH)
SOURCE = open(DEVICES_PATH, encoding="utf-8").read()

IS_WINDOWS = os.name == 'nt'


# ===========================================================================
# 1. It changes nothing
# ===========================================================================

def test_the_only_file_written_is_the_saved_log():
    writes = [line for line in SOURCE.splitlines()
              if "open(" in line and ("'w" in line or "'a" in line
                                      or '"w' in line or '"a' in line)]
    assert len(writes) == 1, writes
    assert "_save_as" in SOURCE


def test_the_registry_is_only_ever_opened_for_reading():
    for banned in ("SetValue", "CreateKey", "DeleteKey", "DeleteValue",
                   "KEY_WRITE", "KEY_SET_VALUE", "KEY_ALL_ACCESS"):
        assert banned not in SOURCE, banned
    assert "KEY_READ" in SOURCE


def test_it_never_shells_out():
    for banned in ("subprocess", "os.system", "os.popen", "Popen",
                   "check_call", "check_output", "ShellExecute",
                   "powershell", "pnputil", "devcon"):
        assert banned not in SOURCE, banned


def test_only_the_plug_and_play_query_calls_are_used():
    """The calls that act on hardware must never appear, not even unused."""
    for banned in ("CM_Reenumerate", "CM_Setup_DevNode", "CM_Enable_DevNode",
                   "CM_Disable_DevNode", "CM_Query_And_Remove",
                   "CM_Request_Device_Eject", "CM_Set_DevNode",
                   "CM_Uninstall", "CM_Register", "SetupDi",
                   "UpdateDriverForPlugAndPlayDevices", "DiInstall",
                   "CM_LOCATE_DEVNODE_PHANTOM"):
        assert banned not in SOURCE, banned
    used = {word for word in SOURCE.replace("(", " ").replace(".", " ").split()
            if word.startswith("CM_") and any(ch.islower() for ch in word)}
    assert used == {"CM_Get_Device_ID_List_SizeW", "CM_Get_Device_ID_ListW",
                    "CM_Locate_DevNodeW", "CM_Get_DevNode_Status"}, used


def test_no_service_or_installer_is_touched():
    for banned in ("StartService", "ControlService", "OpenSCManager",
                   "sc.exe", "msiexec"):
        assert banned not in SOURCE, banned


def test_no_serial_port_is_opened():
    """Opening a COM port asserts DTR and can reset the device behind it."""
    for banned in ("import serial", "serial.Serial", "Serial(", "CreateFile",
                   "\\\\.\\COM"):
        assert banned not in SOURCE, banned


def test_no_visa_session_is_opened():
    for banned in ("pyvisa", "ResourceManager", "open_resource", "query(",
                   "write_raw", "read_raw"):
        assert banned not in SOURCE, banned


def test_nothing_is_sent_on_the_network():
    """The ARP cache is read; nothing is pinged, scanned or connected to."""
    for banned in ("connect(", "sendto(", ".send(", "SendARP", "IcmpSendEcho",
                   "create_connection", "SOCK_RAW", "SOCK_STREAM",
                   "SOCK_DGRAM", "ping(", "'ping", '"ping', "Icmp"):
        assert banned not in SOURCE, banned
    assert "GetIpNetTable" in SOURCE


def test_it_says_what_it_does_not_do():
    assert "read-only" in SOURCE.lower()
    assert "asserts DTR" in SOURCE
    assert "Nothing was pinged, scanned or connected to" in SOURCE


# ===========================================================================
# 2. The words it uses
# ===========================================================================

def test_the_no_driver_code_is_named_plainly():
    assert "NO DRIVER" in devdiag.problem_text(28)
    assert "cable" in devdiag.problem_text(43)
    assert "DISABLED" in devdiag.problem_text(22)
    assert devdiag.problem_text(9999)          # unknown codes still say so


def test_state_text_covers_every_state():
    assert devdiag.state_text(None, 0) == "not connected now"
    text = devdiag.state_text(devdiag.DN_HAS_PROBLEM, 28)
    assert "code 28" in text and "NO DRIVER" in text
    assert "working" in devdiag.state_text(
        devdiag.DN_STARTED | devdiag.DN_DRIVER_LOADED, 0)
    assert "not started" in devdiag.state_text(devdiag.DN_DRIVER_LOADED, 0)
    assert "no driver" in devdiag.state_text(0, 0)


def test_vid_pid_reads_usb_and_ftdi_bus_forms():
    assert devdiag.vid_pid(r"USB\VID_0403&PID_6001\A12345") == ("0403", "6001")
    assert devdiag.vid_pid(r"FTDIBUS\VID_0403+PID_6015+X\0000") == \
        ("0403", "6015")
    assert devdiag.vid_pid(r"ROOT\SYSTEM\0001") == (None, None)


def test_the_vendor_table_names_ftdi():
    assert "FTDI" in devdiag.USB_VENDORS["0403"]
    assert "3923" in devdiag.USB_VENDORS


def test_a_plain_and_an_indirect_name_both_read():
    assert devdiag.readable_name("USB Serial Port (COM5)") == \
        "USB Serial Port (COM5)"
    assert devdiag.readable_name(
        r"@usbxhci.inf,%x%;USB xHCI Compliant Host Controller") == \
        "USB xHCI Compliant Host Controller"
    assert devdiag.readable_name(r"@x.sys,#1;%1 USB %2;(AMD)") == ""
    assert devdiag.readable_name(None) == ""


def test_driver_image_paths_resolve_to_files():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    assert devdiag.resolve_driver_file(
        r"\SystemRoot\System32\drivers\ftdibus.sys") == \
        os.path.join(root, r"System32\drivers\ftdibus.sys")
    assert devdiag.resolve_driver_file(r"System32\drivers\x.sys") == \
        os.path.join(root, r"System32\drivers\x.sys")
    assert devdiag.resolve_driver_file(r"\??\C:\d\y.sys") == r"C:\d\y.sys"
    assert devdiag.resolve_driver_file(None) == ""


def test_filetime_conversion():
    # 1 Jan 2020 00:00 UTC as a FILETIME.
    stamp = 132223104000000000
    moment = devdiag.filetime_to_local(stamp)
    assert moment is not None and moment.year in (2019, 2020)
    assert devdiag.filetime_to_local(0) is None


# ===========================================================================
# 3. Parsing what Windows keeps
# ===========================================================================

FTDI_INF = """
; FTDIBUS.INF
[Version]
Signature="$Windows NT$"
DriverPackageType=PlugAndPlay
Class=USB
ClassGUID={36FC9E60-C465-11CF-8056-444553540000}
Provider=%FTDI%
CatalogFile=ftdibus.cat
DriverVer=07/05/2023,2.12.36.4

[Strings]
FTDI="FTDI"   ; maker
Note="a;b"
"""


def test_an_inf_resolves_its_provider_from_strings():
    parsed = devdiag.parse_inf(FTDI_INF)
    assert parsed["provider"] == "FTDI"
    assert parsed["class"] == "USB"
    assert parsed["date"] == "07/05/2023"
    assert parsed["version"] == "2.12.36.4"
    assert parsed["catalog"] == "ftdibus.cat"
    assert devdiag.is_interesting_package(parsed)


def test_an_inf_with_tokens_in_driverver_resolves_them():
    text = ("[Version]\nProvider=HP\nDriverVer=%D%,%V%\n"
            "[Strings]\nD=01/02/2024\nV=1.2.3\n")
    parsed = devdiag.parse_inf(text)
    assert parsed["date"] == "01/02/2024" and parsed["version"] == "1.2.3"


def test_inf_comments_do_not_cut_quoted_text():
    assert devdiag.strip_inf_comment('X="a;b" ; c') == 'X="a;b" '


def test_a_utf16_inf_is_read(tmp_path=None):
    import tempfile
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "oem99.inf")
    with open(path, "wb") as handle:
        handle.write(FTDI_INF.encode("utf-16"))
    assert "Provider=%FTDI%" in devdiag.read_text_file(path)
    assert devdiag.read_text_file(os.path.join(folder, "none.inf")) == ""


def _arp_row(index, ip, mac, kind):
    phys = bytes.fromhex(mac.replace("-", "")).ljust(8, b"\0")
    address = struct.unpack("<I", socket.inet_aton(ip))[0]
    return struct.pack("<II8sII", index, 6, phys, address, kind)


def test_an_arp_table_parses_and_finds_a_pfeiffer_gauge():
    raw = struct.pack("<I", 3) \
        + _arp_row(10, "192.168.1.50", "00-A0-41-12-34-56", 3) \
        + _arp_row(10, "192.168.1.255", "FF-FF-FF-FF-FF-FF", 4) \
        + _arp_row(10, "224.0.0.22", "01-00-5E-00-00-16", 4)
    rows = devdiag.parse_arp_table(raw)
    assert rows[0] == (10, "192.168.1.50", "00-A0-41-12-34-56", "dynamic")
    neighbours = [row for row in rows if devdiag.is_neighbour(*row[1:])]
    assert len(neighbours) == 1
    assert "Pfeiffer" in devdiag.INSTRUMENT_OUIS[
        devdiag.mac_oui(neighbours[0][2])]


def test_a_truncated_arp_table_does_not_raise():
    raw = struct.pack("<I", 5) + _arp_row(1, "10.0.0.2", "00-11-22-33-44-55",
                                          3)
    assert len(devdiag.parse_arp_table(raw)) == 1
    assert devdiag.parse_arp_table(b"") == []


def test_mac_oui_accepts_both_separators():
    assert devdiag.mac_oui("00-a0-41-12-34-56") == "00A041"
    assert devdiag.mac_oui("00:A0:41:12:34:56") == "00A041"
    assert devdiag.mac_oui("") == ""


# ===========================================================================
# 4. Before and after plugging in
# ===========================================================================

def test_snapshot_diff_reports_arrivals_and_departures():
    before = {"A": "Mouse", "B": "Camera"}
    after = {"B": "Camera", "C": "USB Serial Converter"}
    assert devdiag.snapshot_diff(before, after) == (["C"], ["A"])
    assert devdiag.snapshot_diff(None, after) == (["B", "C"], [])


def _fake_record(instance_id, name, status=None, problem=0, **extra):
    record = {"id": instance_id, "name": name, "service": "", "class": "",
              "maker": "", "location": "", "port": "", "changed": None,
              "status": status, "problem": problem}
    record.update(extra)
    return record


def test_the_changes_section_names_a_new_device_and_its_problem():
    devices = {
        r"USB\VID_0403&PID_6001\X1": _fake_record(
            r"USB\VID_0403&PID_6001\X1", "USB Serial Converter",
            status=devdiag.DN_HAS_PROBLEM, problem=28),
    }
    lines, _p, _w = devdiag.probe_changes({"devices": devices,
                                           "previous": {}})
    text = "\n".join(lines)
    assert "Appeared (1)" in text
    assert "USB Serial Converter" in text and "code 28" in text
    assert "FTDI" in text


def test_nothing_new_points_at_the_cable_not_the_driver():
    devices = {"A": _fake_record("A", "Mouse", status=devdiag.DN_STARTED)}
    lines, _p, warnings = devdiag.probe_changes({"devices": devices,
                                                 "previous": {"A": "Mouse"}})
    assert any("cable" in line for line in lines)
    assert warnings and "not a driver" in warnings[0]


def test_a_problem_device_is_a_problem_and_a_disabled_one_a_note():
    devices = {
        "X": _fake_record("X", "Broken", status=devdiag.DN_HAS_PROBLEM,
                          problem=43),
        "Y": _fake_record("Y", "Off", status=devdiag.DN_HAS_PROBLEM,
                          problem=22),
        "Z": _fake_record("Z", "Fine", status=devdiag.DN_STARTED),
    }
    _lines, problems, warnings = devdiag.probe_attention({"devices": devices})
    assert len(problems) == 1 and "Broken" in problems[0]
    assert len(warnings) == 1 and "Off" in warnings[0]


def test_an_ftdi_device_without_a_com_port_is_flagged():
    devices = {
        r"USB\VID_0403&PID_6001\X1": _fake_record(
            r"USB\VID_0403&PID_6001\X1", "USB Serial Converter",
            status=devdiag.DN_STARTED, service="FTDIBUS"),
    }
    _lines, _p, warnings = devdiag.probe_usb({"devices": devices})
    assert any("Load VCP" in item for item in warnings), warnings
    devices[r"FTDIBUS\VID_0403+PID_6001+X1A\0000"] = _fake_record(
        r"FTDIBUS\VID_0403+PID_6001+X1A\0000", "USB Serial Port (COM5)",
        status=devdiag.DN_STARTED, port="COM5")
    _lines, _p, warnings = devdiag.probe_usb({"devices": devices})
    assert not any("Load VCP" in item for item in warnings), warnings


def test_a_com_port_is_shown_with_its_visa_address():
    lines = []
    devdiag.describe_device(_fake_record("FTDIBUS\\X", "USB Serial Port",
                                         status=devdiag.DN_STARTED,
                                         port="COM5"), lines)
    assert any("ASRL5::INSTR" in line for line in lines)


# ===========================================================================
# 5. The survey runs on this machine
# ===========================================================================

def test_the_survey_runs_every_section_twice():
    keys = {key for key, _label, _cost, _default in devdiag.DEEP_SECTIONS}
    assert keys == set(devdiag.PROBE_FUNCTIONS)
    lines = []
    problems, warnings, snapshot = devdiag.run_survey(keys, lines.append)
    text = "\n".join(lines)
    for heading in ("THIS MACHINE", "CHANGES SINCE THE PREVIOUS RUN",
                    "VERDICT"):
        assert heading in text, heading
    assert isinstance(problems, list) and isinstance(warnings, list)
    if IS_WINDOWS:
        assert snapshot, "Windows always has some device connected"
        lines = []
        devdiag.run_survey(set(), lines.append, snapshot)
        assert "first run" not in "\n".join(lines)


def test_the_survey_touches_no_tk():
    source = inspect.getsource(devdiag.run_survey)
    for banned in ("tk.", "ttk.", "messagebox", "self."):
        assert banned not in source, banned


# ===========================================================================
# 6. The window
# ===========================================================================

def test_the_worker_never_touches_tk():
    source = inspect.getsource(devdiag.ConnectedDevicesDiagnosticsGUI._start)
    work = source.split("def work(", 1)[1].split("self.worker =", 1)[0]
    for banned in ("self._log_line", "self.console", "self.status_var",
                   "self.progress", "self.headline", "self.previous"):
        assert banned not in work, banned
    assert "self.queue.put" in work


def test_the_poll_chain_is_cancelled_on_close():
    source = inspect.getsource(
        devdiag.ConnectedDevicesDiagnosticsGUI._on_closing)
    assert "after_cancel" in source
    assert "self.after_id = None" in source


def test_no_dialog_pops_during_a_run():
    for name in ("_start", "_drain", "_finish"):
        source = inspect.getsource(
            getattr(devdiag.ConnectedDevicesDiagnosticsGUI, name))
        assert "messagebox" not in source, name


def test_it_uses_the_house_palette():
    cls = devdiag.ConnectedDevicesDiagnosticsGUI
    assert cls.CLR_BG_DARK == '#B8A392'
    assert cls.CLR_HEADER == '#E5DCD3'
    assert cls.CLR_ACCENT_GOLD == '#BA6B5E'
    assert cls.FONT_BASE == ('Segoe UI', 11)


# ===========================================================================
# 7. Wiring
# ===========================================================================

def test_it_is_in_script_paths_and_on_disk():
    from pica.main import PICALauncherApp
    path = PICALauncherApp.SCRIPT_PATHS["Connected Device Diagnostics"]
    assert os.path.basename(path) == "Diagnostics_Connected_Devices_GUI.py"
    assert os.path.isfile(path), path


def test_it_is_listed_in_the_diagnostic_tools_menu():
    from pica.main_v2 import DIAGNOSTIC_TOOLS
    keys = {key for _label, key in DIAGNOSTIC_TOOLS}
    assert "Connected Device Diagnostics" in keys


def test_the_advanced_catalogue_suite_matches_the_menu():
    from pica.main_v2 import CATALOG, DIAGNOSTIC_TOOLS
    suites = [s for s in CATALOG if s['category'] == "Diagnostic Tools"]
    assert len(suites) == 1
    keys = {entry[1] for entry in suites[0]['modules']}
    assert keys == {key for _label, key in DIAGNOSTIC_TOOLS}


if __name__ == "__main__":
    tests = [(name, func) for name, func in sorted(globals().items())
             if name.startswith("test_") and callable(func)]
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {name}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
