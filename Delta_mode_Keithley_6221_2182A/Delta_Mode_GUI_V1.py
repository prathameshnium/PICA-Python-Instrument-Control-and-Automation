#-------------------------------------------------------------------------------
# Name:         Delta Mode Time-Series Logger
# Purpose:      Set a fixed delta current and measure voltage and resistance
#               as a function of time.
#
# Author:       Prathamesh Deshmukh
#
# Created:      03/10/2025
#
# Version:      2.0 (Major Revision: Sweep to Time-Series)
#
# Description:  Converted the application from an I-V sweep to a time-series
#               logger. The GUI and backend are now simplified to set a single
#               delta current and record data at regular intervals.
#-------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
import os
import time
import traceback
from datetime import datetime
import csv
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

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Backend_IV_Delta:
    """
    Handles the Keithley 6221 for a fixed-current, time-series measurement.
    """
    def __init__(self):
        self.keithley = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")

    def connect(self, visa_address):
        """Establishes the VISA connection to the instrument."""
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        print("\n--- [Backend] Connecting to Keithley 6221 ---")
        try:
            self.keithley = self.rm.open_resource(visa_address)
            self.keithley.timeout = 25000
            print(f"  Connected to: {self.keithley.query('*IDN?').strip()}")
            return True
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect to the instrument. {e}")
            raise e

    def configure_and_run(self, compliance_v, delta_current):
        """Configures the instrument for a continuous delta measurement."""
        if not self.keithley:
            raise ConnectionError("Instrument not connected. Cannot configure.")
        print("--- [Backend] Configuring for Time-Series Measurement ---")
        self.keithley.write("*rst; status:preset; *cls")
        self.keithley.write(f"SOUR:DELT:PROT {compliance_v}")
        print(f"  Compliance set to {compliance_v} V.")
        self.keithley.write(f"SOUR:DELT:HIGH {delta_current}")
        print(f"  Delta current set to {delta_current:.4e} A.")
        self.keithley.write("SOUR:DELT:ARM")
        time.sleep(0.1)
        self.keithley.write("INIT:IMM")
        time.sleep(0.1)
        print("--- [Backend] System Armed. Measurement running. ---")

    def read_data(self):
        """Queries for the latest available measurement data."""
        if not self.keithley:
            raise ConnectionError("Keithley 6221 is not connected.")
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        data_points = raw_data.strip().split(',')
        voltage = float(data_points[0])
        return voltage

    def close(self):
        """Safely shuts down the source, clears the bus, and closes the connection."""
        print("--- [Backend] Shutting down instrument ---")
        if self.keithley:
            try:
                self.keithley.write("SOUR:CLE")
                print("  Source is OFF.")
                time.sleep(0.1)
                self.keithley.clear()
                print("  VISA interface cleared.")
                self.keithley.write("*RST")
                self.keithley.close()
                print("  Connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning during shutdown: {e}")
            finally:
                self.keithley = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Delta_IV_GUI:
    PROGRAM_VERSION = "2.0"
    LOGO_SIZE = 110
    LOGO_FILE_PATH = "UGC_DAE_CSR.jpeg"

    # --- Theming and Styling ---
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'; CLR_ACCENT_GOLD = '#FFC107'; CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'; CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11; FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Delta Mode Time-Series Logger")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.start_time = 0
        self.measurement_interval_ms = 1000
        self.backend = Backend_IV_Delta()
        self.data_storage = {'time': [], 'voltage': [], 'resistance': []}
        self.logo_image = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
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
        mpl.rcParams['font.family'] = 'Segoe UI'; mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4; mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
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
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        Label(header, text="Delta Mode Time-Series Logger", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=10)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e: self.log(f"ERROR: Failed to load logo. {e}")
        info_text = "Measurement: V & R vs. Time\nInstrument:\n  • Keithley 6221/2182A"
        ttk.Label(frame, text=info_text, justify='left').grid(row=0, column=1, rowspan=2, padx=10, sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Measurement Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady = (5, 5); padx = 10

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=padx, pady=pady, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky='ew')

        Label(frame, text="Delta Current (A):").grid(row=2, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Delta Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Delta Current"].insert(0, "1E-5")
        self.entries["Delta Current"].grid(row=3, column=0, padx=(padx, 5), pady=(0, 5), sticky='ew')

        Label(frame, text="Compliance (V):").grid(row=2, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].insert(0, "10")
        self.entries["Compliance"].grid(row=3, column=1, padx=(5, padx), pady=(0, 5), sticky='ew')

        Label(frame, text="Interval (s):").grid(row=4, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Interval"] = Entry(frame, font=self.FONT_BASE); self.entries["Interval"].insert(0, "1")
        self.entries["Interval"].grid(row=5, column=0, columnspan=2, padx=padx, pady=(0, 5), sticky='ew')

        Label(frame, text="Keithley 6221 VISA:").grid(row=6, column=0, columnspan=2, padx=padx, pady=(10,5), sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(row=7, column=0, columnspan=2, padx=padx, pady=(0,10), sticky='ew')

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=8, column=0, columnspan=2, padx=padx, pady=4, sticky='ew')
        self.file_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=9, column=0, columnspan=2, padx=padx, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Logging", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=10, column=0, padx=(padx,5), pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=10, column=1, padx=(5,padx), pady=(10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized.")
        return frame

    def create_graph_frame(self, parent):
        container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_TEXT_DARK, font=self.FONT_TITLE)
        container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        gs = gridspec.GridSpec(2, 1, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0])
        self.ax_sub = self.figure.add_subplot(gs[1])

        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Voltage vs. Time", fontweight='bold'); self.ax_main.set_ylabel("Voltage (V)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)

        self.line_sub, = self.ax_sub.plot([], [], color=self.CLR_ACCENT_GREEN, marker='s', markersize=4, linestyle=':')
        self.ax_sub.set_xlabel("Elapsed Time (s)"); self.ax_sub.set_ylabel("Resistance (Ω)")
        self.ax_sub.grid(True, which="both", linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def start_measurement(self):
        try:
            self.params = {
                'name': self.entries["Sample Name"].get(),
                'delta_i': float(self.entries["Delta Current"].get()),
                'compliance': float(self.entries["Compliance"].get()),
                'interval': float(self.entries["Interval"].get()),
                'visa': self.keithley_cb.get()
            }
            if not all(self.params.values()) or not hasattr(self, 'save_path'):
                raise ValueError("All fields, VISA address, and save location are required.")
            if self.params['interval'] <= 0: raise ValueError("Interval must be positive.")
            self.measurement_interval_ms = int(self.params['interval'] * 1000)

            self.backend.connect(self.params['visa'])
            self.backend.configure_and_run(self.params['compliance'], self.params['delta_i'])

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.params['name']}_{ts}_Delta_Time.dat"
            self.data_filepath = os.path.join(self.save_path, filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {self.params['name']}", f"Delta Current: {self.params['delta_i']:.2e}A"])
                writer.writerow(["Elapsed Time (s)", "Measured Voltage (V)", "Resistance (Ohm)"])
            self.log(f"Output file created: {filename}")

            self.is_running, self.start_time = True, time.time()
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub]: line.set_data([], [])
            self.ax_main.set_title(f"Live Data: {self.params['name']}", fontweight='bold')
            self.canvas.draw()
            self.log("Starting data logging...")
            self.root.after(self.measurement_interval_ms, self._update_logging_loop)

        except Exception as e:
            self.log(f"ERROR on startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start logging.\n{e}")
            self.backend.close()

    def stop_measurement(self, completed=False):
        if self.is_running:
            self.is_running = False
            self.log("Logging stopped by user.")
            self.backend.close()
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            messagebox.showinfo("Info", "Logging stopped.")

    def _update_logging_loop(self):
        if not self.is_running: return
        try:
            voltage = self.backend.read_data()
            elapsed_time = time.time() - self.start_time
            current = self.params['delta_i']
            resistance = voltage / current if current != 0 else float('inf')

            self.log(f"Time: {elapsed_time:.2f}s, Voltage: {voltage:.6e}V, Resistance: {resistance:.6e}Ω")

            self.data_storage['time'].append(elapsed_time)
            self.data_storage['voltage'].append(voltage)
            self.data_storage['resistance'].append(resistance)
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow([f"{elapsed_time:.4f}", f"{voltage:.6e}", f"{resistance:.6e}"])

            self.line_main.set_data(self.data_storage['time'], self.data_storage['voltage'])
            self.line_sub.set_data(self.data_storage['time'], self.data_storage['resistance'])
            for ax in [self.ax_main, self.ax_sub]:
                ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw()

            self.root.after(self.measurement_interval_ms, self._update_logging_loop)

        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "A critical error occurred. Logging stopped.")

    def _scan_for_visa_instruments(self):
        if not pyvisa or self.backend.rm is None:
            self.log("ERROR: PyVISA not found or failed to initialize."); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_cb['values'] = resources
                for res in resources:
                    if "GPIB" in res and "13" in res: self.keithley_cb.set(res)
            else: self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"Could not scan for instruments: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.save_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Logging is active. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.backend.close()
            self.root.destroy()

def main():
    root = tk.Tk()
    app = Delta_IV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()