import unittest
from unittest import mock
import pytest

import pandas as pd


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


class TestLivePlotter(unittest.TestCase):
    @mock.patch('Utilities.my_plot_utils.select_file')
    @mock.patch('matplotlib.pyplot.show')
    @mock.patch('matplotlib.animation.FuncAnimation')
    def test_live_plot_from_csv(self, mock_animation, mock_show, mock_select_file, dummy_csv_file):
        """
        Tests that the live_plot_from_csv function can be called and attempts to plot.
        """
        # Have the mocked select_file return the path to our dummy file
        mock_select_file.return_value = str(dummy_csv_file)

        # We need to run the script's main execution block.
        # Since it's under `if __name__ == '__main__':`, we can import it.
        with mock.patch('Utilities.my_plot_utils.live_plot_from_csv') as mock_live_plot:
            from Utilities import my_plot_utils # noqa
            # The main block calls live_plot_from_csv, so we check that
            mock_live_plot.assert_called_once()


if __name__ == '__main__':
    unittest.main()