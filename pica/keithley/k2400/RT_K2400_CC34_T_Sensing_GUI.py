"""
Module: RT_K2400_CC34_T_Sensing_GUI.py
Purpose: GUI module for RT K2400 CC34 T Sensing GUI v1.

         Cryocon Model 34 equivalent of RT_K2400_L350_T_Sensing_GUI.py.
         Measurement logic is unchanged: the Keithley 2400 sources a DC
         current and measures voltage while temperature is logged passively.
         Only the thermometry differs -- temperature comes from Cryocon
         input channel A instead of Lakeshore 350 input A.

         The Cryocon is treated as READ ONLY. No *RST, no CONTROL/STOP, no
         heater, loop or configuration command is ever sent, so whatever is
         driving the temperature (a Cryocon ramp, a cryostat, the front
         panel) keeps running untouched.

Cryocon SCPI verified against the Cryo-con User's Guide; the command set is
common to the Model 32/32B/34 family:
  - INPUT? <ch>          -> channel temperature in that channel's display units
  - INPUT <ch>:UNITS?    -> display units (K, C, F, V or O)
  - GPIB: factory address 12, EOI framing, no EOS terminator
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import sys
import re
import time
import traceback
import runpy
from multiprocessing import Process
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa, Keithley2400 = None, None
    PYMEASURE_AVAILABLE = False


# ===============================================================================
# CRYOCON LINK HARDENING  (read-only; inlined so each module stays standalone)
# ===============================================================================
#
# Three failures seen on a Cryo-con Model 34 Rev 3.03A, 28 Aug 2026:
#
#   1. The bus scan identified the instrument, and the very next session's
#      '*IDN?' died inside viWrite with VI_ERROR_TMO. Pressing Start again
#      connected normally. A timeout on the WRITE means the instrument stopped
#      accepting bytes for a moment, not that it is absent or at another
#      address, so the cure is to wait and ask again instead of giving up.
#      Handled by CRYOCON_OPEN_SETTLE_S plus the retry loop in CryoconLink.
#
#   2. A reading query answered with a Cryo-con status string instead of a
#      number and float() raised, which killed the worker thread. The front
#      panel shows dashes for a sensor fault and dots for a reading that is
#      inside the instrument's range but off the sensor's calibration curve;
#      over the bus those arrive as the literal strings below. A reply can
#      also carry a trailing unit character, as in '77.350K', which float()
#      rejects outright. Handled by parse_cryocon_number(), which names the
#      condition instead of raising a bare ValueError.
#
#   3. The Cryocon was picked by address alone. It is at GPIB0::12 as of
#      29 Aug 2026, and the Lakeshore 350 now sits on GPIB1::12 -- the
#      Cryo-con's own factory address. Selection is by '*IDN?' content, so a
#      re-addressed Cryocon is still found and a stranger on the factory
#      address is not mistaken for one.
#
# Nothing in this block writes to the instrument.

# Factory address, used only as a last-resort hint when nothing answers.
CRYOCON_ADDRESS_HINT = "GPIB0::12"
CRYOCON_IDN_MARKERS = ("CRYOCON", "CRYO-CON", "CRYO CON")

CRYOCON_TIMEOUT_MS = 10000          # per-operation VISA timeout
CRYOCON_OPEN_SETTLE_S = 0.30        # pause after open, before the first command
CRYOCON_MIN_GAP_S = 0.08            # minimum gap between consecutive operations
CRYOCON_CONNECT_ATTEMPTS = 3        # tries for the first '*IDN?'
CRYOCON_RETRY_WAIT_S = 1.5          # pause between those tries

# Timeout for the identification pass, matched to the standalone GPIB
# scanner so this module does not call an instrument silent that the scanner
# reads without trouble.
IDN_SCAN_TIMEOUT_MS = 2000
PROBE_RESOURCE_PREFIXES = ("GPIB", "USB", "TCPIP")

# Literal replies that are status, not data.
CRYOCON_STATUS_STRINGS = {
    '-------': "sensor fault: the sensor is open, disconnected or shorted",
    '.......': ("the reading is within the instrument's range but outside "
                "the sensor's calibration curve"),
    'N/A': "the channel is disabled, or the value does not apply",
    'NACK': "the instrument did not acknowledge the command",
}

# Leading signed decimal, with or without an exponent. Used to peel a trailing
# unit character off replies such as '77.350K'.
_CRYOCON_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')

# The dash and dot runs are as long as the display resolution setting makes
# them, so they are matched by shape rather than by a fixed seven characters.
_CRYOCON_FAULT_RE = re.compile(r'^-{2,}$')
_CRYOCON_RANGE_RE = re.compile(r'^\.{2,}$')


class CryoconStatusError(ValueError):
    """A query returned a Cryo-con status string where a number was expected."""


def parse_cryocon_number(raw, what, channel=None):
    """Turn a Cryo-con reply into a float, or say precisely why it is not one.

    Handles three things the plain float() call did not: status strings, a
    trailing unit character, and multi-channel replies, which come back as
    fields separated by semicolons.
    """
    text = str(raw).strip()
    where = f" on channel {channel}" if channel else ""
    if ';' in text:
        text = text.split(';')[0].strip()
    if text in CRYOCON_STATUS_STRINGS:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS[text]}.")
    if _CRYOCON_FAULT_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': "
            f"{CRYOCON_STATUS_STRINGS['-------']}.")
    if _CRYOCON_RANGE_RE.match(text):
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned '{text}': sensor fault, no "
            f"sensor, or {CRYOCON_STATUS_STRINGS['.......']}.")
    if not text:
        raise CryoconStatusError(
            f"Cryocon {what}{where} returned an empty reply.")
    try:
        return float(text)
    except ValueError:
        pass
    match = _CRYOCON_NUMBER_RE.match(text)
    if match:
        return float(match.group(0))
    raise CryoconStatusError(
        f"Cryocon {what}{where} returned '{text}' "
        "(sensor fault, no sensor, or reading out of range).")


def is_cryocon_idn(idn):
    """True if a '*IDN?' reply came from a Cryo-con temperature instrument."""
    return any(marker in str(idn).upper() for marker in CRYOCON_IDN_MARKERS)


def open_cryocon_session(visa_address, log=None):
    """Open a Cryo-con session, retrying the first '*IDN?'.

    Returns (instrument, idn). Raises ConnectionError if nothing answers, or
    if what answers is not a Cryo-con: this module logs the temperature that
    the whole run is indexed by, so reading it off the wrong instrument is
    worse than not running at all.
    """
    if pyvisa is None:
        raise ConnectionError(
            "PyVISA is not available. Install pyvisa and a VISA backend "
            "(NI-VISA or pyvisa-py).")
    say = log if callable(log) else (lambda msg: print(msg))
    rm = pyvisa.ResourceManager()
    last_error = None
    for attempt in range(1, CRYOCON_CONNECT_ATTEMPTS + 1):
        inst = None
        try:
            inst = rm.open_resource(visa_address)
            inst.timeout = CRYOCON_TIMEOUT_MS
            # The Cryocon GPIB port frames lines with EOI and no EOS
            # character, so the PyVISA termination defaults are left alone.
            time.sleep(CRYOCON_OPEN_SETTLE_S)
            idn = inst.query('*IDN?').strip()
            if not idn:
                raise ConnectionError(
                    f"{visa_address} accepted the command but sent no "
                    "identification.")
            if not is_cryocon_idn(idn):
                inst.close()
                raise ConnectionError(
                    f"{visa_address} is not a Cryo-con: it identifies itself "
                    f"as '{idn}'. Scan the bus and pick the Cryocon's actual "
                    f"address (it does not have to be "
                    f"{CRYOCON_ADDRESS_HINT}).")
            if attempt > 1:
                say(f"  Cryocon answered on attempt {attempt}.")
            return inst, idn
        except ConnectionError:
            # Wrong instrument, or a silent one. Retrying will not change
            # the answer, so let it out immediately.
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            raise
        except Exception as exc:
            last_error = exc
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
            if attempt < CRYOCON_CONNECT_ATTEMPTS:
                say(f"  Cryocon did not answer at {visa_address} "
                    f"(attempt {attempt} of {CRYOCON_CONNECT_ATTEMPTS}): "
                    f"{type(exc).__name__}. Retrying in "
                    f"{CRYOCON_RETRY_WAIT_S:.1f} s.")
                time.sleep(CRYOCON_RETRY_WAIT_S)
    raise ConnectionError(
        f"No reply from a Cryo-con at {visa_address} after "
        f"{CRYOCON_CONNECT_ATTEMPTS} attempts. Last error: {last_error}. "
        "Check that the instrument is powered, that its SYS menu has "
        "RIO-Port set to GPIB rather than RS-232, and that RIO-Address "
        "matches this VISA address.")


def identify_resources(rm, resources):
    """Return {resource: idn} for every resource that answers '*IDN?'.

    Never raises: an address that is busy, silent or not SCPI simply does not
    appear in the result. Serial resources are not probed at all.
    """
    found = {}
    for res in resources:
        if not str(res).upper().startswith(PROBE_RESOURCE_PREFIXES):
            continue
        inst = None
        try:
            inst = rm.open_resource(res)
            inst.timeout = IDN_SCAN_TIMEOUT_MS
            idn = inst.query('*IDN?').strip()
            if idn:
                found[res] = idn
        except Exception:
            pass
        finally:
            if inst is not None:
                try:
                    inst.close()
                except Exception:
                    pass
                # Let the address settle before the next one is addressed.
                time.sleep(0.05)
    return found


try:
    # Dynamically find the project root and add it to the path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
    if project_root not in sys.path:
        sys.path.append(project_root)
except Exception:
    # Path manipulation can fail in some environments (e.g., frozen
    # executables)
    pass

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


class RT_Backend_Passive:
    """ Manages communication for passive monitoring. """

    # Cryocon input channel used for thermometry (fixed, as on the
    # Lakeshore version which always reads input A).
    CC_CHANNEL = 'A'

    def __init__(self):
        self.k2400, self.cryocon = None, None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None

    def connect(self, k2400_visa, cc_visa):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure is not available.")
        self.k2400 = Keithley2400(k2400_visa)
        print(f"  K2400 Connected: {self.k2400.id}")
        # A settle delay and a retried first '*IDN?': on 28 Aug 2026 the
        # very first query of a session died inside viWrite with
        # VI_ERROR_TMO seconds after a bus scan had identified the
        # instrument. open_cryocon_session also refuses a non-Cryocon.
        self.cryocon, cc_idn = open_cryocon_session(cc_visa)
        print(f"  Cryocon Connected: {cc_idn}")
        self._verify_units()

    def _verify_units(self):
        """Confirm the channel reports Kelvin.

        INPUT? returns the reading in the channel's own display units, so a
        channel left in C or F would silently log wrong numbers. This is a
        query only -- the units are never changed from here.
        """
        units = self.cryocon.query(
            f'INPUT {self.CC_CHANNEL}:UNITS?').strip().upper()
        if not units.startswith('K'):
            raise ValueError(
                f"Cryocon channel {self.CC_CHANNEL} is reporting in "
                f"'{units}', not Kelvin. Set that channel to K on the "
                "Cryocon front panel (this program never writes to it).")
        print(f"  Cryocon channel {self.CC_CHANNEL} display units: K")

    def read_temperature(self):
        """Read the Cryocon channel in Kelvin."""
        # parse_cryocon_number names the condition and copes with a reply
        # such as '77.350K', which the plain float() call rejected outright.
        raw = self.cryocon.query(f'INPUT? {self.CC_CHANNEL}').strip()
        return parse_cryocon_number(raw, "temperature",
                                    channel=self.CC_CHANNEL)

    def configure_instruments(self, current_ma, compliance_v):
        # The Cryocon needs no configuration: it is read passively and its
        # heater, control loops and settings are deliberately left untouched.

        # Keithley 2400 setup
        self.k2400.reset()
        self.k2400.use_front_terminals()
        self.k2400.apply_current()
        self.k2400.source_current_range = abs(current_ma * 1e-3) * 1.05
        self.k2400.compliance_voltage = compliance_v
        self.k2400.source_current = current_ma * 1e-3
        self.k2400.measure_voltage()
        self.k2400.enable_source()

    def get_measurement(self):
        voltage = self.k2400.voltage
        temperature = self.read_temperature()
        return temperature, voltage

    def shutdown(self):
        if self.k2400:
            try:
                self.k2400.shutdown()
            except BaseException:
                pass
        if self.cryocon:
            try:
                # Passive: close the session only. No STOP and no heater or
                # loop command, so the Cryocon carries on undisturbed.
                self.cryocon.close()
            except BaseException:
                pass
        print("  Instruments shut down and disconnected.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: k2400 -> keithley -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: k2400 -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


class RT_GUI_Passive:
    PROGRAM_VERSION = "1.0"
    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("K2400 & Cryocon 34: R-T (T-Sensing)")
        self.root.geometry("1600x950")
        self.root.minsize(1400, 800)
        self.root.configure(bg=self.CLR_BG_DARK)
        self.is_running = False
        self.logo_image = None
        self.backend = RT_Backend_Passive()
        self.data_storage = {
            'temperature': [],
            'voltage': [],
            'resistance': []}
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_HEADER,
            foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure(
            'TEntry',
            fieldbackground='#F4EFEA',
            foreground=self.CLR_FG_LIGHT,
            insertcolor=self.CLR_FG_LIGHT)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER)
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_BG_DARK), ('hover', self.CLR_BG_DARK)])
        style.configure(
            'Start.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])
        # --- NEW: Style for the Browse button ---
        style.configure(
            'Browse.TButton',
            foreground=self.CLR_TEXT_DARK,
            background='#BA6B5E')
        style.map(
            'Browse.TButton', background=[
                ('active', '#7C899E'), ('hover', '#7C899E')])
        style.configure(
            'TLabelframe',
            background=self.CLR_HEADER,
            bordercolor='#BA6B5E')
        # --- NEW: Style for Comboboxes to make them more visible ---
        style.configure(
            'TCombobox',
            fieldbackground='#F4EFEA',
            foreground=self.CLR_FG_LIGHT,
            arrowcolor=self.CLR_FG_LIGHT,
            selectbackground='#BA6B5E',
            selectforeground=self.CLR_FG_LIGHT)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_HEADER,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        mpl.rcParams.update({'font.family': 'Segoe UI',
                             'font.size': self.FONT_SIZE_BASE,
                             'axes.titlesize': self.FONT_SIZE_BASE + 4,
                             'axes.labelsize': self.FONT_SIZE_BASE + 2})

    def create_widgets(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(
            header,
            text="K2400 & Cryocon 34: R-T (T-Sensing)",
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left',
            padx=20,
            pady=10)

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        # This is now the scrollable_frame
        left_panel = ttk.Frame(canvas, padding=5)
        left_panel.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=left_panel, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = left_panel

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = self._create_right_panel(self.main_pane)
        self.main_pane.add(right_panel, weight=1)
        self._populate_left_panel(left_panel)

        # sashpos() has no effect until the PanedWindow is actually mapped and
        # laid out — an early call fails SILENTLY. So we (a) wait for the
        # window to be drawn, (b) measure the real required width of the
        # left-panel content instead of guessing, and (c) retry until the
        # sash position verifiably sticks.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()  # force geometry to be computed

            # Measure the actual content width: inner scrollable frame +
            # vertical scrollbar + a little breathing room. Falls back to
            # LEFT_PANEL_WIDTH if measurement isn't ready yet.
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30  # scrollbar (~15px) + padding
            else:
                target = self.LEFT_PANEL_WIDTH

            self.main_pane.sashpos(0, target)

            # Verify it stuck; if not (widget not mapped yet), retry.
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, 0)
        self._create_params_panel(panel, 1)
        self._create_control_panel(panel, 2)
        self._create_console_panel(panel, 3)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 110
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_HEADER,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        try:  # Use a more robust relative path
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir,
                "..",
                "..",
                "assets",
                "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")  # This path is correct
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 6, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_HEADER).grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                15,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_HEADER).grid(
            row=1,
            column=1,
            padx=10,
            pady=(
                0,
                5),
            sticky='nw')
        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)
        details_text = ("Program Name: R vs. T (T-Sensing)\n"
                        "Instruments: Keithley 2400, Cryocon 34\n"
                        "Measurement Range: 100 µΩ to 200 MΩ")
        ttk.Label(
            frame,
            text=details_text,
            justify='left',
            background=self.CLR_HEADER).grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(
            panel, text='Live R-T Curve', style='TLabelframe')
        container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor='white')
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Waiting for logging...", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Resistance (Ω)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent)
        container.grid(row=grid_row, column=0, sticky='new', pady=5)
        container.grid_columnconfigure(1, weight=1)
        self.entries = {}

        # --- Measurement Settings ---
        settings_frame = ttk.LabelFrame(container, text='Measurement Settings')
        settings_frame.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky='nsew',
            pady=(
                0,
                5))
        settings_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(settings_frame, "Source Current (mA)", "1", 0)
        self._create_entry(settings_frame, "Compliance (V)", "10", 1)
        self._create_entry(settings_frame, "Logging Delay (s)", "1", 2)

        # --- VISA Address Settings ---
        visa_frame = ttk.LabelFrame(container, text='Instrument Addresses')
        visa_frame.grid(row=1, column=0, columnspan=2, sticky='nsew')
        visa_frame.grid_columnconfigure(1, weight=1)
        self.cc_cb = self._create_combobox(visa_frame, "Cryocon 34 VISA", 0)
        self.k2400_cb = self._create_combobox(
            visa_frame, "Keithley 2400 VISA", 1)

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='File Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_RT_Passive", 0)
        self._create_entry(frame, "Save Location", "", 1, browse=True)
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=4, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(
            button_frame,
            text="Start",
            style='Start.TButton',
            command=self.start_experiment)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            style='Stop.TButton',
            state='disabled',
            command=self.stop_experiment)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            button_frame,
            text="Scan",
            command=self._scan_for_visa).grid(
            row=0,
            column=2,
            sticky='ew',
            padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    def start_experiment(self):
        try:
            self.params = self._validate_and_get_params()
            self.log("Connecting to instruments...")
            self.backend.connect(
                self.params['k2400_visa'],
                self.params['cc_visa'])
            self.backend.configure_instruments(
                self.params['current_ma'], self.params['compliance_v'])
            self.log(
                "All instruments connected and configured for passive logging.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.params['name']}_{ts}_RT_Passive.csv"
            self.data_filepath = os.path.join(
                self.params['save_path'], filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Temperature (K)", "Voltage (V)",
                                "Resistance (Ohm)", "Elapsed Time (s)"])

            self.set_ui_state(running=True)
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {self.params['name']}")
            self.canvas.draw()
            self.log("Starting passive logging...")
            self.start_time = time.time()
            self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Start Failed", f"{e}")
            self.backend.shutdown()

    def stop_experiment(self, reason=""):
        if not self.is_running:
            return
        self.log(
            f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.is_running = False
        self.backend.shutdown()
        self.set_ui_state(running=False)
        self.ax_main.set_title("Logging stopped.")
        self.canvas.draw()
        if reason:
            messagebox.showinfo("Experiment Finished", f"Reason: {reason}")

    def _experiment_loop(self):
        if not self.is_running:
            return
        try:
            temp, voltage = self.backend.get_measurement()
            resistance = voltage / \
                (self.params['current_ma'] * 1e-3) if self.params['current_ma'] != 0 else float('inf')
            elapsed = time.time() - self.start_time
            self.log(f"T: {temp:.3f} K | R: {resistance:.4e} Ω")

            self.data_storage['temperature'].append(temp)
            self.data_storage['voltage'].append(voltage)
            self.data_storage['resistance'].append(resistance)
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow(
                    [f"{temp:.4f}", f"{voltage:.6e}", f"{resistance:.6e}", f"{elapsed:.2f}"])
            self.line_main.set_data(
                self.data_storage['temperature'],
                self.data_storage['resistance'])
            self.ax_main.relim()
            self.ax_main.autoscale_view()
            self.canvas.draw()

            self.root.after(
                int(self.params['delay_s'] * 1000), self._experiment_loop)

        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            messagebox.showerror("Runtime Error", f"{e}")
            self.stop_experiment("Runtime Error")

    def _validate_and_get_params(self):
        try:
            params = {
                'name': self.entries["Sample Name"].get(),
                'save_path': self.entries["Save Location"].get(),
                'cc_visa': self.cc_cb.get(),
                'current_ma': float(
                    self.entries["Source Current (mA)"].get()),
                'compliance_v': float(
                    self.entries["Compliance (V)"].get()),
                'delay_s': float(
                    self.entries["Logging Delay (s)"].get()),
                'k2400_visa': self.k2400_cb.get()}
            if not all(params.values()):
                raise ValueError("All fields must be filled.")
            return params
        except Exception as e:
            raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        self.is_running = running
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values():
            w.config(state=state)
        for cb in [self.cc_cb, self.k2400_cb]:
            cb.config(state=state if state == 'normal' else 'readonly')
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}")
            self.cc_cb['values'] = resources
            self.k2400_cb['values'] = resources
            default_k2400_addr = 'GPIB1::4::INSTR'

            # Pick the Cryocon by what it says it is, not by where it sits.
            # It moved to GPIB0::12 and the Lakeshore 350 now answers on
            # GPIB1::12, which is the Cryocon's own factory address --
            # selecting by address would log the wrong instrument's
            # temperature against every resistance point.
            identities = identify_resources(self.backend.rm, resources)
            for r in resources:
                self.log(f"  {r}  ->  {identities.get(r, 'no reply')}")
            cryocon = next(
                (r for r in resources
                 if is_cryocon_idn(identities.get(r, ''))), None)
            if cryocon:
                self.cc_cb.set(cryocon)
                self.log(f"Cryocon identified at {cryocon} and selected.")
            else:
                hint = next(
                    (r for r in resources if CRYOCON_ADDRESS_HINT in r), None)
                if hint:
                    self.cc_cb.set(hint)
                    self.log(f"WARNING: no Cryo-con answered *IDN?. Selected "
                             f"{hint} on the factory address alone -- check "
                             f"the instrument is powered and in remote.")
                else:
                    self.log("WARNING: no Cryo-con found on the bus. Pick an "
                             "address manually if you know it.")
            if default_k2400_addr in resources:
                self.k2400_cb.set(default_k2400_addr)
        else:
            self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.entries["Save Location"].config(state='normal')
            self.entries["Save Location"].delete(0, 'end')
            self.entries["Save Location"].insert(0, path)
            self.entries["Save Location"].config(state='disabled')

    def _create_entry(
            self,
            parent,
            label_text,
            default_value,
            row,
            browse=False):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=30)
        entry.grid(
            row=row,
            column=1,
            sticky='ew',
            padx=10,
            pady=3,
            columnspan=2)
        entry.insert(0, default_value)
        self.entries[label_text] = entry
        if browse:
            btn = ttk.Button(
                parent,
                text="Browse...",
                style='Browse.TButton',
                command=self._browse_file_location)
            btn.grid(row=row, column=3, sticky='e', padx=(0, 10))
            entry.config(state='disabled')

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        cb = ttk.Combobox(parent, font=self.FONT_BASE, state='readonly')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.is_running and messagebox.askyesno(
                "Exit", "Experiment is running. Stop and exit?"):
            self.stop_experiment("Application closed by user.")
            self.root.destroy()
        elif not self.is_running:
            self.root.destroy()


if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror(
            "Dependency Error",
            "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk()
        app = RT_GUI_Passive(root)
        root.mainloop()
