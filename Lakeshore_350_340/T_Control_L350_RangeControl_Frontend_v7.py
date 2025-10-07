'''
===============================================================================
 PROGRAM:      Lakeshore 350 Temp Ramp GUI

 PURPOSE:      Provide a user-friendly interface for a temperature ramp using
               robust stabilization and a constant high-power ramp.

 DESCRIPTION:  This program provides a graphical user interface (GUI) for
               automating a temperature ramp experiment with a Lakeshore 350
               controller. It features robust temperature stabilization at the
               start point, followed by a hardware-controlled ramp using a
               fixed high-power heater setting for consistent results. The GUI
               provides live plotting of temperature and heater output, a
               console for logging, and safe instrument handling.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre
 
 VERSION:      3.0
 LAST EDITED:  04/10/2025
===============================================================================
'''


# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import csv
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
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
except ImportError:
    pyvisa = None

#===============================================================================
# BACKEND CLASS - Instrument Control Logic
#===============================================================================
class LakeshoreBackend:
    """A dedicated class to handle backend communication with the Lakeshore 350."""
    def __init__(self):
        self.instrument = None
        self.params = {}
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None

    def initialize_instrument(self, parameters):
        """Receives all parameters from the GUI and configures the instrument."""
        print("\n--- [Backend] Initializing Lakeshore 350 ---")
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            print(f"  Connecting to Lakeshore 350 at {self.params['lakeshore_visa']}...")
            self.instrument = self.rm.open_resource(self.params['lakeshore_visa'])
            self.instrument.timeout = 10000
            print(f"    Connected to: {self.instrument.query('*IDN?').strip()}")

            self.instrument.write('*RST'); time.sleep(0.5)
            self.instrument.write('*CLS')
            self.instrument.write('HTRSET 1,1,2,0,1') # 25Ω heater, 1A max, display in %
            print("  Heater configured (25Ω, 1A max).")
            print("--- [Backend] Instrument Initialization Complete ---")
            return True
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure the instrument. {e}")
            raise e

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        """ Configures the instrument's internal ramp generator. """
        self.instrument.write(f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)


    def set_setpoint(self, output, temperature_k):
        """Sets the temperature setpoint."""
        self.instrument.write(f'SETP {output},{temperature_k}')

    def set_heater_range(self, output, heater_range):
        """Sets the heater range ('off', 'low', 'medium', 'high')."""
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None: raise ValueError("Invalid heater range.")
        self.instrument.write(f'RANGE {output},{range_code}')

    def get_measurement(self):
        """Performs a single measurement and returns temperature and heater output."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        try:
            temp_str = self.instrument.query('KRDG? A').strip()
            heater_str = self.instrument.query('HTR? 1').strip()
            return float(temp_str), float(heater_str)
        except (pyvisa.errors.VisaIOError, ValueError):
            return float('nan'), float('nan')

    def close_instrument(self):
        """Safely shuts down the heater and disconnects from the instrument."""
        print("--- [Backend] Closing instrument connection. ---")
        if self.instrument:
            try:
                print("  Turning off heater...")
                self.set_heater_range(1, 'off')
                time.sleep(0.5)
                self.instrument.close()
                print("  Lakeshore 350 connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning: Error during instrument shutdown. {e}")
            finally:
                self.instrument = None

#===============================================================================
# FRONTEND CLASS - The Main GUI Application
#===============================================================================
class LakeshoreRampGUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "3.0"
    LOGO_SIZE = 110
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Modern Dark Theme ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#28A745'
    CLR_ACCENT_RED = '#DC3545'
    CLR_CONSOLE_BG = '#FFFFFF'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Lakeshore 350 Temperature Ramp Control")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self.backend = LakeshoreBackend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': [], 'heater': []}
        self.current_heater_range = 'off'
        self.logo_image = None # Attribute to hold the logo image reference

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TLabelframe', background=self.CLR_BG_DARK, bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)

        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8), foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])

        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#218838')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#C82333')])

        # Matplotlib styling
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2
        mpl.rcParams['figure.facecolor'] = self.CLR_GRAPH_BG

    def create_widgets(self):
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500)
        main_pane.add(left_panel, weight=0) # Give less weight to the control panel
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
        font_title_italic = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold', 'italic')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Lakeshore 350 Temperature Ramp Control", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=font_title_italic).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0, relief='flat')
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo: {e}")
                logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')

        # Institute Name (larger font)
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)
 
        # Program details
        details_text = ("Program Duty: Temperature Ramp\n"
                        "Instrument: Lakeshore 350 Controller\n"
                        "Measurement Range: 1.4 K to 800 K (Sensor Dependent)")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=10, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady_val = (5, 5)
        Label(frame, text="Sample Name:", font=self.FONT_BASE).grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')
        Label(frame, text="Start Temperature (K):", font=self.FONT_BASE).grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Start Temp"].grid(row=3, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        Label(frame, text="End Temperature (K):", font=self.FONT_BASE).grid(row=2, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["End Temp"].grid(row=3, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        Label(frame, text="Ramp Rate (K/min):", font=self.FONT_BASE).grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Rate"].grid(row=5, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')
        Label(frame, text="Safety Cutoff (K):", font=self.FONT_BASE).grid(row=4, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["Safety Cutoff"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Safety Cutoff"].grid(row=5, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')
        Label(frame, text="Lakeshore 350 VISA:", font=self.FONT_BASE).grid(row=6, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_combobox.grid(row=7, column=0, columnspan=2, padx=10, pady=(0, 0), sticky='ew')
        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=8, column=0, columnspan=2, padx=10, pady=10, sticky='ew')
        self.file_location_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_location_button.grid(row=9, column=0, columnspan=2, padx=10, pady=5, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=10, column=0, padx=(10,5), pady=15, sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=10, column=1, padx=(5,10), pady=15, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graphs')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = gridspec.GridSpec(2, 1, figure=self.figure, height_ratios=[3, 1])
        self.ax_main = self.figure.add_subplot(gs[0, 0])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0], sharex=self.ax_main)
        for ax in [self.ax_main, self.ax_sub1]: ax.grid(True, linestyle='--', alpha=0.7)
        self.line_main, = self.ax_main.plot([], [], color='#C00000', marker='o', markersize=4, linestyle='-')
        self.line_sub1, = self.ax_sub1.plot([], [], color='#0070C0', marker='.', markersize=4, linestyle='-')
        self.ax_main.set_title("Temperature vs. Time", fontweight='bold')
        self.ax_main.set_ylabel("Temperature (K)")
        self.ax_main.tick_params(axis='x', labelbottom=False)
        self.ax_sub1.set_xlabel("Elapsed Time (s)")
        self.ax_sub1.set_ylabel("Heater Output (%)")
        self.figure.tight_layout(pad=2.5)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {}
            params['sample_name'] = self.entries["Sample Name"].get()
            params['start_temp'] = float(self.entries["Start Temp"].get())
            params['end_temp'] = float(self.entries["End Temp"].get())
            params['rate'] = float(self.entries["Rate"].get())
            params['safety_cutoff'] = float(self.entries["Safety Cutoff"].get())
            params['lakeshore_visa'] = self.lakeshore_combobox.get()

            if not all([params['sample_name'], params['lakeshore_visa'], self.file_location_path]):
                raise ValueError("Sample Name, VISA address, and Save Location are all required.")
            if not (params['start_temp'] < params['end_temp'] < params['safety_cutoff']):
                raise ValueError("Temperatures must be in ascending order (Start < End < Cutoff).")
            if params['rate'] <= 0:
                raise ValueError("Ramp rate must be a positive number.")

            self.backend.initialize_instrument(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_T_vs_Time.csv"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}", f"Rate: {params['rate']} K/min"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.is_stabilizing = True
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1]: line.set_data([], [])
            self.ax_main.set_title(f"Ramp for Sample: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()


            self.log("Starting stabilization process...")
            self.root.after(1000, self._stabilization_loop)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n\n{e}")

    def stop_measurement(self, reason=""):
        if self.is_running or self.is_stabilizing:
            self.is_running = False
            self.is_stabilizing = False
            if reason: self.log(f"Measurement stopped: {reason}")
            else: self.log("Measurement stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instrument()
            self.log("Instrument connection closed.")
            if reason: messagebox.showinfo("Info", f"Measurement finished.\nReason: {reason}")
            else: messagebox.showinfo("Info", "Measurement stopped and instrument disconnected.")

    def _stabilization_loop(self):
        """Robustly stabilizes at the start temperature, cooling if necessary."""
        if not self.is_stabilizing: return
        try:
            params = self.backend.params
            current_temp, _ = self.backend.get_measurement()

            if current_temp > params['start_temp'] + 0.2:
                self.log(f"Cooling... Current: {current_temp:.4f} K > Target: {params['start_temp']} K")
                self.backend.set_heater_range(1, 'off')
            else:
                self.log(f"Heating... Current: {current_temp:.4f} K <= Target: {params['start_temp']} K")
                self.backend.set_heater_range(1, 'medium')
                self.backend.set_setpoint(1, params['start_temp'])


            if abs(current_temp - params['start_temp']) < 0.1:
                self.log(f"Stabilized at {current_temp:.4f} K. Waiting 5s before starting ramp...")
                self.is_stabilizing = False
                self.root.after(5000, self._start_hardware_ramp)
            else:
                self.root.after(2000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during stabilization: {e}"); self.stop_measurement("Error during stabilization")

    def _start_hardware_ramp(self):
        """Initializes the Lakeshore's internal hardware ramp."""
        params = self.backend.params

        # 1. Set the final setpoint
        self.backend.set_setpoint(1, params['end_temp'])

        # 2. Configure the hardware ramp rate
        self.backend.setup_ramp(1, params['rate'])

        # 3. Set the heater range to High (5) for the entire ramp
        self.current_heater_range = 'high'
        self.backend.set_heater_range(1, self.current_heater_range)

        self.log(f"Hardware ramp started towards {params['end_temp']} K at {params['rate']} K/min.")
        self.log(f"Heater range permanently set to '{self.current_heater_range}' (Range 5).")

        self.start_time = time.time()
        self.root.after(1000, self._update_measurement_loop)

    def _update_measurement_loop(self):
        """Main loop for data acquisition during the hardware ramp."""
        if not self.is_running: return
        try:
            current_temp, heater_output = self.backend.get_measurement()
            params = self.backend.params
            elapsed_time = time.time() - self.start_time

            # --- HEATER LOGIC REMOVED ---
            # Heater is now set to 'high' at the start of the ramp and remains there.
            # No dynamic switching is needed in the loop.

            # --- Logging, Data Storage, and Plotting ---
            self.log(f"Time: {elapsed_time:7.1f}s | Temp: {current_temp:8.4f}K | Heater: {heater_output:5.1f}% ({self.current_heater_range})")


            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                 f"{elapsed_time:.2f}", f"{current_temp:.4f}", f"{heater_output:.2f}"])


            self.data_storage['time'].append(elapsed_time)
            self.data_storage['temperature'].append(current_temp)
            self.data_storage['heater'].append(heater_output)

            self.line_main.set_data(self.data_storage['time'], self.data_storage['temperature'])
            self.line_sub1.set_data(self.data_storage['time'], self.data_storage['heater'])
            for ax in [self.ax_main, self.ax_sub1]:
                ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=2.5)
            self.canvas.draw()

            # --- Check for end conditions ---
            if current_temp >= params['safety_cutoff']:
                self.stop_measurement(f"SAFETY CUTOFF REACHED at {current_temp:.4f} K!")
                return
            if current_temp >= params['end_temp']:
                self.stop_measurement(f"Target temperature of {params['end_temp']} K reached.")
                return

            self.root.after(2000, self._update_measurement_loop) # Loop every 2 seconds

        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement("A critical error occurred.")
            messagebox.showerror("Runtime Error", "An error occurred during measurement. Check console.")

    def _scan_for_visa_instruments(self):
        if pyvisa is None: self.log("ERROR: PyVISA not found. Run 'pip install pyvisa'."); return
        if self.backend.rm is None: self.log("ERROR: VISA manager failed. Is NI-VISA installed?"); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_combobox['values'] = resources
                for res in resources:
                    if "GPIB1::15" in res:
                        self.lakeshore_combobox.set(res)
                        break
                if not self.lakeshore_combobox.get(): self.lakeshore_combobox.set(resources[0])
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno("Exit", "Measurement is running. Stop and exit?"):
                self.stop_measurement(); self.root.destroy()
        else: self.root.destroy()

def main():
    root = tk.Tk()
    app = LakeshoreRampGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
