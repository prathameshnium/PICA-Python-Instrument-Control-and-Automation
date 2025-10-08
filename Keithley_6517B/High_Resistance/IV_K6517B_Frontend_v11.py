# -------------------------------------------------------------------------------
# Name:             High Resistance IV GUI for Keithley 6517B
# Purpose:          Perform a voltage sweep and measure resistance using a
#                   Keithley 6517B Electrometer with a real instrument backend.
# Author:           Prathamesh Deshmukh
# Created:          17/09/2025
# Version:          V: 4.4 (Performance & UI Update)
# -------------------------------------------------------------------------------


# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import threading, queue
import numpy as np
import csv
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
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
        # Assumes the plotter is in a standard location relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(script_dir, "..", "..", "..", "Utilities", "PlotterUtil_Frontend_v3.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")

def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        # Assumes the scanner is in a standard location relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(script_dir, "..", "..", "Utilities", "GPIB_Instrument_Scanner_Frontend_v4.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")
# -------------------------------------------------------------------------------
# --- REAL INSTRUMENT BACKEND ---
# This section has been updated to incorporate the logic from the V5 Core script.
# -------------------------------------------------------------------------------
class Keithley6517B_Backend:
    """
    A dedicated class to handle backend communication with a real Keithley 6517B.
    *** The setup and measurement logic from the V5 Core script is incorporated here. ***
    """
    def __init__(self):
        self.keithley = None
        self.is_connected = False
        if not PYMEASURE_AVAILABLE:
            raise ImportError("PyMeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")

    def initialize_instruments(self, parameters):
        """
        Connects to the instrument and performs the zero-check sequence
        based on the V5 Core script's methodology.
        """
        print(f"\n--- [Backend] Initializing Instrument at {parameters['keithley_visa']} ---")
        try:
            self.keithley = Keithley6517B(parameters['keithley_visa'], timeout=20000)
            print(f"  Successfully connected to: {self.keithley.id}")

            # --- Configure Measurement and Perform Zero Correction (V5 Core Logic) ---
            print("  Configuring instrument and performing zero correction...")
            self.keithley.reset()
            # Set the function to resistance to ensure the ammeter is configured for zero correction.
            self.keithley.measure_resistance()

            # --- Perform Zero Correction Sequence ---
            print("  Starting zero correction procedure...")
            time.sleep(1) # Reduced wait time for GUI responsiveness

            # 1. Enable Zero Check
            print("    Step 1/4: Enabling Zero Check mode...")
            self.keithley.write(':SYSTem:ZCHeck ON')
            time.sleep(2)

            # 2. Acquire the zero measurement
            print("    Step 2/4: Acquiring zero correction value...")
            self.keithley.write(':SYSTem:ZCORrect:ACQuire')
            time.sleep(2) # Allow time for acquisition

            # 3. Disable Zero Check
            print("    Step 3/4: Disabling Zero Check mode...")
            self.keithley.write(':SYSTem:ZCHeck OFF')
            time.sleep(1)

            # 4. Enable Zero Correct
            print("    Step 4/4: Enabling Zero Correction for all measurements.")
            self.keithley.write(':SYSTem:ZCORrect ON')
            time.sleep(1)
            print("  Zero Correction Complete.")

            # Set integration rate for noise reduction (as per V5 core script)
            self.keithley.current_nplc = 1

            self.is_connected = True
            print("--- [Backend] Instrument Initialized and Ready ---")

        except VisaIOError as e:
            print(f"  [VISA Connection Error] Could not connect. Details: {e}")
            raise ConnectionError(f"Could not connect to Keithley 6517B.\nCheck address and connections.") from e
        except Exception as e:
            print(f"  [Unexpected Error] during initialization. Details: {e}")
            raise e

    def set_voltage(self, voltage):
        """Sets the voltage source level and enables the output."""
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")
        self.keithley.source_voltage = voltage
        self.keithley.enable_source()

    def get_measurement(self):
        """
        Reads current and calculates resistance, mirroring the V5 Core script's method.
        """
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")

        # Read voltage and current from the instrument
        voltage = self.keithley.source_voltage # Read back the actual source voltage
        resistance = self.keithley.resistance

        # Calculate resistance as done in the command-line script
        current = voltage / resistance if resistance != 0 else float('inf')

        return resistance, current, voltage

    def close_instruments(self):
        """Safely shuts down the voltage source and disconnects."""
        print("--- [Backend] Closing instrument connection. ---")
        if self.keithley:
            try:
                print("  Shutting down voltage source...")
                self.keithley.shutdown()
                print("  Voltage source OFF. Instrument is safe.")
            except Exception as e:
                print(f"  Warning: Could not gracefully shut down instrument. Error: {e}")
            finally:
                self.is_connected = False
                self.keithley = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class HighResistanceIV_GUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "4.4" # Performance and UI update
    LOGO_SIZE = 110
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        # Path is two directories up from the script location
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'; CLR_ACCENT_GOLD = '#FFC107'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Keithley 6517B: High Resistance I-V Measurement")
        self.root.geometry("1550x900")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 800)

        self.is_running = False
        self.start_time = None
        self.logo_image = None # Attribute to hold the logo image reference
        try:
            self.backend = Keithley6517B_Backend()
        except Exception as e:
            messagebox.showerror("Backend Error", f"Could not initialize the backend.\nError: {e}\n\nPlease ensure PyMeasure and NI-VISA are installed correctly.")
            self.backend = None
        self.file_location_path = ""
        self.data_storage = {'time': [], 'voltage_applied': [], 'current_measured': [], 'resistance': []}
        self.voltage_list = []
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self.plot_backgrounds = None # For blitting
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles and Matplotlib for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE) 
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8), foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500)
        main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg='white')
        main_pane.add(right_panel, weight=3)
        top_controls_frame = ttk.Frame(left_panel)
        left_panel.add(top_controls_frame, weight=0)
        self.create_info_frame(top_controls_frame)
        self.create_input_frame(top_controls_frame)
        console_pane = self.create_console_frame(left_panel)
        left_panel.add(console_pane, weight=1)
        self.create_graph_frame(right_panel)

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')

        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(header_frame, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        Label(header_frame, text="Keithley 6517B: High Resistance I-V Sweep", bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(header_frame, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                # IMPORTANT: Keep a reference to the image to prevent it from being garbage collected
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = ("Program Name: I-V Sweep\n"
                        "Instrument: Keithley 6517B Electrometer\n"
                        "Measurement Range: 10³ Ω to 10¹⁶ Ω")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')


    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        for i in range(4): frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=4, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=4, padx=10, pady=(0, 10), sticky='ew')

        Label(frame, text="Start V:").grid(row=2, column=0, padx=(10,0), pady=pady_val, sticky='w')
        self.entries["Start V"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Start V"].grid(row=2, column=1, padx=(0,10), pady=pady_val, sticky='w')

        Label(frame, text="Stop V:").grid(row=2, column=2, padx=(10,0), pady=pady_val, sticky='w')
        self.entries["Stop V"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Stop V"].grid(row=2, column=3, padx=(0,10), pady=pady_val, sticky='w')

        Label(frame, text="Steps:").grid(row=3, column=0, padx=(10,0), pady=pady_val, sticky='w')
        self.entries["Steps"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Steps"].grid(row=3, column=1, padx=(0,10), pady=pady_val, sticky='w')

        Label(frame, text="Delay (s):").grid(row=3, column=2, padx=(10,0), pady=pady_val, sticky='w')
        self.entries["Delay (s)"] = Entry(frame, font=self.FONT_BASE, width=8)
        self.entries["Delay (s)"].grid(row=3, column=3, padx=(0,10), pady=pady_val, sticky='w')
        self.entries["Delay (s)"].insert(0, "1.0")

        Label(frame, text="Keithley 6517B VISA:").grid(row=4, column=0, columnspan=4, padx=10, pady=(10,5), sticky='w')
        self.keithley_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_combobox.grid(row=5, column=0, columnspan=4, padx=10, pady=(0,5), sticky='ew')

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=6, column=0, columnspan=4, padx=10, pady=5, sticky='ew')

        self.file_location_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_location_button.grid(row=7, column=0, columnspan=4, padx=10, pady=5, sticky='ew')

        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=8, column=0, columnspan=2, padx=10, pady=15, sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=8, column=2, columnspan=2, padx=10, pady=15, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        if not PYMEASURE_AVAILABLE: self.log("CRITICAL: PyMeasure or PyVISA not found. Please run 'pip install pymeasure'.")
        return frame

    def create_graph_frame(self, parent):
        """Creates the frame for graphs, now with two subplots."""
        graph_container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)

        # Create two subplots stacked vertically
        self.ax_iv = self.figure.add_subplot(2, 1, 1) # Top plot
        self.ax_rv = self.figure.add_subplot(2, 1, 2) # Bottom plot

        # --- Configure Top Plot: I-V Curve ---
        self.line_iv, = self.ax_iv.plot([], [], color=self.CLR_ACCENT_BLUE, marker='o', markersize=5, linestyle='-', animated=False)
        self.ax_iv.set_title("Current vs. Voltage", fontweight='bold')
        self.ax_iv.set_ylabel("Measured Current (A)")
        self.ax_iv.grid(True, linestyle='--', alpha=0.6)

        # --- Configure Bottom Plot: R-V Curve ---
        self.line_rv, = self.ax_rv.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=5, linestyle='-', animated=False)
        self.ax_rv.set_title("Resistance vs. Voltage", fontweight='bold')
        self.ax_rv.set_xlabel("Applied Voltage (V)")
        self.ax_rv.set_ylabel("Resistance (Ω)")

        # --- COSMETIC CHANGE HERE ---
        # The y-axis is now logarithmic for better visualization of high resistance changes.
        self.ax_rv.set_yscale('log')

        self.ax_rv.grid(True, which="both", linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        if self.backend is None:
            messagebox.showerror("Backend Error", "Backend is not available. Cannot start measurement.")
            return
        try:
            params = {}
            params['sample_name'] = self.entries["Sample Name"].get()
            start_v = float(self.entries["Start V"].get())
            stop_v = float(self.entries["Stop V"].get())
            steps = int(self.entries["Steps"].get())
            self.delay_ms = int(float(self.entries["Delay (s)"].get()) * 1000)
            params['keithley_visa'] = self.keithley_combobox.get()

            if not all([params['sample_name'], params['keithley_visa']]) or not self.file_location_path:
                raise ValueError("All fields, VISA address, and a save location are required.")
            if steps < 2: raise ValueError("Number of steps must be 2 or more.")

            self.voltage_list = np.linspace(start_v, stop_v, steps)
            self.log(f"Generated voltage sweep from {start_v}V to {stop_v}V in {steps} steps.")

            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_IV.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample Name: {params['sample_name']}"])
                writer.writerow([f"# Voltage Sweep: {start_v}V to {stop_v}V, {steps} steps, {self.delay_ms/1000}s delay"])
                writer.writerow(["Time (s)", "Applied Voltage (V)", "Measured Current (A)", "Resistance (Ohms)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()

            # Clear both plot lines
            self.line_iv.set_data([], [])
            self.line_rv.set_data([], [])

            # Perform a full redraw to clear plots and set the new title
            for ax in [self.ax_iv, self.ax_rv]: ax.relim(); ax.autoscale_view()
            self.ax_iv.set_title(f"I-V Curve: {params['sample_name']}", fontweight='bold')
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw()
            self.log("Measurement sweep started.")
            
            # --- Performance Improvement: Capture static background for blitting ---
            for line in [self.line_iv, self.line_rv]: line.set_animated(True)
            self.canvas.draw()
            self.plot_backgrounds = [self.canvas.copy_from_bbox(ax.bbox) for ax in [self.ax_iv, self.ax_rv]]
            self.log("Blitting enabled for fast graph updates.")

            # Start the worker thread and the queue processor
            self.measurement_thread = threading.Thread(target=self._measurement_worker, args=(self.voltage_list, self.delay_ms), daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")

    def stop_measurement(self, from_user=True):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            # Turn off animation for any final redraws
            for line in [self.line_iv, self.line_rv]: line.set_animated(False)
            self.plot_backgrounds = None
            if self.backend:
                self.backend.close_instruments()
            self.log("Instrument connection closed.")
            if from_user:
                messagebox.showinfo("Info", "Measurement stopped and instrument disconnected.")

    def _measurement_worker(self, voltage_list, delay_ms):
        """Worker thread to perform measurements and put data into a queue."""
        for i, voltage in enumerate(voltage_list):
            if not self.is_running: break
            try:
                self.backend.set_voltage(voltage)
                self.data_queue.put(f"LOG:Step {i + 1}/{len(voltage_list)}: Set V = {voltage:.3f} V. Waiting {delay_ms}ms...")
                time.sleep(delay_ms / 1000.0)
                
                res, cur, volt = self.backend.get_measurement()
                elapsed_time = time.time() - self.start_time
                self.data_queue.put((res, cur, volt, elapsed_time))
            except Exception as e:
                self.data_queue.put(e)
                break
        self.data_queue.put("SWEEP_COMPLETE")

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI. Runs in the main thread."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()

                if isinstance(data, str) and data.startswith("LOG:"):
                    self.log(data[4:])
                elif isinstance(data, str) and data == "SWEEP_COMPLETE":
                    self.log("Sweep finished.")
                    self.stop_measurement(from_user=False)
                    messagebox.showinfo("Finished", "I-V sweep complete.")
                    return
                elif isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {traceback.format_exc()}")
                    self.stop_measurement(from_user=False)
                    messagebox.showerror("Runtime Error", "A critical error occurred. Check console.")
                    return
                else:
                    res, cur, volt, elapsed_time = data
                    self.log(f"  Read -> V: {volt:.3e} V, I: {cur:.3e} A, R: {res:.3e} Ω")
                    with open(self.data_filepath, 'a', newline='') as f:
                        csv.writer(f).writerow([f"{elapsed_time:.3f}", f"{volt:.4e}", f"{cur:.4e}", f"{res:.4e}"])

                    self.data_storage['time'].append(elapsed_time); self.data_storage['voltage_applied'].append(volt)
                    self.data_storage['current_measured'].append(cur); self.data_storage['resistance'].append(res)

                    # --- Performance Improvement: Use blitting for fast graph updates ---
                    if self.plot_backgrounds:
                        # Restore the clean background
                        self.canvas.restore_region(self.plot_backgrounds[0])
                        self.canvas.restore_region(self.plot_backgrounds[1])

                        # Update data and redraw only the artists
                        self.line_iv.set_data(self.data_storage['voltage_applied'], self.data_storage['current_measured'])
                        self.line_rv.set_data(self.data_storage['voltage_applied'], self.data_storage['resistance'])
                        for ax in [self.ax_iv, self.ax_rv]: ax.relim(); ax.autoscale_view()

                        self.ax_iv.draw_artist(self.line_iv)
                        self.ax_rv.draw_artist(self.line_rv)
                        self.canvas.blit(self.figure.bbox)
                    else:
                        # Fallback to a full redraw if blitting isn't ready
                        self.canvas.draw_idle()

        except queue.Empty:
            pass # No data to process, which is normal

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _scan_for_visa_instruments(self):
        if not pyvisa:
            self.log("ERROR: PyVISA is not installed. Cannot scan.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources
                # Attempt to find a likely candidate for the Keithley
                for res in resources:
                    if "GPIB" in res.upper() and ("27" in res or "26" in res or "25" in res):
                        self.keithley_combobox.set(res); break
                else: self.keithley_combobox.set(resources[0])
            else:
                self.log("No VISA instruments found.")
                self.keithley_combobox['values'] = []; self.keithley_combobox.set("")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement sweep is running. Stop and exit?"):
                self.stop_measurement(from_user=False); self.root.destroy()
        else:
            if self.backend and self.backend.is_connected:
                self.backend.close_instruments()
            self.root.destroy()

def main():
    root = tk.Tk()
    app = HighResistanceIV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
