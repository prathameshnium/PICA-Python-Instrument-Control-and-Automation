"""PE Plotter: the numpy file parser and the cheap redraw (no windows)."""

import io
import os
import subprocess
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from pica.utils.PE_plotter import PEPlotterAppGUI  # noqa: E402

HEADER = ["Point", "Time (ms)", "Drive Voltage", "Polarization (uC/cm2)"]

TXT_FILE = (
    "Hysteresis test export\n"
    "Executed: 21/10/2026 10:15:00\n"
    "Sample Area (cm2): 0.0314\n"
    "Sample Thickness (um): 250\n"
    "Volts: 100\n"
    "Hysteresis Period (ms): 10\n"
    "\n"
    + "\t".join(HEADER) + "\n"
    "1\t0.0\t0.0\t0.00\n"
    "2\t2.5\t50.5\t1.25\n"
    "3\t5.0\t100\t2.5e0\n"
    "4\t7.5\t-50.5\t-1.25\n"
)
TXT_DATA = "1\t0.0\t0.0\t0.00\n2\t2.5\t50.5\t1.25\n3\t5.0\t100\t2.5e0\n4\t7.5\t-50.5\t-1.25"

CSV_FILE = (
    "Stored: 20/10/2026 09:00:00\n"
    "Sample Area: 0.5\n"
    + ",".join(HEADER) + "\n"
    "1,0.1,1.0,0.5\n"
    "2,0.2,,0.75\n"
    "3,0.3,NaN,1.0\n"
)
CSV_DATA = "1,0.1,1.0,0.5\n2,0.2,,0.75\n3,0.3,NaN,1.0"


def _parse(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return PEPlotterAppGUI.__new__(PEPlotterAppGUI).parse_file_custom(str(path))


def test_tab_txt_file_columns_and_metadata(tmp_path):
    meta, data = _parse(tmp_path, "loop.txt", TXT_FILE)
    assert list(data) == HEADER
    assert data["Point"].dtype == np.int64
    np.testing.assert_array_equal(data["Point"], [1, 2, 3, 4])
    np.testing.assert_array_equal(data["Drive Voltage"], [0.0, 50.5, 100.0, -50.5])
    np.testing.assert_array_equal(data["Polarization (uC/cm2)"], [0.0, 1.25, 2.5, -1.25])
    assert meta["Measurement Date"] == "21/10/2026 10:15:00"
    assert meta["Sample Area"] == "0.0314"
    assert meta["Sample Thickness"] == "250"
    assert meta["Applied Voltage"] == "100"
    assert meta["Frequency (Hz)"] == "100.0"


def test_comma_csv_with_missing_values(tmp_path):
    meta, data = _parse(tmp_path, "loop.csv", CSV_FILE)
    assert list(data) == HEADER
    assert data["Drive Voltage"].dtype == np.float64
    np.testing.assert_array_equal(data["Drive Voltage"], [1.0, np.nan, np.nan])
    np.testing.assert_array_equal(data["Time (ms)"], [0.1, 0.2, 0.3])
    assert meta["Measurement Date"] == "20/10/2026 09:00:00"


def test_header_count_mismatch_falls_back_to_numbered_columns(tmp_path):
    text = "Point\tTime\tV\n1\t2\t3\t4\n5\t6\t7\t8\n"
    _, data = _parse(tmp_path, "odd.txt", text)
    assert list(data) == [0, 1, 2, 3]
    np.testing.assert_array_equal(data[3], [4, 8])


def test_short_rows_padded_long_rows_rejected(tmp_path):
    _, data = _parse(tmp_path, "short.txt", "Point\tTime\tV\tP\n1\t2\t3\t4\n5\t6\t7\n")
    np.testing.assert_array_equal(data["P"], [4.0, np.nan])
    with pytest.raises(ValueError):
        _parse(tmp_path, "long.txt", "Point\tTime\tV\n1\t2\t3\n5\t6\t7\t8\n")


def test_text_column_kept_as_strings(tmp_path):
    _, data = _parse(tmp_path, "s.txt", "Point\tTime\tTag\n1\t2\tup\n3\t4\tdown\n")
    assert data["Tag"].dtype == object
    assert list(data["Tag"]) == ["up", "down"]


def test_no_table_gives_empty_dict(tmp_path):
    _, data = _parse(tmp_path, "empty.txt", "Sample Area: 1\nnothing here\n")
    assert data == {}


@pytest.mark.parametrize("name,text,body,sep", [
    ("loop.txt", TXT_FILE, TXT_DATA, "\t"),
    ("loop.csv", CSV_FILE, CSV_DATA, ","),
], ids=["tab-txt", "comma-csv"])
def test_matches_the_old_pandas_reader(tmp_path, name, text, body, sep):
    pd = pytest.importorskip("pandas")
    df = pd.read_csv(io.StringIO(body), sep=sep, header=None)
    df.columns = HEADER
    _, data = _parse(tmp_path, name, text)
    for col in HEADER:
        assert data[col].dtype == df[col].to_numpy().dtype, col
        np.testing.assert_array_equal(data[col], df[col].to_numpy())


def test_module_does_not_import_pandas():
    code = ("import sys; sys.path.insert(0, %r); import pica.utils.PE_plotter; "
            "print('pandas' in sys.modules)" % REPO)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, cwd=REPO)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def _fake_app(columns):
    app = PEPlotterAppGUI.__new__(PEPlotterAppGUI)
    app._layout_key = None
    app.ax_main = MagicMock()
    app.ax_main.get_legend_handles_labels.return_value = ([], [])
    app.figure = MagicMock()
    app.canvas = MagicMock()
    app.x_col_cb = MagicMock()
    app.x_col_cb.get.return_value = columns[0]
    app.y_col_cb = MagicMock()
    app.y_col_cb.get.return_value = columns[1]
    var = MagicMock()
    var.get.return_value = True
    app.file_ui_elements = {"a.txt": {"var": var}}
    app.file_data_cache = {"a.txt": {"data": {c: np.arange(3.0) for c in columns},
                                     "metadata": {}}}
    return app


def test_tight_layout_only_when_layout_changes():
    app = _fake_app(["V", "P", "T"])
    app.plot_data()
    app.plot_data()
    assert app.figure.tight_layout.call_count == 1
    assert app.canvas.draw_idle.call_count == 2
    app.y_col_cb.get.return_value = "T"
    app.plot_data()
    assert app.figure.tight_layout.call_count == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
