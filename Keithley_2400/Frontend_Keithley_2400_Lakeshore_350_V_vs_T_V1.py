# -------------------------------------------------------------------------------
# Name:          Temperature Dependent I-V Measurement GUI
# Purpose:       Provide a graphical user interface for controlling a Lakeshore
#                350 and a Keithley 2400 to perform automated V vs. T sweeps.
# Author:        Gemini (UI & Interfacing Expert)
# Created:       18/09/2025
# Version:       2.0
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
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
# BACKEND CLASS - Handles all instrument communication
#===============================================================================
class ExperimentBackend:
    """Manages all communication with the Lakeshore 350 and Keithley 2400."""
    def __init__(self):
        self.lakeshore, self.keithley = None, None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception:
                self.rm = None
        else:
            self.rm = None

    def connect_instruments(self, lakeshore_visa, keithley_visa):
        if not self.rm:
            raise ConnectionError("PyVISA ResourceManager is not available. Check installation.")

        # Connect to Lakeshore
        try:
            print(f"Connecting to Lakeshore 350 at {lakeshore_visa}...")
            self.lakeshore = self.rm.open_resource(lakeshore_visa)
            self.lakeshore.timeout = 10000
            self.lakeshore.write('*RST'); time.sleep(0.5)
            self.lakeshore.write('*CLS')
            # Configure heater: 25Ω, 1A max
            self.lakeshore.write('HTRSET 1,1,2,0,1')
            print(f"  Lakeshore connected: {self.lakeshore.query('*IDN?').strip()}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Lakeshore 350.\n{e}")

        # Connect to Keithley
        try:
            if not PYMEASURE_AVAILABLE:
                raise ImportError("Pymeasure is required. Run 'pip install pymeasure'.")
            print(f"Connecting to Keithley 2400 at {keithley_visa}...")
            self.keithley = Keithley2400(keithley_visa)
            self.keithley.reset()
            print("  Keithley 2400 connected.")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Keithley 2400.\n{e}")

    def set_temperature(self, target_temp_k):
        if not self.lakeshore: return
        print(f"Setting temperature to {target_temp_k} K...")
        self.lakeshore.write(f"SETP 1,{target_temp_k}")
        self.lakeshore.write("RANGE 1,5") # Set heater to High

    def get_temperature(self):
        if not self.lakeshore: return float('nan')
        try:
            return float(self.lakeshore.query('KRDG? A').strip())
        except:
            return float('nan')

    def run_iv_sweep(self, iv_params):
        if not self.keithley: return [], []

        current_data, voltage_data = [], []
        max_current_a = iv_params['max_current_ua'] * 1e-6
        step_current_a = iv_params['step_current_ua'] * 1e-6

        # Configure Keithley for this specific sweep
        self.keithley.apply_current()
        self.keithley.source_current_range = max_current_a if max_current_a > 1e-6 else 1e-6
        self.keithley.compliance_voltage = iv_params['compliance_v']
        self.keithley.source_current = 0
        self.keithley.enable_source()
        self.keithley.measure_voltage()
        time.sleep(1)

        # Full sweep pattern: 0 -> +max -> 0
        forward_sweep = np.arange(0, max_current_a + step_current_a, step_current_a)
        reverse_sweep = np.arange(max_current_a - step_current_a, 0 - step_current_a, -step_current_a)
        full_sweep = np.concatenate([forward_sweep, reverse_sweep])

        for i_set in full_sweep:
            self.keithley.ramp_to_current(i_set, steps=5, pause=0.05)
            time.sleep(iv_params['delay_s'])
            v_meas = self.keithley.voltage
            current_data.append(i_set)
            voltage_data.append(v_meas)

        self.keithley.ramp_to_current(0)
        return current_data, voltage_data

    def shutdown_all(self):
        print("--- Shutting down all instruments ---")
        if self.keithley:
            try:
                self.keithley.shutdown()
                print("  Keithley 2400 shutdown successful.")
            except Exception as e:
                print(f"  Error shutting down Keithley: {e}")
            finally:
                self.keithley = None

        if self.lakeshore:
            try:
                self.lakeshore.write("RANGE 1,0") # Turn off heater
                self.lakeshore.close()
                print("  Lakeshore 350 shutdown successful.")
            except Exception as e:
                print(f"  Error shutting down Lakeshore: {e}")
            finally:
                self.lakeshore = None

#===============================================================================
# FRONTEND CLASS - The Main GUI Application
#===============================================================================
class TemperatureIVGUI:
    PROGRAM_VERSION = "2.0"
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#2B3D4F', '#3A506B', '#EDF2F4'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#A7C957', '#EF233C', '#8D99AE'
    CLR_CONSOLE_BG = '#1E2B38'
    FONT_BASE = ('Segoe UI', 12)
    FONT_TITLE = ('Segoe UI', 14, 'bold')
    LOGO_FILE = "UGC_DAE_CSR.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title("Temperature Dependent I-V Sweep Control")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.backend = ExperimentBackend()
        self.experiment_state = 'idle'
        self.temp_points = []
        self.current_temp_index = 0
        self.file_location_path = ""
        self.base_filename = ""

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.log("Application started. Configure parameters and scan for instruments.")

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8))
        style.map('TButton', background=[('!active', self.CLR_ACCENT_BLUE), ('active', self.CLR_BG_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)
        mpl.rcParams['font.family'] = 'Segoe UI'

    def create_widgets(self):
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(main_pane, width=500); main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg='white'); main_pane.add(right_panel, weight=3)

        # --- Left Panel Layout ---
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        controls_frame = ttk.Frame(left_panel)
        controls_frame.grid(row=0, column=0, sticky='new')

        self.create_info_frame(controls_frame)
        self.create_input_frames(controls_frame)

        console_frame = self.create_console_frame(left_panel)
        console_frame.grid(row=1, column=0, sticky='nsew', pady=(10,0))

        # --- Right Panel ---
        self.create_graph_frame(right_panel)

    # --- Widget Creation Methods (structured and clean) ---
    def create_header(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        Label(header, text="V vs. T Automated I-V Sweep", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header, text=f"v{self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        # ... [Logo and info text similar to examples] ...
        info_text = ("Automated V vs. T Measurement\n"
                     "Instruments:\n"
                     " • Lakeshore Model 350 (Temperature)\n"
                     " • Keithley 2400 (I-V Sweep)")
        Label(frame, text=info_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE, justify='left').pack(padx=20, pady=20)


    def create_input_frames(self, parent):
        self.entries = {}

        # General Experiment Frame
        exp_frame = LabelFrame(parent, text='Experiment Control', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        exp_frame.pack(pady=10, padx=10, fill='x')
        self._create_entry(exp_frame, "Sample Name", "SampleA_Run1")
        self._create_entry(exp_frame, "Save Location", "", browse=True)

        # Temperature Frame
        temp_frame = LabelFrame(parent, text='Temperature Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        temp_frame.pack(pady=10, padx=10, fill='x')
        self._create_entry(temp_frame, "Start Temp (K)", "300")
        self._create_entry(temp_frame, "End Temp (K)", "280")
        self._create_entry(temp_frame, "Temp Step (K)", "-5")
        self.lakeshore_combobox = self._create_combobox(temp_frame, "Lakeshore VISA")

        # IV Sweep Frame
        iv_frame = LabelFrame(parent, text='I-V Sweep Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        iv_frame.pack(pady=10, padx=10, fill='x')
        self._create_entry(iv_frame, "Max Current (µA)", "100")
        self._create_entry(iv_frame, "Step Current (µA)", "5")
        self._create_entry(iv_frame, "Compliance (V)", "10")
        self._create_entry(iv_frame, "Dwell Time (s)", "0.2")
        self.keithley_combobox = self._create_combobox(iv_frame, "Keithley VISA")

        # Control Buttons
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill='x', padx=10, pady=10)
        control_frame.grid_columnconfigure((0,1,2), weight=1)
        self.scan_button = ttk.Button(control_frame, text="Scan Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=0, column=0, padx=5, sticky='ew')
        self.start_button = ttk.Button(control_frame, text="Start", command=self.start_experiment, style='Start.TButton')
        self.start_button.grid(row=0, column=1, padx=5, sticky='ew')
        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_experiment, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=0, column=2, padx=5, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=('Consolas', 10), wrap='word')
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live I-V Curve', relief='groove', bg='white', fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.grid(True, linestyle='--'); self.ax_main.axhline(0, color='k', lw=0.5); self.ax_main.axvline(0, color='k', lw=0.5)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Waiting for experiment to start...", fontweight='bold')
        self.ax_main.set_xlabel("Current (A)"); self.ax_main.set_ylabel("Voltage (V)")
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

    # --- Core Application Logic ---
    def start_experiment(self):
        try:
            # 1. Validate parameters
            params = self._validate_and_get_params()
            self.base_filename = params['sample_name']
            self.file_location_path = params['save_location']

            # 2. Connect to instruments
            self.log("Connecting to instruments...")
            self.backend.connect_instruments(params['lakeshore_visa'], params['keithley_visa'])
            self.log("All instruments connected successfully.")

            # 3. Prepare for experiment loop
            start, end, step = params['start_temp'], params['end_temp'], params['temp_step']
            self.temp_points = np.arange(start, end + step/2 if step > 0 else end - step/2, step)
            if not len(self.temp_points): raise ValueError("Temperature range and step result in zero points.")

            self.current_temp_index = 0
            self.set_ui_state(running=True)
            self.experiment_state = 'set_temp'
            self.log(f"Starting experiment with {len(self.temp_points)} temperature points.")

            # 4. Start the state machine loop
            self.root.after(100, self._experiment_loop)

        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Experiment Start Failed", f"Could not start the experiment.\n\n{e}")
            self.backend.shutdown_all()

    def stop_experiment(self, reason=""):
        if self.experiment_state == 'idle': return
        self.log(f"Stopping experiment... Reason: {reason}" if reason else "Stopping experiment by user request.")
        self.experiment_state = 'idle'
        self.backend.shutdown_all()
        self.set_ui_state(running=False)
        self.ax_main.set_title("Experiment stopped. Ready for new run.", fontweight='bold')
        self.canvas.draw()
        if reason:
            messagebox.showinfo("Experiment Finished", f"The experiment has finished.\n\nReason: {reason}")

    def _experiment_loop(self):
        if self.experiment_state == 'idle': return

        try:
            current_temp_setpoint = self.temp_points[self.current_temp_index]

            # --- State: SET_TEMP ---
            if self.experiment_state == 'set_temp':
                self.log(f"--- Moving to Temperature Point {self.current_temp_index + 1}/{len(self.temp_points)}: {current_temp_setpoint:.2f} K ---")
                self.backend.set_temperature(current_temp_setpoint)
                self.experiment_state = 'stabilizing'
                self.root.after(2000, self._experiment_loop) # Wait 2s before first check

            # --- State: STABILIZING ---
            elif self.experiment_state == 'stabilizing':
                temp_now = self.backend.get_temperature()
                self.log(f"Stabilizing at {current_temp_setpoint:.2f} K... Current temp: {temp_now:.4f} K")
                if abs(temp_now - current_temp_setpoint) < 0.1: # Stability tolerance
                    self.log(f"Temperature stabilized at {temp_now:.4f} K.")
                    self.experiment_state = 'run_sweep'
                    self.root.after(100, self._experiment_loop)
                else:
                    self.root.after(5000, self._experiment_loop) # Check again in 5s

            # --- State: RUN_SWEEP ---
            elif self.experiment_state == 'run_sweep':
                temp_now = self.backend.get_temperature()
                self.log(f"Starting I-V sweep at {temp_now:.4f} K...")
                self.ax_main.set_title(f"Sweeping I-V at {temp_now:.2f} K...", fontweight='bold')
                self.line_main.set_data([], []); self.canvas.draw()

                iv_params = self._validate_and_get_params() # Get fresh params
                currents, voltages = self.backend.run_iv_sweep(iv_params)
                self.log("I-V sweep complete.")

                # Plot and save data
                self.line_main.set_data(currents, voltages)
                self.ax_main.relim(); self.ax_main.autoscale_view(); self.canvas.draw()
                self._save_data(currents, voltages, temp_now)

                # Move to next temperature or finish
                self.current_temp_index += 1
                if self.current_temp_index >= len(self.temp_points):
                    self.stop_experiment("All temperature points measured.")
                else:
                    self.experiment_state = 'set_temp'
                    self.root.after(100, self._experiment_loop)

        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            messagebox.showerror("Runtime Error", f"A critical error occurred. Experiment will stop.\n\n{e}")
            self.stop_experiment("Runtime Error")

    # --- Helper & UI Methods ---
    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')
        print(f"[{ts}] {message}")

    def _validate_and_get_params(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'save_location': self.entries["Save Location"].get(),
                'start_temp': float(self.entries["Start Temp (K)"].get()),
                'end_temp': float(self.entries["End Temp (K)"].get()),
                'temp_step': float(self.entries["Temp Step (K)"].get()),
                'lakeshore_visa': self.lakeshore_combobox.get(),
                'max_current_ua': float(self.entries["Max Current (µA)"].get()),
                'step_current_ua': float(self.entries["Step Current (µA)"].get()),
                'compliance_v': float(self.entries["Compliance (V)"].get()),
                'delay_s': float(self.entries["Dwell Time (s)"].get()),
                'keithley_visa': self.keithley_combobox.get()
            }
            if not all([params['sample_name'], params['save_location'], params['lakeshore_visa'], params['keithley_visa']]):
                raise ValueError("Sample name, save location, and VISA addresses cannot be empty.")
            return params
        except ValueError as e:
            raise ValueError(f"Invalid parameter input. Please check all fields. Details: {e}")

    def _save_data(self, currents, voltages, temp):
        filename = f"{self.base_filename}_{temp:.2f}K.txt"
        filepath = os.path.join(self.file_location_path, filename)
        header = f"# Sample: {self.base_filename}\n# Temperature: {temp:.4f} K\n"
        data = np.array([currents, voltages]).T
        np.savetxt(filepath, data, fmt='%.8e', delimiter='\t', header=header, comments='')
        self.log(f"Data saved to {filename}")

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        for widget in [self.start_button, self.scan_button] + list(self.entries.values()):
            widget.config(state=state)
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa_instruments(self):
        if self.backend.rm is None: self.log("ERROR: VISA library not available."); return
        self.log("Scanning for VISA instruments...")
        resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}")
            self.lakeshore_combobox['values'] = resources
            self.keithley_combobox['values'] = resources
            for r in resources:
                if '12' in r or '13' in r or '15' in r: self.lakeshore_combobox.set(r)
                if '24' in r: self.keithley_combobox.set(r)
        else:
            self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.entries["Save Location"].delete(0, 'end')
            self.entries["Save Location"].insert(0, path)
            self.log(f"Save location set to: {path}")

    def _create_entry(self, parent, label_text, default_value, browse=False):
        frame = ttk.Frame(parent)
        frame.pack(fill='x', padx=10, pady=5)
        Label(frame, text=f"{label_text}:", bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='left')

        entry = Entry(frame, font=self.FONT_BASE)
        entry.pack(side='left', expand=True, fill='x', padx=5)
        entry.insert(0, default_value)
        self.entries[label_text] = entry

        if browse:
            btn = ttk.Button(frame, text="...", width=3, command=self._browse_file_location)
            btn.pack(side='right')

    def _create_combobox(self, parent, label_text):
        frame = ttk.Frame(parent)
        frame.pack(fill='x', padx=10, pady=5)
        Label(frame, text=f"{label_text}:", bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='left')
        combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        combobox.pack(side='left', expand=True, fill='x', padx=5)
        return combobox

    def _on_closing(self):
        if self.experiment_state != 'idle':
            if messagebox.askyesno("Exit", "Experiment is running. Are you sure you want to stop and exit?"):
                self.stop_experiment("Application closed by user.")
                self.root.destroy()
        else:
            self.root.destroy()

#===============================================================================
# Main Execution Block
#===============================================================================
if __name__ == '__main__':
    if pyvisa is None:
        print("FATAL ERROR: PyVISA is not installed. Please run 'pip install pyvisa'")
    else:
        root = tk.Tk()
        app = TemperatureIVGUI(root)
        root.mainloop()
