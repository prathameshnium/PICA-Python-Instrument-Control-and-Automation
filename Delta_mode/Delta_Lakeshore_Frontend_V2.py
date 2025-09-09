# -------------------------------------------------------------------------------
# Name:         Resistance vs Temperature GUI
# Purpose:      Perform a temperature-dependent Delta mode measurement with a
#               Keithley 6221 and Lakeshore 350.
# Author:       Prathamesh
# Created:      09/09/2025
# Version:      2.0 (UI Revamp based on Sketch)
# -------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext
import numpy as np
import csv
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec

# --- Packages for Back end ---
try:
    import pyvisa
except ImportError:
    pyvisa = None

class Combined_Backend:
    """
    A dedicated class to handle backend instrument communication for both the
    Keithley 6221 and the Lakeshore 350. (NO CHANGES MADE TO THIS CLASS)
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

    def initialize_instruments(self, parameters):
        """Receives all parameters from the GUI and configures the instruments."""
        print("\n--- [Backend] Initializing Instruments ---")
        self.params = parameters
        print(f"  Sample Name: {self.params['sample_name']}")
        print(f"  Keithley 6221 VISA: {self.params['keithley_visa']}")
        print(f"  Lakeshore 350 VISA: {self.params['lakeshore_visa']}")
        print(f"  Applied Current (A): {self.params['apply_current']}")

        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")

        try:
            print("  Connecting to Keithley 6221...")
            self.keithley = self.rm.open_resource(self.params['keithley_visa'])
            self.keithley.timeout = 10000
            print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
            self.keithley.write("*rst; status:preset; *cls")
            self.keithley.write(f"SOUR:DELT:HIGH {self.params['apply_current']}")
            self.keithley.write("SOUR:DELT:ARM")
            time.sleep(1)
            self.keithley.write("INIT:IMM")
            print("  Keithley 6221 Configured and Armed.")

            print("  Connecting to Lakeshore 350...")
            self.lakeshore = self.rm.open_resource(self.params['lakeshore_visa'])
            print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
            self.lakeshore.write('*RST')
            time.sleep(0.5)
            self.lakeshore.write('*CLS')
            self.lakeshore.write('RANGE 0')
            print("  Lakeshore 350 Configured (Heater OFF).")

            print("--- [Backend] Instrument Initialization Complete ---")

        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure an instrument. {e}")
            raise e

    def get_measurement(self):
        """Performs a single measurement and returns resistance, voltage, and temperature."""
        if not self.keithley or not self.lakeshore:
            raise ConnectionError("One or more instruments are not connected.")

        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        data_points = raw_data.strip().split(',')
        voltage = float(data_points[0])
        resistance = voltage / self.params['apply_current']

        temp_str = self.lakeshore.query('KRDG? A').strip()
        temperature = float(temp_str)

        return resistance, voltage, temperature

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                print("  Closing Keithley 6221...")
                self.keithley.write("SOUR:CLE")
                self.keithley.write("*RST")
                self.keithley.close()
                print("  Keithley 6221 connection closed.")
            except pyvisa.errors.VisaIOError: pass
            finally: self.keithley = None
        if self.lakeshore:
            try:
                print("  Closing Lakeshore 350...")
                self.lakeshore.write("RANGE 0")
                self.lakeshore.close()
                print("  Lakeshore 350 connection closed.")
            except pyvisa.errors.VisaIOError: pass
            finally: self.lakeshore = None

class MeasurementAppGUI:
    """The main GUI application class (Front End)."""
    # Styling Constants
    BG_COLOR, FRAME_BG, FRAME_FG = '#F0F0F0', '#2C3E50', '#ECF0F1'
    FONT_NORMAL, FONT_BOLD, FONT_TITLE = ('Helvetica', 12), ('Helvetica', 12, 'bold'), ('Helvetica', 14, 'bold')
    COLOR_R, COLOR_T = '#E74C3C', '#2ECC71'
    PROGRAM_VERSION = "2.0"

    def __init__(self, root):
        self.root = root
        self.root.title("Resistance vs Temperature Measurement")
        self.root.geometry("1400x950")
        self.root['background'] = self.BG_COLOR

        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""

        # Data storage for plotting
        self.times, self.voltages, self.resistances, self.temperatures = [], [], [], []

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def create_widgets(self):
        # --- Main Layout Frames ---
        header_frame = tk.Frame(self.root, bg=self.FRAME_BG)
        header_frame.pack(side='top', fill='x')

        left_panel = tk.Frame(self.root, bg=self.BG_COLOR, width=380)
        left_panel.pack(side='left', fill='y', padx=(10, 5), pady=10)
        left_panel.pack_propagate(False)

        right_panel = tk.Frame(self.root, bg=self.BG_COLOR)
        right_panel.pack(side='right', fill='both', expand=True, padx=(5, 10), pady=10)

        # --- Populate Frames ---
        self.create_header(header_frame)
        self.create_info_frame(left_panel)
        self.create_input_frame(left_panel)
        self.create_console_frame(left_panel)
        self.create_graph_frame(right_panel)

    def create_header(self, parent):
        Label(parent, text="Name of the program : Resistance vs Temperature", font=self.FONT_BOLD, fg=self.FRAME_FG, bg=self.FRAME_BG).pack(side='left', padx=20, pady=5)
        Label(parent, text=f"Program version : {self.PROGRAM_VERSION}", font=self.FONT_BOLD, fg=self.FRAME_FG, bg=self.FRAME_BG).pack(side='right', padx=20, pady=5)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=(0, 10), padx=10, fill='x')
        info_text = (
            "Institute: UGC DAE CSR, Mumbai\n"
            "Measurement: Resistance vs Temperature in Delta Mode\n"
            "Instruments: Keithley 6221 & Lakeshore 350"
        )
        Label(frame, text=info_text, font=self.FONT_NORMAL, justify='left', fg=self.FRAME_FG, bg=self.FRAME_BG).pack(padx=10, pady=10, anchor='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')

        self.entries = {}
        fields = ["Sample Name", "Apply Current (A)"]
        for i, field_text in enumerate(fields):
            Label(frame, text=f"{field_text}:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=i, column=0, padx=10, pady=8, sticky='w')
            entry = Entry(frame, width=22, font=self.FONT_NORMAL)
            entry.grid(row=i, column=1, padx=10, pady=8)
            self.entries[field_text] = entry

        current_row = len(fields)
        Label(frame, text="Keithley 6221 VISA:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=current_row, column=0, padx=10, pady=8, sticky='w')
        self.keithley_combobox = ttk.Combobox(frame, width=20, font=self.FONT_NORMAL, state='readonly')
        self.keithley_combobox.grid(row=current_row, column=1, padx=10, pady=8)

        current_row += 1
        Label(frame, text="Lakeshore 350 VISA:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=current_row, column=0, padx=10, pady=8, sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, width=20, font=self.FONT_NORMAL, state='readonly')
        self.lakeshore_combobox.grid(row=current_row, column=1, padx=10, pady=8)

        current_row += 1
        self.scan_button = Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments, font=('Helvetica', 10, 'bold'))
        self.scan_button.grid(row=current_row, column=0, columnspan=2, padx=10, pady=(10,5), sticky='ew')

        current_row += 1
        Label(frame, text="Save Location:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=current_row, column=0, padx=10, pady=8, sticky='w')
        self.file_location_button = Button(frame, text="Browse...", command=self._browse_file_location, font=('Helvetica', 10))
        self.file_location_button.grid(row=current_row, column=1, padx=10, pady=8, sticky='ew')

        current_row += 1
        self.start_button = Button(frame, text="Start", command=self.start_measurement, height=2, font=self.FONT_BOLD)
        self.start_button.grid(row=current_row, column=0, padx=10, pady=20, sticky='ew')

        self.stop_button = Button(frame, text="Stop", command=self.stop_measurement, height=2, font=self.FONT_BOLD, state='disabled')
        self.stop_button.grid(row=current_row, column=1, padx=10, pady=20, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='both', expand=True)
        self.console_widget = scrolledtext.ScrolledText(frame, height=10, state='disabled', bg='#1C2833', fg='#EAECEE', font=('Consolas', 10))
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Scan for instruments.")

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', bd=4, bg='white', fg=self.FRAME_BG, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True)

        controls_frame = tk.Frame(graph_container, bg='white')
        controls_frame.pack(fill='x', pady=5)

        # --- Axis Scale Controls ---
        self.x_scale_var = tk.StringVar(value="linear")
        self.y_scale_var = tk.StringVar(value="linear")

        Label(controls_frame, text="X-Axis:", font=self.FONT_BOLD, bg='white').pack(side='left', padx=(20, 5))
        ttk.Radiobutton(controls_frame, text="Linear", variable=self.x_scale_var, value="linear", command=self._update_plot_scales).pack(side='left')
        ttk.Radiobutton(controls_frame, text="Log", variable=self.x_scale_var, value="log", command=self._update_plot_scales).pack(side='left')

        Label(controls_frame, text="Y-Axis:", font=self.FONT_BOLD, bg='white').pack(side='left', padx=(40, 5))
        ttk.Radiobutton(controls_frame, text="Linear", variable=self.y_scale_var, value="linear", command=self._update_plot_scales).pack(side='left')
        ttk.Radiobutton(controls_frame, text="Log", variable=self.y_scale_var, value="log", command=self._update_plot_scales).pack(side='left')

        # --- Matplotlib Figure and Axes ---
        self.figure = Figure(figsize=(8, 8), dpi=100)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)

        self.ax_main = self.figure.add_subplot(gs[0, :]) # Main plot spans top row
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0]) # Subplot 1
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1]) # Subplot 2

        self.line_main, = self.ax_main.plot([], [], color=self.COLOR_R, marker='o', markersize=4, linestyle='')
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.COLOR_R, marker='.', markersize=4)
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.COLOR_T, marker='.', markersize=4)

        self.ax_main.set_title("Resistance vs. Temperature")
        self.ax_main.set_xlabel("Temperature (K)"); self.ax_main.set_ylabel("Resistance (Ohms)"); self.ax_main.grid(True)
        self.ax_sub1.set_xlabel("Time (s)"); self.ax_sub1.set_ylabel("Resistance (Ohms)"); self.ax_sub1.grid(True)
        self.ax_sub2.set_xlabel("Time (s)"); self.ax_sub2.set_ylabel("Temperature (K)"); self.ax_sub2.grid(True)

        self.figure.tight_layout(pad=3.0)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        print(message)
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', message + '\n')
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {}
            params['sample_name'] = self.entries["Sample Name"].get()
            params['apply_current'] = float(self.entries["Apply Current (A)"].get())
            params['keithley_visa'] = self.keithley_combobox.get()
            params['lakeshore_visa'] = self.lakeshore_combobox.get()

            if not all([params['sample_name'], self.file_location_path,
                        params['keithley_visa'], params['lakeshore_visa']]):
                raise ValueError("All fields, VISA addresses, and a save location are required.")

            self.backend.initialize_instruments(params)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_R_vs_T.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample Name: {params['sample_name']}"])
                writer.writerow([f"# Applied Current (A): {params['apply_current']}"])
                writer.writerow(["Time (s)", "Voltage (V)", "Resistance (Ohms)", "Temperature (K)"])
            self.log(f"Output file created: {self.data_filepath}")

            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')

            # Reset plot data
            self.times, self.voltages, self.resistances, self.temperatures = [], [], [], []
            self.line_main.set_data([], [])
            self.line_sub1.set_data([], [])
            self.line_sub2.set_data([], [])
            self.ax_main.set_title(f"Sample: {params['sample_name']} | Current: {params['apply_current']} A")
            self.canvas.draw()

            self.log("GUI measurement loop started.")
            self.root.after(1000, self._update_measurement_loop)
        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            messagebox.showinfo("Info", "Measurement stopped.")

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            resistance, voltage, temperature = self.backend.get_measurement()
            elapsed_time = time.time() - self.start_time

            # Append data to file
            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"{elapsed_time:.3f}", f"{voltage:.8f}", f"{resistance:.8f}", f"{temperature:.4f}"])

            # Append data to in-memory lists for efficient plotting
            self.times.append(elapsed_time)
            self.voltages.append(voltage)
            self.resistances.append(resistance)
            self.temperatures.append(temperature)

            # Update plot data
            self.line_main.set_data(self.temperatures, self.resistances)
            self.line_sub1.set_data(self.times, self.resistances)
            self.line_sub2.set_data(self.times, self.temperatures)

            # Rescale all axes
            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
                ax.relim()
                ax.autoscale_view()

            self.canvas.draw()

        except Exception:
            self.log(f"\n--- AN ERROR OCCURRED ---\n{traceback.format_exc()}")
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "An error occurred. Check the console.")

        if self.is_running:
            self.root.after(1000, self._update_measurement_loop)

    def _update_plot_scales(self):
        """Callback to change the scale of the main plot axes."""
        self.ax_main.set_xscale(self.x_scale_var.get())
        self.ax_main.set_yscale(self.y_scale_var.get())
        self.canvas.draw()

    def _scan_for_visa_instruments(self):
        if pyvisa is None:
            self.log("ERROR: PyVISA not found. Please run 'pip install pyvisa'.")
            return
        if self.backend.rm is None:
            self.log("ERROR: VISA manager failed. Is NI-VISA installed correctly?")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources
                self.lakeshore_combobox['values'] = resources
                for res in resources:
                    if "13" in res: self.keithley_combobox.set(res)
                    if "15" in res or "12" in res: self.lakeshore_combobox.set(res)
                if not self.keithley_combobox.get() and resources: self.keithley_combobox.set(resources[0])
                if not self.lakeshore_combobox.get() and resources: self.lakeshore_combobox.set(resources[-1])
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.file_location_path = path
            self.log(f"Save location set to: {path}")

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
