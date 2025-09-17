# -------------------------------------------------------------------------------
# Name:         High Resistance IV GUI for Keithley 6517B
# Purpose:      Perform a voltage sweep and measure resistance using a
#               Keithley 6517B Electrometer.
# Author:       Prathamesh Deshmukh
# Created:      17/09/2025
# Version:      V: 2.0 (I-V Plot Focus)
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
except ImportError:
    pyvisa = None

# -------------------------------------------------------------------------------
# --- BACKEND (PLACEHOLDER) ---
# -------------------------------------------------------------------------------
class Keithley6517B_Backend_Placeholder:
    """
    A placeholder class to simulate the Keithley 6517B Electrometer.
    This allows for GUI development and testing without a live instrument.
    It simulates measuring a ~10 GigaOhm resistor.
    """
    def __init__(self):
        self.params = {}
        self.keithley = None
        self.current_voltage_setting = 0.0
        self.is_connected = False
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception:
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, parameters):
        """Receives all parameters from the GUI and 'configures' the placeholder."""
        print("\n--- [Backend Placeholder] Initializing Instrument ---")
        self.params = parameters
        if not self.params['keithley_visa']:
             raise ConnectionError("VISA address for Keithley 6517B not provided.")
        print(f"  Attempting to connect to {self.params['keithley_visa']}...")
        time.sleep(0.5)
        print("  Connected to: FAKE KEITHLEY 6517B,0,0,0")
        print("  Configuring for SVMI (Source Voltage, Measure Current) measurement.")
        self.is_connected = True
        print("--- [Backend Placeholder] Instrument Initialized ---")

    def set_voltage(self, voltage):
        """Placeholder for setting the voltage source."""
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")
        self.current_voltage_setting = voltage

    def get_measurement(self):
        """
        Performs a single 'measurement' and returns simulated data.
        Simulates a 10 G-Ohm resistor (I = V / 10e9) with some random noise.
        """
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")
        ideal_current = self.current_voltage_setting / 10e9
        noise = ideal_current * 0.02 * (np.random.rand() - 0.5)
        measured_current = ideal_current + noise
        if self.current_voltage_setting == 0:
            resistance = float('inf')
        else:
            resistance = self.current_voltage_setting / measured_current
        return resistance, measured_current, self.current_voltage_setting

    def close_instruments(self):
        """Safely 'shuts down' and disconnects from the placeholder instrument."""
        print("--- [Backend Placeholder] Closing instrument connection. ---")
        if self.is_connected:
            print("  Voltage source turned OFF.")
            print(f"  Connection to FAKE KEITHLEY 6517B closed.")
            self.is_connected = False


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class HighResistanceIV_GUI:
    """The main GUI application class (Front End)."""
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
        self.backend = Keithley6517B_Backend_Placeholder()
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
                          "  • Voltage Range: 1µV to 200V\n"
                          "  • Current Range: 10aA to 20mA\n"
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
        if not pyvisa: self.log("WARNING: PyVISA not found. Run 'pip install pyvisa'.")
        if not os.path.exists("UGC_DAE_CSR.jpeg"): self.log("WARNING: 'UGC_DAE_CSR.jpeg' not found for logo.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live I-V Curve', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 6), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_iv = self.figure.add_subplot(111)

        self.line_iv, = self.ax_iv.plot([], [], color=self.CLR_ACCENT_BLUE, marker='o', markersize=5, linestyle='-')

        self.ax_iv.set_title("Current vs. Voltage", fontweight='bold')
        self.ax_iv.set_xlabel("Applied Voltage (V)")
        self.ax_iv.set_ylabel("Measured Current (A)")
        self.ax_iv.grid(True, linestyle='--', alpha=0.6)

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
            self.line_iv.set_data([], [])
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
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow([f"{elapsed_time:.3f}", f"{volt:.4e}", f"{cur:.4e}", f"{res:.4e}"])

            self.data_storage['time'].append(elapsed_time)
            self.data_storage['voltage_applied'].append(volt)
            self.data_storage['current_measured'].append(cur)
            self.data_storage['resistance'].append(res)

            self.line_iv.set_data(self.data_storage['voltage_applied'], self.data_storage['current_measured'])
            self.ax_iv.relim(); self.ax_iv.autoscale_view()
            self.figure.tight_layout(pad=2.5)
            self.canvas.draw()

            self.current_step_index += 1
            if self.is_running: self.root.after(10, self._update_measurement_loop)
        except Exception as e:
            self.log(f"READ ERROR: {traceback.format_exc()}"); self.stop_measurement()
            messagebox.showerror("Runtime Error", f"An error occurred while reading data. Check console.\n{e}")

    def _scan_for_visa_instruments(self):
        if self.backend.rm is None:
            self.log("ERROR: VISA manager failed. Is NI-VISA or similar installed?"); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources
                for res in resources:
                    if "GPIB" in res and ("24" in res or "25" in res or "26" in res):
                         self.keithley_combobox.set(res); break
                else: self.keithley_combobox.set(resources[0])
            else:
                self.log("No VISA instruments found.")
                self.keithley_combobox['values'] = []; self.keithley_combobox.set("")
        except Exception as e:
            self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement sweep is running. Stop and exit?"):
                self.stop_measurement(); self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = HighResistanceIV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
