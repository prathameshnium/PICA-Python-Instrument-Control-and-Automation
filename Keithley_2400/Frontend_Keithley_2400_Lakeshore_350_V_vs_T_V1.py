# -------------------------------------------------------------------------------
# Name:          Temperature Dependent I-V Measurement GUI
# Purpose:       Provide a professional graphical user interface for controlling
#                a Lakeshore 350 and a Keithley 2400.
# Author:        Prathamesh Deshmukh
# Created:       18/09/2025
# Version:       5.2 (Original Theme Restored)
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk
from tkinter import ttk, Entry, Button, filedialog, messagebox, scrolledtext
import numpy as np
import os
import time
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Instrument Control Packages ---
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

#===============================================================================
# BACKEND CLASS - Handles all instrument communication (UNCHANGED)
#===============================================================================
class ExperimentBackend:
    """Manages all communication with the Lakeshore 350 and Keithley 2400."""
    def __init__(self):
        self.lakeshore, self.keithley = None, None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception: self.rm = None
        else: self.rm = None

    def connect_instruments(self, lakeshore_visa, keithley_visa):
        if not self.rm: raise ConnectionError("PyVISA ResourceManager is not available.")
        try:
            self.lakeshore = self.rm.open_resource(lakeshore_visa)
            self.lakeshore.timeout = 10000; self.lakeshore.write('*RST'); time.sleep(0.5)
            self.lakeshore.write('*CLS'); self.lakeshore.write('HTRSET 1,1,2,0,1')
        except Exception as e: raise ConnectionError(f"Failed to connect to Lakeshore 350.\n{e}")
        try:
            if not PYMEASURE_AVAILABLE: raise ImportError("Pymeasure required.")
            self.keithley = Keithley2400(keithley_visa)
            self.keithley.reset()
        except Exception as e: raise ConnectionError(f"Failed to connect to Keithley 2400.\n{e}")

    def set_temperature(self, target_temp_k):
        if self.lakeshore: self.lakeshore.write(f"SETP 1,{target_temp_k}"); self.lakeshore.write("RANGE 1,5")

    def get_temperature(self):
        return float(self.lakeshore.query('KRDG? A').strip()) if self.lakeshore else float('nan')

    def run_iv_sweep(self, iv_params):
        if not self.keithley: return [], []
        current_data, voltage_data = [], []
        max_a, step_a = iv_params['max_current_ua'] * 1e-6, iv_params['step_current_ua'] * 1e-6
        self.keithley.apply_current(); self.keithley.source_current_range = max_a if max_a > 1e-6 else 1e-6
        self.keithley.compliance_voltage = iv_params['compliance_v']
        self.keithley.source_current = 0; self.keithley.enable_source(); self.keithley.measure_voltage(); time.sleep(1)
        fwd = np.arange(0, max_a + step_a, step_a); rev = np.arange(max_a - step_a, 0 - step_a, -step_a)
        sweep = np.concatenate([fwd, rev])
        for i_set in sweep:
            self.keithley.ramp_to_current(i_set, steps=5, pause=0.05); time.sleep(iv_params['delay_s'])
            voltage_data.append(self.keithley.voltage); current_data.append(i_set)
        self.keithley.ramp_to_current(0)
        return current_data, voltage_data

    def shutdown_all(self):
        if self.keithley:
            try: self.keithley.shutdown()
            except: pass
            self.keithley = None
        if self.lakeshore:
            try: self.lakeshore.write("RANGE 1,0"); self.lakeshore.close()
            except: pass
            self.lakeshore = None

#===============================================================================
# FRONTEND CLASS - The Main GUI Application
#===============================================================================
class TemperatureIVGUI:
    PROGRAM_VERSION = "5.2"
    # --- Original Theme Colors Restored ---
    CLR_BG, CLR_HEADER, CLR_FG = '#2B3D4F', '#3A506B', '#EDF2F4'
    CLR_FRAME_BG, CLR_INPUT_BG = '#3A506B', '#4C566A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#A7C957', '#EF233C', '#8D99AE'
    CLR_CONSOLE_BG = '#1E2B38'
    FONT_BASE = ('Segoe UI', 10); FONT_TITLE = ('Segoe UI', 12, 'bold')
    LOGO_FILE = "UGC_DAE_CSR.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title("Temperature Dependent I-V Sweep Control")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG)
        self.root.minsize(1400, 800)
        self.backend = ExperimentBackend(); self.experiment_state = 'idle'
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.log("Application started.")

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG, foreground=self.CLR_FG, font=self.FONT_BASE, bordercolor=self.CLR_ACCENT_BLUE)
        style.configure('TFrame', background=self.CLR_BG)
        style.configure('TPanedWindow', background=self.CLR_BG)
        style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_FG, bordercolor=self.CLR_ACCENT_BLUE, insertcolor=self.CLR_FG)
        style.map('TEntry', background=[('active', self.CLR_INPUT_BG), ('!disabled', self.CLR_INPUT_BG)])
        style.configure('TButton', padding=(10, 8), background=self.CLR_ACCENT_BLUE, foreground=self.CLR_BG)
        style.map('TButton', background=[('active', self.CLR_FG), ('!disabled', self.CLR_ACCENT_BLUE)], foreground=[('active', self.CLR_BG)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)
        style.map('TButton', foreground=[('disabled', '#6e7a91')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG, font=self.FONT_TITLE)
        mpl.rcParams['font.family'] = 'Segoe UI'

    def create_widgets(self):
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=(0,10))
        left_panel = self._create_left_panel(main_pane); main_pane.add(left_panel, weight=2)
        right_panel = self._create_right_panel(main_pane); main_pane.add(right_panel, weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, grid_row=0)
        self._create_params_panel(panel, grid_row=1)
        self._create_control_panel(panel, grid_row=2)
        self._create_console_panel(panel, grid_row=3)
        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(panel, text='Live I-V Curve')
        container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor='white'); self.ax_main = self.figure.add_subplot(111)
        self.ax_main.set_facecolor('#f0f0f0')
        self.ax_main.grid(True, linestyle='--', color='white'); self.ax_main.axhline(0, color='k', lw=0.5); self.ax_main.axvline(0, color='k', lw=0.5)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Waiting for experiment...", fontweight='bold', color=self.CLR_BG)
        self.ax_main.set_xlabel("Current (A)", color=self.CLR_BG); self.ax_main.set_ylabel("Voltage (V)", color=self.CLR_BG)
        self.ax_main.tick_params(colors=self.CLR_BG); self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container); self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def create_header(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', pady=(0, 10))
        ttk.Label(header, text="V vs. T Automated I-V Sweep", style='Header.TLabel', font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        ttk.Label(header, text=f"v{self.PROGRAM_VERSION}", style='Header.TLabel', font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = tk.Canvas(frame, width=80, height=80, bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, padx=10, pady=10)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(self.LOGO_FILE).resize((80, 80), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img); logo_canvas.create_image(40, 40, image=self.logo_image)
            except Exception: pass
        info_text = ("Institute: UGC DAE CSR, Mumbai\nMeasurement: Voltage vs. Temperature\nInstruments: Lakeshore 350 & Keithley 2400")
        ttk.Label(frame, text=info_text, justify='left').grid(row=0, column=1, sticky='w', padx=5)

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent)
        container.grid(row=grid_row, column=0, sticky='new', pady=5)
        container.grid_columnconfigure((0, 1), weight=1)
        self.entries = {}
        temp_frame = ttk.LabelFrame(container, text='Temperature')
        temp_frame.grid(row=0, column=0, sticky='nsew', padx=(0,5))
        temp_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(temp_frame, "Start Temp (K)", "300", 0)
        self._create_entry(temp_frame, "End Temp (K)", "280", 1)
        self._create_entry(temp_frame, "Temp Step (K)", "-5", 2)
        self.lakeshore_combobox = self._create_combobox(temp_frame, "Lakeshore VISA", 3)
        iv_frame = ttk.LabelFrame(container, text='I-V Sweep')
        iv_frame.grid(row=0, column=1, sticky='nsew', padx=(5,0))
        iv_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(iv_frame, "Max Current (µA)", "100", 0)
        self._create_entry(iv_frame, "Step Current (µA)", "5", 1)
        self._create_entry(iv_frame, "Compliance (V)", "10", 2)
        self._create_entry(iv_frame, "Dwell Time (s)", "0.2", 3)
        self.keithley_combobox = self._create_combobox(iv_frame, "Keithley VISA", 4)

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Experiment Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "SampleA_Run1", 0)
        self._create_entry(frame, "Save Location", "", 1, browse=True)
        button_frame = ttk.Frame(frame); button_frame.grid(row=2, column=0, columnspan=3, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0,1,2), weight=1)
        self.start_button = ttk.Button(button_frame, text="Start", style='Start.TButton', command=self.start_experiment)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(button_frame, text="Stop", style='Stop.TButton', state='disabled', command=self.stop_experiment)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        self.scan_button = ttk.Button(button_frame, text="Scan Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=0, column=2, sticky='ew', padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console Output')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG, font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    # --- Core Application Logic (UNCHANGED) ---
    def start_experiment(self):
        try:
            params = self._validate_and_get_params()
            self.base_filename = params['sample_name']; self.file_location_path = params['save_location']
            self.log("Connecting to instruments..."); self.backend.connect_instruments(params['lakeshore_visa'], params['keithley_visa'])
            self.log("All instruments connected successfully.")
            start, end, step = params['start_temp'], params['end_temp'], params['temp_step']
            if step == 0: raise ValueError("Temp Step cannot be zero.")
            self.temp_points = np.arange(start, end + step/2 if step > 0 else end - step/2, step)
            if not len(self.temp_points): raise ValueError("Temperature range results in zero points.")
            self.current_temp_index = 0
            self.set_ui_state(running=True); self.experiment_state = 'set_temp'
            self.log(f"Starting experiment: {len(self.temp_points)} temperature points.")
            self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}"); messagebox.showerror("Start Failed", f"{e}"); self.backend.shutdown_all()

    def stop_experiment(self, reason=""):
        if self.experiment_state == 'idle': return
        self.log(f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.experiment_state = 'idle'; self.backend.shutdown_all(); self.set_ui_state(running=False)
        self.ax_main.set_title("Experiment stopped."); self.canvas.draw()
        if reason: messagebox.showinfo("Experiment Finished", f"Reason: {reason}")

    def _experiment_loop(self):
        if self.experiment_state == 'idle': return
        try:
            temp_setpoint = self.temp_points[self.current_temp_index]
            if self.experiment_state == 'set_temp':
                self.log(f"--- Moving to T point {self.current_temp_index + 1}/{len(self.temp_points)}: {temp_setpoint:.2f} K ---")
                self.backend.set_temperature(temp_setpoint); self.experiment_state = 'stabilizing'
                self.root.after(2000, self._experiment_loop)
            elif self.experiment_state == 'stabilizing':
                temp_now = self.backend.get_temperature()
                self.log(f"Stabilizing... Current: {temp_now:.4f} K")
                if abs(temp_now - temp_setpoint) < 0.1:
                    self.log(f"Temperature stabilized at {temp_now:.4f} K."); self.experiment_state = 'run_sweep'
                    self.root.after(100, self._experiment_loop)
                else:
                    self.root.after(5000, self._experiment_loop)
            elif self.experiment_state == 'run_sweep':
                temp_now = self.backend.get_temperature()
                self.log(f"Starting I-V sweep at {temp_now:.4f} K...")
                self.ax_main.set_title(f"Sweeping at {temp_now:.2f} K..."); self.line_main.set_data([], []); self.canvas.draw()
                currents, voltages = self.backend.run_iv_sweep(self._validate_and_get_params())
                self.log("I-V sweep complete."); self.line_main.set_data(currents, voltages)
                self.ax_main.relim(); self.ax_main.autoscale_view(); self.canvas.draw()
                self._save_data(currents, voltages, temp_now)
                self.current_temp_index += 1
                if self.current_temp_index >= len(self.temp_points):
                    self.stop_experiment("All points measured.")
                else:
                    self.experiment_state = 'set_temp'; self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}"); messagebox.showerror("Runtime Error", f"{e}"); self.stop_experiment("Runtime Error")

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal'); self.console.insert('end', log_msg)
        self.console.see('end'); self.console.config(state='disabled'); print(log_msg.strip())

    def _validate_and_get_params(self):
        try:
            return {'sample_name': self.entries["Sample Name"].get(), 'save_location': self.entries["Save Location"].get(),
                    'start_temp': float(self.entries["Start Temp (K)"].get()), 'end_temp': float(self.entries["End Temp (K)"].get()),
                    'temp_step': float(self.entries["Temp Step (K)"].get()), 'lakeshore_visa': self.lakeshore_combobox.get(),
                    'max_current_ua': float(self.entries["Max Current (µA)"].get()), 'step_current_ua': float(self.entries["Step Current (µA)"].get()),
                    'compliance_v': float(self.entries["Compliance (V)"].get()), 'delay_s': float(self.entries["Dwell Time (s)"].get()),
                    'keithley_visa': self.keithley_combobox.get()}
        except Exception as e: raise ValueError(f"Invalid parameter input: {e}")

    def _save_data(self, currents, voltages, temp):
        filename = f"{self.base_filename}_{temp:.2f}K.txt"
        filepath = os.path.join(self.file_location_path, filename)
        header = f"# Sample: {self.base_filename}\n# Temperature: {temp:.4f} K"
        np.savetxt(filepath, np.array([currents, voltages]).T, fmt='%.8e', delimiter='\t', header=header, comments='')
        self.log(f"Data saved to {filename}")

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        for w in [self.start_button, self.scan_button] + list(self.entries.values()): w.config(state=state)
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa_instruments(self):
        if self.backend.rm is None: self.log("ERROR: VISA library missing."); return
        self.log("Scanning for VISA instruments..."); resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}"); self.lakeshore_combobox['values'] = resources; self.keithley_combobox['values'] = resources
            for r in resources:
                if '12' in r or '13' in r or '15' in r: self.lakeshore_combobox.set(r)
                if '24' in r: self.keithley_combobox.set(r)
        else: self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.entries["Save Location"].delete(0, 'end'); self.entries["Save Location"].insert(0, path)

    def _create_entry(self, parent, label_text, default_value, row, browse=False):
        ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky='w', padx=10, pady=3)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=2 if browse else 1)
        entry.insert(0, default_value); self.entries[label_text] = entry
        if browse:
            btn = ttk.Button(parent, text="...", width=3, command=self._browse_file_location)
            btn.grid(row=row, column=3, sticky='e', padx=(0,10))

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky='w', padx=10, pady=3)
        cb = ttk.Combobox(parent, font=self.FONT_BASE, state='readonly')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.experiment_state != 'idle' and messagebox.askyesno("Exit", "Experiment running. Stop and exit?"):
            self.stop_experiment("Application closed by user."); self.root.destroy()
        elif self.experiment_state == 'idle': self.root.destroy()

if __name__ == '__main__':
    if pyvisa is None: print("FATAL ERROR: PyVISA is not installed. Please run 'pip install pyvisa'")
    else: root = tk.Tk(); app = TemperatureIVGUI(root); root.mainloop()
