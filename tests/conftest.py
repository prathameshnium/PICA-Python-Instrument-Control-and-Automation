# tests/conftest.py
import pytest
from unittest.mock import MagicMock, patch
import sys
import matplotlib

# Force Agg backend immediately when tests start
matplotlib.use('Agg')
# Import pyplot explicitly: safe_matplotlib's teardown touches matplotlib.pyplot,
# which only resolves if pyplot has already been imported by something else.
import matplotlib.pyplot as plt
import gc

try:
    import tkinter as _real_tkinter
except ImportError:          # Python built without Tk: nothing to clean up
    _real_tkinter = None

# Count every real Tk interpreter a test creates (subclasses and tkinter.Tcl()
# both go through Tk.__init__).
_tk_created = [0]
if _real_tkinter is not None:
    _orig_tk_init = _real_tkinter.Tk.__init__

    def _counting_tk_init(self, *args, **kwargs):
        _tk_created[0] += 1
        _orig_tk_init(self, *args, **kwargs)

    _real_tkinter.Tk.__init__ = _counting_tk_init


@pytest.fixture(autouse=True)
def _free_tk_objects_on_the_main_thread():
    """Collect a test's leftover Tk objects on the main thread.

    Tcl aborts the whole process ("Fatal Python error: Illegal instruction")
    if a Tk object is freed from any other thread. A leftover window or
    variable from one test could otherwise be garbage-collected later inside
    another test's worker thread, so the run crashed or not depending on test
    order. Only tests that created a real Tk pay for the collection.
    """
    before = _tk_created[0]
    yield
    if _tk_created[0] != before:
        gc.collect()


@pytest.fixture
def safe_matplotlib():
    """
    Ensures plots are closed after test to free memory.
    """
    yield
    plt.close('all')

@pytest.fixture
def mock_tkinter():
    """
    A comprehensive pytest fixture that mocks essential libraries (tkinter,
    pyvisa, etc.) to prevent any actual GUI rendering or hardware
    communication during tests. This is crucial for CI/CD environments.
    """
    # Create a mock for pymeasure that acts like a package
    mock_pymeasure = MagicMock()
    mock_pymeasure.instruments.keithley.Keithley2400 = MagicMock()
    mock_pymeasure.instruments.keithley.Keithley6517B = MagicMock()
    mock_pymeasure.instruments.agilent.AgilentE4980 = MagicMock()
    
    # A more robust tkinter mock to fix the KeyError during canvas creation
    mock_tk_app = MagicMock()
    canvas_instance_mock = MagicMock()
    # This is the key fix: make the result of internal tk.call() a string,
    # so that winfo_toplevel() and nametowidget() don't fail on a mock object.
    canvas_instance_mock.tk.call.return_value = ".dummy.widget.path"
    mock_tk_app.Canvas.return_value = canvas_instance_mock

    # A bare MagicMock is not a package, so 'from pyvisa.errors import
    # VisaIOError' would fail unless an earlier test had already imported the
    # real submodule. Supply the real errors module so the result does not
    # depend on test order, and so 'except VisaIOError' gets a real exception.
    import pyvisa.errors as real_pyvisa_errors
    mock_pyvisa = MagicMock()
    mock_pyvisa.errors = real_pyvisa_errors

    # Mock libraries that would otherwise create windows or require hardware
    mocked_modules = {
        # '_tkinter' and the matplotlib Tk backends are stubbed so the suite runs
        # on machines with an incomplete Tcl install, where importing a GUI module
        # otherwise dies with "Failed to load Tcl_SetVar". CI has a working Tcl,
        # but developer machines frequently do not.
        '_tkinter': MagicMock(),
        'matplotlib.backends.backend_tkagg': MagicMock(),
        'matplotlib.backends._backend_tk': MagicMock(),
        'tkinter': mock_tk_app,
        'tkinter.ttk': MagicMock(),
        'tkinter.messagebox': MagicMock(),
        'tkinter.filedialog': MagicMock(),
        'tkinter.simpledialog': MagicMock(),
        'tkinter.font': MagicMock(),
        'pyvisa': mock_pyvisa,
        'pyvisa.errors': real_pyvisa_errors,
        'pymeasure': mock_pymeasure,
        'pymeasure.instruments': mock_pymeasure.instruments,
        'pymeasure.instruments.keithley': mock_pymeasure.instruments.keithley,
        'pymeasure.instruments.agilent': mock_pymeasure.instruments.agilent,
        'PIL': MagicMock(),
        'PIL.Image': MagicMock(),
        'PIL.ImageTk': MagicMock(),
    }
    with patch.dict('sys.modules', {
        **mocked_modules,
        # Keep real sys and warnings
        'sys': sys.modules['sys'], 
        'warnings': sys.modules['warnings'],
    }) as patched_modules:
        yield