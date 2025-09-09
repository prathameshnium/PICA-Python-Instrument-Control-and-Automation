# -------------------------------------------------------------------------------
# Name:         Delta and Lakeshore GUI
# Purpose:      Perform a Delta mode measurement with a Keithley 6221 while
#               simultaneously monitoring temperature with a Lakeshore 350,
#               controlled by a graphical user interface.
# Author:       Prathamesh
# Created:      09/09/2025
# Version:      1.0
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

# --- Packages for Back end ---
try:
    import pyvisa
except ImportError:
    pyvisa = None

class Combined_Backend:
    """
    A dedicated class to handle backend instrument communication for both the
    Keithley 6221 and the Lakeshore 350.
    """
    def __init__(self):
        self.params = {}
        self.keithley = None
        self.lakeshore = None
        if pyvisa:
            try:
                # Specify the 64-bit VISA library path for robustness
                visa_library_path = 'C:\\Windows\\System32\\visa64.dll'
                self.rm = pyvisa.ResourceManager(visa_library_path)
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
            # --- Initialize Keithley 6221 ---
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

            # --- Initialize Lakeshore 350 ---
            print("  Connecting to Lakeshore 350...")
            self.lakeshore = self.rm.open_resource(self.params['lakeshore_visa'])
            print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
            self.lakeshore.write('*RST')
            time.sleep(0.5)
            self.lakeshore.write('*CLS')
            # Per user request, heater is off. Setting RANGE to 0.
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

        # Get data from Keithley 6221
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        data_points = raw_data.strip().split(',')
        voltage = float(data_points[0])
        # Calculate resistance based on the known applied current
        resistance = voltage / self.params['apply_current']

        # Get data from Lakeshore 350
        temp_str = self.lakeshore.query('KRDG? A').strip()
        temperature = float(temp_str)

        return resistance, voltage, temperature

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                print("  Closing Keithley 6221...")
                self.keithley.write("SOUR:CLE") # Abort measurement, turn off source
                self.keithley.write("*RST")
                self.keithley.close()
                print("  Keithley 6221 connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning during Keithley shutdown: {e}")
            finally:
                self.keithley = None
        if self.lakeshore:
            try:
                print("  Closing Lakeshore 350...")
                self.lakeshore.write("RANGE 0") # Ensure heater is off
                self.lakeshore.close()
                print("  Lakeshore 350 connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning during Lakeshore shutdown: {e}")
            finally:
                self.lakeshore = None

class MeasurementAppGUI:
    """The main GUI application class (Front End)."""
    # Styling Constants
    BG_COLOR, FRAME_BG, FRAME_FG = '#F0F0F0', '#2C3E50', '#ECF0F1'
    FONT_NORMAL, FONT_BOLD, FONT_TITLE = ('Helvetica', 12), ('Helvetica', 12, 'bold'), ('Helvetica', 14, 'bold')
    COLOR_R, COLOR_V, COLOR_T = '#E74C3C', '#3498DB', '#2ECC71' # Red, Blue, Green

    def __init__(self, root):
        self.root = root
        self.root.title("Delta Mode and Temperature Measurement")
        self.root.geometry("1200x900")
        self.root['background'] = self.BG_COLOR
        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def create_widgets(self):
        left_panel = tk.Frame(self.root, bg=self.BG_COLOR)
        left_panel.grid(row=0, column=0, sticky="ns", padx=(10,0))
        self.create_input_frame(left_panel)
        self.create_console_frame(left_panel)
        self.create_graph_frame()

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
        # Keithley VISA
        Label(frame, text="Keithley 6221 VISA:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=current_row, column=0, padx=10, pady=8, sticky='w')
        self.keithley_combobox = ttk.Combobox(frame, width=20, font=self.FONT_NORMAL, state='readonly')
        self.keithley_combobox.grid(row=current_row, column=1, padx=10, pady=8)

        current_row += 1
        # Lakeshore VISA
        Label(frame, text="Lakeshore 350 VISA:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=current_row, column=0, padx=10, pady=8, sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, width=20, font=self.FONT_NORMAL, state='readonly')
        self.lakeshore_combobox.grid(row=current_row, column=1, padx=10, pady=8)

        current_row += 1
        self.scan_button = Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments, font=('Helvetica', 10, 'bold'))
        self.scan_button.grid(row=current_row, column=0, columnspan=2, padx=10, pady=(10,5), sticky='ew')

        current_row += 1
        Label(frame, text="Save Location:", font=self.FONT_NORMAL, fg=self.FRAME_FG, bg=self.FRAME_BG).grid(row=current_row, column=0, padx=10, pady=8, sticky='w')
        self.file_location_button = Button(frame, text="Browse...", command=self._browse_file_location, font=('Helvetica', 10), bg=self.COLOR_V, fg=self.FRAME_FG)
        self.file_location_button.grid(row=current_row, column=1, padx=10, pady=8, sticky='ew')

        current_row += 1
        self.start_button = Button(frame, text="Start Measurement", command=self.start_measurement, height=2, font=self.FONT_BOLD, bg=self.COLOR_T, fg=self.FRAME_FG)
        self.start_button.grid(row=current_row, column=0, columnspan=2, padx=10, pady=20, sticky='ew')

        current_row += 1
        self.stop_button = Button(frame, text="Stop Measurement", command=self.stop_measurement, height=2, font=self.FONT_BOLD, bg=self.COLOR_R, fg=self.FRAME_FG, state='disabled')
        self.stop_button.grid(row=current_row, column=0, columnspan=2, padx=10, pady=5, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', bd=4, bg=self.FRAME_BG, fg=self.FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='both', expand=True)
        self.console_widget = scrolledtext.ScrolledText(frame, height=10, state='disabled', bg='#1C2833', fg='#EAECEE', font=('Consolas', 10))
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Scan for instruments.")

    def create_graph_frame(self):
        """Builds the right-side panel for R, V, and T vs. Time graphs."""
        frame = LabelFrame(self.root, text='Live Graphs', bd=4, bg='white', fg=self.FRAME_BG, font=self.FONT_TITLE)
        frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        self.figure = Figure(figsize=(8, 9), dpi=100)
        self.ax1, self.ax2, self.ax3 = self.figure.subplots(3, 1, sharex=True)

        self.line_r, = self.ax1.plot([], [], color=self.COLOR_R, marker='.', markersize=4)
        self.line_v, = self.ax2.plot([], [], color=self.COLOR_V, marker='.', markersize=4)
        self.line_t, = self.ax3.plot([], [], color=self.COLOR_T, marker='.', markersize=4)

        self.ax1.set_ylabel("Resistance (Ohms)"); self.ax1.grid(True)
        self.ax2.set_ylabel("Voltage (V)"); self.ax2.grid(True)
        self.ax3.set_ylabel("Temperature (K)"); self.ax3.set_xlabel("Time (s)"); self.ax3.grid(True)

        self.figure.tight_layout(pad=2.0)
        self.canvas = FigureCanvasTkAgg(self.figure, frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

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

            if not all([params['sample_name'], hasattr(self, 'file_location_path'),
                        self.file_location_path, params['keithley_visa'], params['lakeshore_visa']]):
                raise ValueError("All fields and VISA addresses are required.")

            self.backend.initialize_instruments(params)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_Delta_Temp.dat"
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

            self.line_r.set_data([], []); self.line_v.set_data([], []); self.line_t.set_data([], [])
            self.ax1.set_title(f"Sample: {params['sample_name']} | Current: {params['apply_current']} A")
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

            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"{elapsed_time:.3f}", f"{voltage:.8f}", f"{resistance:.8f}", f"{temperature:.4f}"])

            # It's more efficient to append to lists than to reload the file each time for plotting
            data = np.loadtxt(self.data_filepath, delimiter=',', skiprows=3)
            if data.ndim == 1: data = data.reshape(1, -1)

            times, voltages, resistances, temperatures = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
            self.line_r.set_data(times, resistances)
            self.line_v.set_data(times, voltages)
            self.line_t.set_data(times, temperatures)

            self.ax1.relim(); self.ax1.autoscale_view()
            self.ax2.relim(); self.ax2.autoscale_view()
            self.ax3.relim(); self.ax3.autoscale_view()
            self.canvas.draw()

        except Exception:
            self.log(f"\n--- AN ERROR OCCURRED ---\n{traceback.format_exc()}")
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "An error occurred. Check the console.")

        if self.is_running:
            self.root.after(1000, self._update_measurement_loop)

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
                # Try to pre-select common addresses
                for res in resources:
                    if "13" in res: self.keithley_combobox.set(res)
                    if "15" in res or "12" in res: self.lakeshore_combobox.set(res)
                if not self.keithley_combobox.get(): self.keithley_combobox.set(resources[0])
                if not self.lakeshore_combobox.get(): self.lakeshore_combobox.set(resources[-1])
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
