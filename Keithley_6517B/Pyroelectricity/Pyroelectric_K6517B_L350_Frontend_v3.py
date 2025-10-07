# -------------------------------------------------------------------------------
# Name:         Pyroelectric Measurement GUI
# Purpose:      Perform a pyroelectric current measurement with enhanced, two-stage
#               temperature ramp control.
# Author:       Prathamesh Deshmukh
# Created:      17/09/2025
# Version:      V2.5 (Matplotlib Style Fix)
# -------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import threading
import queue
import matplotlib.pyplot as plt
from matplotlib import gridspec

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
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False


class PyroelectricBackend:
    """
    Handles all backend instrument communication.
    Integrates advanced ramp control with pyroelectric current measurement.
    """
    def __init__(self):
        self.params = {}
        self.keithley = None
        self.lakeshore = None
        if PYVISA_AVAILABLE:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, parameters):
        """Connects, resets, and performs initial configuration of instruments."""
        print("\n--- [Backend] Initializing Instruments ---")
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            # --- Connect and Configure Lakeshore 350 ---
            print(f"  Connecting to Lakeshore 350 via {self.params['lakeshore_visa']}...")
            self.lakeshore = self.rm.open_resource(self.params['lakeshore_visa'])
            self.lakeshore.timeout = 15000
            print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")

            self.lakeshore.write('*RST'); time.sleep(0.5)
            self.lakeshore.write('*CLS'); time.sleep(0.5)

            # HTRSET <output>,<resistance>,<max current>,<max user current>,<display>
            # resistance=1 (25Ω), max_current=2 (1A)
            self.lakeshore.write('HTRSET 1,1,2,0,1')
            print("  Lakeshore heater configured (25Ω, 1A max).")

            # --- Connect and Configure Keithley 6517B ---
            print(f"  Connecting to Keithley 6517B via {self.params['keithley_visa']}...")
            self.keithley = Keithley6517B(self.params['keithley_visa'])
            time.sleep(1)
            print(f"    Connected to: {self.keithley.id}")
            self.keithley.measure_current()
            print("  Keithley 6517B configured to measure current.")
            print("--- [Backend] Instrument Initialization Complete ---")

        except Exception as e:
            print(f"  ERROR: Could not connect/configure an instrument. {e}")
            self.close_instruments()
            raise e

    def start_stabilization(self):
        """Begins moving to the start temperature for stabilization."""
        print(f"  Moving to start temperature: {self.params['start_temp']} K")
        self.lakeshore.write(f"SETP 1,{self.params['start_temp']}")
        self.lakeshore.write("RANGE 1,4") # Use 'medium' range for stabilization
        print("  Heater range set to 'Medium' for stabilization.")

    def start_ramp(self):
        """Configures and starts the temperature ramp."""
        print(f"  Ramp starting towards {self.params['end_temp']} K at {self.params['rate']} K/min.")
        # RAMP <output>,<on/off>,<rate>
        self.lakeshore.write(f"RAMP 1,1,{self.params['rate']}")
        self.lakeshore.write(f"SETP 1,{self.params['end_temp']}")
        # Ensure heater range is sufficient for ramp
        self.lakeshore.write("RANGE 1,4") # 'Medium' is often a good choice
        print("  Ramp configured and setpoint updated.")

    def get_measurement(self):
        """Reads temperature and current from the instruments."""
        if not self.keithley or not self.lakeshore:
            raise ConnectionError("One or more instruments are not connected.")
        try:
            temp_str = self.lakeshore.query('KRDG? A').strip()
            temperature = float(temp_str)
            current = self.keithley.current
            return temperature, current
        except (pyvisa.errors.VisaIOError, ValueError):
            return float('nan'), float('nan') # Return NaN on error

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                self.keithley.shutdown()
                print("  Keithley 6517B connection closed.")
            except Exception: pass
            finally: self.keithley = None
        if self.lakeshore:
            try:
                self.lakeshore.write("RANGE 1, 0") # Turn off heater
                self.lakeshore.close()
                print("  Lakeshore 350 connection closed.")
            except Exception: pass
            finally: self.lakeshore = None


class PyroelectricAppGUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "3.0" # Performance and UI update
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'
    CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11; FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Pyroelectric Measurement Interface")
        self.root.geometry("1600x950"); self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running, self.start_time = False, None
        self.experiment_state = 'idle'  # States: idle, stabilizing, ramping
        self.backend = PyroelectricBackend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': [], 'current': []}
        self.data_queue = queue.Queue()
        self.measurement_thread = None
        self.plot_backgrounds = None # For blitting

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles for a modern, beautiful look."""
        style = ttk.Style(self.root); style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton',
                        font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton',
                        font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton',
                        font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_BG_DARK, bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal'); main_pane.pack(fill='both', expand=True, padx=15, pady=15)

        # --- Create a scrollable left panel ---
        left_panel_container = ttk.Frame(main_pane, width=500)
        main_pane.add(left_panel_container, weight=1)

        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Create the right panel for graphs ---
        right_panel = tk.Frame(main_pane, bg=self.CLR_BG_DARK); 
        main_pane.add(right_panel, weight=3)

        # --- Populate the scrollable frame ---
        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)
        self.create_graph_frame(right_panel)

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Pyroelectric Measurement", bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(0, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 110
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=15)
        
        # Corrected logo path
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
        except NameError:
            logo_path = "../../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"
        self.logo_image = self._process_logo_image(logo_path, size=LOGO_SIZE)
        if self.logo_image: logo_canvas.create_image(LOGO_SIZE/2, LOGO_SIZE/2, image=self.logo_image) 
        else: logo_canvas.create_text(LOGO_SIZE/2, LOGO_SIZE/2, text="LOGO", font=self.FONT_TITLE, fill=self.CLR_FG_LIGHT)

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8) 

        details_text = ("Program Name: Pyroelectric Current vs. T\n"
                        "Instruments: Keithley 6517B, Lakeshore 350\n"
                        "Measurement Range: 1 fA to 20 mA")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=10, padx=10, fill='x', expand=False)
        for i in range(2): frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        Label(frame, text="Start Temp (K):").grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries['Start Temp'] = Entry(frame, font=self.FONT_BASE); self.entries['Start Temp'].grid(row=3, column=0, padx=(10,5), pady=(0,10), sticky='ew')

        Label(frame, text="End Temp (K):").grid(row=2, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries['End Temp'] = Entry(frame, font=self.FONT_BASE); self.entries['End Temp'].grid(row=3, column=1, padx=(5,10), pady=(0,10), sticky='ew')

        Label(frame, text="Ramp Rate (K/min):").grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries['Ramp Rate'] = Entry(frame, font=self.FONT_BASE); self.entries['Ramp Rate'].grid(row=5, column=0, padx=(10,5), pady=(0,15), sticky='ew')

        Label(frame, text="Safety Cutoff (K):").grid(row=4, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries['Safety Cutoff'] = Entry(frame, font=self.FONT_BASE); self.entries['Safety Cutoff'].grid(row=5, column=1, padx=(5,10), pady=(0,15), sticky='ew')

        Label(frame, text="Lakeshore 350 VISA:").grid(row=6, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.lakeshore_combobox.grid(row=7, column=0, columnspan=2, padx=10, pady=(0,5), sticky='ew')

        Label(frame, text="Keithley 6517B VISA:").grid(row=8, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.keithley_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.keithley_combobox.grid(row=9, column=0, columnspan=2, padx=10, pady=(0,15), sticky='ew')

        self.scan_button = ttk.Button(frame, text="Scan Instruments", command=self._scan_for_visa_instruments); self.scan_button.grid(row=10, column=0, columnspan=2, padx=10, pady=5, sticky='ew')
        self.file_location_button = ttk.Button(frame, text="Browse Save Location", command=self._browse_file_location); self.file_location_button.grid(row=11, column=0, columnspan=2, padx=10, pady=5, sticky='ew')
        
        button_frame = ttk.Frame(frame); button_frame.grid(row=12, column=0, columnspan=2, padx=10, pady=10, sticky='ew')
        button_frame.grid_columnconfigure(0, weight=1); button_frame.grid_columnconfigure(1, weight=1)
        self.start_button = ttk.Button(button_frame, text="Start Measurement", command=self.start_measurement, style='Start.TButton'); self.start_button.grid(row=0, column=0, padx=(0,5), pady=5, sticky='ew')
        self.stop_button = ttk.Button(button_frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled'); self.stop_button.grid(row=0, column=1, padx=(5,0), pady=5, sticky='ew')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output')
        frame.pack(pady=10, padx=10, fill='both', expand=True)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0, relief='flat', height=10)
        self.console_widget.pack(pady=10, padx=10, fill='both', expand=True)
        self.log("Console initialized.")
        if not PIL_AVAILABLE: self.log("Note: 'Pillow' not found. Logo cannot be displayed. Run 'pip install Pillow'.")
        if not PYVISA_AVAILABLE: self.log("CRITICAL ERROR: pyvisa or pymeasure not found.")
        else: self.log("Please select a save location and scan for instruments.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graphs')
        graph_container.pack(fill='both', expand=True, padx=(10, 0), pady=0)
        
        # --- FIX IMPLEMENTED HERE ---
        # This try-except block makes the plotting style compatible with different
        # versions of matplotlib.
        try:
            # Use the newer style name for modern matplotlib versions
            plt.style.use('seaborn-v0_8-whitegrid')
        except OSError:
            # Fallback to the older name for compatibility
            try:
                plt.style.use('seaborn-whitegrid')
            except OSError:
                # If both fail, log a warning but continue without a special style
                self.log("Warning: Seaborn plot style not found. Using default.")
                pass # Continue with the default matplotlib style

        self.figure = Figure(figsize=(10, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = self.figure.add_gridspec(2, 2, height_ratios=[2, 1.2])
        self.ax_main = self.figure.add_subplot(gs[0, :]); self.ax_sub1 = self.figure.add_subplot(gs[1, 0]); self.ax_sub2 = self.figure.add_subplot(gs[1, 1])
        self.axes = [self.ax_main, self.ax_sub1, self.ax_sub2]

        self.line_main, = self.ax_main.plot([], [], color='#e63946', marker='o', markersize=4, linestyle='-', linewidth=1.5)
        self.ax_main.set_title("Current vs. Temperature", fontweight='bold'); self.ax_main.set_xlabel("Temperature (K)"); self.ax_main.set_ylabel("Current (A)")

        self.line_sub1, = self.ax_sub1.plot([], [], color='#0077b6', marker='.', markersize=4, linestyle='-', linewidth=1)
        self.ax_sub1.set_title("Temp vs. Time"); self.ax_sub1.set_xlabel("Time (s)"); self.ax_sub1.set_ylabel("Temperature (K)")

        self.line_sub2, = self.ax_sub2.plot([], [], color='#06d6a0', marker='.', markersize=4, linestyle='-', linewidth=1)
        self.ax_sub2.set_title("Current vs. Time"); self.ax_sub2.set_xlabel("Time (s)"); self.ax_sub2.set_ylabel("Current (A)")

        for ax in self.axes: ax.grid(True, linestyle='--', alpha=0.7); ax.ticklabel_format(axis='y', style='sci', scilimits=(-2, 3), useMathText=True)
        self.figure.tight_layout(pad=3.0); self.canvas = FigureCanvasTkAgg(self.figure, graph_container); self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

    def _process_logo_image(self, input_path, size=100):
        if not (PIL_AVAILABLE and os.path.exists(input_path)): return None
        try:
            with Image.open(input_path) as img:
                img_resized = img.resize((size, size), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img_resized)
        except Exception as e:
            print(f"ERROR: Could not process logo image '{input_path}'. Reason: {e}")
            return None

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal'); self.console_widget.insert('end', f"[{timestamp}] {message}\n"); self.console_widget.see('end'); self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'start_temp': float(self.entries["Start Temp"].get()),
                'end_temp': float(self.entries["End Temp"].get()),
                'rate': float(self.entries["Ramp Rate"].get()),
                'safety_cutoff': float(self.entries["Safety Cutoff"].get()),
                'lakeshore_visa': self.lakeshore_combobox.get(),
                'keithley_visa': self.keithley_combobox.get()
            }
            if not all([params['sample_name'], params['lakeshore_visa'], params['keithley_visa'], self.file_location_path]): raise ValueError("All fields, VISA addresses, and a save location are required.")
            if not (params['start_temp'] < params['end_temp'] < params['safety_cutoff']): raise ValueError("Temperatures must be in ascending order (Start < End < Cutoff).")
            if params['rate'] <= 0: raise ValueError("Ramp rate must be a positive number.")

            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S"); file_name = f"{params['sample_name']}_{timestamp}_Pyro.csv"; self.data_filepath = os.path.join(self.file_location_path, file_name)
            with open(self.data_filepath, 'w', newline='') as f:
                header = f"# Sample: {params['sample_name']}\n# Start: {params['start_temp']} K, End: {params['end_temp']} K, Ramp: {params['rate']} K/min\n"
                f.write(header); f.write("Time (s),Temperature (K),Current (A)\n")
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True; self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.ax_main.set_title(f"I vs T | Sample: {params['sample_name']}", fontweight='bold')
            
            # --- Performance Improvement: Capture static background for blitting ---
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_animated(True)
            self.canvas.draw()
            self.plot_backgrounds = [self.canvas.copy_from_bbox(ax.bbox) for ax in self.axes]
            self.log("Blitting enabled for fast graph updates.")
            # --- End of performance improvement ---

            self.log("Moving to start temperature for stabilization..."); self.experiment_state = 'stabilizing'; self.backend.start_stabilization()
            
            # Start the worker thread and the queue processor
            self.measurement_thread = threading.Thread(target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}"); messagebox.showerror("Initialization Error", f"Could not start measurement.\n\nDetails:\n{e}")

    def stop_measurement(self, reason="stopped by user"):
        if self.is_running:
            self.is_running = False; self.experiment_state = 'idle'; self.log(f"Measurement loop {reason}.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            # Turn off animation for any final redraws
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_animated(False)
            self.plot_backgrounds = None
            self.backend.close_instruments()
            self.log("Instrument connections closed."); messagebox.showinfo("Info", f"Measurement stopped.\nReason: {reason}")

    def _measurement_worker(self):
        """Worker thread to perform measurements and put data into a queue."""
        while self.is_running:
            try:
                current_temp, current_val = self.backend.get_measurement()
                self.data_queue.put((current_temp, current_val, self.experiment_state))
                time.sleep(2) # Control the measurement frequency
            except Exception as e:
                self.data_queue.put(e)
                break
        self.data_queue.put(None) # Sentinel value to signal completion

    def _process_data_queue(self):
        """Processes data from the queue to update the GUI. Runs in the main thread."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if data is None: return # Thread finished
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {traceback.format_exc()}")
                    self.stop_measurement("runtime error"); messagebox.showerror("Runtime Error", "An error occurred. Check console."); return

                current_temp, current_val, state = data
                params = self.backend.params

                if state == 'stabilizing':
                    self.log(f"Stabilizing... Current Temp: {current_temp:.4f} K (Target: {params['start_temp']} K)")
                    if abs(current_temp - params['start_temp']) < 0.1:
                        self.log(f"Stabilized at {params['start_temp']} K. Starting ramp.")
                        self.experiment_state = 'ramping'
                        self.backend.start_ramp()
                        self.start_time = time.time()

                elif state == 'ramping':
                    elapsed_time = time.time() - self.start_time
                    self.log(f"Ramping... Time: {elapsed_time:7.1f}s | Temp: {current_temp:8.4f}K | Current: {current_val:.3e} A")

                    with open(self.data_filepath, 'a', newline='') as f: f.write(f"{elapsed_time:.2f},{current_temp:.4f},{current_val}\n")

                    self.data_storage['time'].append(elapsed_time)
                    self.data_storage['temperature'].append(current_temp)
                    self.data_storage['current'].append(current_val)

                    # --- Performance Improvement: Use blitting for fast graph updates ---
                    if self.plot_backgrounds:
                        # Restore the clean backgrounds
                        for bg in self.plot_backgrounds: self.canvas.restore_region(bg)
                        # Update data and redraw only the artists
                        self.line_main.set_data(self.data_storage['temperature'], self.data_storage['current'])
                        self.line_sub1.set_data(self.data_storage['time'], self.data_storage['temperature'])
                        self.line_sub2.set_data(self.data_storage['time'], self.data_storage['current'])
                        for ax, line in zip(self.axes, [self.line_main, self.line_sub1, self.line_sub2]):
                            ax.relim(); ax.autoscale_view()
                            ax.draw_artist(line)
                        self.canvas.blit(self.figure.bbox)
                    else: # Fallback to a full redraw
                        self.figure.tight_layout(pad=3.0); self.canvas.draw_idle()
                    # --- End of performance improvement ---

                    if current_temp >= params['safety_cutoff']: self.stop_measurement(f"SAFETY CUTOFF REACHED at {current_temp:.4f} K!"); return
                    if current_temp >= params['end_temp']: self.stop_measurement(f"Target temperature of {params['end_temp']} K reached."); return

        except queue.Empty:
            pass # No data to process, which is normal

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE: self.log("ERROR: PyVISA not installed."); return
        if self.backend.rm is None: self.log("ERROR: VISA manager failed. Is NI-VISA installed?"); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}"); self.keithley_combobox['values'] = resources; self.lakeshore_combobox['values'] = resources
                for res in resources:
                    if "GPIB1::27" in res: self.keithley_combobox.set(res)
                    if "GPIB1::15" in res: self.lakeshore_combobox.set(res)
                if not self.keithley_combobox.get() and resources: self.keithley_combobox.set(resources[0])
                if not self.lakeshore_combobox.get() and resources: self.lakeshore_combobox.set(resources[-1])
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement is running. Stop and exit?"): self.stop_measurement(); self.root.destroy()
        else: self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    app = PyroelectricAppGUI(root)
    root.mainloop()
