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


def test_plotter_reads_the_novocontrol_windeta_reference_exports():
    reader = _plotter_reader()
    ref = os.path.join(REPO_ROOT, "pica", "novocontrol", "data_file_for_ref")
    expected = {"Fscan_data_Novo_Windeta.dat": (40, 9, "Freq. [Hz]"),
                "Sample_Fscan_RT.TXT": (115, 9, "Freq. [Hz]"),
                "Sample_Tscan.TXT": (77, 3, "Temp. [°C]")}
    for name, (rows, cols, first) in expected.items():
        path = os.path.join(ref, name)
        if not os.path.exists(path):
            continue  # reference exports are optional in a checkout
        headers, delim, data = reader._read_data_from_file(path)
        assert headers[0] == first and headers[1] == "Eps'" and delim == "\t", name
        assert data.shape == (rows, cols) and np.isfinite(data).all(), name


def test_plotter_reads_both_alpha_freqscan_output_formats():
    # The real writer of the Alpha-AN frequency scan, driven headless
    import test_fscan_against_references as fscan_test
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        gui = fscan_test.make_headless_gui(d)
        gui._open_output_files(fscan_test.base_params())
        for k in range(3):
            gui._write_row([20.0 * 1.4 ** k, 1e3, 2e2, 1e-4, 2e-5, 3e4, 4e5, 5e-6, -6e-7],
                           [0.2, -89.9, 1e-9, 3e-5])
        # WinDETA: Tan(Delta) after Eps'', Phi last (11); PICA adds Cp, G (13)
        for path, first, n in ((gui.txt_filepath, "Freq. [Hz]", 11),
                               (gui.dat_filepath, "Frequency", 13)):
            headers, _, data = reader._read_data_from_file(path)
            assert headers[0] == first and len(headers) == n, path
            assert data.shape == (3, n) and data[2, 0] == 39.2, path
            assert data[0, 3] == 0.2 and data[0, 9] == -6e-7 and data[0, 10] == -89.9, path


def test_plotter_reads_both_alpha_tscan_output_formats():
    # The real per-frequency writer of the Alpha-AN temperature scan
    import test_temprature_scan_alphaan as tscan_test
    tscan = tscan_test.mod
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        gui = tscan_test._files(d)
        valid = tscan.STATUS_RESULT_VALID
        gui._save_cycle_to_files({"points": [
            (300.0 + k, 1000.0, 1e5, -2e6, valid, 1000.0) for k in range(3)]})
        headers, _, data = reader._read_data_from_file(gui.windeta_filepaths[1000.0])
        assert headers == ["Temp. [°C]", "Eps'", "Eps''"]
        assert data.shape == (3, 3) and np.isclose(data[2, 0], 28.85)
        headers, _, data = reader._read_data_from_file(gui.freq_filepaths[1000.0])
        assert headers[0] == "Temperature" and len(headers) == 19
        assert data.shape == (3, 19) and data[1, 0] == 301.0


def test_plotter_reads_both_alpha_step_freqscan_output_formats():
    # The real per-setpoint writer of the Alpha-AN step frequency scan
    import test_step_frequency_scan_alphaan as step_test
    reader = _plotter_reader()
    with tempfile.TemporaryDirectory() as d:
        gui, _ = step_test._gui(save_dir=d)
        gui._run_frequency_sweep(150.0, 0, 2)
        for name in sorted(os.listdir(d)):
            headers, _, data = reader._read_data_from_file(os.path.join(d, name))
            if name.endswith("_windeta_format.txt"):
                assert headers[0] == "Freq. [Hz]" and data.shape == (2, 9), name
            else:
                assert name.endswith("_pica_format.dat"), name
                assert headers[0] == "Frequency" and data.shape == (2, 20), name
            assert np.isfinite(data).all(), name


def test_plotter_matches_the_same_quantity_across_pica_and_windeta_names():
    match = _plotter_reader()._match_column
    windeta = ["Freq. [Hz]", "Eps'", "Eps''", "Zp' [Ohms]", "Sig' [S/cm]"]
    pica = ["Frequency", "Eps'", "Eps''", "Zp'", "Sig'"]
    assert match("Frequency", windeta) == "Freq. [Hz]"
    assert match("Freq. [Hz]", pica) == "Frequency"
    assert match("Zp'", windeta) == "Zp' [Ohms]" and match("Sig' [S/cm]", pica) == "Sig'"
    assert match("Eps''", windeta) == "Eps''"  # exact name, not Eps'
    # different units, different quantities and ambiguity never match
    assert match("Current [mA]", ["Current [A]"]) is None
    assert match("Temperature", ["Temp. [°C]"]) is None
    assert match("Zp'", ["Zp' [Ohms]", "Zp' [kOhms]"]) is None


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


# --- Live update, cache and plotting (Agg canvas, no Tk) ---------------------

class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo(_Var):
    """Stands in for the X/Y column ttk.Combobox."""

    def __init__(self, value=""):
        super().__init__(value)
        self.items = {}

    def __setitem__(self, key, value):
        self.items[key] = value


def _live_app(path, x_col="", y_col=""):
    """Bare plotter with stub widgets and a real (Agg) figure, `path` active."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    app = _plotter_reader()
    app.logged = []
    app.log = app.logged.append
    app.live_update_var = _Var(False)
    app.x_log_var, app.y_log_var = _Var(False), _Var(False)
    app.x_col_cb, app.y_col_cb = _Combo(x_col), _Combo(y_col)
    app.column_source_var, app.comment_lines_var, app.comment_source_var = _Var(""), _Var(""), _Var("")
    app.file_watcher_job = None
    app.figure = Figure()
    app.ax_main = app.figure.add_subplot(111)
    app.canvas = FigureCanvasAgg(app.figure)
    app._plot_lines, app._plot_sig = {}, None
    app.file_ui_elements = {path: {"var": _Var(True)}}
    app.file_data_cache = {path: {"path": path}}
    app.active_filepath = path
    return app


def _append(path, text):
    with open(path, "a", newline="") as fh:
        fh.write(text)
    # mtime resolution on some filesystems is coarse; make the change visible
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def test_append_reader_stops_at_a_half_written_last_line():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "live.csv")
        with open(path, "w", newline="") as fh:
            fh.write("T,R\n1,10\n2,1.23")  # writer is mid-way through '1.2345'
        lines, offset, end = _plotter_reader()._read_appended_lines(path, 4, "utf-8")
        assert lines == ["1,10"] and offset == len(b"T,R\n1,10\n")
        assert end == os.path.getsize(path)
        _append(path, "45\n3,7\n4,")
        lines, offset2, end = _plotter_reader()._read_appended_lines(path, offset, "utf-8")
        assert lines == ["2,1.2345", "3,7"]
        assert offset2 == os.path.getsize(path) - len(b"4,")
        # nothing complete yet -> offset unchanged
        assert _plotter_reader()._read_appended_lines(path, offset2, "utf-8") == (
            [], offset2, os.path.getsize(path))


def test_append_parser_matches_the_full_load_rules():
    rows = _plotter_reader()._parse_appended_lines(
        ["# note", "", "1,,3", "4,5,6,", "7,8", '"# Instrument: A,B,C"', "9,x,1 # tail"],
        ["a", "b", "c"], ",")
    assert len(rows) == 3
    assert rows[0][0] == 1 and np.isnan(rows[0][1]) and rows[1] == [4, 5, 6]
    assert rows[2][0] == 9 and np.isnan(rows[2][1]) and rows[2][2] == 1
    assert _plotter_reader()._parse_appended_lines(["1  2", "3"], ["a", "b"], None) == [[1, 2]]


def test_live_update_never_keeps_a_cut_short_value():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "live.csv")
        with open(path, "w", newline="") as fh:
            fh.write("# run\nT,R\n1,10\n2,1.23")
        app = _live_app(path)
        app.load_file_data(path)
        info = app.file_data_cache[path]
        # the full load shows the unterminated line but remembers it was partial
        assert list(info["data"]["R"]) == [10, 1.23] and info["tail_row"]
        assert info["size"] == len(b"# run\nT,R\n1,10\n")
        _append(path, "45\n3,30\n4,4")
        app.append_file_data()
        assert list(info["data"]["T"]) == [1, 2, 3]
        assert list(info["data"]["R"]) == [10, 1.2345, 30]
        _append(path, "0\n")
        app.append_file_data()
        assert list(info["data"]["R"]) == [10, 1.2345, 30, 40]
        assert info["size"] == os.path.getsize(path) and not info.get("tail_row")
        assert info["encoding"] == "utf-8"


def test_live_update_follows_a_cr_only_file():
    """Old-Mac style line ends (a lone CR): the resume point used to look
    for LF only, so no new row ever appeared (review, 22 Oct 2026)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "live.csv")
        with open(path, "w", newline="") as fh:
            fh.write("a,b\r1,2\r")
        app = _live_app(path)
        app.load_file_data(path)
        _append(path, "3,4\r5,6\r")
        app.append_file_data()
        info = app.file_data_cache[path]
        assert list(info["data"]["a"]) == [1, 3, 5]
        assert list(info["data"]["b"]) == [2, 4, 6]


class _Root:
    def after(self, ms, fn):
        return None

    def after_cancel(self, job):
        pass


def test_a_file_overwritten_by_a_shorter_one_is_reloaded_not_spliced():
    """The watcher compared the new length with the resume point; a shorter
    file still past the end of the old last complete line was then
    'appended' onto the old rows (review, 22 Oct 2026)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "live.csv")
        with open(path, "w", newline="") as fh:
            fh.write("a,b\n1,2\n3,4\n5,66")          # 18 bytes, last line open
        app = _live_app(path)
        app.root, app.live_update_var = _Root(), _Var(True)
        app.load_file_data(path)
        with open(path, "w", newline="") as fh:
            fh.write("a,b\n7,8\n9,1\n2,3\n")        # 17 bytes, a new run
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        app.check_for_updates()
        info = app.file_data_cache[path]
        assert list(info["data"]["a"]) == [7, 9, 2]
        assert list(info["data"]["b"]) == [8, 1, 3]


def test_cached_file_is_not_read_again_until_it_changes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.csv")
        with open(path, "w", newline="") as fh:
            fh.write("T,R\n1,10\n2,20\n")
        app = _live_app(path)
        reads = []
        real = app._read_data_snapshot

        def counting(fp, comment_lines=None):
            reads.append(comment_lines)
            return real(fp, comment_lines)
        app._read_data_snapshot = counting
        app.active_filepath = None
        app._load_file_data_into_cache(path)          # add_files caches it ...
        app.active_filepath = path
        app.load_file_data(path)                      # ... and activating reuses it
        app.load_file_data(path)                      # clicking the label again
        assert reads == [None]
        _append(path, "3,30\n")
        app.load_file_data(path)                      # changed on disk -> re-read
        assert len(reads) == 2 and len(app.file_data_cache[path]["data"]["T"]) == 3
        # applying a comment-line count reads once, then the reload uses the cache
        app.comment_lines_var.set("1")
        app._apply_comment_lines()
        assert reads[2:] == [1] and app.file_data_cache[path]["headers"] == ["Col1", "Col2"]
        app._reset_comment_lines()
        assert reads[3:] == [None] and app.file_data_cache[path]["headers"] == ["T", "R"]


def test_loadtxt_fast_path_and_genfromtxt_fallback_agree():
    parse = _plotter_reader()._parse_table_lines
    clean = ["a,b", "1,2", "# c", "", "3,4e-3"]
    expected = np.genfromtxt(clean, delimiter=",", comments="#", autostrip=True,
                             invalid_raise=False, skip_header=1)
    assert np.array_equal(parse(clean, ",", 1), expected)
    messy = ["a,b", "1,", "3,4", "5,6,7"]  # loadtxt refuses -> genfromtxt result
    expected = np.genfromtxt(messy, delimiter=",", comments="#", autostrip=True,
                             invalid_raise=False, skip_header=1)
    assert np.array_equal(parse(messy, ",", 1), expected, equal_nan=True)


def test_live_append_updates_the_line_in_place():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.csv")
        with open(path, "w", newline="") as fh:
            fh.write("T,R\n" + "".join(f"{i},{i * 2}\n" for i in range(1, 501)))
        app = _live_app(path, "T", "R")
        app.load_file_data(path)
        line = app._plot_lines[path]
        legend = app.ax_main.get_legend()
        assert line.get_markevery() == 2 and legend is not None  # 500 // 200
        _append(path, "1000,5000\n")
        app.append_file_data()
        assert app._plot_lines[path] is line and app.ax_main.get_legend() is legend
        assert len(line.get_xdata()) == 501
        assert app.ax_main.get_xlim()[1] >= 1000 and app.ax_main.get_ylim()[1] >= 5000
        # a changed setting (log Y) is a different plot -> full replot
        app.y_log_var.set(True)
        _append(path, "1001,5001\n")
        app.append_file_data()
        assert app._plot_lines[path] is not line and app.ax_main.get_yscale() == "log"


def test_small_files_keep_a_marker_on_every_point():
    m = _plotter_reader()._markevery
    assert m(0) == 1 and m(399) == 1 and m(400) == 2 and m(100_000) == 500


if __name__ == "__main__":
    run_module_tests(globals())
