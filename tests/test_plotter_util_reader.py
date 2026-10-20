"""PlotterUtil_GUI file reader: header detection and data loading.

Checks that every data file the three memristor modules write (sweep,
retention write/reads, endurance summary/raw) loads in the plotter with the
same columns and row count that the memristor analysis GUI sees, and that
files with empty cells, a trailing delimiter, or whitespace separation still
find their header row. No Tk: the reader is driven on a bare instance.
"""

import importlib.util
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from memristor_test_support import (  # noqa: E402
    FakeK2400, REPO_ROOT, analysis, run_endurance, run_retention, run_sweep, run_module_tests)


def _plotter_reader():
    path = os.path.join(REPO_ROOT, "pica", "utils", "PlotterUtil_GUI.py")
    spec = importlib.util.spec_from_file_location("plotter_util_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return object.__new__(mod.PlotterAppGUI)


def _memristor_outputs(folder):
    for sub in ("s", "r", "e"):
        os.makedirs(os.path.join(folder, sub))
    _, _, sweep_path = run_sweep(FakeK2400(1e6), os.path.join(folder, "s"), vmax_plus=3.0, vmax_minus=3.0)
    _, _, write_path, reads_path = run_retention(FakeK2400(1e5), os.path.join(folder, "r"), t_stop=3)
    res = run_endurance(FakeK2400(1e6), os.path.join(folder, "e"), n_cycles=3)
    endurance = [p for p in res if isinstance(p, str) and p.endswith(".csv")]
    assert len(endurance) == 2
    return [sweep_path, write_path, reads_path] + endurance


def test_plotter_reads_every_memristor_file_like_the_analysis_gui():
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        paths = _memristor_outputs(d)
        kinds = set()
        for path in paths:
            headers, delim, data = reader._read_data_from_file(path)
            header_lines, columns, rows = analysis.parse_pica_csv(path)
            kinds.add(analysis.file_kind(analysis.header_blocks(header_lines), columns))
            assert delim == ","
            assert list(headers) == list(columns), os.path.basename(path)
            assert data.shape == (len(rows), len(columns)), os.path.basename(path)
            # every numeric column the analysis sees is numeric for the plotter too
            for j, col in enumerate(columns):
                vals = [r[col] for r in rows if r[col] != ""]
                if vals and all(isinstance(v, float) for v in vals):
                    assert np.isfinite(data[:, j]).sum() == len(vals), col
        assert kinds == {"sweep", "retention_reads", "endurance_summary", "endurance_raw"}


def test_plotter_keeps_the_header_when_a_row_has_empty_cells():
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.csv")
        with open(path, "w") as fh:
            fh.write("# comment\ncycle,R_LRS_ohm,V_set_trip_V,window\n1,1e6,,\n2,1e6,5,2.5\n")
        headers, delim, data = reader._read_data_from_file(path)
        assert headers == ["cycle", "R_LRS_ohm", "V_set_trip_V", "window"]
        assert data.shape == (2, 4) and np.isnan(data[0, 2]) and data[1, 2] == 5


def test_plotter_tolerates_a_trailing_delimiter_and_whitespace_files():
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.csv")
        with open(path, "w") as fh:
            fh.write("a,b,c\n1,2,3,\n4,5,6,\n")
        headers, _, data = reader._read_data_from_file(path)
        assert headers == ["a", "b", "c"] and data.shape == (2, 3)
        path = os.path.join(d, "w.dat")
        with open(path, "w") as fh:
            fh.write("Title line\nT_K  R_ohm\n300 1.5\n310 1.7\n")
        headers, delim, data = reader._read_data_from_file(path)
        assert headers == ["T_K", "R_ohm"] and delim is None and data.shape == (2, 2)


def test_plotter_reads_files_resaved_by_excel_with_a_bom():
    # 'CSV UTF-8' adds a UTF-8 BOM before the first '#'; 'Unicode Text' is UTF-16 + tabs
    reader = _plotter_reader()
    text = ("# title line\n# [block] key=a,b | other=1\n"
            "t_s,phase,V_V,I_A\n,settle,0,1e-6\n1.5,read,0.1,2e-3\n2.5,read,0.1,3e-3\n")
    with tempfile.TemporaryDirectory() as d:
        for name, enc, body in (("b.csv", "utf-8-sig", text),
                                ("u.txt", "utf-16", text.replace(",", "\t"))):
            path = os.path.join(d, name)
            with open(path, "w", encoding=enc, newline="") as fh:
                fh.write(body)
            headers, _, data = reader._read_data_from_file(path)
            assert headers == ["t_s", "phase", "V_V", "I_A"], name
            assert data.shape == (3, 4), name
            assert np.isnan(data[0, 0]) and data[2, 0] == 2.5 and data[2, 3] == 3e-3, name


def test_plotter_reads_a_cp1252_windeta_style_export():
    # Windows-native exports write the degree sign as the single byte 0xB0
    reader = _plotter_reader()
    text = ("Demo title, 1.1.2020, 10:00\n"
            "Diameter[mm] 1.000e+01  Thickness[mm] 1.000e+00\n"
            "Fixed value(s) :  Freq. [Hz]=1.000e+06  AC Volt  [Vrms]=1.000e+00\n"
            " Temp. [°C]\t Eps'    \t Eps''   \n"
            "1.0e+02\t2.0e+01\t-1.0e-01\n1.1e+02\t2.1e+01\t1.0e-01\n")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "w.txt")
        with open(path, "w", encoding="cp1252", newline="\r\n") as fh:
            fh.write(text)
        headers, delim, data = reader._read_data_from_file(path)
        assert headers == ["Temp. [°C]", "Eps'", "Eps''"] and delim == "\t"
        assert data.shape == (2, 3) and data[1, 0] == 110.0 and data[0, 2] == -0.1


def test_plotter_skips_csv_quoted_comment_lines():
    # csv.writer.writerow(["# ..."]) quotes a comment that contains a comma
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "q.dat")
        with open(path, "w", newline="") as fh:
            fh.write('"# Instrument: VENDOR,MODEL,1,1.0"\n# plain comment\n'
                     "Timestamp,Elapsed_s,T_K\n12:00:00,0.0,300.0\n12:00:01,1.0,300.5\n")
        headers, delim, data = reader._read_data_from_file(path)
        assert headers == ["Timestamp", "Elapsed_s", "T_K"] and delim == ","
        assert data.shape == (2, 3) and data[1, 2] == 300.5


def test_plotter_reports_and_overrides_the_comment_line_count():
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.txt")
        with open(path, "w", newline="") as fh:
            fh.write("Sample run\nOperator notes\n"
                     "T,R\n10,1.5\n20,2.5\n")
        headers, _, data, n = reader._read_data_table(path)
        assert headers == ["T", "R"] and n == 2 and data.shape == (2, 2)
        # user says three comment lines: header line skipped, data headerless
        headers, _, data, n = reader._read_data_table(path, 3)
        assert headers == ["Col1", "Col2"] and n == 3
        assert data.shape == (2, 2) and data[1, 1] == 2.5
        # four: the first data row now counts as a comment
        headers, _, data, n = reader._read_data_table(path, 4)
        assert n == 4 and data.shape == (1, 2) and data[0, 0] == 20.0


def test_plotter_comment_override_rejects_bad_lines():
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.txt")
        with open(path, "w", newline="") as fh:
            fh.write("Sample run\nOperator notes\nT,R\n10,1.5\n")
        # title line with a space (not 2 columns against a comma file); past the end
        for bad in (0, 9):
            try:
                reader._read_data_table(path, bad)
            except ValueError:
                continue
            raise AssertionError(f"comment_lines={bad} should be rejected")


def test_plotter_comment_count_for_headerless_files():
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "h.txt")
        with open(path, "w", newline="") as fh:
            fh.write("# note\n1 2\n3 4\n")
        headers, _, data, n = reader._read_data_table(path)
        assert headers == ["Col1", "Col2"] and n == 1 and data.shape == (2, 2)


if __name__ == "__main__":
    run_module_tests(globals())
