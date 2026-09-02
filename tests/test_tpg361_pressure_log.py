"""Tests for the Pfeiffer TPG 361 pressure logger module.

No Tk window and no serial port: only the protocol-facing helpers (which are
module-level functions for exactly this reason) and the launcher wiring are
exercised. The reply strings used here are the forms the TPG 26x/36x manual
documents for AYT / UNI / PR1.

Runnable as plain Python as well as under pytest.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

TPG_PATH = os.path.join(REPO_ROOT, "pica", "pfeiffer",
                        "Pressure_Log_TPG361_GUI.py")
V1_PATH = os.path.join(REPO_ROOT, "pica", "main.py")
V2_PATH = os.path.join(REPO_ROOT, "pica", "main_v2.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tpg = _load("pica_tpg361_module", TPG_PATH)
TPG_SOURCE = open(TPG_PATH, encoding="utf-8").read()


# ------------------------------------------------------------ PR1 parsing

def test_a_good_pr1_reply_gives_status_zero_and_the_pressure():
    status, value = tpg.parse_pressure_reply("0,+1.0000E-03")
    assert status == 0
    assert abs(value - 1.0e-3) < 1e-12


def test_a_negative_exponent_at_atmosphere_parses():
    status, value = tpg.parse_pressure_reply("0,+1.0133E+03")
    assert status == 0
    assert abs(value - 1013.3) < 1e-9


def test_an_underrange_reply_keeps_its_status_code():
    status, _value = tpg.parse_pressure_reply("1,+7.3000E-11")
    assert status == 1
    assert tpg.status_text(status) == "Underrange"


def test_whitespace_around_the_fields_is_tolerated():
    status, value = tpg.parse_pressure_reply(" 0 , +2.5000E-02 ")
    assert status == 0
    assert abs(value - 2.5e-2) < 1e-12


def test_a_truncated_reply_is_an_error_not_a_silent_zero():
    for bad in ("", "0", "no,data", "x,+1.0E-03"):
        try:
            tpg.parse_pressure_reply(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


def test_every_documented_status_code_has_words():
    for code in range(7):
        assert tpg.status_text(code) != f"Unknown status {code}"


def test_an_undocumented_status_code_still_reads_as_something():
    assert "9" in tpg.status_text(9)


# ------------------------------------------------------------ UNI parsing

def test_the_unit_codes_map_to_their_names():
    assert tpg.parse_unit_reply("0") == "mbar"
    assert tpg.parse_unit_reply("1") == "Torr"
    assert tpg.parse_unit_reply("2") == "Pa"


def test_an_unknown_unit_code_does_not_stop_a_run():
    # The pressure is still valid, only its label is in doubt.
    assert tpg.parse_unit_reply("9") == "unit-9"
    assert tpg.parse_unit_reply("junk") == "unknown"


def test_pressure_is_always_formatted_in_exponent_form():
    assert tpg.format_pressure(1.0e-6, "mbar") == "1.000E-06 mbar"


# ------------------------------------------------------- protocol details

def test_the_enq_is_sent_raw_so_no_terminator_is_appended():
    # write() appends CRLF; the controller would then wait forever instead of
    # answering. This is the single easiest way to break the module.
    assert tpg.ENQ == b"\x05"
    assert "write_raw(ENQ)" in TPG_SOURCE


def test_nothing_in_the_backend_writes_a_setting_to_the_gauge():
    # Only AYT, UNI and PR1 -- all queries. A pressure logger that could
    # change a gauge setting has no business near somebody else's pump-down.
    for mnemonic in ("'AYT'", "'UNI'", "'PR1'"):
        assert f"query_mnemonic({mnemonic})" in TPG_SOURCE
    for forbidden in ("'UNI,", "'RES", "'SEN", "'SP1", "'CAL"):
        assert forbidden not in TPG_SOURCE


def test_the_worker_recovers_from_comm_errors_instead_of_giving_up():
    # Unattended pump-down logs: a knocked cable must not end the run.
    assert "_reconnect_with_backoff" in TPG_SOURCE
    assert "RECONNECT_BACKOFFS" in TPG_SOURCE


def test_every_data_row_is_fsynced():
    assert "os.fsync" in TPG_SOURCE


def test_no_modal_dialog_can_appear_once_logging_has_started():
    # messagebox is allowed at startup and on close (somebody is there); a
    # dialog raised mid-run would block the queue drain and stop the log.
    runtime = TPG_SOURCE.split("def _measurement_worker", 1)[1]
    runtime = runtime.split("def _scan_for_serial_ports", 1)[0]
    assert "messagebox" not in runtime


# ------------------------------------------------ USB / Ethernet addressing

WINDOWS_ARP = """
Interface: 192.168.1.2 --- 0xa
  Internet Address      Physical Address      Type
  192.168.1.1           6c-4f-89-21-47-43     dynamic
  192.168.1.77          00-a0-41-1b-2c-3d     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.251           01-00-5e-00-00-fb     static
"""

UNIX_ARP = """
? (10.0.0.5) at 00:A0:41:aa:bb:cc [ether] on eth0
? (10.0.0.1) at 00:11:22:33:44:55 [ether] on eth0
"""


def test_a_com_port_in_any_spelling_becomes_an_asrl_resource():
    for given in ("COM5", "com5", "5", "ASRL5", "asrl5::INSTR", " COM5 "):
        assert tpg.normalise_resource(given) == "ASRL5::INSTR", given


def test_an_ip_address_becomes_a_socket_resource_on_port_8000():
    # The TPG 36x Ethernet port is fixed at TCP 8000; nobody should have to
    # know the VISA SOCKET syntax to use it.
    assert tpg.TPG_TCP_PORT == 8000
    assert tpg.normalise_resource("192.168.1.50") ==         "TCPIP0::192.168.1.50::8000::SOCKET"
    assert tpg.normalise_resource("192.168.1.50:8000") ==         "TCPIP0::192.168.1.50::8000::SOCKET"
    assert tpg.normalise_resource("tpg361.lab") ==         "TCPIP0::tpg361.lab::8000::SOCKET"


def test_a_full_visa_string_passes_through_untouched():
    for given in ("TCPIP0::192.168.1.50::8000::SOCKET", "USB0::0x1234::0x5678::INSTR"):
        assert tpg.normalise_resource(given) == given
    assert tpg.normalise_resource("") == ""
    assert tpg.normalise_resource(None) == ""


def test_the_baud_is_only_applied_on_a_serial_link():
    assert tpg.is_network_resource("TCPIP0::192.168.1.50::8000::SOCKET")
    assert not tpg.is_network_resource("ASRL5::INSTR")
    # connect() must guard the serial attribute block on this predicate.
    connect = TPG_SOURCE.split("def connect", 1)[1].split("def _drain", 1)[0]
    assert "is_network_resource(self.visa_address)" in connect


def test_the_file_header_says_which_link_was_used():
    assert "Ethernet" in tpg.describe_link("TCPIP0::10.0.0.5::8000::SOCKET", 9600)
    assert "9600 baud" in tpg.describe_link("ASRL5::INSTR", 9600)


def test_pfeiffer_hosts_are_picked_out_of_the_arp_table_by_oui():
    assert tpg.PFEIFFER_OUI == "00-A0-41"
    assert tpg.pfeiffer_hosts_from_arp(WINDOWS_ARP) == ["192.168.1.77"]
    assert tpg.pfeiffer_hosts_from_arp(UNIX_ARP) == ["10.0.0.5"]
    assert tpg.pfeiffer_hosts_from_arp("") == []


def test_the_lan_search_only_ever_speaks_to_pfeiffer_macs():
    # AYT (read-only) goes to hosts from pfeiffer_hosts_from_arp and nowhere
    # else; the sweep itself is plain ping.
    worker = TPG_SOURCE.split("def _find_on_lan_worker", 1)[1]
    worker = worker.split("def _offer_lan_choices", 1)[0]
    assert "pfeiffer_hosts_from_arp" in worker
    assert "for ip in hosts" in worker
    assert "_ayt_at(ip)" in worker
    ayt = TPG_SOURCE.split("def _ayt_at", 1)[1].split("def _find_on_lan_worker", 1)[0]
    assert "write('AYT')" in ayt and "write_raw(ENQ)" in ayt


def test_the_ping_sweep_is_opt_in():
    assert "lan_sweep_var = tk.BooleanVar(value=False)" in TPG_SOURCE


def test_a_dropdown_choice_gives_back_the_bare_resource():
    assert tpg.resource_from_choice(
        "ASRL5::INSTR -- USB Serial Port (COM5)  [FTDI: likely the TPG]") == "ASRL5::INSTR"
    assert tpg.resource_from_choice("ASRL5::INSTR") == "ASRL5::INSTR"


def test_port_labelling_never_drops_a_port():
    given = ["ASRL1::INSTR", "ASRL99::INSTR"]
    got = tpg.serial_port_choices(given)
    assert sorted(tpg.resource_from_choice(c) for c in got) == sorted(given)


def test_the_launcher_normalises_addresses_the_same_way():
    v2 = _v2()
    assert v2.GAUGE_TCP_PORT == 8000
    assert v2.normalise_gauge_resource("COM7") == "ASRL7::INSTR"
    assert v2.normalise_gauge_resource("192.168.1.50") ==         "TCPIP0::192.168.1.50::8000::SOCKET"
    assert v2.is_network_gauge("TCPIP0::192.168.1.50::8000::SOCKET")
    assert v2.pfeiffer_hosts_from_arp(WINDOWS_ARP) == ["192.168.1.77"]


def test_a_saved_ip_address_comes_back_as_a_socket_resource(tmp_path=None):
    v2 = _v2()
    path = (os.path.join(str(tmp_path), "gauge.json") if tmp_path
            else os.path.join(REPO_ROOT, "tests", "_gauge_ip_test.json"))
    try:
        assert v2.save_pressure_gauge("192.168.1.50", 9600, path)
        assert v2.load_pressure_gauge(path) == {
            "resource": "TCPIP0::192.168.1.50::8000::SOCKET", "baud": 9600}
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_the_launcher_never_sends_idn_to_the_configured_gauge():
    # Even if VISA lists the gauge's resource (a TCPIP one would be
    # "probeable"), the scan loop must skip it: it speaks mnemonics, not
    # SCPI, and a *IDN? is not something it should ever see.
    v2_source = open(V2_PATH, encoding="utf-8").read()
    loop = v2_source.split("for res in resources:", 1)[1].split("idn = None", 1)[0]
    assert "gauge['resource'].upper()" in loop
    assert "read by its own protocol" in loop


# --------------------------------------------------------- launcher wiring

def _v2():
    return _load("pica_main_v2_tpg", V2_PATH)


def test_the_launcher_reads_no_serial_port_until_one_is_configured():
    # The whole point of the opt-in file: with no gauge set, the launcher's
    # scan must not open any ASRL resource at all.
    v2 = _v2()
    missing = os.path.join(REPO_ROOT, "tests", "no_such_gauge_file.json")
    assert v2.load_pressure_gauge(missing) is None


def test_a_saved_gauge_round_trips(tmp_path=None):
    import tempfile
    v2 = _v2()
    path = os.path.join(tempfile.mkdtemp(), "gauge.json")
    assert v2.save_pressure_gauge("ASRL3::INSTR", 19200, path)
    cfg = v2.load_pressure_gauge(path)
    assert cfg == {"resource": "ASRL3::INSTR", "baud": 19200}
    # Clearing removes the file, which is what switches the tile back off.
    assert v2.save_pressure_gauge(None, path=path)
    assert v2.load_pressure_gauge(path) is None


def test_a_corrupt_gauge_file_reads_as_no_gauge():
    import tempfile
    v2 = _v2()
    path = os.path.join(tempfile.mkdtemp(), "gauge.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    assert v2.load_pressure_gauge(path) is None


def test_the_launcher_speaks_the_same_three_step_protocol():
    v2 = _v2()
    assert v2.GAUGE_ENQ == b""
    v2_source = open(V2_PATH, encoding="utf-8").read()
    assert "write_raw(GAUGE_ENQ)" in v2_source
    # UNI and PR1 only -- both queries.
    assert "_gauge_query(inst, 'UNI')" in v2_source
    assert "_gauge_query(inst, 'PR1')" in v2_source


def test_a_port_choice_survives_its_friendly_label():
    v2 = _v2()
    assert v2.gauge_resource_from_choice(
        "ASRL3::INSTR — USB Serial Port (COM3)") == "ASRL3::INSTR"
    assert v2.gauge_resource_from_choice("ASRL3::INSTR") == "ASRL3::INSTR"
    assert v2.gauge_resource_from_choice("") == ""


def test_port_labelling_never_drops_a_resource():
    # Without pyserial, or for a port it does not know, the resource must
    # still reach the dropdown -- unlabelled is fine, missing is not.
    v2 = _v2()
    given = ["ASRL1::INSTR", "ASRL99::INSTR"]
    got = v2.gauge_port_choices(given)
    assert len(got) == len(given)
    for res, choice in zip(given, got):
        assert v2.gauge_resource_from_choice(choice) == res


def test_the_scan_result_always_carries_the_pressure_keys():
    v2 = _v2()
    v2_source = open(V2_PATH, encoding="utf-8").read()
    for key in ("'pressure'", "'pressure_units'", "'pressure_source'",
                "'pressure_error'"):
        assert key in v2_source


def test_the_module_is_registered_in_both_launchers():
    v1 = _load("pica_main_v1_tpg", V1_PATH)
    key = "TPG361 Pressure Log"
    paths = v1.PICALauncherApp.SCRIPT_PATHS
    assert key in paths
    assert os.path.exists(os.path.abspath(paths[key]))

    v2 = _load("pica_main_v2_tpg", V2_PATH)
    keys = {k for cat in v2.CATALOG for _label, k, _family in cat["modules"]}
    assert key in keys


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print("all passed" if not failures else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
