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
