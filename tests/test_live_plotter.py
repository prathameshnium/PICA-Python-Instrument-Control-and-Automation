import os
import pandas as pd
import matplotlib.pyplot as plt
from unittest import mock
from Utilities.LivePlotter_v10 import live_plot_from_csv
import pytest

@pytest.fixture
def dummy_csv_file(tmp_path):
    # Create a dummy CSV file for testing
    data = {
        'Time (s)': [0, 1, 2, 3, 4],
        'Temperature (K)': [100, 110, 105, 115, 120],
        'Voltage (V)': [1, 1.1, 1.05, 1.15, 1.2]
    }
    df = pd.DataFrame(data)
    csv_path = tmp_path / "test_data.csv"
    df.to_csv(csv_path, index=False)
    return csv_path

def test_live_plot_from_csv(dummy_csv_file):
    # Mock plt.show() to prevent it from blocking the test
    with mock.patch('matplotlib.pyplot.show') as mock_show:
        # Mock FuncAnimation to prevent actual animation from running
        with mock.patch('matplotlib.animation.FuncAnimation'):
            # Mock the plot and scatter methods of the axes
            with mock.patch('matplotlib.axes.Axes.plot') as mock_plot, \
                 mock.patch('matplotlib.axes.Axes.scatter') as mock_scatter:
                live_plot_from_csv(dummy_csv_file)

                # Assert that plt.show() was called
                mock_show.assert_called_once()

                # Assert that plot and scatter were called multiple times
                assert mock_plot.call_count >= 3  # For the three subplots
                assert mock_scatter.call_count >= 3 # For the three subplots
