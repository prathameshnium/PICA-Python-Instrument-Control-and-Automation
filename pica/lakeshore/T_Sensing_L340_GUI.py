"""
Module: T_Sensing_L340_GUI.py
Purpose: Passive temperature monitor for the Lake Shore Model 340.
         Port of T_Sensing_L350_GUI.py to the Model 340 command set.

What is different from the Model 350 version, and why
-----------------------------------------------------
  * RANGE takes no output number on a 340: "RANGE 0" turns the (Loop 1)
    heater off, "RANGE?" reads it back.  The 350 form "RANGE 1,0" is a
    syntax error on a 340.                       (340 manual, printed 9-40)
  * HTR? takes no argument on a 340 and always reports Loop 1 in percent.
                                                 (340 manual, printed 9-33)
  * No *RST at start.  On a 340 "*RST sets controller parameters to
    power-up settings" (printed 9-24): it can change the control loop,
    setpoint and ramp.  A passive monitor has no business doing that.  Only
    *CLS is sent.
  * Heater-off at start is a checkbox, ON by default.  When ticked the
    monitor sends "RANGE 0" once, reads "RANGE?" back and refuses to log if
    the readback is not 0.  When unticked the heater is left exactly as it
    was, like the 350 version.
  * The sensor input is selectable (A or B on a base Model 340; C and D are
    only present with the 3462 dual-input option card).
  * RDGST? is read with every sample.  A non-zero status (invalid, old,
    under/over range, units zero/overrange) is logged with the reading so
    a 0.000 K row in the file is never mistaken for a real reading.
  * Runtime errors do not open a modal dialog.  Unattended runs log, show
    a banner and beep; a dialog would hide the console and block nothing
    useful.

Commands used (all verified against the Model 340 User's Manual, Chapter 9):
  *IDN?          LSCI,MODEL340,<serial>,<firmware>
  *CLS           clear status registers
  RANGE 0        heater off (Loop 1)          RANGE?  -> n
  HTR?           Loop 1 heater output, %      (no argument)
  KRDG? <input>  kelvin reading, +nnn.nnnE+n
  RDGST? <input> reading status bit weighting
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import traceback
import threading
import queue
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

import runpy
from multiprocessing import Process


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
        plotter_path = os.path.join(
            script_dir,
            "..", "utils", "PlotterUtil_GUI.py")
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
        scanner_path = os.path.join(
            script_dir,
            "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
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


class Lakeshore340_Backend:
    """Passive monitor for the Lake Shore Model 340.

    The only command that changes anything is the optional "RANGE 0" sent
    once at start when the operator has asked for the heater to be turned
    off. Everything else is a query.
    """

    MODEL_TOKENS = ("MODEL340", "MODEL 340")

    def __init__(self, visa_address, resource_manager=None):
        self.instrument = None
        rm = resource_manager or pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        # The 340 answers with <CR><LF> and EOI by default (IEEE command,
        # printed 9-33); '\n' as read terminator is what the curve loader
        # uses on the lab's 340 and it works.
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
            self.instrument.write('RANGE 0')
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

    def get_heater_output(self):
        """HTR? -> Loop 1 heater output in percent.  No argument on a 340."""
        return float(self.instrument.query('HTR?').strip())

    def get_temperature(self, sensor='A'):
        """KRDG? <input> -> kelvin."""
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def get_reading_status(self, sensor='A'):
        """RDGST? <input> -> bit-weighted status (0 = good)."""
        return int(float(self.instrument.query(f'RDGST? {sensor}').strip()))

    def close(self):
        """Closes the connection to the instrument, changing nothing."""
        if self.instrument:
            try:
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class TempMonitorGUI:
    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Modern Dark Theme (PICA Standard) ---
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
    FONT_STATUS = ('Segoe UI', 28, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("Lakeshore 340 Passive Temperature Monitor")
        try:
            self.root.state('zoomed')  # Launch maximized
        except tk.TclError:
            # 'zoomed' is a Windows-only window state; X11 (including the
            # xvfb display CI runs under) rejects it.
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = None
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': []}
        self.logo_image = None
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self.data_filepath = None

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
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)

        style.configure(
            'Status.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_STATUS)

        style.configure('TEntry',
                        fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK,
                        borderwidth=0)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_GRAPH_BG,
            foreground=self.CLR_TEXT_DARK,
            arrowcolor=self.CLR_TEXT_DARK,
            selectbackground=self.CLR_ACCENT_GOLD,
            selectforeground=self.CLR_TEXT_DARK)

        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
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
            padding=(10, 9),
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])

        style.configure(
            'TLabelframe',
            background=self.CLR_BG_DARK,
            bordercolor=self.CLR_HEADER,
            borderwidth=1)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)
        right_panel = tk.Frame(self.main_pane, bg=self.CLR_GRAPH_BG)
        self.main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_status_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

        self.create_graph_frame(right_panel)

        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30
            else:
                target = self.LEFT_PANEL_WIDTH
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        plotter_button = ttk.Button(
            header_frame,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(
            header_frame,
            text="Passive Temperature Monitor (Lakeshore 340)",
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
            font=self.FONT_BASE).pack(
            side='right',
            padx=20,
            pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
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

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = ("Program Name: Temperature Monitor\n"
                        "Instrument: Lakeshore 340 Controller\n"
                        "Inputs: A, B (C, D with 3462 option card)\n"
                        "Only queries are sent, plus one optional RANGE 0.")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        Label(
            frame,
            text="Log File Name:").grid(
            row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        ttk.Label(
            frame,
            text="Logging Delay (s):").grid(
            row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Delay"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(
            row=3, column=0, padx=10, pady=(0, 5), sticky='ew')
        self.entries["Delay"].insert(0, "1.0")

        ttk.Label(
            frame,
            text="Sensor Input:").grid(
            row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.sensor_var = tk.StringVar(value='A')
        self.sensor_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly',
            textvariable=self.sensor_var, values=['A', 'B', 'C', 'D'])
        self.sensor_cb.grid(row=5, column=0, padx=10, pady=(0, 5), sticky='ew')

        # Heater-off at start. ON by default: a passive monitor is normally
        # started on a system that should be drifting freely, and the 340
        # keeps whatever heater range was last set through the keypad.
        self.heater_off_var = tk.BooleanVar(value=True)
        self.heater_off_cb = ttk.Checkbutton(
            frame,
            text="Turn heater OFF at start (RANGE 0, verified with RANGE?)",
            variable=self.heater_off_var)
        self.heater_off_cb.grid(
            row=6, column=0, padx=10, pady=(4, 6), sticky='w')

        ttk.Label(
            frame,
            text="Lakeshore VISA:").grid(
            row=7, column=0, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_cb = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(
            row=8, column=0, padx=10, pady=(0, 10), sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=9, column=0, padx=10, pady=4, sticky='ew')
        self.file_button = ttk.Button(
            frame,
            text="Browse Save Location...",
            command=self._browse_file_location)
        self.file_button.grid(row=10, column=0, padx=10, pady=4, sticky='ew')

        control_frame = ttk.Frame(frame)
        control_frame.grid(row=11, column=0, padx=10, pady=(10, 10), sticky='ew')
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(
            control_frame,
            text="Start Logging",
            command=self.start_measurement,
            style='Start.TButton')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.stop_button = ttk.Button(
            control_frame,
            text="Stop",
            command=self.stop_measurement,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=(5, 0))

    def create_status_frame(self, parent):
        """Creates the frame for displaying live temperature."""
        frame = ttk.LabelFrame(parent, text='Live Status')
        frame.pack(pady=5, padx=10, fill='x')

        status_inner_frame = ttk.Frame(frame, style='TFrame')
        status_inner_frame.pack(fill='x', expand=True, padx=5, pady=5)

        self.temp_label_var = tk.StringVar(value="--.---- K")
        status_label = ttk.Label(
            status_inner_frame,
            textvariable=self.temp_label_var,
            style='Status.TLabel',
            anchor='center',
            padding=(0, 10))
        status_label.pack(pady=10, fill='x')

        # Small line under the big number: heater range / heater % / reading
        # status.  This is where an unattended operator sees that the
        # heater really is off, or that the sensor is out of range.
        self.sub_status_var = tk.StringVar(value="Heater: --   Output: --")
        ttk.Label(
            status_inner_frame,
            textvariable=self.sub_status_var,
            anchor='center').pack(fill='x', pady=(0, 6))

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(
            parent,
            text='Console Output',
            style='TLabelframe')
        frame.pack(pady=5, padx=10, fill='x', expand=True)
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0,
            relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log(
            "Console initialized. Configure parameters and scan for instruments.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graph')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100,
                             facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Temperature vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed Time (s)")
        self.ax_main.set_ylabel("Temperature (K)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _beep(self):
        try:
            self.root.bell()
        except Exception:
            pass

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get().strip(),
                'delay': float(self.entries["Delay"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'sensor': self.sensor_var.get(),
                'heater_off': bool(self.heater_off_var.get()),
            }
            if (not params['sample_name'] or not params['lakeshore_visa']
                    or not self.file_location_path):
                raise ValueError(
                    "Log file name, VISA address and save location are required.")
            if params['delay'] <= 0:
                raise ValueError("Logging delay must be positive.")

            self.backend = Lakeshore340_Backend(params['lakeshore_visa'])
            self.log(f"Connected: {self.backend.idn}")
            if not self.backend.is_model_340():
                raise RuntimeError(
                    f"'{params['lakeshore_visa']}' answered '{self.backend.idn}', "
                    "which is not a Lake Shore Model 340. Refusing to send "
                    "RANGE 0 to an unknown instrument. Pick the right address.")

            rng = self.backend.configure_for_monitoring(
                heater_off=params['heater_off'])
            if params['heater_off']:
                self.log("Heater turned OFF (RANGE 0 sent, RANGE? = 0).")
            else:
                self.log(f"Heater left as found: RANGE? = {rng} "
                         f"({'off' if rng == 0 else 'ON'}).")
            self.log(f"Backend initialized for: {params['sample_name']} "
                     f"(input {params['sensor']})")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_Temp_passive.dat"
            self.data_filepath = os.path.join(
                self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Log File: {params['sample_name']}"])
                writer.writerow([f"# Instrument: {self.backend.idn}"])
                writer.writerow([f"# Sensor input: {params['sensor']}; "
                                 f"heater at start: "
                                 f"{'turned off' if params['heater_off'] else 'unchanged'}"])
                writer.writerow(
                    ["Timestamp", "Elapsed Time (s)", "Temperature (K)",
                     "Heater Output (%)", "Reading Status"])
            self.log(
                f"Output file created: {os.path.basename(self.data_filepath)}")

            self.params = params
            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.heater_off_cb.config(state='disabled')
            self.sensor_cb.config(state='disabled')
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(
                f"Temperature Log: {params['sample_name']}",
                fontweight='bold')
            self.canvas.draw()

            self.log("Starting passive data logging...")
            self.start_time = time.time()

            self.measurement_thread = threading.Thread(
                target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            # Start is an attended action, so a dialog is acceptable here.
            messagebox.showerror(
                "Initialization Error",
                f"Could not start logging.\n{e}")
            if self.backend:
                self.backend.close()
                self.backend = None

    def stop_measurement(self, reason="Measurement stopped by user."):
        if self.is_running:
            self.is_running = False
            self.log(reason)
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.heater_off_cb.config(state='normal')
            self.sensor_cb.config(state='readonly')
            if self.backend:
                self.backend.close()
                self.backend = None
            # No dialog here: an unattended run must not end behind a modal.
            self.log("Logging stopped and instrument disconnected. "
                     "Heater state was not touched at stop.")

    def _measurement_worker(self):
        """Worker thread for handling blocking instrument calls."""
        delay_s = self.params['delay']
        sensor = self.params['sensor']
        while self.is_running:
            try:
                temp = self.backend.get_temperature(sensor)
                status = self.backend.get_reading_status(sensor)
                htr = self.backend.get_heater_output()
                elapsed = time.time() - self.start_time
                self.data_queue.put((elapsed, temp, htr, status))
                time.sleep(delay_s)
            except Exception as e:
                self.data_queue.put(e)
                break

    def _process_data_queue(self):
        """Processes data from the worker thread to update the GUI."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {data!r}")
                    self.temp_label_var.set("ERROR")
                    self.sub_status_var.set("Worker stopped. See console.")
                    self._beep()
                    self.stop_measurement(
                        reason="Logging stopped after a communication error.")
                    return

                elapsed, temp, htr, status = data
                status_text = describe_reading_status(status)
                self.temp_label_var.set(f"{temp:.4f} K")
                self.sub_status_var.set(
                    f"Heater output: {htr:.1f} %   "
                    f"Reading: {'OK' if not status_text else status_text}")
                if status_text:
                    self.log(f"T:{temp:.3f} K   [RDGST {status}: {status_text}]")
                else:
                    self.log(f"T:{temp:.3f} K")

                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().strftime(
                        '%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}", f"{temp:.4f}",
                        f"{htr:.1f}", status])
                    f.flush()
                    os.fsync(f.fileno())

                self.data_storage['time'].append(elapsed)
                self.data_storage['temperature'].append(temp)

                self.line_main.set_data(
                    self.data_storage['time'],
                    self.data_storage['temperature'])
                self.ax_main.relim()
                self.ax_main.autoscale_view()
                self.figure.tight_layout(pad=3.0)
                self.canvas.draw_idle()

        except queue.Empty:
            pass

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not installed.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_cb['values'] = resources
                # No address is guessed and nothing is probed: the 340's
                # GPIB address is whatever the lab set on its front panel.
                if len(resources) == 1:
                    self.lakeshore_cb.set(resources[0])
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit",
                                   "Measurement running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            if self.backend:
                self.backend.close()
            self.root.destroy()


def main():
    root = tk.Tk()
    TempMonitorGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
