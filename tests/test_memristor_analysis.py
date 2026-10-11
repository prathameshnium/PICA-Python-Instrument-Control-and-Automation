"""Memristor_Analysis_GUI.py: file reading and header parsing, rule 1 and
rule 2 for V set / V reset (none when nothing switches), retention
pairing on the common window only, drift rule, export layout, command-line
arguments, file-kind detection from real module outputs, and the proof that
the raw file is never modified.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memristor_test_support import (  # noqa: E402
    FakeK2400, analysis, run_module_tests, run_sweep, run_retention, run_endurance)

def test_analysis_rule1_finds_the_trip_and_rule2_the_steepest_point_and_never_touches_the_file():
    with tempfile.TemporaryDirectory() as d:
        _, _, path = run_sweep(FakeK2400(1e6), d)
        before = (os.path.getmtime(path), os.path.getsize(path))
        header, columns, rows = analysis.parse_pica_csv(path)
        blocks = analysis.header_blocks(header)
        assert analysis.file_kind(blocks, columns) == "sweep"
        assert blocks["sample"]["name"] == "VCO01" and blocks["instrument"]["I_cc_A"] == "1e-05"
        loops = analysis.sweep_loops(rows)
        assert sorted(loops) == [1] and sorted(loops[1]) == [1, 2, 3, 4]
        res = analysis.estimate_switching(loops[1], rule=1, factor=10, floor_v=1.0, set_sign=1)
        assert res["v_set"] == 11.0 and res["how_set"] == "first compliance point"
        # a resistor never resets: |I| only grows on the negative leg, so rule 1 reports none
        assert res["reset_segment"] == 3 and res["v_reset"] is None and res["how_reset"] == "none"
        res2 = analysis.estimate_switching(loops[1], rule=2, factor=10, floor_v=1.0, set_sign=1)
        assert res2["v_set"] is not None and res2["how_set"] == "largest |dI/dV|"
        res_neg = analysis.estimate_switching(loops[1], rule=1, factor=10, floor_v=1.0, set_sign=-1)
        assert res_neg["set_segment"] == 3 and res_neg["v_set"] == -11.0
        after = (os.path.getmtime(path), os.path.getsize(path))
        assert before == after


def test_analysis_rule1_reports_none_when_nothing_switches():
    rows = [{"phase": "sweep", "loop": 1, "segment": 1, "V_set_V": v, "V_meas_V": v, "I_A": v * 1e-9, "compliance_flag": 0}
            for v in range(0, 6)]
    rows += [{"phase": "sweep", "loop": 1, "segment": 2, "V_set_V": v, "V_meas_V": v, "I_A": v * 1e-9, "compliance_flag": 0}
             for v in range(4, -1, -1)]
    loops = analysis.sweep_loops(rows)
    res = analysis.estimate_switching(loops[1], 1, 10, 1.0, 1)
    assert res["v_set"] is None and res["how_set"] == "none" and analysis.fmt_v(res["v_set"]) == "none"
    # unipolar with a jump: SET found by the jump rule, RESET searched after it on the same segment
    rows = [{"phase": "sweep", "loop": 1, "segment": 1, "V_set_V": v, "V_meas_V": v,
             "I_A": (v * 1e-9 if v < 3 else v * 1e-6), "compliance_flag": 0} for v in range(0, 6)]
    loops = analysis.sweep_loops(rows)
    res = analysis.estimate_switching(loops[1], 1, 10, 1.0, 1)
    assert res["v_set"] == 3.0 and "jump" in res["how_set"]


def test_analysis_retention_pairing_uses_the_common_window_only():
    import numpy as np
    t_l = np.array([1, 2, 5, 10, 20, 50, 100], dtype=float)
    r_l = np.full(7, 1e4)
    t_h = np.array([2, 5, 10, 20, 50], dtype=float)
    r_h = np.array([1e6, 1e6, 1e5, 5e4, 5e4], dtype=float)
    fire, window, grid, ratio = analysis.ratio_criterion(t_l, r_l, t_h, r_h, 10)
    assert window == (2.0, 50.0) and list(grid) == [2, 5, 10, 20, 50]
    assert fire == 20.0
    fire, window, grid, ratio = analysis.ratio_criterion(t_l, r_l, t_h, r_h, 1.0)
    assert fire is None
    assert analysis.ratio_criterion(t_l, r_l, np.array([200.0, 300.0]), np.array([1.0, 1.0]), 10)[1] is None
    t = np.array([1, 10, 100, 1000], dtype=float)
    r = np.array([1e6, 1e6, 1.5e6, 3e6], dtype=float)
    fire, r_ref = analysis.drift_criterion(t, r, 10, 2.0)
    assert fire == 1000.0 and abs(r_ref - 1e6) < 1
    assert analysis.drift_criterion(t, r, 5000, 2.0) == (None, None)


def test_analysis_export_writes_into_an_analysis_folder_with_sources_and_parameters():
    with tempfile.TemporaryDirectory() as d:
        path = analysis.write_analysis_file(d, "x.csv", ["raw1.csv"], [("rule", "1")], ("a", "b"),
                                            [("f", 1.5), ("g", None)], notes=["pairing: none"])
        assert os.path.dirname(path).endswith("analysis")
        text = open(path, encoding="utf-8").read().splitlines()
        assert text[0].startswith("# PICA memristor analysis")
        assert "rule=1" in text[1] and "[source] file=raw1.csv" in text[2] and text[3] == "# pairing: none"
        assert text[4] == "a,b" and text[5].startswith("f,1.5") and text[6] == "g,"


def test_analysis_cli_arguments():
    args = analysis.parse_args(["--tab", "retention", "--folder", "C:/x", "--file", "a.csv", "--file", "b.csv"])
    assert args.tab == "retention" and args.folder == "C:/x" and args.file == ["a.csv", "b.csv"]
    assert analysis.parse_args([]).tab is None


def test_analysis_file_kinds_from_real_outputs():
    with tempfile.TemporaryDirectory() as d:
        _, _, w, r = run_retention(FakeK2400(1e6), d, t_stop=0.3, t_rec=0.05, t_soak=0.05)
        for path, kind in ((w, "sweep"), (r, "retention_reads")):
            header, columns, _ = analysis.parse_pica_csv(path)
            assert analysis.file_kind(analysis.header_blocks(header), columns) == kind
    with tempfile.TemporaryDirectory() as d:
        _, _, s, raw = run_endurance(FakeK2400(1e6), d, n_cycles=1)
        for path, kind in ((s, "endurance_summary"), (raw, "endurance_raw")):
            header, columns, _ = analysis.parse_pica_csv(path)
            assert analysis.file_kind(analysis.header_blocks(header), columns) == kind

if __name__ == "__main__":
    run_module_tests(globals())
