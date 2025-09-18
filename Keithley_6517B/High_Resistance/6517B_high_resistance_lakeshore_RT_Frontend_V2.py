# -------------------------------------------------------------------------------
# Name:          Integrated R-T Measurement GUI (Corrected Logic)
# Purpose:       Provide a graphical user interface for the combined Lakeshore 350
#                and Keithley 6517B Resistance vs. Temperature experiment.
# Author:        Prathamesh Deshmukh (Logic corrected by Gemini)
# Created:       18/09/2025
# Version:       V: 2.0 (Corrected Heating/Cooling Stabilization Logic)
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
import matplotlib.gridspec as gridspec
import matplotlib as mpl

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk, ImageDraw
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

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# This section contains the instrument control logic.
# IT HAS NOT BEEN MODIFIED to ensure experimental integrity.
# -------------------------------------------------------------------------------

class Lakeshore350_Backend:
    """A class to control the Lakeshore Model 350 Temperature Controller."""
    def __init__(self, visa_address):
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        print(f"Lakeshore Connected: {self.instrument.query('*IDN?').strip()}")

    def reset_and_clear(self):
        self.instrument.write('*RST'); time.sleep(0.5)
        self.instrument.write('*CLS'); time.sleep(1)

    def setup_heater(self, output, resistance_code, max_current_code):
        self.instrument.write(f'HTRSET {output},{resistance_code},{max_current_code},0,1')
        time.sleep(0.5)

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        self.instrument.write(f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)

    def set_setpoint(self, output, temperature_k):
        self.instrument.write(f'SETP {output},{temperature_k}')

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None: raise ValueError("Invalid heater range.")
        self.instrument.write(f'RANGE {output},{range_code}')

    def get_temperature(self, sensor):
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def get_heater_output(self, output):
        return float(self.instrument.query(f'HTR? {output}').strip())

    def close(self):
        if self.instrument:
            try:
                self.set_heater_range(1, 'off') # Uses default output 1
                time.sleep(0.5)
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None

class Combined_Backend:
    """Manages both the Lakeshore 350 and Keithley 6517B."""
    def __init__(self):
        self.lakeshore = None
        self.keithley = None
        self.params = {}

    def initialize_instruments(self, parameters):
        """Connects to and configures both instruments."""
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore350_Backend(self.params['lakeshore_visa'])
        self.lakeshore.reset_and_clear()
        self.lakeshore.setup_heater(1, 1, 2) # Using default HTRSET values

        self.keithley = Keithley6517B(self.params['keithley_visa'])
        print(f"Keithley Connected: {self.keithley.id}")
        self._perform_keithley_zero_check()

        self.keithley.source_voltage = self.params['source_voltage']
        self.keithley.current_nplc = 1
        self.keithley.enable_source()
        print(f"Keithley source enabled: {self.params['source_voltage']} V")

    def _perform_keithley_zero_check(self):
        """Performs the zero check and correction procedure."""
        print("  --- Starting Keithley Zero Correction ---")
        self.keithley.reset()
        self.keithley.measure_resistance()
        print("  Step 1: Enabling Zero Check...")
        self.keithley.write(':SYSTem:ZCHeck ON'); time.sleep(2)
        print("  Step 2: Acquiring zero correction...")
        # Note: pymeasure's reset might handle some of this, but manual is explicit.
        time.sleep(2)
        print("  Step 3: Disabling Zero Check...")
        self.keithley.write(':SYSTem:ZCHeck OFF'); time.sleep(1)
        print("  Step 4: Enabling Zero Correction...")
        self.keithley.write(':SYSTem:ZCORrect ON'); time.sleep(1)
        print("  Zero Correction Complete.")

    def get_measurement(self):
        """Gets a single synchronised reading from both instruments."""
        time.sleep(self.params['delay'])
        current_temp = self.lakeshore.get_temperature('A')
        heater_output = self.lakeshore.get_heater_output(1)
        measured_current = self.keithley.current
        resistance = abs(self.params['source_voltage'] / measured_current) if measured_current != 0 else float('inf')
        return current_temp, heater_output, measured_current, resistance

    def close_instruments(self):
        """Safely shuts down both instruments."""
        print("\n--- [Backend] Closing all instrument connections. ---")
        if self.keithley:
            self.keithley.shutdown()
            print("  Keithley connection closed and source OFF.")
        if self.lakeshore:
            self.lakeshore.close()
            print("  Lakeshore connection closed and heater OFF.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Integrated_RT_GUI:
    """The main GUI application class."""
    PROGRAM_VERSION = "2.0"
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FFONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Integrated R-T Measurement (Lakeshore 350 + Keithley 6517B)")
        self.root.geometry("1550x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': [], 'current': [], 'resistance': []}

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
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8))
        style.map('TButton', foreground=[('!active', self.CLR_BG_DARK), ('active', self.CLR_FG_LIGHT)],
                  background=[('!active', self.CLR_ACCENT_BLUE), ('active', self.CLR_BG_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
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
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Resistance vs. Temperature Measurement", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def _process_logo_image(self, input_path, size=120):
        if not (PIL_AVAILABLE and os.path.exists(input_path)): return None
        try:
            with Image.open(input_path) as img:
                img_cropped = img.crop((18, 18, 237, 237))
                mask = Image.new('L', img_cropped.size, 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0) + img_cropped.size, fill=255)
                img_cropped.putalpha(mask)
                img_hd = img_cropped.resize((size, size), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img_hd)
        except Exception: return None

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=120, height=120, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=10)
        self.logo_image = self._process_logo_image("UGC_DAE_CSR.jpeg")
        if self.logo_image: logo_canvas.create_image(60, 60, image=self.logo_image)

        info_text = ("Institute: UGC DAE CSR, Mumbai\n"
                     "Instruments:\n"
                     "  • Lakeshore 350 Controller\n"
                     "  • Keithley 6517B Electrometer")
        ttk.Label(frame, text=info_text, justify='left').grid(row=0, column=1, rowspan=2, padx=10, sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        # Row 0: Sample Name
        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        # Row 2: Temperature Params
        Label(frame, text="Start Temp (K):").grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Start Temp"].grid(row=3, column=0, padx=(10,5), pady=(0,5), sticky='ew')

        Label(frame, text="End Temp (K):").grid(row=2, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["End Temp"].grid(row=3, column=1, padx=(5,10), pady=(0,5), sticky='ew')

        # Row 4: Rate and Cutoff
        Label(frame, text="Ramp Rate (K/min):").grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Rate"].grid(row=5, column=0, padx=(10,5), pady=(0,10), sticky='ew')

        Label(frame, text="Safety Cutoff (K):").grid(row=4, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["Cutoff"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Cutoff"].grid(row=5, column=1, padx=(5,10), pady=(0,10), sticky='ew')

        # Row 6: Keithley Params
        Label(frame, text="Source Voltage (V):").grid(row=6, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Source Voltage"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Source Voltage"].grid(row=7, column=0, padx=(10,5), pady=(0,5), sticky='ew')

        Label(frame, text="Settling Delay (s):").grid(row=6, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(row=7, column=1, padx=(5,10), pady=(0,5), sticky='ew')
        self.entries["Delay"].insert(0, "0.5")

        # Row 8: VISA Addresses
        Label(frame, text="Lakeshore VISA:").grid(row=8, column=0, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(row=9, column=0, padx=(10,5), pady=(0,10), sticky='ew')

        Label(frame, text="Keithley VISA:").grid(row=8, column=1, padx=10, pady=pady_val, sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(row=9, column=1, padx=(5,10), pady=(0,10), sticky='ew')

        # Row 10: Buttons
        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=10, column=0, columnspan=2, padx=10, pady=5, sticky='ew')

        self.file_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=11, column=0, columnspan=2, padx=10, pady=5, sticky='ew')

        self.start_button = ttk.Button(frame, text="Start Measurement", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=12, column=0, padx=10, pady=15, sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=12, column=1, padx=10, pady=15, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        if not PYMEASURE_AVAILABLE: self.log("CRITICAL: PyMeasure or PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)

        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_ylabel("Resistance (Ω)")
        self.ax_main.set_yscale('log')
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_BLUE, marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Current (A)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)

        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)

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
        # <<< LOGIC CORRECTION START >>>
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'start_temp': float(self.entries["Start Temp"].get()),
                'end_temp': float(self.entries["End Temp"].get()),
                'rate': float(self.entries["Rate"].get()),
                'cutoff': float(self.entries["Cutoff"].get()),
                'source_voltage': float(self.entries["Source Voltage"].get()),
                'delay': float(self.entries["Delay"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'keithley_visa': self.keithley_cb.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError("All fields, VISA addresses, and save location are required.")
            if not (params['start_temp'] < params['end_temp'] < params['cutoff']):
                raise ValueError("Temperatures must be in order: start < end < cutoff.")

            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_RT.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}", f"Source V: {params['source_voltage']}V"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)",
                                 "Applied Voltage (V)", "Measured Current (A)", "Resistance (Ohm)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_stabilizing, self.is_running = True, False
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()

            # The key change is here: We no longer turn the heater on.
            # We just start the stabilization loop, which will decide what to do.
            self.log("Starting stabilization process...")
            self.root.after(1000, self._stabilization_loop)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")
        # <<< LOGIC CORRECTION END >>>

    def stop_measurement(self):
        if self.is_running or self.is_stabilizing:
            self.is_running, self.is_stabilizing = False, False
            self.log("Measurement stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            messagebox.showinfo("Info", "Measurement stopped and instruments disconnected.")

    def _stabilization_loop(self):
        # <<< LOGIC CORRECTION START >>>
        if not self.is_stabilizing: return
        try:
            params = self.backend.params
            current_temp = self.backend.lakeshore.get_temperature('A')
            
            # --- New Dynamic Heating/Cooling Logic ---
            # CASE 1: System is WARMER than start temp -> need to cool down.
            if current_temp > params['start_temp'] + 0.2: # Using a 0.2K tolerance
                self.log(f"Cooling... Current Temp: {current_temp:.4f} K > Target: {params['start_temp']} K")
                # Ensure heater is off while cooling
                self.backend.lakeshore.set_heater_range(1, 'off')
                
            # CASE 2: System is COLDER than start temp -> need to heat up.
            else:
                self.log(f"Heating... Current Temp: {current_temp:.4f} K < Target: {params['start_temp']} K")
                # Turn heater on to a medium range and set the target setpoint
                self.backend.lakeshore.set_heater_range(1, 'medium')
                self.backend.lakeshore.set_setpoint(1, params['start_temp'])

            # Check if we have reached the stabilization point
            if abs(current_temp - params['start_temp']) < 0.1:
                self.log(f"Stabilized at {current_temp:.4f} K. Waiting 5s before starting ramp...")
                self.is_stabilizing = False
                # Ensure heater is off before starting the ramp to avoid overshoot
                self.backend.lakeshore.set_heater_range(1, 'off') 
                self.root.after(5000, self._start_ramp_and_measurement)
            else:
                # If not stabilized, continue this loop
                self.root.after(2000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during stabilization: {e}"); self.stop_measurement()
        # <<< LOGIC CORRECTION END >>>

    def _start_ramp_and_measurement(self):
        params = self.backend.params
        self.backend.lakeshore.setup_ramp(1, params['rate'])
        self.backend.lakeshore.set_setpoint(1, params['end_temp'])
        self.log(f"Ramp started towards {params['end_temp']} K at {params['rate']} K/min.")
        self.is_running = True
        self.start_time = time.time()
        self.root.after(1000, self._update_measurement_loop)

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            temp, htr, cur, res = self.backend.get_measurement()
            elapsed = time.time() - self.start_time

            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}",
                    f"{temp:.4f}", f"{htr:.2f}", f"{self.backend.params['source_voltage']:.4e}",
                    f"{cur:.4e}", f"{res:.4e}"
                ])

            self.data_storage['time'].append(elapsed)
            self.data_storage['temperature'].append(temp)
            self.data_storage['current'].append(cur)
            self.data_storage['resistance'].append(res)

            self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])
            self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['current'])
            self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature'])

            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
                ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw()

            if temp >= self.backend.params['cutoff']:
                self.log(f"!!! SAFETY CUTOFF REACHED at {temp:.4f} K !!!")
                self.stop_measurement()
            elif temp >= self.backend.params['end_temp']:
                self.log(f"Target temperature reached. Measurement complete.")
                self.stop_measurement()
            else:
                # Use the delay from user input for the loop interval
                loop_delay_ms = max(1000, int(self.backend.params['delay'] * 1000))
                self.root.after(loop_delay_ms, self._update_measurement_loop)

        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}"); self.stop_measurement()

    def _scan_for_visa_instruments(self):
        if not pyvisa: self.log("ERROR: PyVISA is not installed."); return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_cb['values'] = resources
                self.keithley_cb['values'] = resources
                # Heuristic to set defaults
                for res in resources:
                    if "15" in res or "12" in res: self.lakeshore_cb.set(res)
                    if "27" in res or "26" in res: self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno("Exit", "Measurement running. Stop and exit?"):
                self.stop_measurement(); self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = Integrated_RT_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
