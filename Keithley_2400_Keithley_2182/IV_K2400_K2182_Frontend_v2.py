# -------------------------------------------------------------------------------
# Name:         IV Sweep GUI for Keithley 2400/2182
# Purpose:      Provide a professional GUI for performing I-V sweeps using a
#               Keithley 2400 as a current source and a Keithley 2182
#               as a nanovoltmeter.
# Author:       Prathamesh Deshmukh 
# Created:      04/10/2025
# Version:      1.0
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk; from tkinter import Canvas
from tkinter import ttk, filedialog, messagebox, scrolledtext
import numpy as np
import os
import time
import traceback
import csv
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

# --- Instrument Control Packages ---
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
class IV_Backend:
    """ Manages communication with the Keithley 2400 and 2182. """
    def __init__(self):
        self.k2400, self.k2182 = None, None
        if pyvisa:
            try: self.rm = pyvisa.ResourceManager()
            except Exception as e: print(f"Could not initialize VISA: {e}"); self.rm = None

    def connect(self, k2400_visa, k2182_visa):
        if not self.rm: raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE: raise ImportError("Pymeasure is not available.")
        self.k2400 = Keithley2400(k2400_visa)
        print(f"  K2400 Connected: {self.k2400.id}")
        self.k2182 = self.rm.open_resource(k2182_visa)
        print(f"  K2182 Connected: {self.k2182.query('*IDN?').strip()}")

    def configure_instruments(self, compliance_v, current_range_a):
        # Keithley 2400 setup
        self.k2400.reset()
        self.k2400.apply_current()
        self.k2400.source_current_range = current_range_a
        self.k2400.compliance_voltage = compliance_v
        self.k2400.source_current = 0
        self.k2400.enable_source()

        # Keithley 2182 setup
        self.k2182.write("*rst; status:preset; *cls")
        time.sleep(1)

    def measure_voltage_at_current(self, current_a, delay_s):
        self.k2400.ramp_to_current(current_a, steps=10, pause=0.05)
        time.sleep(delay_s)

        # K2182 measurement sequence
        self.k2182.write("status:measurement:enable 512; *sre 1")
        self.k2182.write("sample:count 2")
        self.k2182.write("trigger:source bus")
        self.k2182.write("trigger:delay 0.1")
        self.k2182.write("trace:points 2")
        self.k2182.write("trace:feed sense1; feed:control next")
        self.k2182.write("initiate")
        self.k2182.assert_trigger()
        self.k2182.wait_for_srq(timeout=10)
        voltages = self.k2182.query_ascii_values("trace:data?")
        self.k2182.query("status:measurement?")
        self.k2182.write("trace:clear; feed:control next")

        return sum(voltages) / len(voltages) if voltages else float('nan')

    def shutdown(self):
        if self.k2400:
            try: self.k2400.shutdown()
            except: pass
        if self.k2182:
            try: self.k2182.write("*rst"); self.k2182.close()
            except: pass
        print("  Instruments shut down and disconnected.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class IV_GUI:
    PROGRAM_VERSION = "2.1" # Performance and UI update
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_FRAME_BG = '#3A506B'; CLR_INPUT_BG = '#4C566A'; CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#A7C957', '#E74C3C', '#8D99AE' 
    CLR_ACCENT_GOLD = '#FFC107'; CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold'); FONT_CONSOLE = ('Consolas', 10)
    
    def __init__(self, root):
        self.root = root
        self.root.title(f"I-V Sweep (K2400 + K2182) v{self.PROGRAM_VERSION}")
        self.root.geometry("1650x950")
        self.root.minsize(1400, 800)
        self.root.configure(bg=self.CLR_BG_DARK)
        self.is_running = False
        self.logo_image = None
        self.backend = IV_Backend()
        self.data_storage = {'current': [], 'voltage': []}
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK); style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_FG_LIGHT, insertcolor=self.CLR_FG_LIGHT)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_BG_DARK), ('hover', self.CLR_BG_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11, 'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        header = tk.Frame(self.root, bg=self.CLR_HEADER); header.pack(side='top', fill='x')
        ttk.Label(header, text=f"I-V Sweep (K2400 + K2182)", style='Header.TLabel', font=font_title_main, foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)
        main_pane = ttk.PanedWindow(self.root, orient='horizontal'); main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=2) # Give more weight to controls

        # --- Make the left panel scrollable ---
        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical", command=canvas.yview)
        left_panel = ttk.Frame(canvas, padding=5) # This is now the scrollable_frame
        left_panel.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=left_panel, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = self._create_right_panel(main_pane); main_pane.add(right_panel, weight=4)
        self._populate_left_panel(left_panel)

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1); panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, 0)
        self._create_params_panel(panel, 1); self._create_control_panel(panel, 2); self._create_console_panel(panel, 3)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information'); frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1); LOGO_SIZE = 110
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE, bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        try: # Use a more robust relative path
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE/2, LOGO_SIZE/2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1], 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_FRAME_BG).grid(row=0, column=1, padx=10, pady=(15,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_FRAME_BG).grid(row=1, column=1, padx=10, pady=(0,5), sticky='nw')
        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)
        details_text = ("Program Duty: I-V Sweep (4-Probe)\n"
                        "Instruments: Keithley 2400, Keithley 2182\n"
                        "Measurement Range: 10⁻⁶ Ω to 10⁹ Ω")
        ttk.Label(frame, text=details_text, justify='left', background=self.CLR_FRAME_BG).grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(panel, text='Live I-V Curve'); container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Waiting for experiment...", fontweight='bold'); self.ax_main.set_xlabel("Voltage (V)"); self.ax_main.set_ylabel("Current (A)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6); self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container); self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent); container.grid(row=grid_row, column=0, sticky='new', pady=5)
        container.grid_columnconfigure(0, weight=1); self.entries = {}

        sweep_frame = ttk.LabelFrame(container, text='Sweep Parameters'); sweep_frame.grid(row=0, column=0, sticky='nsew', pady=(0,5))
        sweep_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(sweep_frame, "Start Current (mA)", "-1", 0)
        self._create_entry(sweep_frame, "Stop Current (mA)", "1", 1)
        self._create_entry(sweep_frame, "Step Current (mA)", "0.1", 2)
        self._create_entry(sweep_frame, "Compliance (V)", "10", 3)
        self._create_entry(sweep_frame, "Dwell Time (s)", "0.5", 4)

        visa_frame = ttk.LabelFrame(container, text='Instrument Addresses'); visa_frame.grid(row=1, column=0, sticky='nsew')
        visa_frame.grid_columnconfigure(1, weight=1)
        self.k2400_cb = self._create_combobox(visa_frame, "Keithley 2400 VISA", 0)
        self.k2182_cb = self._create_combobox(visa_frame, "Keithley 2182 VISA", 1)


    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Experiment Control'); frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_IV", 0)
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
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S"); log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal'); self.console.insert('end', log_msg); self.console.see('end'); self.console.config(state='disabled')

    def start_experiment(self):
        try:
            self.params = self._validate_and_get_params()
            self.log("Connecting to instruments..."); self.backend.connect(self.params['k2400_visa'], self.params['k2182_visa'])
            self.backend.configure_instruments(self.params['compliance_v'], self.params['stop_i'])
            self.log("All instruments connected and configured.")

            start_i, stop_i, step_i = self.params['start_i'], self.params['stop_i'], self.params['step_i']
            self.current_points = np.arange(start_i, stop_i + step_i/2, step_i)
            if not len(self.current_points): raise ValueError("Current sweep results in zero points.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); filename = f"{self.params['name']}_{ts}_IV.csv"
            self.data_filepath = os.path.join(self.params['save_path'], filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f); writer.writerow(["Current (A)", "Voltage (V)"])

            self.current_step_index = 0; self.set_ui_state(running=True)
            for key in self.data_storage: self.data_storage[key].clear()
            self.line_main.set_data([], []); self.ax_main.set_title(f"I-V Curve: {self.params['name']}"); self.canvas.draw()
            self.log(f"Starting sweep: {len(self.current_points)} points from {start_i:.1e} A to {stop_i:.1e} A.")
            self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}"); messagebox.showerror("Start Failed", f"{e}"); self.backend.shutdown()

    def stop_experiment(self, reason=""):
        if not self.is_running: return
        self.log(f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.is_running = False; self.backend.shutdown(); self.set_ui_state(running=False)
        self.ax_main.set_title("Experiment stopped."); self.canvas.draw_idle()
        if reason: messagebox.showinfo("Experiment Finished", f"Reason: {reason}")

    def _experiment_loop(self):
        if not self.is_running: return
        try:
            current_setpoint = self.current_points[self.current_step_index]
            self.log(f"--- Setting current to {current_setpoint:.3e} A ({self.current_step_index + 1}/{len(self.current_points)}) ---")
            self.ax_main.set_title(f"Measuring at {current_setpoint:.3e} A..."); self.canvas.draw_idle()

            voltage = self.backend.measure_voltage_at_current(current_setpoint, self.params['delay_s'])
            self.log(f"  Read: V = {voltage:.6e} V")

            self.data_storage['current'].append(current_setpoint); self.data_storage['voltage'].append(voltage)
            with open(self.data_filepath, 'a', newline='') as f: csv.writer(f).writerow([f"{current_setpoint:.6e}", f"{voltage:.6e}"])
            self.line_main.set_data(self.data_storage['voltage'], self.data_storage['current']); self.ax_main.relim()
            self.ax_main.autoscale_view(); self.canvas.draw_idle()

            self.current_step_index += 1
            if self.current_step_index >= len(self.current_points):
                self.stop_experiment("All points measured.")
            else:
                self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}"); messagebox.showerror("Runtime Error", f"{e}"); self.stop_experiment("Runtime Error")

    def _validate_and_get_params(self):
        try:
            params = {
                'name': self.entries["Sample Name"].get(), 'save_path': self.entries["Save Location"].get(),
                'start_i': float(self.entries["Start Current (mA)"].get()) * 1e-3,
                'stop_i': float(self.entries["Stop Current (mA)"].get()) * 1e-3,
                'step_i': float(self.entries["Step Current (mA)"].get()) * 1e-3,
                'compliance_v': float(self.entries["Compliance (V)"].get()),
                'delay_s': float(self.entries["Dwell Time (s)"].get()),
                'k2400_visa': self.k2400_cb.get(), 'k2182_visa': self.k2182_cb.get()
            }
            if not all(params.values()): raise ValueError("All fields must be filled.")
            if params['step_i'] == 0: raise ValueError("Step Current cannot be zero.")
            return params
        except Exception as e: raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        self.is_running = running
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values(): w.config(state=state)
        for cb in [self.k2400_cb, self.k2182_cb]: cb.config(state=state if state == 'normal' else 'readonly')
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa(self):
        if self.backend.rm is None: self.log("ERROR: PyVISA library missing."); return
        self.log("Scanning for VISA instruments..."); resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}"); self.k2400_cb['values'] = resources; self.k2182_cb['values'] = resources
            for r in resources:
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
        cb = ttk.Combobox(parent, font=self.FONT_BASE, state='readonly', style='TCombobox')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.is_running and messagebox.askyesno("Exit", "Experiment is running. Stop and exit?"):
            self.stop_experiment("Application closed by user."); self.root.destroy()
        elif not self.is_running: self.root.destroy()

if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror("Dependency Error", "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk(); app = IV_GUI(root); root.mainloop()