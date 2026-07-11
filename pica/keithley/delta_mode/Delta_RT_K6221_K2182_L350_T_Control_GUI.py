"""
Module: Delta_RT_K6221_K2182_L350_T_Control_GUI.py
Purpose: GUI module for Delta RT K6221 K2182 L350 T Control GUI v5.
"""

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, messagebox, scrolledtext, Canvas, filedialog
import os
import sys
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import threading
import queue
import matplotlib as mpl
import runpy
from multiprocessing import Process

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None


def run_script_process(script_path):

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


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Active_Delta_Backend:

    def __init__(self):
        self.keithley = None
        self.lakeshore = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(
                    f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, keithley_visa, lakeshore_visa):
        """ Connects to both instruments. """
        print("\n--- [Backend] Initializing Instruments ---")
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        # Connect to Keithley
        print(f"  Connecting to Keithley 6221 at {keithley_visa}...")
        self.keithley = self.rm.open_resource(keithley_visa)
        self.keithley.timeout = 25000
        print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
        # Connect to Lakeshore
        print(f"  Connecting to Lakeshore 350 at {lakeshore_visa}...")
        self.lakeshore = self.rm.open_resource(lakeshore_visa)
        self.lakeshore.write('*RST')
        time.sleep(0.5)
        self.lakeshore.write('*CLS')
        print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
        print("--- [Backend] Instrument Initialization Complete ---")

    def setup_keithley_delta(self, current, compliance):
        """ Configures the Keithley for a Delta Mode measurement. """
        if not self.keithley:
            return
        print("  Configuring Keithley for Delta Mode...")
        self.keithley.write("*rst; status:preset; *cls")
        self.keithley.write(f"SOUR:DELT:HIGH {current}")
        self.keithley.write(f"SOUR:DELT:PROT {compliance}")
        self.keithley.write("SOUR:DELT:ARM")
        time.sleep(1)
        self.keithley.write("INIT:IMM")
        print("  Keithley Armed for Delta Measurement.")

    # --- NEW HELPER METHODS to support advanced GUI logic ---
    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None:
            raise ValueError("Invalid heater range.")
        self.lakeshore.write(f'RANGE {output},{range_code}')

    def set_setpoint(self, output, temperature_k):
        self.lakeshore.write(f'SETP {output},{temperature_k}')

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        self.lakeshore.write(
            f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)

    def get_heater_output(self, output):
        return float(self.lakeshore.query(f'HTR? {output}').strip())

    def get_temperature(self):
        if not self.lakeshore:
            return 0.0
        return float(self.lakeshore.query('KRDG? A').strip())

    def get_delta_measurement(self):
        if not self.keithley:
            return 0.0
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        voltage = float(raw_data.strip().split(',')[0])
        return voltage

    def close_instruments(self):
        print("--- [Backend] Closing instrument connections. ---")
        try:
            if self.lakeshore:
                print("  SAFETY: Setting Lakeshore heater to OFF (Range 0).")
                self.set_heater_range(1, 'off')
            if self.keithley:
                print("  Clearing Keithley source.")
                self.keithley.write("SOUR:CLE")
                self.keithley.write("*RST")
        except Exception as e:
            print(
                f"  WARNING: A non-critical error occurred during shutdown: {e}")
        finally:
            if self.keithley:
                self.keithley.close()
                self.keithley = None
                print("  Keithley connection closed.")
            if self.lakeshore:
                self.lakeshore.close()
                self.lakeshore = None
                print("  Lakeshore connection closed.")


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Advanced_Delta_GUI:
    PROGRAM_VERSION = "2.1"
    LOGO_SIZE = 110
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = resource_path("../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg")

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
        self.root.title("K6221/2182 & L350: Delta Mode R-T (T-Control) - range 5")
        self.root.geometry("1550x950")
        self.root.minsize(1200, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        # Plot updates are decoupled from data acquisition: data callbacks
        # only set this flag; _refresh_plot redraws on a fixed cadence.
        # Must be set before create_widgets() — _update_y_scale touches it.
        self._plot_dirty = False
        self.data_file_handle = None
        self.backend = Active_Delta_Backend()
        self.file_location_path = ""
        self.data_storage = {
            'time': [],
            'temperature': [],
            'voltage': [],
            'resistance': []}
        # Explicit master: don't rely on tkinter's implicit default root.
        self.log_scale_var = tk.BooleanVar(master=self.root, value=True)
        self.current_heater_range = 'off'
        self.logo_image = None
        self.visa_queue = queue.Queue()

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
        style.map('TCheckbutton', background=[('active', self.CLR_GRAPH_BG)])
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
        mpl.rcParams.update({'font.family': 'Segoe UI',
                             'font.size': self.FONT_SIZE_BASE,
                             'axes.titlesize': self.FONT_SIZE_BASE + 4,
                             'axes.labelsize': self.FONT_SIZE_BASE + 2})

    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    def create_widgets(self):
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

        # Create the console frame first to initialize the logger, but
        # don't pack it yet.
        console_pane = self.create_console_frame(scrollable_frame)

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)

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
        font_title_italic = (
            'Segoe UI',
            self.FONT_SIZE_BASE + 2,
            'bold italic')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        Label(
            header_frame,
            text="K6221/2182 & L350: Delta Mode R-T (T-Control) - range 5",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_italic).pack(
            side='left',
            padx=20,
            pady=10)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)
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
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)
        # Defer logo loading to improve startup time
        self.root.after(50, lambda: self._load_logo(logo_canvas))

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE, 'bold')
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

        details_text = ("Program Name: Delta Mode R vs. T (T-Control) - range 5\n"
                        "Instruments: Keithley 6221/2182, Lakeshore 350\n"
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
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(
                    self.LOGO_FILE_PATH).resize(
                    (self.LOGO_SIZE,
                     self.LOGO_SIZE),
                    Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")

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
        padx_val = 10
        Label(
            frame,
            text="Sample Name:").grid(
            row=0,
            column=0,
            columnspan=2,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=1, column=0, columnspan=2, padx=padx_val, pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Start Temp (K):").grid(
            row=2,
            column=0,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Start Temp"].grid(
            row=3, column=0, padx=(
                padx_val, 5), pady=(
                0, 5), sticky='ew')

        Label(
            frame,
            text="End Temp (K):").grid(
            row=2,
            column=1,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["End Temp"].grid(
            row=3, column=1, padx=(
                5, padx_val), pady=(
                0, 5), sticky='ew')

        Label(frame, text="Ramp Rate (K/min):").grid(row=4,
                                                     column=0, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Rate"].grid(
            row=5, column=0, padx=(
                padx_val, 5), pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Safety Cutoff (K):").grid(
            row=4,
            column=1,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.entries["Cutoff"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Cutoff"].grid(
            row=5, column=1, padx=(
                5, padx_val), pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Apply Current (A):").grid(
            row=6,
            column=0,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.entries["Apply Current"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Apply Current"].grid(
            row=7, column=0, padx=(
                padx_val, 5), pady=(
                0, 5), sticky='ew')
        self.entries["Apply Current"].insert(0, "1E-6")

        Label(
            frame,
            text="Compliance (V):").grid(
            row=6,
            column=1,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Compliance"].grid(
            row=7, column=1, padx=(
                5, padx_val), pady=(
                0, 5), sticky='ew')
        self.entries["Compliance"].insert(0, "10")

        Label(
            frame,
            text="Lakeshore VISA:").grid(
            row=8,
            column=0,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.lakeshore_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(
            row=9, column=0, padx=(
                padx_val, 5), pady=(
                0, 10), sticky='ew')

        Label(
            frame,
            text="Keithley VISA:").grid(
            row=8,
            column=1,
            padx=padx_val,
            pady=pady_val,
            sticky='w')
        self.keithley_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(
            row=9, column=1, padx=(
                5, padx_val), pady=(
                0, 10), sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self.start_visa_scan)
        self.scan_button.grid(
            row=10,
            column=0,
            columnspan=2,
            padx=padx_val,
            pady=4,
            sticky='ew')

        ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location).grid(
            row=11,
            column=0,
            columnspan=2,
            padx=padx_val,
            pady=4,
            sticky='ew')

        self.start_button = ttk.Button(
            frame,
            text="Start Measurement",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(
            row=13, column=0, padx=padx_val, pady=(
                10, 10), sticky='ew')

        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=13, column=1, padx=padx_val, pady=(
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
        # Use a standard tk.Frame and set its background explicitly to match
        # the graph
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        ttk.Checkbutton(
            top_bar,
            text="Log Resistance Axis",
            variable=self.log_scale_var,
            command=self._update_y_scale).pack(
            side='right',
            padx=5)

        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_ylabel("Resistance (Ω)")
        self._update_y_scale()
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        self.line_sub1, = self.ax_sub1.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Voltage (V)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        self.line_sub2, = self.ax_sub2.plot(
            [], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _update_y_scale(self):
        self.ax_main.set_yscale(
            'log' if self.log_scale_var.get() else 'linear')
        self._plot_dirty = True
        self.canvas.draw_idle()

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{ts}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            self.params = {
                'sample_name': self.entries["Sample Name"].get(),
                'start_temp': float(self.entries["Start Temp"].get()),
                'end_temp': float(self.entries["End Temp"].get()),
                'rate': float(self.entries["Rate"].get()),
                'cutoff': float(self.entries["Cutoff"].get()),
                'current': float(self.entries["Apply Current"].get()),
                'compliance': float(self.entries["Compliance"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'keithley_visa': self.keithley_cb.get()
            }
            if not all(self.params.values()) or not self.file_location_path:
                raise ValueError(
                    "All fields, VISA addresses, and save location are required.")
            if not (self.params['start_temp'] <
                    self.params['end_temp'] < self.params['cutoff']):
                raise ValueError(
                    "Temperatures must be in order: start < end < cutoff.")

            self.backend.initialize_instruments(
                self.params['keithley_visa'],
                self.params['lakeshore_visa'])
            self.backend.setup_keithley_delta(
                self.params['current'], self.params['compliance'])

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{self.params['sample_name']}_{ts}_Delta_RT.dat"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)
            self.data_file_handle = open(self.data_filepath, 'w', newline='')
            writer = csv.writer(self.data_file_handle)
            writer.writerow(
                [f"# Sample: {self.params['sample_name']}", f"Applied Current: {self.params['current']}A"])
            writer.writerow(["Timestamp",
                             "Elapsed Time (s)",
                             "Temperature (K)",
                             "Heater Output (%)",
                             "Measured Voltage (V)",
                             "Resistance (Ohm)"])
            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_stabilizing, self.is_running = True, False
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            for key in self.data_storage:
                self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]:
                line.set_data([], [])
            self.ax_main.set_title(
                f"R-T Curve: {self.params['sample_name']}",
                fontweight='bold')
            # Single redraw to clear plots and show the new title.
            self._plot_dirty = False
            self.canvas.draw_idle()

            self.log("Starting stabilization process...")
            self.root.after(1000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"{e}")

    def stop_measurement(self):
        if self.is_running or self.is_stabilizing:
            self.is_running, self.is_stabilizing = False, False
            self.log("Measurement stopped by user.")
            self.backend.close_instruments()
            if self.data_file_handle:
                self.data_file_handle.close()
                self.data_file_handle = None
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            # Final flush: is_running is already False, so this won't
            # reschedule the timer.
            self._refresh_plot()
            self.canvas.draw_idle()
            messagebox.showinfo(
                "Info", "Measurement stopped and instruments disconnected.")

    def _stabilization_loop(self):
        if not self.is_stabilizing:
            return
        try:
            current_temp = self.backend.get_temperature()
            if current_temp > self.params['start_temp'] + 0.2:
                self.log(
                    f"Stabilizing (Cooling)... Current: {current_temp:.4f} K > Target: {self.params['start_temp']} K")
                self.backend.set_heater_range(1, 'off')
            else:
                self.log(
                    f"Stabilizing (Heating)... Current: {current_temp:.4f} K <= Target: {self.params['start_temp']} K")
                self.backend.set_heater_range(1, 'high')
                self.backend.set_setpoint(1, self.params['start_temp'])

            if abs(current_temp - self.params['start_temp']) < 5.0:
                self.log(
                    f"Stabilized at {current_temp:.4f} K. Waiting 5s before starting ramp...")
                self.is_stabilizing = False
                self.root.after(
                    5000, self._start_hardware_ramp)  # Move to next stage
            else:
                self.root.after(2000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during stabilization: {e}")
            self.stop_measurement()

    def _start_hardware_ramp(self):
        self.backend.set_setpoint(1, self.params['end_temp'])
        self.backend.setup_ramp(1, self.params['rate'])
        self.current_heater_range = 'high'
        self.backend.set_heater_range(1, self.current_heater_range)
        self.log(
            f"Hardware ramp started towards {self.params['end_temp']} K at {self.params['rate']} K/min.")
        self.is_running = True
        self.start_time = time.time()
        self.root.after(1000, self._update_measurement_loop)
        # Start the plot refresh timer here (not in start_measurement):
        # _refresh_plot only reschedules while is_running is True.
        self.root.after(250, self._refresh_plot)

    def _update_measurement_loop(self):
        if not self.is_running:
            return
        try:
            temp = self.backend.get_temperature()
            htr = self.backend.get_heater_output(1)
            voltage = self.backend.get_delta_measurement()
            res = voltage / \
                self.params['current'] if self.params['current'] != 0 else float('inf')
            elapsed = time.time() - self.start_time

            self.log(
                f"T:{temp:.3f}K | R:{res:.3e}Ω | Htr:{htr:.1f}% ({self.current_heater_range})")
            if self.data_file_handle:
                csv.writer(self.data_file_handle).writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    f"{elapsed:.2f}", f"{temp:.4f}", f"{htr:.2f}", f"{voltage:.4e}", f"{res:.4e}"])

            self.data_storage['time'].append(elapsed)
            self.data_storage['temperature'].append(temp)
            self.data_storage['voltage'].append(voltage)
            self.data_storage['resistance'].append(res)

            self._plot_dirty = True

            if temp >= self.params['cutoff']:
                self.log(f"!!! SAFETY CUTOFF REACHED at {temp:.4f} K !!!")
                self.stop_measurement()
            elif temp >= self.params['end_temp']:
                self.log("Target temperature reached. Measurement complete.")
                self.stop_measurement()
            else:
                # Slightly less than 1s to prevent drift
                self.root.after(900, self._update_measurement_loop)
        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement()

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
            self._autoscale_axis(self.ax_main, x=temps, y=res,
                                 log_y=self.log_scale_var.get())
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
                self.lakeshore_cb['values'] = result
                self.keithley_cb['values'] = result
                # Auto-select common addresses
                for res in result:
                    if "GPIB1::15" in res:
                        self.lakeshore_cb.set(res)
                    if "GPIB0::13" in res:
                        self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")

            self.scan_button.config(state='normal')

        except queue.Empty:

            self.root.after(100, self._process_visa_queue)

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno("Exit",
                                   "Measurement running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    Advanced_Delta_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()