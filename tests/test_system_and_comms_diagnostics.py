"""Tests for the machine and bus diagnostics consoles (17 Sep 2026):

    pica/utils/Diagnostics_System_Info_GUI.py
    pica/utils/Diagnostics_Comms_Interfaces_GUI.py

The first asks what this computer has installed for talking to
instruments -- NI-488.2, NI-VISA, the Keysight IO libraries -- and reads
each driver DLL's bit-ness out of its PE header. The second asks what
interfaces physically exist: GPIB cards and adapters, serial ports, USB
controllers, network, and the VISA addresses they add up to.

Both are read-only, and the comms one is quieter than that: it must
never send *IDN? and must never OPEN a serial port, because opening one
asserts DTR and can reset the instrument on the other end. Those two
promises are the ones a future edit is most likely to break by accident,
so they are pinned here by name.

No hardware, and nothing here touches the bus. Runnable as plain Python
as well as under pytest:
    python tests/test_system_and_comms_diagnostics.py
"""

import importlib.util
import inspect
import os
import struct
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

SYSTEM_PATH = os.path.join(project_root, "pica", "utils",
                           "Diagnostics_System_Info_GUI.py")
COMMS_PATH = os.path.join(project_root, "pica", "utils",
                          "Diagnostics_Comms_Interfaces_GUI.py")


def _load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


sysdiag = _load("pica_system_diagnostics_module", SYSTEM_PATH)
comms = _load("pica_comms_diagnostics_module", COMMS_PATH)
SYSTEM_SOURCE = open(SYSTEM_PATH, encoding="utf-8").read()
COMMS_SOURCE = open(COMMS_PATH, encoding="utf-8").read()

IS_WINDOWS = os.name == 'nt'


# ===========================================================================
# 1. Both change nothing
# ===========================================================================

def test_neither_program_has_a_write_path():
    for source, name in ((SYSTEM_SOURCE, "system"), (COMMS_SOURCE, "comms")):
        writes = [line for line in source.splitlines()
                  if "io.open(" in line and "'w'" in line]
        assert len(writes) == 1, (name, writes)   # the Save Log As... file
        assert "_save_as" in source, name


def test_the_registry_is_only_ever_opened_for_reading():
    for source, name in ((SYSTEM_SOURCE, "system"), (COMMS_SOURCE, "comms")):
        for banned in ("SetValue", "CreateKey", "DeleteKey", "DeleteValue",
                       "KEY_WRITE", "KEY_SET_VALUE", "KEY_ALL_ACCESS"):
            assert banned not in source, (name, banned)
        assert "KEY_READ" in source, name


def test_neither_program_shells_out():
    """A read-only tool that can run a command is not a read-only tool."""
    for source, name in ((SYSTEM_SOURCE, "system"), (COMMS_SOURCE, "comms")):
        for banned in ("subprocess", "os.system", "os.popen", "Popen",
                       "check_call", "check_output"):
            assert banned not in source, (name, banned)


def test_no_service_or_installer_is_touched():
    for banned in ("StartService", "ControlService", "sc.exe", "msiexec",
                   "OpenSCManager"):
        assert banned not in SYSTEM_SOURCE, banned


# ===========================================================================
# 2. The comms tool is quieter than read-only
# ===========================================================================

def test_the_comms_tool_never_sends_idn():
    """Identification belongs to the Instrument Status window.

    The Novocontrol Alpha must never be probed automatically, and a
    diagnostic that addresses every enumerated address is exactly such a
    probe.
    """
    # *IDN? appears only in prose saying it is NOT sent; what matters is
    # that no VISA session is ever opened or written to.
    for banned in ("query(", "open_resource", "write_raw", "read_raw",
                   "visa_write"):
        assert banned not in COMMS_SOURCE, banned
    for line in COMMS_SOURCE.splitlines():
        if "*IDN" not in line:
            continue
        assert any(word in line for word in ("No *IDN?", "no *IDN?",
                                             "sends *IDN?")), line


def test_the_comms_tool_never_opens_a_serial_port():
    """Opening a COM port asserts DTR and can reset the device behind it."""
    for banned in ("import serial", "serial.Serial", "Serial(", "pyserial"):
        assert banned not in COMMS_SOURCE.replace("USB-serial", "").replace(
                "serial port", "").replace("SERIAL_SERVICES", ""), banned


def test_the_comms_tool_only_enumerates_visa():
    source = inspect.getsource(comms.probe_visa)
    assert "list_resources" in source
    assert "open_resource" not in source
    assert "write" not in source


def test_both_programs_say_what_they_do_not_do():
    assert "read-only" in SYSTEM_SOURCE.lower()
    assert "no *IDN? is sent" in COMMS_SOURCE
    assert "asserts DTR" in COMMS_SOURCE


# ===========================================================================
# 3. Bit-ness: the reason the system tool exists
# ===========================================================================

def test_pe_machine_reads_a_real_binary_on_this_machine():
    """python.exe is a PE file whose bit-ness we already know."""
    if not IS_WINDOWS:
        return
    verdict = sysdiag.pe_machine(sys.executable)
    bits = struct.calcsize("P") * 8
    assert f"{bits}-bit" in verdict, verdict


def test_pe_machine_refuses_a_file_that_is_not_a_binary():
    verdict = sysdiag.pe_machine(__file__)
    assert verdict == "not a Windows binary", verdict


def test_pe_machine_does_not_raise_on_a_missing_file():
    verdict = sysdiag.pe_machine(os.path.join(os.sep, "no", "such", "x.dll"))
    assert "unreadable" in verdict, verdict


def test_the_pe_machine_table_covers_both_bit_nesses():
    values = set(sysdiag.PE_MACHINES.values())
    assert any("32-bit" in item for item in values)
    assert any("64-bit" in item for item in values)


def test_the_survey_states_this_python_s_bit_ness():
    lines = []
    sysdiag.run_survey(set(), lines.append)
    bits = struct.calcsize("P") * 8
    assert any(f"{bits}-bit" in line for line in lines)


# ===========================================================================
# 4. Windows name parsing (the bit that produced "(AMD,3.10,1.10)")
# ===========================================================================

def test_a_plain_device_name_is_returned_as_is():
    assert comms.readable_name("HP Wide Vision HD Camera") == \
        "HP Wide Vision HD Camera"


def test_an_indirect_name_resolves_to_the_half_after_the_first_semicolon():
    value = r"@usbxhci.inf,%pci\cc_0c0330.devicedesc%;USB xHCI Compliant " \
            r"Host Controller"
    assert comms.readable_name(value) == "USB xHCI Compliant Host Controller"


def test_a_name_with_unfilled_placeholders_is_declined():
    """Taking the LAST semicolon field is how a controller got reported as
    its firmware revision."""
    value = (r"@System32\drivers\usbxhci.sys,#1073807361;%1 USB %2 "
             r"eXtensible Host Controller - %3 (Microsoft);(AMD,3.10,1.10)")
    assert comms.readable_name(value) == ""


def test_an_empty_or_missing_name_is_empty():
    assert comms.readable_name(None) == ""
    assert comms.readable_name("") == ""
    assert comms.readable_name("@only.inf,%nothing%") == ""


# ===========================================================================
# 5. Telling a USB-serial adapter from anything else
# ===========================================================================

def test_a_composite_device_is_not_a_serial_adapter():
    """'USB Composite Device' contains the letters 'com'."""
    assert not comms.is_usb_serial("USB Composite Device", "usbccgp")


def test_a_real_adapter_is_recognised_by_its_driver():
    assert comms.is_usb_serial("", "usbser")
    assert comms.is_usb_serial("", "FTDIBUS")
    assert comms.is_usb_serial("", "silabser")


def test_a_real_adapter_is_recognised_by_its_name():
    assert comms.is_usb_serial("USB Serial Port (COM3)", "")
    assert comms.is_usb_serial("FTDI FT232R USB UART", "")


def test_the_vendor_table_names_the_makers_this_lab_uses():
    assert "0403" in comms.USB_VENDORS, "FTDI -- the TPG 361's USB cable"
    assert "3923" in comms.USB_VENDORS, "National Instruments"
    assert "1093" in comms.PCI_VENDORS, "the NI PCI GPIB card"


def test_vendor_of_reads_both_usb_and_pci_id_forms():
    name, code = comms.vendor_of("VID_0403&PID_6001", comms.USB_VENDORS)
    assert code == "0403" and "FTDI" in name
    name, code = comms.vendor_of("VEN_1093&DEV_C801", comms.PCI_VENDORS)
    assert code == "1093" and "National Instruments" in name
    name, code = comms.vendor_of("ROOT_HUB30", comms.USB_VENDORS)
    assert name is None and code is None


# ===========================================================================
# 6. The surveys run
# ===========================================================================

def test_the_system_survey_runs_every_section():
    keys = {key for key, _label, _cost, _default in sysdiag.DEEP_SECTIONS}
    assert keys == set(sysdiag.PROBE_FUNCTIONS)
    lines = []
    problems, warnings = sysdiag.run_survey(keys, lines.append)
    text = "\n".join(lines)
    assert "THIS MACHINE" in text
    assert "VERDICT" in text
    assert isinstance(problems, list) and isinstance(warnings, list)


def test_the_comms_survey_runs_every_section():
    keys = {key for key, _label, _cost, _default in comms.DEEP_SECTIONS}
    assert keys == set(comms.PROBE_FUNCTIONS)
    lines = []
    problems, warnings = comms.run_survey(keys, lines.append)
    text = "\n".join(lines)
    assert "THIS MACHINE" in text
    assert "VERDICT" in text
    assert isinstance(problems, list) and isinstance(warnings, list)


def test_the_system_survey_reports_the_driver_libraries_by_name():
    lines = []
    sysdiag.run_survey({"dlls"}, lines.append)
    text = "\n".join(lines)
    if IS_WINDOWS:
        for name, _purpose in sysdiag.DRIVER_DLLS:
            assert name in text, name


def test_a_machine_with_no_gpib_driver_is_told_so_rather_than_left_guessing():
    lines = []
    _problems, warnings = comms.run_survey({"gpib"}, lines.append)
    text = "\n".join(lines)
    assert "GPIB INTERFACES" in text
    if IS_WINDOWS and "none found" in text and "none registered" in text:
        assert any("GPIB" in item for item in warnings), warnings


def test_the_comms_survey_groups_addresses_by_bus():
    families = {name for name, _purpose in comms.VISA_BUS_NAMES}
    assert {"GPIB", "USB", "TCPIP", "ASRL"} <= families


def test_neither_survey_touches_tk():
    for module in (sysdiag, comms):
        source = inspect.getsource(module.run_survey)
        for banned in ("tk.", "ttk.", "messagebox", "self."):
            assert banned not in source, (module.__name__, banned)


# ===========================================================================
# 7. The consoles
# ===========================================================================

def test_the_workers_never_touch_tk():
    for module, cls in ((sysdiag, sysdiag.SystemInfoDiagnosticsGUI),
                        (comms, comms.CommsInterfaceDiagnosticsGUI)):
        source = inspect.getsource(cls._start)
        work = source.split("def work(", 1)[1].split("self.worker =", 1)[0]
        for banned in ("self._log_line", "self.console", "self.status_var",
                       "self.progress"):
            assert banned not in work, (module.__name__, banned)
        assert "self.queue.put" in work


def test_the_poll_chains_are_cancelled_on_close():
    for cls in (sysdiag.SystemInfoDiagnosticsGUI,
                comms.CommsInterfaceDiagnosticsGUI):
        source = inspect.getsource(cls._on_closing)
        assert "after_cancel" in source
        assert "self.after_id = None" in source


def test_both_consoles_use_the_house_palette():
    for cls in (sysdiag.SystemInfoDiagnosticsGUI,
                comms.CommsInterfaceDiagnosticsGUI):
        assert cls.CLR_BG_DARK == '#B8A392'
        assert cls.CLR_HEADER == '#E5DCD3'
        assert cls.CLR_ACCENT_GOLD == '#BA6B5E'
        assert cls.FONT_BASE == ('Segoe UI', 11)


def test_neither_is_mistaken_for_a_measurement():
    for source in (SYSTEM_SOURCE, COMMS_SOURCE):
        for banned in ("sample_name", "FigureCanvas", "pyplot", "savefig"):
            assert banned not in source, banned


# ===========================================================================
# 8. Wiring: Diagnostic Tools in both launchers
# ===========================================================================

def test_both_are_in_script_paths_and_on_disk():
    from pica.main import PICALauncherApp
    for key, filename in (
            ("System and Driver Diagnostics",
             "Diagnostics_System_Info_GUI.py"),
            ("Communication Interface Diagnostics",
             "Diagnostics_Comms_Interfaces_GUI.py")):
        path = PICALauncherApp.SCRIPT_PATHS[key]
        assert os.path.basename(path) == filename
        assert os.path.isfile(path), path


def test_both_are_listed_in_the_diagnostic_tools_menu():
    from pica.main_v2 import DIAGNOSTIC_TOOLS
    keys = {key for _label, key in DIAGNOSTIC_TOOLS}
    assert "System and Driver Diagnostics" in keys
    assert "Communication Interface Diagnostics" in keys


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
