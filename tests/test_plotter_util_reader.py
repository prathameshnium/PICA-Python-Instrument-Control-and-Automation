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


if __name__ == "__main__":
    run_module_tests(globals())
