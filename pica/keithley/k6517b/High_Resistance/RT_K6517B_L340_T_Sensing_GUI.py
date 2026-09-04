"""
Module: RT_K6517B_L340_T_Sensing_GUI.py
Purpose: High-resistance R-T (Keithley 6517B electrometer) with passive
         temperature sensing from a Lake Shore Model 340.
         Port of RT_K6517B_L350_T_Sensing_GUI.py to the Model 340 command
         set.  The Keithley 6517B logic (zero check / zero correct, source,
         resistance read) is untouched; only the Lake Shore side changes.

What is different from the Model 350 version, and why
-----------------------------------------------------
  * *IDN? must contain MODEL340 or Start is refused.  The lab's 340, 350,
    Cryocon 34 and Keithley 6221 have all lived at IEEE address 12 at some
    point; sending RANGE 0 to the wrong box is not acceptable.
  * *CLS only.  Never *RST: on a 340 "*RST sets controller parameters to
    power-up settings" (340 manual, printed 9-24), loop, setpoint and ramp
    included.  A passive monitor has no business doing that.
  * Heater-off at start is a checkbox, ON by default.  When ticked the
    monitor sends "RANGE 0" once and "RANGE?" must read back 0
    (printed 9-40 / 9-41).  RANGE takes no loop argument on a 340; the 350
    form "RANGE 1,0" is a syntax error.  When unticked the heater is left
    exactly as found.  This checkbox replaces the unconditional
    "RANGE 1,0" the 350 version sent at start.
  * HTR? takes no argument on a 340 and always reports Loop 1 in percent
    (printed 9-33).  The 350 form "HTR? 1" is a syntax error.
  * The heater is never touched at stop or close (the 350 version did not
    touch it at close either; its close-time comment just said so).
  * The sensor input is selectable (A or B on a base Model 340; C and D
    are only present with the 3462 dual-input option card).  The 350
    version was fixed to input A.
  * RDGST? <input> is read with every sample (printed 9-41).  A non-zero
    status (invalid, old, under/over range, units zero/overrange) is logged
    with the reading and written to the data file, so a 0.000 K row is
    never mistaken for a real reading.
  * The bus scan pre-selects the lab's 340 at IEEE address 19 ("::19::",
    since 3 Sep 2026).  Addresses 12 and 15 are never auto-picked.

Commands used on the Lake Shore (Model 340 User's Manual, Chapter 9):
  *IDN?          LSCI,MODEL340,<serial>,<firmware>
  *CLS           clear status registers
  RANGE 0        heater off (Loop 1), no loop argument   RANGE?  -> n
  HTR?           Loop 1 heater output, %                 (no argument)
  KRDG? <input>  kelvin reading
  RDGST? <input> reading status bit weighting
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import threading
import queue
import os
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley6517B
    from pyvisa.errors import VisaIOError
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa = None
    Keithley6517B = None
    VisaIOError = None
    PYMEASURE_AVAILABLE = False


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
        # Go up 3 levels: High_Resistance -> k6517b -> keithley -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "..", "..", "utils", "PlotterUtil_GUI.py")
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
        # Go up 3 levels: High_Resistance -> k6517b -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

# RDGST? bit weights, Model 340 manual printed 9-41.
RDGST_BITS = (
    (1, "invalid reading"),
    (2, "old reading"),
    (16, "temp underrange"),
    (32, "temp overrange"),
    (64, "units zero"),
    (128, "units overrange"),
)


def describe_reading_status(status):
    """Turn an RDGST? integer into readable text ('' when the reading is good)."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return f"unparseable status '{status}'"
    if status == 0:
        return ""
    names = [name for bit, name in RDGST_BITS if status & bit]
    if not names:
        names = [f"unknown bit(s) {status}"]
    return ", ".join(names)


# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


class Lakeshore340_Backend:
    """Passive monitor for the Lake Shore Model 340.

    The only command that changes anything is the optional "RANGE 0" sent
    once at start when the operator has asked for the heater to be turned
    off. Everything else is a query.  Never *RST.
    """

    MODEL_TOKENS = ("MODEL340", "MODEL 340")

    def __init__(self, visa_address):
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address, timeout=10000)
        # The 340 answers with <CR><LF> and EOI by default (IEEE command,
        # printed 9-33); '\n' as read terminator works on the lab's 340.
        self.instrument.read_termination = '\n'
        self.instrument.write_termination = '\n'
        self.idn = self.instrument.query('*IDN?').strip()
        print(f"Lakeshore Connected: {self.idn}")

    def is_model_340(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def configure_for_monitoring(self, heater_off=True):
        """Clear the status registers; optionally turn the heater off.

        No *RST: on a 340 that resets loop, setpoint and ramp settings.
        Returns the heater range read back after configuration.
        """
        self.instrument.write('*CLS')
        time.sleep(0.2)
        if heater_off:
            self.instrument.write('RANGE 0')  # Loop 1 heater off (9-40)
            time.sleep(0.3)
        rng = self.get_heater_range()
        if heater_off and rng != 0:
            raise RuntimeError(
                f"Sent RANGE 0 but RANGE? reads back {rng}. Heater is NOT off. "
                "Check the front panel (Remote/Local) and try again.")
        return rng

    def get_heater_range(self):
        """RANGE? -> 0 (off) .. 5.  No output argument on a 340."""
        return int(float(self.instrument.query('RANGE?').strip()))

    def get_temperature(self, sensor):
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def get_reading_status(self, sensor):
        """RDGST? <input> -> bit-weighted status (0 = good)."""
        return int(float(self.instrument.query(f'RDGST? {sensor}').strip()))

    def get_heater_output(self):
        """HTR? -> Loop 1 heater output in percent.  No argument on a 340."""
        return float(self.instrument.query('HTR?').strip())

    def close(self):
        if self.instrument:
            try:
                # In passive mode, we don't want to change the heater state on close.
                # The user might be monitoring an ongoing experiment.
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")


class Combined_Backend:
    """Manages both the Lakeshore 340 and Keithley 6517B."""

    def __init__(self):
        self.lakeshore = None
        self.keithley = None
        self.params = {}

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore340_Backend(self.params['lakeshore_visa'])
        if not self.lakeshore.is_model_340():
            raise RuntimeError(
                f"'{self.params['lakeshore_visa']}' answered "
                f"'{self.lakeshore.idn}', which is not a Lake Shore Model 340. "
                "Refusing to send RANGE 0 to an unknown instrument. Pick the "
                "right address.")
        # *CLS only (never *RST); heater off only if the checkbox asks for it,
        # and then verified with RANGE?.
        rng = self.lakeshore.configure_for_monitoring(
            heater_off=self.params.get('heater_off', True))
        if self.params.get('heater_off', True):
            print("Lakeshore 340 heater turned OFF (RANGE 0 sent, RANGE? = 0).")
        else:
            print(f"Lakeshore 340 heater left as found: RANGE? = {rng} "
                  f"({'off' if rng == 0 else 'ON'}).")

        self.keithley = Keithley6517B(self.params['keithley_visa'])
        print(f"Keithley Connected: {self.keithley.id}")
        self._perform_keithley_zero_check()

        self.keithley.source_voltage = self.params['source_voltage']
        self.keithley.current_nplc = 1
        self.keithley.enable_source()
        print(f"Keithley source enabled: {self.params['source_voltage']} V")

    def _perform_keithley_zero_check(self):
        print("  --- Starting Keithley Zero Correction ---")
        self.keithley.reset()
        self.keithley.measure_resistance()
        print("  Step 1: Enabling Zero Check (shorts the input)...")
        self.keithley.write(':SYSTem:ZCHeck ON')
        time.sleep(2)
        print("  Step 2: Acquiring the zero correction value...")
        self.keithley.write(':SYSTem:ZCORrect:ACQuire')
        time.sleep(3)
        print("  Step 3: Disabling Zero Check...")
        self.keithley.write(':SYSTem:ZCHeck OFF')
        time.sleep(1)
        print("  Step 4: Enabling Zero Correction for all measurements...")
        self.keithley.write(':SYSTem:ZCORrect ON')
        time.sleep(1)
        print("  Zero Correction Complete.")

    def get_measurement(self):
        time.sleep(self.params['delay'])
        sensor = self.params.get('sensor', 'A')
        current_temp = self.lakeshore.get_temperature(sensor)
        # RDGST? with every sample (printed 9-41) so an out-of-range
        # 0.000 K is never taken as a real reading.
        reading_status = self.lakeshore.get_reading_status(sensor)
        heater_output = self.lakeshore.get_heater_output()  # HTR?, no argument
        resistance = self.keithley.resistance
        if resistance != 0 and resistance != float(
                'inf') and resistance == resistance:
            current = self.params['source_voltage'] / resistance
        else:
            current = 0.0
        return current_temp, heater_output, current, resistance, reading_status

    def close_instruments(self):
        print("\n--- [Backend] Closing all instrument connections. ---")
        if self.keithley:
            self.keithley.shutdown()
            print("  Keithley connection closed and source OFF.")
        if self.lakeshore:
            self.lakeshore.close()  # Heater state is not touched at close.
            print("  Lakeshore connection closed; heater state not touched.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class Integrated_RT_GUI:
    PROGRAM_VERSION = "4.2"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 400  # default sash position so the left panel starts fully visible

    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        # Path is three directories up from the script location
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

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
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_INPUT = ('Segoe UI', FONT_SIZE_BASE - 1)  # Smaller font for inputs
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("K6517B & L340: R-T (T-Sensing)")
        self.root.geometry("1550x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {
            'time': [],
            'temperature': [],
            'current': [],
            'resistance': []}
        self.log_scale_var = tk.BooleanVar(value=True)
        self.logo_image = None  # Attribute to hold the logo image reference
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self._plot_dirty = False

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure(
            'TCheckbutton',
            background=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            font=self.FONT_BASE)
        style.map(
            'TCheckbutton', background=[
                ('active', self.CLR_GRAPH_BG)], indicatorcolor=[
                ('selected', self.CLR_ACCENT_GREEN)])
        style.configure(
            'Heater.TCheckbutton',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.map(
            'Heater.TCheckbutton', background=[
                ('active', self.CLR_BG_DARK)], indicatorcolor=[
                ('selected', self.CLR_ACCENT_GREEN)])
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
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
        # This is the frame that will be scrolled
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)  # More weight for the graph panel

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)
        self.create_graph_frame(right_panel)

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

    def create_header(self):
        # --- NEW: Define an italic font for the program name ---
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')

        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="K6517B & L340: R-T (T-Sensing)",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)
        Label(
            header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL).pack(
            side='right',
            padx=20,
            pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Information',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
                # IMPORTANT: Keep a reference to the image to prevent it from
                # being garbage collected
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT,
                    justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT,
                justify='center')

        # Institute Name (larger font)
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 6, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                10,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=1,
            column=1,
            padx=10,
            sticky='nw')

        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)

        # Program details
        details_text = ("Program Name: R vs. T (T-Sensing)\n"
                        "Instruments: Lakeshore 340, Keithley 6517B\n"
                        "Lakeshore: queries only, plus optional RANGE 0 at start\n"
                        "Measurement Range: 1 Ω to 10 PΩ")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Experiment Parameters',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady_val = (5, 5)

        # --- SIMPLIFIED INPUTS ---
        Label(
            frame,
            text="Sample Name:").grid(
            row=0,
            column=0,
            columnspan=2,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=1, column=0, columnspan=2, padx=10, pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Source Voltage (V):").grid(
            row=2,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Source Voltage"] = Entry(
            frame, font=self.FONT_BASE, width=15)
        self.entries["Source Voltage"].grid(
            row=3, column=0, padx=(
                10, 5), pady=(
                0, 5), sticky='ew')

        Label(
            frame,
            text="Logging Delay (s):").grid(
            row=2,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE, width=15)
        self.entries["Delay"].grid(
            row=3, column=1, padx=(
                5, 10), pady=(
                0, 5), sticky='ew')
        self.entries["Delay"].insert(0, "1.0")  # Default to 1 second

        Label(
            frame,
            text="Lakeshore VISA:").grid(
            row=4,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.lakeshore_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(
            row=5, column=0, padx=(
                10, 5), pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Keithley VISA:").grid(
            row=4,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.keithley_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(
            row=5, column=1, padx=(
                5, 10), pady=(
                0, 10), sticky='ew')

        # --- Lake Shore 340 sensor input and heater-off option ---
        Label(
            frame,
            text="Lakeshore Sensor Input:").grid(
            row=6,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.sensor_var = tk.StringVar(value='A')
        self.sensor_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            textvariable=self.sensor_var, values=['A', 'B', 'C', 'D'])
        self.sensor_cb.grid(
            row=7, column=0, padx=(
                10, 5), pady=(
                0, 10), sticky='ew')
        # Heater-off at start. ON by default: a passive run is normally
        # started on a system that should be drifting freely, and the 340
        # keeps whatever heater range was last set through the keypad.
        self.heater_off_var = tk.BooleanVar(value=True)
        self.heater_off_cb = ttk.Checkbutton(
            frame,
            text="Turn heater OFF at start (RANGE 0)",
            variable=self.heater_off_var,
            style='Heater.TCheckbutton')
        self.heater_off_cb.grid(
            row=7, column=1, padx=(
                5, 10), pady=(
                0, 10), sticky='w')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=8,
            column=0,
            columnspan=2,
            padx=10,
            pady=4,
            sticky='ew')
        self.file_button = ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location)
        self.file_button.grid(
            row=9,
            column=0,
            columnspan=2,
            padx=10,
            pady=4,
            sticky='ew')
        self.start_button = ttk.Button(
            frame,
            text="Start Logging",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=10, column=0, padx=(
                10, 5), pady=(
                10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=10, column=1, padx=(
                5, 10), pady=(
                10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Console Output',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log(
            "Console initialized. Configure parameters and scan for instruments.")
        if not PYMEASURE_AVAILABLE:
            self.log("CRITICAL: PyMeasure or PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(
            parent,
            text='Live Graphs',
            relief='groove',
            bg=self.CLR_GRAPH_BG,
            fg=self.CLR_BG_DARK,
            font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        # Use a standard tk.Frame and set its background to match the graph
        # to make the checkbox appear integrated with the graph area.
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        self.log_scale_cb = ttk.Checkbutton(
            top_bar,
            text="Logarithmic Resistance Axis",
            variable=self.log_scale_var,
            command=self._update_y_scale)
        self.log_scale_cb.pack(side='right', padx=5)
        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(
            gs[1, 0], sharex=self.ax_main)  # Share X-axis with main plot
        self.ax_sub2 = self.figure.add_subplot(
            gs[1, 1])  # Temp vs Time has its own X-axis
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_ylabel("Resistance (Ω)")
        if self.log_scale_var.get():
            self.ax_main.set_yscale('log')
        else:
            self.ax_main.set_yscale('linear')
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)
        self.line_sub1, = self.ax_sub1.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Current (A)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)
        self.line_sub2, = self.ax_sub2.plot(
            [], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _update_y_scale(self):
        self.ax_main.set_yscale('log' if self.log_scale_var.get() else 'linear')
        self._plot_dirty = True       # force a rescale on next refresh
        self.canvas.draw_idle()

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'source_voltage': float(self.entries["Source Voltage"].get()),
                'delay': float(self.entries["Delay"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'keithley_visa': self.keithley_cb.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError(
                    "All fields, VISA addresses, and save location are required.")
            # Added after the all() check: a False checkbox is a valid value.
            params['sensor'] = self.sensor_var.get()
            params['heater_off'] = bool(self.heater_off_var.get())

            self.backend.initialize_instruments(params)
            self.log(f"Lakeshore 340: {self.backend.lakeshore.idn}")
            if params['heater_off']:
                self.log("Lakeshore 340 heater turned OFF (RANGE 0 sent, RANGE? = 0).")
            else:
                self.log("Lakeshore 340 heater left as found (checkbox off).")
            self.log(
                f"Backend initialized for sample: {params['sample_name']} "
                f"(Lakeshore input {params['sensor']})")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_RT_passive.dat"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(
                    [f"# Sample: {params['sample_name']}", f"Source V: {params['source_voltage']}V"])
                writer.writerow([f"# Lakeshore 340: {self.backend.lakeshore.idn}",
                                 f"Sensor input: {params['sensor']}",
                                 f"Heater at start: "
                                 f"{'turned off' if params['heater_off'] else 'unchanged'}"])
                writer.writerow(["Timestamp",
                                 "Elapsed Time (s)",
                                 "Temperature (K)",
                                 "Heater Output (%)",
                                 "Applied Voltage (V)",
                                 "Measured Current (A)",
                                 "Resistance (Ohm)",
                                 "LS340 Reading Status"])

            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}")

            # --- START LOGGING DIRECTLY ---
            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.sensor_cb.config(state='disabled')
            self.heater_off_cb.config(state='disabled')
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]:
                line.set_data([], [])
            self.ax_main.set_title(
                f"R-T Curve: {params['sample_name']}",
                fontweight='bold')

            self.canvas.draw_idle()

            self.log("Starting passive data logging...")
            self.start_time = time.time()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)
            self.root.after(250, self._refresh_plot)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror(
                "Initialization Error",
                f"Could not start measurement.\n{e}")

    def stop_measurement(self, from_user=True):
        if self.is_running:
            self.is_running = False
            self.log("Measurement stopped by user.")
            self.canvas.draw_idle()
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.sensor_cb.config(state='readonly')
            self.heater_off_cb.config(state='normal')
            self.backend.close_instruments()
            self.log("Lakeshore heater state was not touched at stop.")
            if from_user:
                messagebox.showinfo(
                    "Info", "Measurement stopped and instruments disconnected.")

    def _measurement_worker(self):
        """Worker thread to perform measurements and put data into a queue."""
        while self.is_running:
            try:
                temp, htr, cur, res, status = self.backend.get_measurement()
                elapsed = time.time() - self.start_time
                self.data_queue.put((temp, htr, cur, res, status, elapsed))
            except Exception as e:
                self.data_queue.put(e)
                break

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
                    self.stop_measurement(False)
                    messagebox.showerror(
                        "Runtime Error", f"A critical error occurred: {data}")
                    return

                temp, htr, cur, res, status, elapsed = data
                status_text = describe_reading_status(status)
                if status_text:
                    self.log(f"T:{temp:.3f}K | R:{res:.3e}Ω | I:{cur:.3e}A"
                             f"   [RDGST {status}: {status_text}]")
                else:
                    self.log(f"T:{temp:.3f}K | R:{res:.3e}Ω | I:{cur:.3e}A")
                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            f"{elapsed:.2f}",
                            f"{temp:.4f}",
                            f"{htr:.2f}",
                            f"{self.backend.params['source_voltage']:.4e}",
                            f"{cur:.4e}",
                            f"{res:.4e}",
                            status])

                self.data_storage['time'].append(elapsed)
                self.data_storage['temperature'].append(temp)
                self.data_storage['current'].append(cur)
                self.data_storage['resistance'].append(res)
                # Mark that the plot needs a refresh; actual redraw is
                # decoupled and throttled (see _refresh_plot).
                self._plot_dirty = True

        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _refresh_plot(self):
        """Redraws the plots at a fixed cadence, independent of data rate.

        A normal (non-blitted) draw is used so that the axes — ticks,
        limits, gridlines and scale — always stay in sync with the data.
        """
        if self._plot_dirty:
            self._plot_dirty = False

            temps = self.data_storage['temperature']
            res = self.data_storage['resistance']
            cur = self.data_storage['current']
            t = self.data_storage['time']

            self.line_main.set_data(temps, res)
            self.line_sub1.set_data(temps, cur)
            self.line_sub2.set_data(t, temps)

            # Recompute and apply limits on every axis.
            self._autoscale_axis(self.ax_main, x=temps, y=res,
                                 log_y=self.log_scale_var.get())
            self._autoscale_axis(self.ax_sub1, x=temps, y=cur)
            self._autoscale_axis(self.ax_sub2, x=t, y=temps)

            # Full redraw keeps ticks/labels/gridlines correct and is
            # resize-proof. draw_idle() coalesces redraws efficiently.
            self.canvas.draw_idle()

        if self.is_running:
            self.root.after(250, self._refresh_plot)

    def _autoscale_axis(self, ax, x, y, log_y=False, margin=0.05):
        """Rescale an axis, ignoring non-finite and (for log) non-positive
        values so the axis never collapses or freezes."""
        import math

        xs = [v for v in x if v is not None and math.isfinite(v)]
        if log_y:
            ys = [v for v in y if v is not None and math.isfinite(v) and v > 0]
        else:
            ys = [v for v in y if v is not None and math.isfinite(v)]

        if not xs or not ys:
            return

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)

        # X padding (linear)
        xpad = (xmax - xmin) * margin or 0.5
        ax.set_xlim(xmin - xpad, xmax + xpad)

        # Y padding
        if log_y:
            # pad multiplicatively in log space
            ax.set_ylim(ymin / (1 + margin), ymax * (1 + margin))
        else:
            ypad = (ymax - ymin) * margin or abs(ymax) * margin or 1e-12
            ax.set_ylim(ymin - ypad, ymax + ypad)

    def _scan_for_visa_instruments(self):
        if not pyvisa:
            self.log("ERROR: PyVISA is not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_cb['values'] = resources
                self.keithley_cb['values'] = resources
                # Nothing is probed. The lab's 340 is at address 19 (3 Sep
                # 2026); 12 and 15 are never auto-picked because other
                # instruments live there.
                for res in resources:
                    if LAKESHORE340_ADDRESS_HINT in res:
                        self.lakeshore_cb.set(res)
                    if "GPIB1::27" in res:
                        self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "Measurement running. Stop and exit?"):
                self.stop_measurement(from_user=False)
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    Integrated_RT_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
