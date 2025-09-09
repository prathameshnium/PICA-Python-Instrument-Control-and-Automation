# -------------------------------------------------------------------------------
# Name:         Keithley 2400 I-V Measurement GUI
# Purpose:      Perform an automated I-V sweep using a Keithley 2400.
#               (Grid-based layout for stability)
# Author:       Prathamesh 
# Created:      10/09/2025
# Version:      11.0 (Robust Grid Layout)
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
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    Keithley2400 = None
    PYMEASURE_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

class Keithley2400_IV_Backend:
    """A dedicated class to handle backend communication with the Keithley 2400 for I-V sweeps."""
    def __init__(self):
        self.keithley = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def connect_and_configure(self, visa_address, params):
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure library is required. Please run 'pip install pymeasure'.")

        self.keithley = Keithley2400(visa_address)
        self.keithley.reset()
        self.keithley.use_front_terminals()
        self.keithley.apply_current()
        self.keithley.source_current = 0
        max_abs_current = max(abs(params['max_current']), abs(params['min_current']))
        self.keithley.source_current_range = max_abs_current * 1.05 if max_abs_current > 0 else 1e-5
        self.keithley.compliance_voltage = params['compliance_v']
        self.keithley.enable_source()

    def generate_sweep_points(self, params):
        imin, imax, istep = params['min_current'], params['max_current'], params['step_current']
        forward = np.arange(imin, imax + istep, istep)
        reverse = np.arange(imax, imin - istep, -istep)
        zero_to_max = np.arange(0, imax + istep, istep)
        max_to_min = np.arange(imax, imin - istep, -istep)
        min_to_zero = np.arange(imin, 0 + istep, istep)
        sweep_type = params['sweep_type']
        if sweep_type == "Forward": base_sweep = forward
        elif sweep_type == "Forward & Reverse": base_sweep = np.concatenate([forward, reverse[1:]])
        elif sweep_type == "Hysteresis (0->Max->Min->0)": base_sweep = np.concatenate([zero_to_max, max_to_min[1:], min_to_zero[1:]])
        else: base_sweep = np.array([])
        return np.tile(base_sweep, params['num_loops'])

    def measure_at_current(self, current_setpoint, delay):
        self.keithley.ramp_to_current(current_setpoint, steps=5, pause=0.01)
        time.sleep(delay)
        return self.keithley.voltage

    def shutdown(self):
        if self.keithley:
            try:
                self.keithley.shutdown()
            finally:
                self.keithley = None

class MeasurementAppGUI:
    PROGRAM_VERSION = "11.0"
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#2B3D4F', '#3A506B', '#EDF2F4'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED = '#A7C957', '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    LOGO_FILE = "UGC_DAE_CSR.jpeg"
    LOGO_SIZE = 120

    def __init__(self, root):
        self.root = root
        self.root.title("Keithley 2400 I-V Measurement")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.backend = Keithley2400_IV_Backend()
        self.file_location_path = ""
        self.data_storage = {'current': [], 'voltage': []}
        self.logo_image = None
        self.pre_init_logs = []

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8))
        style.map('TButton', foreground=[('!active', '#2B3D4F'), ('active', '#EDF2F4')],
                  background=[('!active', '#8D99AE'), ('active', '#2B3D4F')])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)
        style.configure('TProgressbar', thickness=25, background=self.CLR_ACCENT_GREEN)
        mpl.rcParams['font.family'] = 'Segoe UI'

    def create_widgets(self):
        self.create_header()

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # --- ROBUST GRID LAYOUT FOR LEFT PANEL ---
        left_panel = ttk.Frame(main_pane)
        # Configure the grid rows/columns for the left_panel itself
        left_panel.grid_rowconfigure(1, weight=1) # Row 1 (console) will expand
        left_panel.grid_columnconfigure(0, weight=1) # The single column will expand

        main_pane.add(left_panel, weight=1)

        right_panel = tk.Frame(main_pane, bg='white')
        main_pane.add(right_panel, weight=3)

        top_controls_frame = ttk.Frame(left_panel)
        self.create_info_frame(top_controls_frame)
        self.create_input_frame(top_controls_frame)

        console_pane = self.create_console_frame(left_panel)

        # Place the frames into the left_panel's grid
        top_controls_frame.grid(row=0, column=0, sticky="ew")
        console_pane.grid(row=1, column=0, sticky="nsew", pady=(10,0))

        self.create_graph_frame(right_panel)

    def create_header(self):
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Keithley 2400 I-V Sweep Measurement", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, padx=15, pady=15, sticky='ns')

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(self.LOGO_FILE).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"WARNING: Could not process logo file: {e}")
                logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill="white", justify='center')
        else:
            self.log(f"WARNING: Logo file '{self.LOGO_FILE}' not found or Pillow not installed.")
            logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill="white", justify='center')

        info_text_frame = ttk.Frame(frame)
        info_text_frame.grid(row=0, column=1, padx=10, sticky='ns')
        info_text = ("Automated I-V sweep using a Keithley 2400.\n\n"
                     "1. Set sweep parameters.\n"
                     "2. Select instrument & save location.\n"
                     "3. Press Start.")
        ttk.Label(info_text_frame, text=info_text, justify='left').pack(pady=20, anchor='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Sweep Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        self.entries = {}
        grid = ttk.Frame(frame); grid.pack(padx=10, pady=10, fill='x')
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ttk.Label(grid, text="Sample Name:").grid(row=0, column=0, columnspan=4, sticky='w')
        self.entries["Sample Name"] = Entry(grid, font=self.FONT_BASE); self.entries["Sample Name"].grid(row=1, column=0, columnspan=4, sticky='ew', pady=(0, 10))

        ttk.Label(grid, text="Min Current (µA):").grid(row=2, column=0, sticky='w'); self.entries["Min Current"] = Entry(grid, font=self.FONT_BASE); self.entries["Min Current"].grid(row=3, column=0, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Max Current (µA):").grid(row=2, column=1, sticky='w'); self.entries["Max Current"] = Entry(grid, font=self.FONT_BASE); self.entries["Max Current"].grid(row=3, column=1, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Step Current (µA):").grid(row=2, column=2, sticky='w'); self.entries["Step Current"] = Entry(grid, font=self.FONT_BASE); self.entries["Step Current"].grid(row=3, column=2, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Loops:").grid(row=2, column=3, sticky='w'); self.entries["Num Loops"] = Entry(grid, font=self.FONT_BASE); self.entries["Num Loops"].grid(row=3, column=3, sticky='ew'); self.entries["Num Loops"].insert(0, "1")

        ttk.Label(grid, text="Compliance (V):").grid(row=4, column=0, columnspan=2, sticky='w', pady=(10, 0)); self.entries["Compliance"] = Entry(grid, font=self.FONT_BASE); self.entries["Compliance"].grid(row=5, column=0, columnspan=2, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Delay (s):").grid(row=4, column=2, columnspan=2, sticky='w', pady=(10, 0)); self.entries["Delay"] = Entry(grid, font=self.FONT_BASE); self.entries["Delay"].grid(row=5, column=2, columnspan=2, sticky='ew'); self.entries["Delay"].insert(0, "0.1")

        ttk.Label(grid, text="Sweep Type:").grid(row=6, column=0, columnspan=4, sticky='w', pady=(10, 0)); self.sweep_type_var = tk.StringVar(); self.sweep_type_cb = ttk.Combobox(grid, textvariable=self.sweep_type_var, state='readonly', font=self.FONT_BASE, values=["Forward", "Forward & Reverse", "Hysteresis (0->Max->Min->0)"]); self.sweep_type_cb.grid(row=7, column=0, columnspan=4, sticky='ew', pady=(0, 10)); self.sweep_type_cb.set("Forward")
        ttk.Label(grid, text="Keithley 2400 VISA:").grid(row=8, column=0, columnspan=4, sticky='w'); self.keithley_combobox = ttk.Combobox(grid, font=self.FONT_BASE, state='readonly'); self.keithley_combobox.grid(row=9, column=0, columnspan=4, sticky='ew', pady=(0, 10))

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments); self.scan_button.pack(padx=10, pady=5, fill='x')
        self.file_location_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location); self.file_location_button.pack(padx=10, pady=5, fill='x')

        bf = ttk.Frame(frame); bf.pack(padx=10, pady=10, fill='x'); bf.grid_columnconfigure((0,1), weight=1)
        self.start_button = ttk.Button(bf, text="Start", command=self.start_measurement, style='Start.TButton'); self.start_button.grid(row=0, column=0, sticky='ew', padx=(0,5))
        self.stop_button = ttk.Button(bf, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled'); self.stop_button.grid(row=0, column=1, sticky='ew')

        self.progress_bar = ttk.Progressbar(frame, orient='horizontal', mode='determinate'); self.progress_bar.pack(padx=10, pady=(5,10), fill='x')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=('Consolas', 10), wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)

        if self.pre_init_logs:
            self.console_widget.config(state='normal')
            for msg in self.pre_init_logs:
                self.console_widget.insert('end', msg)
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')
            self.pre_init_logs = []

        self.log("Console initialized.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live I-V Curve', relief='groove', bg='white', fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.grid(True, linestyle='--', alpha=0.7)
        self.ax_main.axhline(0, color='k', linestyle='--', linewidth=0.7, alpha=0.5)
        self.ax_main.axvline(0, color='k', linestyle='--', linewidth=0.7, alpha=0.5)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Voltage vs. Current", fontweight='bold'); self.ax_main.set_xlabel("Current (A)"); self.ax_main.set_ylabel("Voltage (V)")
        self.figure.tight_layout(pad=2.5)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container); self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        if hasattr(self, 'console_widget'):
            self.console_widget.config(state='normal'); self.console_widget.insert('end', log_line); self.console_widget.see('end'); self.console_widget.config(state='disabled')
        else:
            self.pre_init_logs.append(log_line)

    def start_measurement(self):
        try:
            params = { 'sample_name': self.entries["Sample Name"].get(), 'min_current': float(self.entries["Min Current"].get())*1e-6, 'max_current': float(self.entries["Max Current"].get())*1e-6, 'step_current': float(self.entries["Step Current"].get())*1e-6, 'num_loops': int(self.entries["Num Loops"].get()), 'compliance_v': float(self.entries["Compliance"].get()), 'delay_s': float(self.entries["Delay"].get()), 'sweep_type': self.sweep_type_var.get() }
            visa_address = self.keithley_combobox.get()
            if not all([params['sample_name'], visa_address, self.file_location_path]): raise ValueError("All fields are required.")

            self.backend.connect_and_configure(visa_address, params)
            self.sweep_points = self.backend.generate_sweep_points(params)
            self.log(f"Generated sweep with {len(self.sweep_points)} points.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); file_name = f"{params['sample_name']}_{ts}_IV.dat"; self.data_filepath = os.path.join(self.file_location_path, file_name)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f, delimiter='\t'); writer.writerow([f"# Sample: {params['sample_name']}", f"Compliance: {params['compliance_v']} V"]); writer.writerow(["Current (A)", "Voltage (V)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True; self.sweep_index = 0
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            self.line_main.set_data([], []); self.canvas.draw()
            self.progress_bar['value'] = 0; self.progress_bar['maximum'] = len(self.sweep_points)
            self.ax_main.set_title(f"Sample: {params['sample_name']}", fontweight='bold'); self.canvas.draw()
            self.log("Measurement sweep started.")
            self.root.after(100, self._run_sweep_step)
        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}"); messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}"); self.backend.shutdown()

    def stop_measurement(self):
        if self.is_running: self.is_running = False; self.log("Measurement sweep stopped by user.")
        self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
        self.backend.shutdown(); messagebox.showinfo("Info", "Measurement stopped and instrument disconnected.")

    def _run_sweep_step(self):
        if not self.is_running or self.sweep_index >= len(self.sweep_points):
            if self.is_running: self.log("Sweep complete."); self.stop_measurement()
            return
        try:
            current = self.sweep_points[self.sweep_index]
            voltage = self.backend.measure_at_current(current, float(self.entries["Delay"].get()))
            self.data_storage['current'].append(current); self.data_storage['voltage'].append(voltage)
            with open(self.data_filepath, 'a', newline='') as f: csv.writer(f, delimiter='\t').writerow([f"{current:.8e}", f"{voltage:.8e}"])

            self.line_main.set_data(self.data_storage['current'], self.data_storage['voltage'])
            self.ax_main.relim(); self.ax_main.autoscale_view(); self.canvas.draw()
            self.progress_bar['value'] = self.sweep_index + 1

            self.sweep_index += 1
            self.root.after(10, self._run_sweep_step)
        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}"); messagebox.showerror("Runtime Error", "An error occurred during the sweep. Check console."); self.stop_measurement()

    def _scan_for_visa_instruments(self):
        if pyvisa is None or self.backend.rm is None: self.log("ERROR: PyVISA not found or NI-VISA backend is missing."); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}"); self.keithley_combobox['values'] = resources
                for res in resources:
                    if "24" in res: self.keithley_combobox.set(res)
                if not self.keithley_combobox.get() and resources: self.keithley_combobox.set(resources[0])
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running and messagebox.askyesno("Exit", "Measurement is running. Stop and exit?"):
            self.stop_measurement(); self.root.destroy()
        elif not self.is_running: self.root.destroy()

def main():
    root = tk.Tk()
    app = MeasurementAppGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
