# -------------------------------------------------------------------------------
# Name:         Delta Lakeshore Passive Measurement GUI
# Purpose:      Perform a temperature-dependent Delta mode measurement with a
#               Keithley 6221/2182 while passively monitoring temperature
#               from a Lakeshore 350.
#
# Author:       Prathamesh Deshmukh
#
# Created:      03/10/2025
#
# Version:      8.0 (Passive Monitoring Merge)
#
# Description:  This version combines the core delta measurement logic from
#               Delta_Lakeshore_Front_end_V7.py with the modern GUI, passive
#               temperature sensing, and improved plotting/saving from
#               6517B_high_resistance_lakeshore_RT_Frontend_V12_Passive.py.
# -------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
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

    # Import the plotter launch function from the main PICA launcher
    from PICA_v6 import launch_plotter_utility

except (ImportError, ModuleNotFoundError):
    # Fallback if the script is run standalone
    launch_plotter_utility = lambda: print("Plotter launch function not found.")

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Combined_Backend:
    """
    A dedicated class to handle backend instrument communication.
    - Keithley 6221 logic is preserved from Delta_Lakeshore_Front_end_V7.py.
    - Lakeshore 350 logic is modified for passive temperature sensing only.
    """
    def __init__(self):
        self.params = {}
        self.keithley = None
        self.lakeshore = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
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
            self.keithley.write(f"SOUR:DELT:HIGH {self.params['apply_current']}")
            self.keithley.write(f"SOUR:DELT:PROT {self.params['compliance_v']}") # Set compliance voltage
            self.keithley.write("SOUR:DELT:ARM")
            time.sleep(1)
            self.keithley.write("INIT:IMM")
            print("  Keithley 6221/2182 Configured and Armed for Delta Mode.")

            # --- Initialize Lakeshore 350 (Passive Mode) ---
            print("  Connecting to Lakeshore 350 for passive monitoring...")
            self.lakeshore = self.rm.open_resource(self.params['lakeshore_visa'])
            print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
            print("  Lakeshore 350 connection is passive. No settings will be changed.")

            print("--- [Backend] Instrument Initialization Complete ---")
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure an instrument. {e}")
            raise e

    def get_measurement(self):
        """Performs a single measurement and returns all relevant data."""
        if not self.keithley or not self.lakeshore:
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

        # Get data from Lakeshore
        temp_str = self.lakeshore.query('KRDG? A').strip()
        temperature = float(temp_str)

        return resistance, voltage, temperature

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                self.keithley.write("SOUR:CLE"); self.keithley.write("*RST"); self.keithley.close()
                print("  Keithley 6221 connection closed.")
            except pyvisa.errors.VisaIOError: pass
            finally: self.keithley = None
        if self.lakeshore:
            try:
                self.lakeshore.close()
                print("  Lakeshore 350 connection closed (was in passive mode).")
            except pyvisa.errors.VisaIOError: pass
            finally: self.lakeshore = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class MeasurementAppGUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "8.0"
    LOGO_SIZE = 110
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Theming and Styling (from 6517B...V12) ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("K6221/2182 & L350: Delta Mode R-T (Passive Sensing)")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running, self.start_time = False, None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'voltage': [], 'resistance': [], 'temperature': []}
        self.logo_image = None # Attribute to hold the logo image reference
        self.data_queue = queue.Queue()
        self.plot_backgrounds = None # For blitting
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
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
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
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
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
        Label(header_frame, text="K6221/2182 & L350: Delta Mode R-T (Passive Sensing)", bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)
        
        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(header_frame, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)
        self.root.after(50, lambda: self._load_logo(logo_canvas))
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)

        details_text = ("Program Mode: R vs. T (Passive Sensing)\n"
                        "Instruments: Keithley 6221/2182, Lakeshore 350\n"
                        "Measurement Range: 10⁻⁹ Ω to 10⁸ Ω")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def _load_logo(self, canvas):
        """Loads the logo image after the main window is drawn."""
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img) # Keep a reference
                canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady_val = (5, 5)

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        Label(frame, text="Apply Current (A):").grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Apply Current"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Apply Current"].grid(row=3, column=0, padx=(10,5), pady=(0,5), sticky='ew')
        self.entries["Apply Current"].insert(0, "1E-6") # Default value

        Label(frame, text="Compliance Voltage (V):").grid(row=2, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["Compliance Voltage"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Compliance Voltage"].grid(row=3, column=1, padx=(5,10), pady=(0,5), sticky='ew')
        self.entries["Compliance Voltage"].insert(0, "10") # Default value

        Label(frame, text="Keithley 6221 VISA:").grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(row=5, column=0, padx=(10,5), pady=(0,10), sticky='ew')

        Label(frame, text="Lakeshore 350 VISA:").grid(row=4, column=1, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(row=5, column=1, padx=(5,10), pady=(0,10), sticky='ew')

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self.start_visa_scan)
        self.scan_button.grid(row=6, column=0, columnspan=2, padx=10, pady=4, sticky='ew')
        self.file_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=7, column=0, columnspan=2, padx=10, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Measurement", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=8, column=0, padx=(10,5), pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=8, column=1, padx=(5,10), pady=(10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        if not pyvisa: self.log("CRITICAL: PyVISA not found. Please run 'pip install pyvisa'.")
        if not PIL_AVAILABLE: self.log("WARNING: Pillow not found. Logo will not display. Run 'pip install Pillow'.")
        if not os.path.exists(self.LOGO_FILE_PATH): self.log(f"WARNING: '{self.LOGO_FILE_PATH}' not found. Logo cannot be displayed.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        # Main Plot: Resistance vs Temperature
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-', animated=True)
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Resistance (Ω)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        # Sub Plot 1: Voltage vs Temperature
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=4, linestyle='-', animated=True)
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Voltage (V)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        # Sub Plot 2: Temperature vs Time
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=4, linestyle='-', animated=True)
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
                'apply_current': float(self.entries["Apply Current"].get()),
                'compliance_v': float(self.entries["Compliance Voltage"].get()),
                'keithley_visa': self.keithley_cb.get(),
                'lakeshore_visa': self.lakeshore_cb.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError("All fields, VISA addresses, and a save location are required.")

            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_Delta_passive.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}", f"Applied Current: {params['apply_current']:.4e} A"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Voltage (V)", "Resistance (Ohm)"])

            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")
            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.ax_main.set_title(f"Sample: {params['sample_name']} | I = {params['apply_current']:.2e} A", fontweight='bold')
            
            # --- Performance Improvement: Full draw before starting loop ---
            self.canvas.draw()
            # Capture the background for blitting
            self.plot_backgrounds = [self.canvas.copy_from_bbox(ax.bbox) for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]]
            self.log("Blitting enabled for fast graph updates.")

            self.log("Measurement loop started.")
            
            # Start the worker thread and the queue processor
            self.measurement_thread = threading.Thread(target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")
            if self.backend: self.backend.close_instruments()

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            # --- Performance Improvement: Disable blitting on stop ---
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_animated(False)
            self.plot_backgrounds = None
            self.canvas.draw_idle()
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            self.log("Instrument connections closed.")
            messagebox.showinfo("Info", "Measurement stopped and instruments disconnected.")

    def _measurement_worker(self):
        """Worker thread to perform measurements and put data into a queue."""
        while self.is_running:
            try:
                res, volt, temp = self.backend.get_measurement()
                elapsed = time.time() - self.start_time
                # Put the acquired data into the queue for the main thread
                self.data_queue.put((res, volt, temp, elapsed))
                time.sleep(1) # Control the measurement frequency
            except Exception as e:
                # If an error occurs, put it in the queue to be handled by the main thread
                self.data_queue.put(e)
                break
        if not self.is_running:
            # Signal that the thread is done
            self.data_queue.put(None)

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI. Runs in the main thread."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {traceback.format_exc()}")
                    self.stop_measurement()
                    messagebox.showerror("Runtime Error", "A critical error occurred in the measurement thread. Check console.")
                    return
                if data is None: # Sentinel value indicating thread finished
                    raise data # Re-raise the exception in the main thread

                res, volt, temp, elapsed = data
                self.log(f"T: {temp:.3f} K | R: {res:.4e} Ω | V: {volt:.4e} V")
                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}", f"{temp:.4f}", f"{volt:.6e}", f"{res:.6e}"])

                self.data_storage['time'].append(elapsed); self.data_storage['temperature'].append(temp)
                self.data_storage['voltage'].append(volt); self.data_storage['resistance'].append(res)

                self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])

                # --- Performance Improvement: Use blitting for fast updates ---
                if self.plot_backgrounds:
                    # Restore the clean backgrounds
                    for bg in self.plot_backgrounds: self.canvas.restore_region(bg)
                    
                    # Update data for all plots
                    self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['voltage'])
                    self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature'])
                    
                    # Redraw only the artists
                    for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
                        ax.relim(); ax.autoscale_view()
                        self.canvas.blit(ax.bbox)
                else: # Fallback to full redraw if blitting isn't ready
                    self.figure.tight_layout(pad=3.0)
                    self.canvas.draw_idle()

        except queue.Empty:
            pass # This is normal, no data to process
        except Exception as e: # Catches other exceptions, like the sentinel
            self.log(f"GUI processing error or thread stopped: {e}")

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def start_visa_scan(self):
        """Starts the VISA scan in a separate thread to keep the GUI responsive."""
        self.scan_button.config(state='disabled')
        self.log("Scanning for VISA instruments...")
        threading.Thread(target=self._visa_scan_worker, daemon=True).start()
        self.root.after(100, self._process_visa_queue)

    def _visa_scan_worker(self):
        """Worker function that performs the slow VISA scan."""
        if not pyvisa: self.log("ERROR: PyVISA is not installed."); return
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
                    if "GPIB1::15" in res: self.lakeshore_cb.set(res)
                    if "GPIB0::13" in res: self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")
            
            self.scan_button.config(state='normal')

        except queue.Empty:
            # If the queue is empty, it means the worker is still running.
            # We schedule another check.
            self.root.after(100, self._process_visa_queue)

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement is running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = MeasurementAppGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
