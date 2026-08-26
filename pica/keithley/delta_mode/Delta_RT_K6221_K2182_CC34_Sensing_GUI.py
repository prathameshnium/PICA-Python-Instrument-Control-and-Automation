"""
Module: Delta_RT_K6221_K2182_CC34_Sensing_GUI.py
Purpose: GUI module for Delta RT K6221 K2182 CC34 Sensing GUI v1.

         Cryocon Model 34 equivalent of
         Delta_RT_K6221_K2182_L350_Sensing_GUI.py. Measurement logic is
         unchanged: the Keithley 6221/2182 pair runs in delta mode while
         temperature is logged passively. Only the thermometry differs --
         temperature comes from Cryocon input channel A instead of
         Lakeshore 350 input A.

         The Cryocon is treated as READ ONLY. No *RST, no CONTROL/STOP, no
         heater, loop or configuration command is ever sent, so whatever is
         driving the temperature keeps running untouched.

Cryocon SCPI verified against the Cryo-con User's Guide; the command set is
common to the Model 32/32B/34 family:
  - INPUT? <ch>          -> channel temperature in that channel's display units
  - INPUT <ch>:UNITS?    -> display units (K, C, F, V or O)
  - GPIB: factory address 12, EOI framing, no EOS terminator
"""

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, messagebox, scrolledtext, Canvas, filedialog
import sys
import os
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import queue
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
except ImportError:
    pyvisa = None

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
        # Go up 2 levels: delta_mode -> keithley -> pica
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
        # Go up 2 levels: delta_mode -> keithley -> pica
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


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Combined_Backend:
    """
    A dedicated class to handle backend instrument communication.
    - Keithley 6221 logic is preserved from Delta_Lakeshore_Front_end_V7.py.
    - Cryocon 34 is read passively for temperature sensing only.
    """

    # Cryocon input channel used for thermometry (fixed, as on the
    # Lakeshore version which always reads input A).
    CC_CHANNEL = 'A'

    def __init__(self):
        self.params = {}
        self.keithley = None
        self.cryocon = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(
                    f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, parameters):
        """Receives all parameters from the GUI and configures the instruments."""
        print("\n--- [Backend] Initializing Instruments ---")
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            # --- Initialize Keithley 6221 (Unaltered Logic) ---
            print("  Connecting to Keithley 6221...")
            self.keithley = self.rm.open_resource(self.params['keithley_visa'])
            self.keithley.timeout = 25000
            print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
            self.keithley.write("*rst; status:preset; *cls")
            self.keithley.write(
                f"SOUR:DELT:HIGH {self.params['apply_current']}")
            # Set compliance voltage
            self.keithley.write(
                f"SOUR:DELT:PROT {self.params['compliance_v']}")
            self.keithley.write("SOUR:DELT:ARM")
            time.sleep(1)
            self.keithley.write("INIT:IMM")
            print("  Keithley 6221/2182 Configured and Armed for Delta Mode.")

            # --- Initialize Cryocon 34 (Passive Mode) ---
            # The Cryocon GPIB port frames lines with EOI and no EOS
            # character, so the PyVISA termination defaults are left alone.
            print("  Connecting to Cryocon 34 for passive monitoring...")
            self.cryocon = self.rm.open_resource(
                self.params['cryocon_visa'])
            self.cryocon.timeout = 10000
            print(f"    Connected to: {self.cryocon.query('*IDN?').strip()}")
            self._verify_units()
            print("  Cryocon 34 connection is passive. No settings will be changed.")

            print("--- [Backend] Instrument Initialization Complete ---")
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure an instrument. {e}")
            raise e

    def get_measurement(self):
        """Performs a single measurement and returns all relevant data."""
        if not self.keithley or not self.cryocon:
            raise ConnectionError("One or more instruments are not connected.")

        # Get data from Keithley
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        data_points = raw_data.strip().split(',')
        voltage = float(data_points[0])

        # Avoid division by zero if current is zero
        if self.params['apply_current'] != 0:
            resistance = voltage / self.params['apply_current']
        else:
            resistance = float('inf')

        # Get data from the Cryocon
        temperature = self.read_temperature()

        return resistance, voltage, temperature

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
        print(f"    Cryocon channel {self.CC_CHANNEL} display units: K")

    def read_temperature(self):
        """Read the Cryocon channel in Kelvin."""
        raw = self.cryocon.query(f'INPUT? {self.CC_CHANNEL}').strip()
        try:
            return float(raw)
        except ValueError:
            raise ValueError(
                f"Cryocon channel {self.CC_CHANNEL} returned '{raw}' "
                "(sensor fault, no sensor, or reading out of range).")

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                self.keithley.write("SOUR:CLE")
                self.keithley.write("*RST")
                self.keithley.close()
                print("  Keithley 6221 connection closed.")
            except pyvisa.errors.VisaIOError:
                pass
            finally:
                self.keithley = None
        if self.cryocon:
            try:
                # Passive: close the session only. No STOP and no heater or
                # loop command, so the Cryocon carries on undisturbed.
                self.cryocon.close()
                print("  Cryocon 34 connection closed (was in passive mode).")
            except pyvisa.errors.VisaIOError:
                pass
            finally:
                self.cryocon = None


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class MeasurementAppGUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "8.1"
    LOGO_SIZE = 110
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theming and Styling ---
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
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(
            "K6221/2182 & Cryocon 34: Delta Mode R-T (Passive Sensing)")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {
            'time': [],
            'voltage': [],
            'resistance': [],
            'temperature': []}
        self.logo_image = None  # Attribute to hold the logo image reference
        self.data_queue = queue.Queue()
        # Plot updates are decoupled from data acquisition: data callbacks
        # only set this flag; _refresh_plot redraws on a fixed cadence.
        self._plot_dirty = False
        self.visa_queue = queue.Queue()
        self.measurement_thread = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles for the modern look."""
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
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_TEXT_DARK,
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

    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # pack_propagate(False) makes the requested width stick; weight=0
        # keeps the left panel from being squeezed as the window resizes,
        # while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(
            self.main_pane, width=self.LEFT_PANEL_WIDTH)
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

        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw")
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
        self.main_pane.add(right_panel, weight=1)

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        console_pane = self.create_console_frame(scrollable_frame)
        # Pack the console last so it appears at the bottom of the scroll area.
        console_pane.pack(pady=5, padx=10, fill='x')

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
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(
            header_frame,
            text="K6221/2182 & Cryocon 34: Delta Mode R-T (Passive Sensing)",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)

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
            text=f"v{self.PROGRAM_VERSION}",
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
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)
        self.root.after(50, lambda: self._load_logo(logo_canvas))
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font).grid(
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
            font=institute_font).grid(
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

        details_text = ("Program Mode: R vs. T (Passive Sensing)\n"
                        "Instruments: Keithley 6221/2182, Cryocon 34\n"
                        "Measurement Range: 10 nΩ to 100 MΩ")
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

    def _load_logo(self, canvas):
        """Loads the logo image after the main window is drawn."""
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)  # Keep a reference
                canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT,
                    justify='center')
        else:
            canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT,
                justify='center')

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
            text="Apply Current (A):").grid(
            row=2,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Apply Current"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Apply Current"].grid(
            row=3, column=0, padx=(
                10, 5), pady=(
                0, 5), sticky='ew')
        self.entries["Apply Current"].insert(0, "1E-6")  # Default value

        Label(
            frame,
            text="Compliance Voltage (V):").grid(
            row=2,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.entries["Compliance Voltage"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Compliance Voltage"].grid(
            row=3, column=1, padx=(
                5, 10), pady=(
                0, 5), sticky='ew')
        self.entries["Compliance Voltage"].insert(0, "10")  # Default value

        Label(
            frame,
            text="Keithley 6221 VISA:").grid(
            row=4,
            column=0,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.keithley_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(
            row=5, column=0, padx=(
                10, 5), pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Cryocon 34 VISA:").grid(
            row=4,
            column=1,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.cryocon_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.cryocon_cb.grid(
            row=5, column=1, padx=(
                5, 10), pady=(
                0, 10), sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self.start_visa_scan)
        self.scan_button.grid(
            row=6,
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
            row=7,
            column=0,
            columnspan=2,
            padx=10,
            pady=4,
            sticky='ew')
        self.start_button = ttk.Button(
            frame,
            text="Start Measurement",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=8, column=0, padx=(
                10, 5), pady=(
                10, 10), sticky='ew')
        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=8, column=1, padx=(
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
        if not pyvisa:
            self.log("CRITICAL: PyVISA not found. Please run 'pip install pyvisa'.")
        if not PIL_AVAILABLE:
            self.log(
                "WARNING: Pillow not found. Logo will not display. Run 'pip install Pillow'.")
        if not os.path.exists(self.LOGO_FILE_PATH):
            self.log(
                f"WARNING: '{self.LOGO_FILE_PATH}' not found. Logo cannot be displayed.")
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
        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        # Main Plot: Resistance vs Temperature
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o',
                                            markersize=3, linestyle='-')
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Resistance (Ω)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        # Sub Plot 1: Voltage vs Temperature
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.',
                                            markersize=4, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Voltage (V)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        # Sub Plot 2: Temperature vs Time
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.',
                                            markersize=4, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

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
                'apply_current': float(
                    self.entries["Apply Current"].get()),
                'compliance_v': float(
                    self.entries["Compliance Voltage"].get()),
                'keithley_visa': self.keithley_cb.get(),
                'cryocon_visa': self.cryocon_cb.get()}
            if not all(params.values()) or not self.file_location_path:
                raise ValueError(
                    "All fields, VISA addresses, and a save location are required.")

            self.backend.initialize_instruments(params)
            self.log(
                f"Backend initialized for sample: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_Delta_passive.dat"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}",
                                 f"Applied Current: {params['apply_current']:.4e} A"])
                writer.writerow(["Timestamp",
                                 "Elapsed Time (s)",
                                 "Temperature (K)",
                                 "Voltage (V)",
                                 "Resistance (Ohm)"])

            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}")
            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]:
                line.set_data([], [])
            self.ax_main.set_title(
                f"Sample: {params['sample_name']} | I = {params['apply_current']:.2e} A",
                fontweight='bold')

            self._plot_dirty = False
            self.canvas.draw_idle()

            self.log("Measurement loop started.")

            # Start the worker thread, the queue processor and the plot timer
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
            if self.backend:
                self.backend.close_instruments()

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            # Final flush: is_running is already False, so this won't
            # reschedule the timer.
            self._refresh_plot()
            self.canvas.draw_idle()
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            self.log("Instrument connections closed.")
            messagebox.showinfo(
                "Info", "Measurement stopped and instruments disconnected.")

    def _measurement_worker(self):
        """Worker thread to perform measurements and put data into a queue."""
        while self.is_running:
            try:
                res, volt, temp = self.backend.get_measurement()
                elapsed = time.time() - self.start_time
                # Put the acquired data into the queue for the main thread
                self.data_queue.put((res, volt, temp, elapsed))
                time.sleep(1)  # Control the measurement frequency
            except Exception as e:
                # If an error occurs, put it in the queue to be handled by the
                # main thread
                self.data_queue.put(e)
                break
        if not self.is_running:
            # Signal that the thread is done
            self.data_queue.put(None)

    def _process_data_queue(self):
        """Processes data from the queue. Refactored to reduce complexity."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if isinstance(data, Exception):
                    self.log(
                        f"RUNTIME ERROR in worker thread: {traceback.format_exc()}")
                    self.stop_measurement()
                    messagebox.showerror(
                        "Runtime Error",
                        "A critical error occurred in the measurement thread.")
                    return  # Stop processing
                if data is None:  # Sentinel value
                    return  # Stop processing

                # Unpack and save data
                self._handle_new_data_point(data)

        except queue.Empty:
            pass

        # Schedule the next check if the measurement is still running
        if self.is_running:
            self.root.after(100, self._process_data_queue)

    def _handle_new_data_point(self, data):
        """Helper: Unpacks, logs, and saves a single data point."""
        res, volt, temp, elapsed = data
        self.log(f"T: {temp:.3f} K | R: {res:.4e} Ω | V: {volt:.4e} V")
        with open(self.data_filepath, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             f"{elapsed:.2f}", f"{temp:.4f}", f"{volt:.6e}", f"{res:.6e}"])

        self.data_storage['time'].append(elapsed)
        self.data_storage['temperature'].append(temp)
        self.data_storage['voltage'].append(volt)
        self.data_storage['resistance'].append(res)
        self._plot_dirty = True

    def _refresh_plot(self):
        """Redraws the plots at a fixed cadence, independent of data rate.

        A normal (non-blitted) draw is used so that the axes — ticks,
        limits, gridlines and scale — always stay in sync with the data.
        """
        if self._plot_dirty:
            self._plot_dirty = False

            temps = self.data_storage['temperature']
            res = self.data_storage['resistance']
            volts = self.data_storage['voltage']
            t = self.data_storage['time']

            self.line_main.set_data(temps, res)
            self.line_sub1.set_data(temps, volts)
            self.line_sub2.set_data(t, temps)

            # Recompute and apply limits on every axis.
            self._autoscale_axis(self.ax_main, x=temps, y=res)
            self._autoscale_axis(self.ax_sub1, x=temps, y=volts)
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

    def start_visa_scan(self):
        """Starts the VISA scan in a separate thread to keep the GUI responsive."""
        self.scan_button.config(state='disabled')
        self.log("Scanning for VISA instruments...")
        threading.Thread(target=self._visa_scan_worker, daemon=True).start()
        self.root.after(100, self._process_visa_queue)

    def _visa_scan_worker(self):
        """Worker function that performs the slow VISA scan."""
        if not pyvisa:
            self.log("ERROR: PyVISA is not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            resources = rm.list_resources()
            self.visa_queue.put(resources)
        except Exception as e:
            self.visa_queue.put(e)

    def _process_visa_queue(self):
        """Checks the queue for results from the VISA scan worker."""
        try:
            result = self.visa_queue.get_nowait()
            if isinstance(result, Exception):
                self.log(f"ERROR during VISA scan: {result}")
            elif result:
                self.log(f"Found: {result}")
                self.cryocon_cb['values'] = result
                self.keithley_cb['values'] = result
                # Auto-select common addresses
                for res in result:
                    if "GPIB1::12" in res:
                        self.cryocon_cb.set(res)
                    if "GPIB0::13" in res:
                        self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")

            self.scan_button.config(state='normal')

        except queue.Empty:
            # If the queue is empty, it means the worker is still running.
            self.root.after(100, self._process_visa_queue)

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "Measurement is running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    MeasurementAppGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
