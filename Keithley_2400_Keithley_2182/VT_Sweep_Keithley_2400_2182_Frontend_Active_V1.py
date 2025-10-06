# -------------------------------------------------------------------------------
# Name:         V-T Sweep Active Frontend for K2400/2182 & LS350
# Purpose:      Provide a professional GUI for performing automated V vs T sweeps
#               with active temperature control (stabilize then ramp).
# Author:       Prathamesh Deshmukh (Adapted from VT_Sweep_..._V1.py)
# Created:      05/10/2025
# Version:      1.0
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
import os
import time
import traceback
from datetime import datetime; import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa, Keithley2400 = None, None
    PYMEASURE_AVAILABLE = False

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------
class VT_Backend:
    """ Manages communication with the K2400, K2182, and Lakeshore 350. """
    def __init__(self):
        self.k2400, self.k2182, self.lakeshore = None, None, None
        if pyvisa:
            try: self.rm = pyvisa.ResourceManager()
            except Exception as e: print(f"Could not initialize VISA: {e}"); self.rm = None

    def connect(self, k2400_visa, k2182_visa, ls_visa):
        if not self.rm: raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE: raise ImportError("Pymeasure is not available.")
        self.k2400 = Keithley2400(k2400_visa); print(f"  K2400 Connected: {self.k2400.id}")
        self.k2182 = self.rm.open_resource(k2182_visa); print(f"  K2182 Connected: {self.k2182.query('*IDN?').strip()}")
        self.lakeshore = self.rm.open_resource(ls_visa); print(f"  Lakeshore Connected: {self.lakeshore.query('*IDN?').strip()}")

    def configure_instruments(self, current_ma, compliance_v):
        # Lakeshore setup
        self.lakeshore.write('*RST'); time.sleep(0.5); self.lakeshore.write('*CLS')
        self.lakeshore.write('HTRSET 1,1,2,0,1') # 25Ω heater, 1A max

        # Keithley 2400/2182 setup
        self.k2400.reset(); self.k2400.apply_current()
        self.k2400.source_current_range = abs(current_ma * 1e-3) * 1.05
        self.k2400.compliance_voltage = compliance_v
        self.k2400.source_current = current_ma * 1e-3
        self.k2400.enable_source()
        self.k2182.write("*rst; status:preset; *cls")
        time.sleep(1)

    def stabilize_at_start(self, start_temp, stability_log_callback):
        while True:
            current_temp = float(self.lakeshore.query('KRDG? A').strip())
            if current_temp > start_temp + 0.2:
                stability_log_callback(f"Cooling... Current: {current_temp:.4f} K > Target: {start_temp} K")
                self.lakeshore.write('RANGE 1,0') # Heater Off
            else:
                stability_log_callback(f"Heating... Current: {current_temp:.4f} K <= Target: {start_temp} K")
                self.lakeshore.write('RANGE 1,4') # Heater Medium
                self.lakeshore.write(f'SETP 1,{start_temp}')

            if abs(current_temp - start_temp) < 0.1:
                stability_log_callback(f"Stabilized at {current_temp:.4f} K. Waiting 5s before ramp.")
                time.sleep(5)
                return
            time.sleep(2)

    def start_ramp(self, end_temp, rate_k_min):
        self.lakeshore.write(f'SETP 1,{end_temp}')
        self.lakeshore.write(f'RAMP 1,1,{rate_k_min}')
        self.lakeshore.write('RANGE 1,5') # Heater High for ramp

    def get_measurement(self):
        # K2182 measurement sequence
        self.k2182.write("status:measurement:enable 512; *sre 1")
        self.k2182.write("sample:count 2"); self.k2182.write("trigger:source bus")
        self.k2182.write("trigger:delay 0.1"); self.k2182.write("trace:points 2")
        self.k2182.write("trace:feed sense1; feed:control next"); self.k2182.write("initiate")
        self.k2182.assert_trigger(); self.k2182.wait_for_srq(timeout=10)
        voltages = self.k2182.query_ascii_values("trace:data?")
        self.k2182.query("status:measurement?"); self.k2182.write("trace:clear; feed:control next")
        voltage = sum(voltages) / len(voltages) if voltages else float('nan')

        # Lakeshore temperature reading
        temperature = float(self.lakeshore.query('KRDG? A').strip())
        return temperature, voltage

    def shutdown(self):
        if self.k2400:
            try: self.k2400.shutdown()
            except: pass
        if self.k2182:
            try: self.k2182.write("*rst"); self.k2182.close()
            except: pass
        if self.lakeshore:
            try: self.lakeshore.write("RANGE 1,0"); self.lakeshore.close()
            except: pass
        print("  Instruments shut down and disconnected.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class VT_GUI_Active:
    PROGRAM_VERSION = "1.0"
    CLR_BG = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG = '#EDF2F4'
    CLR_FRAME_BG = '#3A506B'; CLR_INPUT_BG = '#4C566A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#A7C957', '#E74C3C', '#8D99AE'
    CLR_ACCENT_GOLD = '#FFC107'; CLR_CONSOLE_BG = '#1E2B38'
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold')

    def __init__(self, root):
        self.root = root; self.root.title("Active V-T Sweep (K2400/2182 + LS350)")
        self.root.geometry("1600x950"); self.root.minsize(1400, 800); self.root.configure(bg=self.CLR_BG)
        self.experiment_state = 'idle'; self.logo_image = None
        self.backend = VT_Backend(); self.data_storage = {'temperature': [], 'voltage': []}
        self.setup_styles(); self.create_widgets(); self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam')
        style.configure('.', background=self.CLR_BG, foreground=self.CLR_FG, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG); style.configure('TPanedWindow', background=self.CLR_BG)
        style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_FG, insertcolor=self.CLR_FG)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_BG), ('hover', self.CLR_BG)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_BG)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG, font=self.FONT_TITLE)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11, 'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER); header.pack(side='top', fill='x')
        ttk.Label(header, text=f"Active V-T Sweep (K2400/2182) v{self.PROGRAM_VERSION}", style='Header.TLabel', font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        main_pane = ttk.PanedWindow(self.root, orient='horizontal'); main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = self._create_left_panel(main_pane); main_pane.add(left_panel, weight=2)
        right_panel = self._create_right_panel(main_pane); main_pane.add(right_panel, weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, padding=5); panel.grid_columnconfigure(0, weight=1); panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, 0)
        self._create_params_panel(panel, 1); self._create_control_panel(panel, 2); self._create_console_panel(panel, 3)
        return panel

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information'); frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=80, height=80, bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "_assets", "LOGO", "UGC_DAE_CSR.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize((80, 80), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(40, 40, image=self.logo_image)
        except Exception as e: self.log(f"Warning: Could not load logo. {e}")
        info_text = ("Institute: UGC DAE CSR, Mumbai\nMeasurement: V vs. T Sweep (Active)\nInstruments: K2400, K2182, LS350")
        ttk.Label(frame, text=info_text, justify='left').grid(row=0, column=1, rowspan=2, sticky='w', padx=5)

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(panel, text='Live V-T Curve'); container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor='white')
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Waiting for experiment...", fontweight='bold'); self.ax_main.set_xlabel("Temperature (K)"); self.ax_main.set_ylabel("Voltage (V)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6); self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container); self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent); container.grid(row=grid_row, column=0, sticky='new', pady=5)
        container.grid_columnconfigure((0, 1), weight=1); self.entries = {}
        temp_frame = ttk.LabelFrame(container, text='Temperature'); temp_frame.grid(row=0, column=0, sticky='nsew', padx=(0,5))
        temp_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(temp_frame, "Start Temp (K)", "300", 0); self._create_entry(temp_frame, "End Temp (K)", "310", 1)
        self._create_entry(temp_frame, "Ramp Rate (K/min)", "2", 2); self._create_entry(temp_frame, "Safety Cutoff (K)", "320", 3)
        self.ls_cb = self._create_combobox(temp_frame, "Lakeshore VISA", 4)
        iv_frame = ttk.LabelFrame(container, text='I-V Settings'); iv_frame.grid(row=0, column=1, sticky='nsew', padx=(5,0))
        iv_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(iv_frame, "Source Current (mA)", "1", 0); self._create_entry(iv_frame, "Compliance (V)", "10", 1)
        self._create_entry(iv_frame, "Logging Delay (s)", "1", 2)
        self.k2400_cb = self._create_combobox(iv_frame, "Keithley 2400 VISA", 3)
        self.k2182_cb = self._create_combobox(iv_frame, "Keithley 2182 VISA", 4)

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Experiment Control'); frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_VT_Active", 0)
        self._create_entry(frame, "Save Location", "", 1, browse=True)
        button_frame = ttk.Frame(frame); button_frame.grid(row=2, column=0, columnspan=4, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0,1,2), weight=1)
        self.start_button = ttk.Button(button_frame, text="Start", style='Start.TButton', command=self.start_experiment)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(button_frame, text="Stop", style='Stop.TButton', state='disabled', command=self.stop_experiment)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(button_frame, text="Scan", command=self._scan_for_visa).grid(row=0, column=2, sticky='ew', padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console'); frame.grid(row=grid_row, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG, font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S"); log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal'); self.console.insert('end', log_msg); self.console.see('end'); self.console.config(state='disabled')

    def start_experiment(self):
        try:
            self.params = self._validate_and_get_params()
            self.log("Connecting to instruments..."); self.backend.connect(self.params['k2400_visa'], self.params['k2182_visa'], self.params['ls_visa'])
            self.backend.configure_instruments(self.params['current_ma'], self.params['compliance_v']); self.log("All instruments connected and configured.")
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); filename = f"{self.params['name']}_{ts}_VT_Active.csv"
            self.data_filepath = os.path.join(self.params['save_path'], filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f); writer.writerow(["Temperature (K)", "Voltage (V)", "Elapsed Time (s)"])

            self.set_ui_state(running=True); self.experiment_state = 'stabilizing'
            for key in self.data_storage: self.data_storage[key].clear()
            self.line_main.set_data([], []); self.ax_main.set_title(f"V-T Curve: {self.params['name']}"); self.canvas.draw()
            self.log(f"Starting stabilization at {self.params['start_temp']} K...")
            self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}"); messagebox.showerror("Start Failed", f"{e}"); self.backend.shutdown()

    def stop_experiment(self, reason=""):
        if self.experiment_state == 'idle': return
        self.log(f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.experiment_state = 'idle'; self.backend.shutdown(); self.set_ui_state(running=False)
        self.ax_main.set_title("Experiment stopped."); self.canvas.draw()
        if reason: messagebox.showinfo("Experiment Finished", f"Reason: {reason}")

    def _experiment_loop(self):
        if self.experiment_state == 'idle': return
        try:
            if self.experiment_state == 'stabilizing':
                self.backend.stabilize_at_start(self.params['start_temp'], self.log)
                self.backend.start_ramp(self.params['end_temp'], self.params['rate'])
                self.log(f"Ramp started towards {self.params['end_temp']} K.")
                self.experiment_state = 'ramping'; self.start_time = time.time()
                self.root.after(100, self._experiment_loop)
            
            elif self.experiment_state == 'ramping':
                temp, voltage = self.backend.get_measurement()
                elapsed = time.time() - self.start_time
                self.log(f"T: {temp:.3f} K | V: {voltage:.6e} V")

                self.data_storage['temperature'].append(temp); self.data_storage['voltage'].append(voltage)
                with open(self.data_filepath, 'a', newline='') as f: csv.writer(f).writerow([f"{temp:.4f}", f"{voltage:.6e}", f"{elapsed:.2f}"])
                self.line_main.set_data(self.data_storage['temperature'], self.data_storage['voltage'])
                self.ax_main.relim(); self.ax_main.autoscale_view(); self.figure.tight_layout(); self.canvas.draw()

                if temp >= self.params['cutoff']:
                    self.stop_experiment(f"Safety cutoff reached at {temp:.2f} K.")
                elif temp >= self.params['end_temp']:
                    self.stop_experiment("End temperature reached.")
                else:
                    self.root.after(int(self.params['delay_s'] * 1000), self._experiment_loop)

        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}"); messagebox.showerror("Runtime Error", f"{e}"); self.stop_experiment("Runtime Error")

    def _validate_and_get_params(self):
        try:
            params = {'name': self.entries["Sample Name"].get(), 'save_path': self.entries["Save Location"].get(),
                    'start_temp': float(self.entries["Start Temp (K)"].get()), 'end_temp': float(self.entries["End Temp (K)"].get()),
                    'rate': float(self.entries["Ramp Rate (K/min)"].get()), 'cutoff': float(self.entries["Safety Cutoff (K)"].get()),
                    'ls_visa': self.ls_cb.get(), 'current_ma': float(self.entries["Source Current (mA)"].get()), 
                    'compliance_v': float(self.entries["Compliance (V)"].get()), 'delay_s': float(self.entries["Logging Delay (s)"].get()),
                    'k2400_visa': self.k2400_cb.get(), 'k2182_visa': self.k2182_cb.get()}
            if not all([p for k, p in params.items() if k not in ['rate', 'cutoff']]): raise ValueError("A required field is empty.")
            if not (params['start_temp'] < params['end_temp'] < params['cutoff']): raise ValueError("Temperatures must be in order: start < end < cutoff.")
            return params
        except Exception as e: raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values(): w.config(state=state)
        for cb in [self.ls_cb, self.k2400_cb, self.k2182_cb]: cb.config(state=state if state == 'normal' else 'readonly')
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa(self):
        if self.backend.rm is None: self.log("ERROR: PyVISA library missing."); return
        self.log("Scanning for VISA instruments..."); resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}"); self.ls_cb['values'] = resources; self.k2400_cb['values'] = resources; self.k2182_cb['values'] = resources
            for r in resources:
                if '12' in r or '15' in r: self.ls_cb.set(r)
                if '2400' in r or 'GPIB::4' in r: self.k2400_cb.set(r)
                if '2182' in r or 'GPIB::7' in r: self.k2182_cb.set(r)
        else: self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.entries["Save Location"].config(state='normal'); self.entries["Save Location"].delete(0, 'end')
            self.entries["Save Location"].insert(0, path); self.entries["Save Location"].config(state='disabled')

    def _create_entry(self, parent, label_text, default_value, row, browse=False):
        ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky='w', padx=10, pady=3)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=2 if browse else 1)
        entry.insert(0, default_value); self.entries[label_text] = entry
        if browse:
            btn = ttk.Button(parent, text="...", width=3, command=self._browse_file_location)
            btn.grid(row=row, column=3, sticky='e', padx=(0,10))
            entry.config(state='disabled')

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky='w', padx=10, pady=3)
        cb = ttk.Combobox(parent, font=self.FONT_BASE, state='readonly')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.experiment_state != 'idle' and messagebox.askyesno("Exit", "Experiment is running. Stop and exit?"):
            self.stop_experiment("Application closed by user."); self.root.destroy()
        elif self.experiment_state == 'idle': self.root.destroy()

if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror("Dependency Error", "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk(); app = VT_GUI_Active(root); root.mainloop()
