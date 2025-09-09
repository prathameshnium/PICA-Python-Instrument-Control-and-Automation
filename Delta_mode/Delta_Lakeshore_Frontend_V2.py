# -------------------------------------------------------------------------------
# Name:         Resistance vs Temperature GUI
# Purpose:      Perform a temperature-dependent Delta mode measurement with a
#               Keithley 6221/2182A and Lakeshore 350.
# Author:       Prathamesh
# Created:      09/09/2025
# Version:      4.0 (Professional Layout & Aesthetics)
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
            self.keithley.timeout = 25000
            print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
            self.keithley.write("*rst; status:preset; *cls")
            self.keithley.write(f"SOUR:DELT:HIGH {self.params['apply_current']}")
            self.keithley.write("SOUR:DELT:ARM")
            time.sleep(1)
            self.keithley.write("INIT:IMM")
            print("  Keithley 6221/2182A Configured and Armed for Delta Mode.")

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
    # --- Theming and Styling ---
    PROGRAM_VERSION = "4.0"
    # Colors
    CLR_BG_MAIN = '#EDF2F4'
    CLR_FRAME_BG = '#2B3D4F'
    CLR_FRAME_FG = '#EDF2F4'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_CONSOLE_FG = '#DDE8EE'
    # Fonts
    FONT_NORMAL = ('Segoe UI', 11)
    FONT_BOLD = ('Segoe UI', 11, 'bold')
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_HEADER = ('Segoe UI', 12, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Resistance vs. Temperature Measurement")
        self.root.geometry("1450x900")
        self.root.configure(bg=self.CLR_BG_MAIN)
        self.root.minsize(1200, 800)

        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'voltage': [], 'resistance': [], 'temperature': []}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')

        style.configure('TFrame', background=self.CLR_BG_MAIN)
        style.configure('Header.TFrame', background=self.CLR_FRAME_BG)
        style.configure('TLabel', background=self.CLR_BG_MAIN, foreground=self.CLR_FRAME_BG, font=self.FONT_NORMAL)
        style.configure('Header.TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FRAME_FG, font=self.FONT_HEADER)
        style.configure('Info.TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FRAME_FG, font=self.FONT_NORMAL)

        style.configure('TButton', font=self.FONT_BOLD, padding=(10, 8))
        style.map('TButton',
                  foreground=[('!active', self.CLR_FRAME_BG), ('active', self.CLR_FRAME_FG)],
                  background=[('!active', self.CLR_ACCENT_BLUE), ('active', self.CLR_FRAME_BG)])

        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        header_frame = ttk.Frame(self.root, style='Header.TFrame')
        header_frame.pack(side='top', fill='x')

        left_panel = ttk.Frame(self.root, width=420, style='TFrame')
        left_panel.pack(side='left', fill='y', padx=(10, 5), pady=10)
        left_panel.pack_propagate(False)

        right_panel = ttk.Frame(self.root, style='TFrame')
        right_panel.pack(side='right', fill='both', expand=True, padx=(5, 10), pady=10)

        self.create_header(header_frame)
        self.create_info_frame(left_panel)
        self.create_input_frame(left_panel)
        self.create_console_frame(left_panel)
        self.create_graph_frame(right_panel)

    def create_header(self, parent):
        ttk.Label(parent, text="Resistance vs Temperature Measurement System", style='Header.TLabel').pack(side='left', padx=20, pady=10)
        ttk.Label(parent, text=f"Version: {self.PROGRAM_VERSION}", style='Header.TLabel').pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', bd=2, relief='groove', bg=self.CLR_FRAME_BG, fg=self.CLR_FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=(0, 10), padx=10, fill='x')

        logo_canvas = Canvas(frame, width=80, height=80, bg=self.CLR_FRAME_FG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, padx=15, pady=20, rowspan=2)
        logo_canvas.create_text(42, 42, text="LOGO", font=self.FONT_TITLE, fill=self.CLR_FRAME_BG)

        info_text = (
            "Institute: UGC DAE CSR, Mumbai\n"
            "Measurement: 4-Probe Resistance (Delta Mode)\n\n"
            "Instruments:\n"
            "  • Keithley 6221/2182A (VISA: 13)\n"
            "  • Lakeshore 350 (VISA: 15)"
        )
        ttk.Label(frame, text=info_text, style='Info.TLabel', justify='left', wraplength=250).grid(row=0, column=1, pady=20, sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', bd=2, relief='groove', bg=self.CLR_FRAME_BG, fg=self.CLR_FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        self.entries = {}
        fields = ["Sample Name", "Apply Current (A)"]
        for i, field_text in enumerate(fields):
            Label(frame, text=f"{field_text}:", font=self.FONT_NORMAL, fg=self.CLR_FRAME_FG, bg=self.CLR_FRAME_BG).grid(row=i, column=0, padx=10, pady=10, sticky='w')
            entry = Entry(frame, width=25, font=self.FONT_NORMAL)
            entry.grid(row=i, column=1, padx=10, pady=10, sticky='ew')
            self.entries[field_text] = entry

        current_row = len(fields)
        Label(frame, text="Keithley 6221 VISA:", font=self.FONT_NORMAL, fg=self.CLR_FRAME_FG, bg=self.CLR_FRAME_BG).grid(row=current_row, column=0, padx=10, pady=10, sticky='w')
        self.keithley_combobox = ttk.Combobox(frame, font=self.FONT_NORMAL, state='readonly')
        self.keithley_combobox.grid(row=current_row, column=1, padx=10, pady=10, sticky='ew')

        current_row += 1
        Label(frame, text="Lakeshore 350 VISA:", font=self.FONT_NORMAL, fg=self.CLR_FRAME_FG, bg=self.CLR_FRAME_BG).grid(row=current_row, column=0, padx=10, pady=10, sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, font=self.FONT_NORMAL, state='readonly')
        self.lakeshore_combobox.grid(row=current_row, column=1, padx=10, pady=10, sticky='ew')

        current_row += 1
        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=current_row, column=0, columnspan=2, padx=10, pady=(15,5), sticky='ew')

        current_row += 1
        Label(frame, text="Save Location:", font=self.FONT_NORMAL, fg=self.CLR_FRAME_FG, bg=self.CLR_FRAME_BG).grid(row=current_row, column=0, padx=10, pady=10, sticky='w')
        self.file_location_button = ttk.Button(frame, text="Browse...", command=self._browse_file_location)
        self.file_location_button.grid(row=current_row, column=1, padx=10, pady=10, sticky='ew')

        current_row += 1
        self.start_button = ttk.Button(frame, text="Start", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=current_row, column=0, padx=10, pady=20, sticky='ew')

        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=current_row, column=1, padx=10, pady=20, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', bd=2, relief='groove', bg=self.CLR_FRAME_BG, fg=self.CLR_FRAME_FG, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='both', expand=True) # This makes it fill remaining space
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_CONSOLE_FG, font=self.FONT_CONSOLE, wrap='word')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Please scan for instruments.")

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', bd=2, relief='groove', bg='white', fg=self.CLR_FRAME_BG, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True)

        controls_frame = tk.Frame(graph_container, bg='white')
        controls_frame.pack(fill='x', pady=5, padx=10)

        self.x_scale_var, self.y_scale_var = tk.StringVar(value="linear"), tk.StringVar(value="linear")
        Label(controls_frame, text="X-Axis:", font=self.FONT_BOLD, bg='white').pack(side='left', padx=(10, 5))
        ttk.Radiobutton(controls_frame, text="Linear", variable=self.x_scale_var, value="linear", command=self._update_plot_scales).pack(side='left')
        ttk.Radiobutton(controls_frame, text="Log", variable=self.x_scale_var, value="log", command=self._update_plot_scales).pack(side='left')
        Label(controls_frame, text="Y-Axis:", font=self.FONT_BOLD, bg='white').pack(side='left', padx=(30, 5))
        ttk.Radiobutton(controls_frame, text="Linear", variable=self.y_scale_var, value="linear", command=self._update_plot_scales).pack(side='left')
        ttk.Radiobutton(controls_frame, text="Log", variable=self.y_scale_var, value="log", command=self._update_plot_scales).pack(side='left')

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor='white')
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=5, linestyle='')
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_BLUE, marker='.', markersize=5)
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=5)

        for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]: ax.grid(True, linestyle='--', alpha=0.7)
        self.ax_main.set_title("Resistance vs. Temperature", fontdict={'fontsize': 16, 'fontweight': 'bold'})
        self.ax_main.set_xlabel("Temperature (K)"); self.ax_main.set_ylabel("Resistance (Ohms)")
        self.ax_sub1.set_xlabel("Temperature (K)"); self.ax_sub1.set_ylabel("Voltage (V)")
        self.ax_sub2.set_xlabel("Time (s)"); self.ax_sub2.set_ylabel("Temperature (K)")

        self.figure.tight_layout(pad=3.0)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

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
            params['apply_current'] = float(self.entries["Apply Current (A)"].get())
            params['keithley_visa'] = self.keithley_combobox.get()
            params['lakeshore_visa'] = self.lakeshore_combobox.get()

            if not all([params['sample_name'], self.file_location_path, params['keithley_visa'], params['lakeshore_visa']]):
                raise ValueError("All fields, VISA addresses, and a save location are required.")

            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_R_vs_T.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample Name: {params['sample_name']}"])
                writer.writerow([f"# Applied Current (A): {params['apply_current']}"])
                writer.writerow(["Time (s)", "Voltage (V)", "Resistance (Ohms)", "Temperature (K)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')

            for key in self.data_storage: self.data_storage[key].clear()
            self.line_main.set_data([], []); self.line_sub1.set_data([], []); self.line_sub2.set_data([], [])
            self.ax_main.set_title(f"Sample: {params['sample_name']} | Current: {params['apply_current']} A", fontdict={'fontsize': 16, 'fontweight': 'bold'})
            self.canvas.draw()

            self.log("Measurement loop started.")
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
            self.log("Instrument connections closed.")
            messagebox.showinfo("Info", "Measurement stopped and instruments disconnected.")

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            resistance, voltage, temperature = self.backend.get_measurement()
            elapsed_time = time.time() - self.start_time

            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"{elapsed_time:.3f}", f"{voltage:.8f}", f"{resistance:.8f}", f"{temperature:.4f}"])

            self.data_storage['time'].append(elapsed_time)
            self.data_storage['voltage'].append(voltage)
            self.data_storage['resistance'].append(resistance)
            self.data_storage['temperature'].append(temperature)

            self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])
            self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['voltage'])
            self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature'])

            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
                ax.relim()
                ax.autoscale_view()
            self.canvas.draw()

        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "An error occurred during measurement. Check the console.")

        if self.is_running:
            self.root.after(1000, self._update_measurement_loop)

    def _update_plot_scales(self):
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
