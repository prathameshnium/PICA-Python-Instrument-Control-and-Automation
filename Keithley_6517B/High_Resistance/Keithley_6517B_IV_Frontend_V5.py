# -------------------------------------------------------------------------------
# Name:         High Resistance IV GUI for Keithley 6517B
# Purpose:      Perform a voltage sweep and measure resistance using a
#               Keithley 6517B Electrometer with a real instrument backend.
# Author:       Prathamesh Deshmukh
# Created:      17/09/2025
# Version:      V: 4.0 (Dual Plot & Enhanced Logging)
# -------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
import csv
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
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
# --- REAL INSTRUMENT BACKEND ---
# -------------------------------------------------------------------------------
class Keithley6517B_Backend:
    """
    A dedicated class to handle backend communication with a real Keithley 6517B
    using the PyMeasure library. It incorporates proper initialization,
    zero-correction, and shutdown procedures.
    """
    def __init__(self):
        self.keithley = None
        self.is_connected = False
        if not PYMEASURE_AVAILABLE:
            raise ImportError("PyMeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")

    def initialize_instruments(self, parameters):
        """Connects to the instrument and performs the crucial zero-check sequence."""
        print(f"\n--- [Backend] Initializing Instrument at {parameters['keithley_visa']} ---")
        try:
            # Set a timeout for the connection
            self.keithley = Keithley6517B(parameters['keithley_visa'], timeout=20000)
            print(f"  Successfully connected to: {self.keithley.id}")

            # --- Configure Measurement and Perform Zero Correction ---
            print("  Configuring instrument and performing zero correction...")
            self.keithley.reset()
            self.keithley.clear()
            time.sleep(1)

            # 1. Enable Zero Check (connects ammeter to internal reference)
            print("    Step 1/4: Enabling Zero Check mode...")
            self.keithley.write(':SYSTem:ZCHeck ON')
            time.sleep(1)

            # 2. Acquire the zero measurement
            print("    Step 2/4: Acquiring zero correction value...")
            self.keithley.write(':SYSTem:ZCORrect:ACQuire')
            time.sleep(2) # Allow time for acquisition

            # 3. Disable Zero Check (reconnects input)
            print("    Step 3/4: Disabling Zero Check mode...")
            self.keithley.write(':SYSTem:ZCHeck OFF')

            # 4. Enable Zero Correct (subtracts offset from future measurements)
            print("    Step 4/4: Enabling Zero Correction for all measurements.")
            self.keithley.write(':SYSTem:ZCORrect ON')

            # Set up the instrument for resistance measurement
            self.keithley.measure_resistance()
            self.keithley.resistance_nplc = 1  # Integration rate for noise reduction (1 PLC)

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
        """Reads resistance and current from the instrument."""
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")

        # PyMeasure properties handle the SCPI commands to get the readings
        resistance = self.keithley.resistance
        current = self.keithley.current
        voltage = self.keithley.source_voltage # Read back the set voltage

        # Handle over-range condition, often returned as a very large number
        if resistance > 1e37:
            resistance = float('inf') # Standardize over-range representation

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
    PROGRAM_VERSION = "4.0"
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
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("High Resistance I-V Measurement (Keithley 6517B)")
        self.root.geometry("1550x900")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 800)

        self.is_running = False
        self.start_time = None
        try:
            self.backend = Keithley6517B_Backend()
        except Exception as e:
            messagebox.showerror("Backend Error", f"Could not initialize the backend.\nError: {e}\n\nPlease ensure PyMeasure and NI-VISA are installed correctly.")
            self.backend = None
        self.file_location_path = ""
        self.data_storage = {'time': [], 'voltage_applied': [], 'current_measured': [], 'resistance': []}
        self.voltage_list = []
        self.current_step_index = 0

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
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="High Resistance I-V Sweep", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def _process_logo_image(self, input_path, size=120):
        """Dynamically processes the input jpeg to a circular, transparent-background image."""
        if not (PIL_AVAILABLE and os.path.exists(input_path)):
            return None
        try:
            with Image.open(input_path) as img:
                img_cropped = img.crop((18, 18, 237, 237)) # Cropped for this specific logo
                mask = Image.new('L', img_cropped.size, 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0) + img_cropped.size, fill=255)
                img_cropped.putalpha(mask)
                img_hd = img_cropped.resize((size, size), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img_hd)
        except Exception as e:
            print(f"ERROR: Could not process logo image '{input_path}'. Reason: {e}")
            return None

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=120, height=120, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=10)
        self.logo_image = self._process_logo_image("UGC_DAE_CSR.jpeg")
        if self.logo_image:
            logo_canvas.create_image(60, 60, image=self.logo_image)
        else:
            logo_canvas.create_text(60, 60, text="LOGO", font=self.FONT_TITLE, fill=self.CLR_FG_LIGHT)

        info_text_institute = "Institute: UGC DAE CSR, Mumbai\nInstrument: Keithley 6517B Electrometer"
        ttk.Label(frame, text=info_text_institute, justify='left').grid(row=0, column=1, padx=10, pady=(10,5), sticky='w')

        info_text_meas = ("High Resistance Measurement:\n"
                          "  • Voltage Range: up to ±1000V\n"
                          "  • Current Range: 1fA to 20mA\n"
                          "  • Resistance Range: up to 10¹⁸ Ω")
        ttk.Label(frame, text=info_text_meas, justify='left').grid(row=1, column=1, padx=10, pady=(0,10), sticky='w')

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
        if not os.path.exists("UGC_DAE_CSR.jpeg"): self.log("WARNING: 'UGC_DAE_CSR.jpeg' not found for logo.")
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
        self.line_iv, = self.ax_iv.plot([], [], color=self.CLR_ACCENT_BLUE, marker='o', markersize=5, linestyle='-')
        self.ax_iv.set_title("Current vs. Voltage", fontweight='bold')
        self.ax_iv.set_ylabel("Measured Current (A)")
        self.ax_iv.grid(True, linestyle='--', alpha=0.6)

        # --- Configure Bottom Plot: R-V Curve ---
        self.line_rv, = self.ax_rv.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=5, linestyle='-')
        self.ax_rv.set_title("Resistance vs. Voltage", fontweight='bold')
        self.ax_rv.set_xlabel("Applied Voltage (V)")
        self.ax_rv.set_ylabel("Resistance (Ω)")
        self.ax_rv.set_yscale('log') # Resistance is often better viewed on a log scale
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

            self.ax_iv.set_title(f"I-V Curve: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()
            self.log("Measurement sweep started.")
            self.current_step_index = 0
            self.root.after(100, self._update_measurement_loop)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            if self.backend:
                self.backend.close_instruments()
            self.log("Instrument connection closed.")
            messagebox.showinfo("Info", "Measurement stopped and instrument disconnected.")

    def _update_measurement_loop(self):
        if not self.is_running or self.current_step_index >= len(self.voltage_list):
            if self.is_running: self.log("Sweep finished."); self.stop_measurement()
            return
        try:
            voltage = self.voltage_list[self.current_step_index]
            self.backend.set_voltage(voltage)
            self.log(f"Step {self.current_step_index + 1}/{len(self.voltage_list)}: Set V = {voltage:.3f} V. Waiting {self.delay_ms}ms...")
            self.root.after(self.delay_ms, self._perform_actual_read)
        except Exception as e:
            self.log(f"SWEEP ERROR: {traceback.format_exc()}"); self.stop_measurement()
            messagebox.showerror("Runtime Error", f"An error occurred during the sweep. Check console.\n{e}")

    def _perform_actual_read(self):
        if not self.is_running: return
        try:
            res, cur, volt = self.backend.get_measurement()
            elapsed_time = time.time() - self.start_time

            # Log the detailed reading to the console
            self.log(f"  Read -> V: {volt:.3e} V, I: {cur:.3e} A, R: {res:.3e} Ω, t: {elapsed_time:.2f} s")

            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow([f"{elapsed_time:.3f}", f"{volt:.4e}", f"{cur:.4e}", f"{res:.4e}"])

            self.data_storage['time'].append(elapsed_time)
            self.data_storage['voltage_applied'].append(volt)
            self.data_storage['current_measured'].append(cur)
            self.data_storage['resistance'].append(res)

            # Update I-V plot
            self.line_iv.set_data(self.data_storage['voltage_applied'], self.data_storage['current_measured'])
            self.ax_iv.relim(); self.ax_iv.autoscale_view()

            # Update R-V plot
            self.line_rv.set_data(self.data_storage['voltage_applied'], self.data_storage['resistance'])
            self.ax_rv.relim(); self.ax_rv.autoscale_view()

            self.figure.tight_layout(pad=3.0)
            self.canvas.draw()

            self.current_step_index += 1
            if self.is_running: self.root.after(10, self._update_measurement_loop)
        except Exception as e:
            self.log(f"READ ERROR: {traceback.format_exc()}"); self.stop_measurement()
            messagebox.showerror("Runtime Error", f"An error occurred while reading data. Check console.\n{e}")

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
                self.stop_measurement(); self.root.destroy()
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
