"""Memristor_Analysis_GUI.py: file reading and header parsing, rule 1 and
rule 2 for V set / V reset (none when nothing switches), retention
pairing on the common window only, drift rule, command-line
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




def test_analysis_cli_arguments():
    args = analysis.parse_args(["--tab", "retention"])
    assert args.tab == "retention" and not hasattr(args, "file") and not hasattr(args, "folder")
    assert analysis.parse_args([]).tab is None
    # the module writes nothing: no export path exists any more
    assert not hasattr(analysis, "write_analysis_file")
    src = open(analysis.__file__, encoding="utf-8").read()
    assert src.count("open(") == 1 and 'open(path, "r"' in src     # the single open is the read-only parser


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

def test_analysis_still_reads_old_files_with_comment_lines_after_the_data():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "old.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# PICA memristor sweep v1.2 | protocol v1.1\n# [protocol] module=sweep\n# columns: a,b\na,b\n"
                     "1,2\n# RETRY n=1 | t_s=0.5 | what=x | error=y\n# RECONNECT after 1.0 s | x succeeded on attempt 2\n"
                     "3,4\n# END reason=completed | points=2 | end_time=2026-10-11T10:00:00+05:30\n")
        header, columns, rows = analysis.parse_pica_csv(path)
        assert columns == ["a", "b"] and [r["a"] for r in rows] == [1.0, 3.0]
        assert analysis.header_blocks(header)["end"]["reason"] == "completed"


def test_analysis_raw_cycle_viewer_draws_one_line_per_leg_segment():
    """K's screenshot of raw cycle 10: SET and RESET share segment numbers 1
    and 2, so the (loop, segment) key joined them and drew all in SET colour."""
    from matplotlib.figure import Figure
    from matplotlib.colors import to_hex
    with tempfile.TemporaryDirectory() as d:
        _, _, _, raw = run_endurance(FakeK2400(2e5), d, n_cycles=1, raw_cycles_text="1", raw_cycles={1},
                                   i_cc_reset=1e-4)
        _, _, rows = analysis.parse_pica_csv(raw)
    segs = analysis.endurance_leg_segments(rows, 1)
    assert [(leg, seg) for leg, seg, _ in segs] == [("set", 1), ("set", 2), ("reset", 1), ("reset", 2)]
    assert all(r["leg"] == leg and r["segment"] == seg for leg, seg, srows in segs for r in srows)
    ax = Figure().add_subplot(111)
    analysis.plot_raw_cycle(ax, rows, 1)
    lines = [ln for ln in ax.get_lines() if ln.get_linestyle() != "None"]     # hollow markers have no line
    assert len(lines) == 4
    colours = [to_hex(ln.get_color()) for ln in lines]
    assert colours.count(analysis.COLOUR_LRS.lower()) == 2 and colours.count(analysis.COLOUR_HRS.lower()) == 2
    assert [ln.get_linestyle() for ln in lines] == ["-", "--", "-", "--"]
    assert ax.get_legend_handles_labels()[1] == ["SET", "RESET"]
    hollow = [ln for ln in ax.get_lines() if ln.get_linestyle() == "None"]
    assert hollow and all(to_hex(ln.get_color()) == analysis.COLOUR_LRS.lower() for ln in hollow)  # 2e5 ohm trips on SET only
    # sweep and retention files keep the (loop, segment) grouping
    assert sorted(analysis.sweep_loops(rows, 1)[1]) == [1, 2]


def _raw_cycle_rows(cycle=1):
    """Hand-made endurance raw cycle: SET trips (compliance) at +3 V, RESET
    has its current drop at -3 V. Both legs number their segments 1 and 2
    exactly as the module writes them."""
    rows = []

    def add(leg, seg, v, i, cc):
        rows.append({"phase": "sweep", "cycle": cycle, "loop": cycle, "leg": leg, "segment": seg,
                     "V_set_V": v, "V_meas_V": v, "I_A": i, "compliance_flag": cc})
    for v, i, cc in ((0, 0, 0), (1, 1e-6, 0), (2, 2e-6, 0), (3, 1e-5, 1), (4, 1e-5, 1)):
        add("set", 1, float(v), i, cc)
    for v, i in ((3, 1e-5), (2, 6e-6), (1, 3e-6), (0, 0)):
        add("set", 2, float(v), i, 0)
    for v, i in ((0, 0), (-1, -3e-6), (-2, -6e-6), (-3, -1e-6), (-4, -1.2e-6)):
        add("reset", 1, float(v), i, 0)
    for v, i in ((-3, -9e-7), (-2, -6e-7), (-1, -3e-7), (0, 0)):
        add("reset", 2, float(v), i, 0)
    return rows


def test_analysis_sweep_tab_keeps_set_and_reset_legs_apart_for_an_endurance_cycle():
    """Review finding: the Sweep tab fed sweep_loops() with an endurance raw
    cycle, whose SET and RESET legs both use segments 1 and 2, so the two
    outgoing legs were joined into one segment of mixed sign (V set none,
    V reset -0 V on a cycle that tripped on both legs)."""
    rows = _raw_cycle_rows(7)
    groups = analysis.switching_groups(rows, "endurance_raw", 7)
    assert [label for label, _ in groups] == ["cycle 7"]
    segs = groups[0][1]
    assert sorted(segs) == [1, 2, 3, 4]
    assert [r["leg"] for r in segs[1]] == ["set"] * 5 and [r["leg"] for r in segs[3]] == ["reset"] * 5
    res = analysis.estimate_switching(segs, rule=1, factor=10.0, floor_v=1.0, set_sign=1)
    assert res["v_set"] == 3.0 and res["how_set"] == "first compliance point" and res["set_segment"] == 1
    assert res["v_reset"] == -3.0 and res["how_reset"] == "largest drop in |I|" and res["reset_segment"] == 3
    # the old grouping really did merge the legs
    merged = analysis.sweep_loops(rows, 7)[7]
    assert sorted(merged) == [1, 2] and len(merged[1]) == 10
    assert analysis.switching_groups(rows, "endurance_raw", 8) == []
    # sweep / retention-write files keep one group per loop keyed by segment
    srows = [dict(r, phase="sweep", loop=2) for r in rows if r["leg"] == "set"]
    (label, segs2), = analysis.switching_groups(srows, "sweep")
    assert label == "loop 2" and sorted(segs2) == [1, 2]
    # and a real raw file from the module goes through the same path
    with tempfile.TemporaryDirectory() as d:
        _, _, _, raw = run_endurance(FakeK2400(2e5), d, n_cycles=1, raw_cycles_text="1", raw_cycles={1})
        _, _, frows = analysis.parse_pica_csv(raw)
    (label, fsegs), = analysis.switching_groups(frows, "endurance_raw", 1)
    res = analysis.estimate_switching(fsegs, 1, 10.0, 1.0, 1)
    assert label == "cycle 1" and res["v_set"] == 3.0 and res["set_segment"] == 1
    assert res["reset_segment"] == 3 and all(r["leg"] == "reset" for r in fsegs[3])
    assert res["v_reset"] is None     # a linear resistor has no current drop: 'none', not -0 V


if __name__ == "__main__":
    run_module_tests(globals())
