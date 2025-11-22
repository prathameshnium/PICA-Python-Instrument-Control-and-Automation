import pytest
import matplotlib

# Force matplotlib to not use any X11/Windows GUI backend
# This prevents "KeyError" and display issues during testing
matplotlib.use('Agg')


@pytest.fixture(autouse=True)
def reset_matplotlib_params():
    """Ensures matplotlib config is clean for every test."""
    matplotlib.rcdefaults()