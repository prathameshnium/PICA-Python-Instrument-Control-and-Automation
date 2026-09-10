"""
Purpose: No-hardware tests for
pica/keysight/Field_Step_Freq_Scan_E4980A_GUI.py - the field-list
generator, the file-name builder, the header builder, the FETCh reply
parser, the two temperature-reply parsers (including the Cryo-con dots
case) and the unattended-run policy of the worker code.

Runnable as plain python too:
    python tests/test_field_step_freq_scan.py
"""

import inspect
import math
import os
import sys
import tempfile

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.keysight import Field_Step_Freq_Scan_E4980A_GUI as m


# ----------------------------------------------------------------- field list

def test_generate_ascending_by_step():
    assert m.generate_field_list(0, 1000, step=250) == [0, 250, 500, 750, 1000]


def test_generate_descending_by_step_sign_of_step_ignored():
    assert m.generate_field_list(1000, 0, step=-250) == [1000, 750, 500, 250, 0]


def test_generate_negative_and_decimal_fields():
    assert m.generate_field_list(-2.25, 2.25, step=1.5) == [-2.25, -0.75, 0.75, 2.25]
    assert m.generate_field_list(500, -500, step=500) == [500, 0, -500]


def test_generate_end_not_on_grid_is_excluded_like_T_step_builders():
    assert m.generate_field_list(0, 1000, step=300) == [0, 300, 600, 900]


def test_generate_by_number_of_points_includes_both_ends():
    assert m.generate_field_list(-1000, 1000, points=5) == [-1000, -500, 0, 500, 1000]
    assert m.generate_field_list(11.5, 11.5, points=3) == [11.5]
    assert m.generate_field_list(7, 9, points=1) == [7]


def test_generate_no_float_drift():
    vals = m.generate_field_list(0, 1, step=0.1)
    assert vals == [round(0.1 * i, 6) for i in range(11)]


def test_generate_rejects_zero_step_and_no_spec():
    for bad in (dict(step=0), dict(), dict(points=0)):
        try:
            m.generate_field_list(0, 10, **bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad}")


def test_add_fields_keeps_user_order_and_dedupes_by_default():
    merged, skipped = m.add_fields([0, 500], [500, -500, 0.0000001, 11.5])
    assert merged == [0, 500, -500, 11.5]
    assert skipped == 2


def test_add_fields_allow_repeats_for_hysteresis_loops():
    merged, skipped = m.add_fields([0, 500, 0], [-500, 0], allow_repeats=True)
    assert merged == [0, 500, 0, -500, 0]
    assert skipped == 0


# ---------------------------------------------------------------- formatting

def test_format_field_compact_ascii():
    assert m.format_field(500) == "500"
    assert m.format_field(-500) == "-500"
    assert m.format_field(11.5) == "11.5"
    assert m.format_field(-2.25) == "-2.25"
    assert m.format_field(-0.0) == "0"
    assert m.format_field(90000.0) == "90000"


def test_oe_to_tesla_and_display():
    assert m.oe_to_tesla(10000) == 1.0
    assert m.field_display(500) == "500 Oe (0.05 T)"
    assert m.field_display(-2.25) == "-2.25 Oe (-0.000225 T)"


def test_sanitize_sample_name():
    assert m.sanitize_sample_name("Co 07 / batch#2") == "Co_07_batch_2"
    assert m.sanitize_sample_name("   ") == "Sample"
    assert m.sanitize_sample_name("BaTiO₃").isascii()


# ------------------------------------------------------------------ filename

def test_filename_first_run_positive_field():
    assert (m.build_filename("Co07", 300, 500, "20260910_101010")
            == "Co07_T300K_H500Oe_20260910_101010.txt")


def test_filename_negative_and_decimal_fields():
    assert (m.build_filename("Co07", 77.5, -500, "20260910_101010")
            == "Co07_T77.5K_H-500Oe_20260910_101010.txt")
    assert (m.build_filename("Co07", 300, 11.5, "s")
            == "Co07_T300K_H11.5Oe_s.txt")
    assert (m.build_filename("Co07", 300, -2.25, "s")
            == "Co07_T300K_H-2.25Oe_s.txt")


def test_filename_run_index_appended_for_repeats():
    assert m.build_filename("Co07", 300, 0, "s", run_index=1) == "Co07_T300K_H0Oe_s.txt"
    assert m.build_filename("Co07", 300, 0, "s", run_index=2) == "Co07_T300K_H0Oe_run2_s.txt"


def test_filename_is_ascii_even_for_unicode_sample():
    name = m.build_filename("Sampleµ", 300, 0, "s")
    assert name.isascii()


def test_unique_path_never_overwrites():
    with tempfile.TemporaryDirectory() as d:
        p1 = m.unique_path(d, "a_T300K_H0Oe_s.txt")
        assert p1 == os.path.join(d, "a_T300K_H0Oe_s.txt")
        open(p1, "w").close()
        p2 = m.unique_path(d, "a_T300K_H0Oe_s.txt")
        assert p2.endswith("a_T300K_H0Oe_s_dup2.txt")
        open(p2, "w").close()
        assert m.unique_path(d, "a_T300K_H0Oe_s.txt").endswith("_dup3.txt")


# -------------------------------------------------------------------- header

def _info(**over):
    base = dict(sample="Co07", t_nominal=300, field_oe=-500, run_index=1,
                controller="Lake Shore 350", controller_idn="LSCI,MODEL350,1,1.0",
                controller_channel="A", lcr_idn="Keysight,E4980A,MY1,A.02",
                t_start=300.0123, t_end=300.0456, t_min=300.01, t_max=300.05,
                ac_bias=1.0, dc_bias=0.0, aper="MED", alc=True, corr=True,
                cable_len="1", delay=0.2, n_points=377, f_min="40", f_max="2e+06",
                started="2026-09-10 10:10:10", finished="2026-09-10 10:15:00",
                filename="Co07_T300K_H-500Oe_20260910_101010.csv", status="complete")
    base.update(over)
    return base


def test_header_has_all_required_facts_and_column_line_last():
    lines = m.build_header_lines(_info())
    text = "\n".join(lines[:-1])
    assert all(l.startswith("#") for l in lines[:-1])
    assert lines[-1] == m.DATA_HEADER
    for needle in ("Sample: Co07", "T_nominal(K): 300", "H_set(Oe): -500",
                   "-0.05 T", "Lake Shore 350", "LSCI,MODEL350",
                   "T_measured_start(K): 300.0123", "T_measured_end(K): 300.0456",
                   "E4980A", "AC level(Vrms): 1.0", "Aperture: MED",
                   "Frequency points: 377", "Started: 2026-09-10 10:10:10",
                   "Finished: 2026-09-10 10:15:00", "Status: complete",
                   "Co07_T300K_H-500Oe_20260910_101010.csv"):
        assert needle in text, needle


def test_header_missing_temperature_says_not_recorded():
    lines = m.build_header_lines(_info(t_start=None, t_end=float("nan"),
                                       controller="no thermometer"))
    text = "\n".join(lines)
    assert "T_measured_start(K): not recorded" in text
    assert "T_measured_end(K): not recorded" in text


def test_header_is_ascii_only():
    lines = m.build_header_lines(_info(sample="Sampleµ", lcr_idn="é"))
    assert all(l.isascii() for l in lines)


def test_data_header_columns():
    cols = m.DATA_HEADER.split("\t")
    assert cols[0] == "Frequency"
    assert cols[-2:] == ["T_set(K)", "H_set(Oe)"]
    assert len(cols) == 21          # 19 legacy columns + 2 identifiers


# --------------------------------------------------------------- FETCh parser

def test_parse_fetch_reply_three_fields():
    assert m.parse_fetch_reply("+1.23456E+03,-4.56789E+05,+0\n") == (1234.56, -456789.0, 0)


def test_parse_fetch_reply_status_and_missing_status():
    a, b, st = m.parse_fetch_reply("1.0,2.0,+1")
    assert (a, b, st) == (1.0, 2.0, 1)
    assert m.parse_fetch_reply("1.0,2.0") == (1.0, 2.0, 0)


def test_parse_fetch_reply_garbage_raises():
    for bad in ("", "abc", "1.0"):
        try:
            m.parse_fetch_reply(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


# ------------------------------------------------------- temperature parsers

def test_lakeshore_parser():
    assert m.parse_lakeshore_temperature("+2.95000E+02\r\n") == 295.0
    assert m.parse_lakeshore_temperature("77.35") == 77.35
    assert m.parse_lakeshore_temperature("77.35,300.1") == 77.35
    assert m.parse_lakeshore_temperature("") is None
    assert m.parse_lakeshore_temperature("garbage") is None


def test_cryocon_parser_numbers():
    assert m.parse_cryocon_temperature("77.350") == 77.35
    assert m.parse_cryocon_temperature("77.350K") == 77.35
    assert m.parse_cryocon_temperature("2.9501E+02\r\n") == 295.01
    assert m.parse_cryocon_temperature("77.350;300.000") == 77.35


def test_cryocon_parser_status_strings_are_no_reading():
    for status in (".......", "-------", "....", "---", "N/A", "NACK", ""):
        assert m.parse_cryocon_temperature(status) is None, status


# ----------------------------------------------------------- impedance maths

def test_impedance_parameters_ideal_capacitor():
    f = 1000.0
    C = 1e-9
    X = -1.0 / (2 * math.pi * f * C)
    vals = m.calculate_impedance_parameters(f, 0.0, X)
    assert len(vals) == 18
    assert abs(vals[4] - C) / C < 1e-9          # Cp
    assert abs(vals[2]) < 1e-15                 # G ~ 0
    assert abs(vals[15] - 2 * math.pi * f) < 1e-9   # omega


def test_default_grid_is_the_frequency_scan_grid():
    g = m.default_sweep_frequencies()
    assert g[0] == 40 and g[-1] == 2000000 and len(g) == 377


# ----------------------------------------------------- unattended-run policy

def test_worker_measure_uses_fsync_and_never_a_messagebox():
    src = inspect.getsource(m.FieldStepFreqScanGUI._w_measure)
    assert "fsync" in inspect.getsource(m.FieldStepFreqScanGUI._durable_write)
    assert "_durable_write" in src
    assert "messagebox" not in src
    for fn in (m.FieldStepFreqScanGUI._apply_event,
               m.FieldStepFreqScanGUI._on_scan_finished,
               m.FieldStepFreqScanGUI._on_scan_failed,
               m.FieldStepFreqScanGUI._worker_loop):
        assert "messagebox" not in inspect.getsource(fn), fn.__name__


def test_measurement_retries_same_point_after_comm_recovery():
    src = inspect.getsource(m.FieldStepFreqScanGUI._w_measure)
    assert "_w_comm_recover" in src and "while idx" in src and "continue" in src


def test_start_is_idempotent_and_stop_is_a_flag():
    src = inspect.getsource(m.FieldStepFreqScanGUI.start_measurement)
    assert src.strip().splitlines()[1].strip().startswith("if self.is_running")
    assert "stop_event.set()" in inspect.getsource(m.FieldStepFreqScanGUI.stop_measurement)


def test_thermometer_links_are_read_only():
    import ast
    for cls in (m.Lakeshore350_Link, m.CryoconLink):
        # Look at CODE only (string constants that are SCPI commands), not at
        # docstrings or comments that merely mention *RST.
        tree = ast.parse(inspect.getsource(cls))
        commands = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        commands.append(arg.value)
                    elif isinstance(arg, ast.JoinedStr):
                        commands.append("".join(v.value for v in arg.values
                                                if isinstance(v, ast.Constant)))
                if isinstance(node.func, ast.Attribute):
                    assert node.func.attr != "write", cls.__name__
        assert not any("*RST" in c for c in commands), cls.__name__
        assert all("?" in c for c in commands
                   if c.startswith(("*", "KRDG", "INPUT"))), cls.__name__
    assert "KRDG?" in inspect.getsource(m.Lakeshore350_Link.read_temperature)
    assert "INPUT?" in inspect.getsource(m.CryoconLink.read_temperature)


def test_cryocon_link_refuses_commands_without_question_mark():
    link = m.CryoconLink.__new__(m.CryoconLink)
    link.instrument = object()
    link._last_io = 0.0
    try:
        link.query("*RST")
    except ValueError:
        return
    raise AssertionError("CryoconLink.query accepted a non-query command")


def test_thermometer_defaults():
    assert m.THERMO_DEFAULT_ADDR[m.THERMO_LS350] == "GPIB0::12::INSTR"
    assert m.THERMO_DEFAULT_ADDR[m.THERMO_CC34] == "GPIB0::23::INSTR"
    assert m.open_thermometer(m.THERMO_NONE, "", "A") is None


def test_registered_in_launchers():
    with open(os.path.join(project_root, "pica", "main.py"), encoding="utf-8") as fh:
        assert "Field_Step_Freq_Scan_E4980A_GUI.py" in fh.read()
    with open(os.path.join(project_root, "pica", "main_v2.py"), encoding="utf-8") as fh:
        assert "LCR Field Step Freq. Scan (PPMS, manual H)" in fh.read()
    with open(os.path.join(project_root, "pica", "cli.py"), encoding="utf-8") as fh:
        assert "pica.keysight.Field_Step_Freq_Scan_E4980A_GUI" in fh.read()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
