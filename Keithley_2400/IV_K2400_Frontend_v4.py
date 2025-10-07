# -------------------------------------------------------------------------------
# Name:           Keithley 2400 I-V Measurement GUI
# Purpose:        Perform an automated I-V sweep using a Keithley 2400.
#                 (Grid-based layout for stability)
# Author:         Prathamesh (Modified by Gemini)
# Created:        10/09/2025
# Version:        12.0 (User-Requested Sweep Protocols)
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

        # --- MODIFIED: Determine max current range based on sweep type ---
        max_abs_current = 0
        if params['sweep_type'] == 'Custom List':
            try:
                points = [float(p.strip()) * 1e-6 for p in params['custom_list_str'].split(',') if p.strip()]
                if points:
                    max_abs_current = max(abs(p) for p in points)
            except:
                 max_abs_current = 1.0 # Default to 1A range if parsing fails
        else:
            max_abs_current = abs(params['max_current'])

        self.keithley.source_current_range = max_abs_current * 1.05 if max_abs_current > 0 else 1e-5
        self.keithley.compliance_voltage = params['compliance_v']
        self.keithley.enable_source()

    def generate_sweep_points(self, params):
        # --- MODIFIED: New sweep generation logic ---
        sweep_type = params['sweep_type']
        base_sweep = np.array([])

        if sweep_type == "Custom List":
            try:
                custom_list_str = params['custom_list_str']
                # Convert comma-separated string of µA values to a numpy array of A values
                points = [float(p.strip()) * 1e-6 for p in custom_list_str.split(',') if p.strip()]
                if not points:
                    raise ValueError("Custom list is empty or invalid.")
                base_sweep = np.array(points)
            except ValueError as e:
                raise ValueError(f"Invalid format in custom list. Please use comma-separated numbers. Error: {e}")

        else: # Handle the other sweep types
            imax, istep = params['max_current'], params['step_current']
            if istep <= 0:
                raise ValueError("Step Current must be positive.")

            if sweep_type == "0 to Max":
                base_sweep = np.arange(0, imax + istep, istep)

            elif sweep_type == "Loop (0 → Max → 0 → -Max → 0)":
                s1 = np.arange(0, imax + istep, istep)           # 0 -> Max
                s2 = np.arange(imax, 0 - istep, -istep)          # Max -> 0
                s3 = np.arange(0, -imax - istep, -istep)         # 0 -> -Max
                s4 = np.arange(-imax, 0 + istep, istep)          # -Max -> 0
                base_sweep = np.concatenate([s1, s2[1:], s3[1:], s4[1:]])

        if base_sweep.size == 0 and sweep_type != "Custom List":
             raise ValueError(f"Unknown sweep type or invalid parameters for '{sweep_type}'")

        return np.tile(base_sweep, params['num_loops'])


    def measure_at_current(self, current_setpoint, delay):
        self.keithley.ramp_to_current(current_setpoint, steps=5, pause=0.01)
        time.sleep(delay)
        # The .voltage property can return a list. We need to ensure we get a single float.
        voltage_reading = self.keithley.voltage
        # If it's a list, take the first element. Otherwise, use the value as is.
        return voltage_reading[0] if isinstance(voltage_reading, list) else voltage_reading

    def shutdown(self):
        if self.keithley:
            try:
                self.keithley.shutdown()
            finally:
                self.keithley = None

class MeasurementAppGUI:
    PROGRAM_VERSION = "12.0" # Updated Version
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#2B3D4F', '#3A506B', '#EDF2F4'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED = '#A7C957', '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE = "../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"
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
        self.data_storage = {'current': [], 'voltage': [], 'resistance': []}
        self.logo_image = None
        # --- NEW: Blitting optimization ---
        self.plot_bg = None
        self.is_resizing = False
        self.resize_timer = None
        self.pre_init_logs = []

        # --- NEW: Initialize custom UI widget variables ---
        self.custom_list_label = None
        self.custom_list_text = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.bind('<Configure>', self._on_resize)
        self._on_sweep_type_change() # Call once to set initial UI state

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

        # --- Left Panel ---
        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=1)

        # --- Right Panel ---
        right_panel_container = tk.Frame(main_pane, bg='white')
        main_pane.add(right_panel_container, weight=3)

        # --- Make the left panel scrollable ---
        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)
        self.create_graph_frame(right_panel_container)

    def create_header(self):
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        Label(header_frame, text="Keithley 2400: I-V Measurement", bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

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

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)
 
        details_text = ("Program Name: I-V Sweep (4-Probe)\n"
                        "Instrument: Keithley 2400\n"
                        "Measurement Range: 10⁻³ Ω to 10⁹ Ω")
        ttk.Label(frame, text=details_text, justify='left', background=self.CLR_BG_DARK).grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        # --- MODIFIED: Updated UI layout for new sweep options ---
        frame = LabelFrame(parent, text='Sweep Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        self.entries = {}
        grid = ttk.Frame(frame); grid.pack(padx=10, pady=10, fill='x')
        grid.grid_columnconfigure((0, 1, 2), weight=1)

        ttk.Label(grid, text="Sample Name:").grid(row=0, column=0, columnspan=3, sticky='w')
        self.entries["Sample Name"] = Entry(grid, font=self.FONT_BASE, width=20); self.entries["Sample Name"].grid(row=1, column=0, columnspan=3, sticky='ew', pady=(0, 10))

        ttk.Label(grid, text="Max Current (µA):").grid(row=2, column=0, sticky='w'); self.entries["Max Current"] = Entry(grid, font=self.FONT_BASE, width=10); self.entries["Max Current"].grid(row=3, column=0, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Step Current (µA):").grid(row=2, column=1, sticky='w'); self.entries["Step Current"] = Entry(grid, font=self.FONT_BASE, width=10); self.entries["Step Current"].grid(row=3, column=1, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Loops:").grid(row=2, column=2, sticky='w'); self.entries["Num Loops"] = Entry(grid, font=self.FONT_BASE, width=5); self.entries["Num Loops"].grid(row=3, column=2, sticky='ew'); self.entries["Num Loops"].insert(0, "1")

        ttk.Label(grid, text="Compliance (V):").grid(row=4, column=0, columnspan=2, sticky='w', pady=(10, 0)); self.entries["Compliance"] = Entry(grid, font=self.FONT_BASE, width=10); self.entries["Compliance"].grid(row=5, column=0, columnspan=2, sticky='ew', padx=(0, 5))
        ttk.Label(grid, text="Delay (s):").grid(row=4, column=2, sticky='w', pady=(10, 0)); self.entries["Delay"] = Entry(grid, font=self.FONT_BASE, width=5); self.entries["Delay"].grid(row=5, column=2, sticky='ew'); self.entries["Delay"].insert(0, "0.1")

        ttk.Label(grid, text="Sweep Type:").grid(row=6, column=0, columnspan=3, sticky='w', pady=(10, 0))
        self.sweep_type_var = tk.StringVar()
        self.sweep_type_cb = ttk.Combobox(grid, textvariable=self.sweep_type_var, state='readonly', font=self.FONT_BASE, values=["0 to Max", "Loop (0 → Max → 0 → -Max → 0)", "Custom List"])
        self.sweep_type_cb.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(0, 10))
        self.sweep_type_cb.set("0 to Max")
        self.sweep_type_cb.bind("<<ComboboxSelected>>", self._on_sweep_type_change)

        # --- NEW: Custom list widgets ---
        self.custom_list_label = ttk.Label(grid, text="Custom Current List (µA, comma-separated):")
        self.custom_list_label.grid(row=8, column=0, columnspan=3, sticky='w', pady=(10, 0))
        self.custom_list_text = scrolledtext.ScrolledText(grid, height=4, font=self.FONT_BASE, wrap='word')
        self.custom_list_text.grid(row=9, column=0, columnspan=3, sticky='ew')

        ttk.Label(grid, text="Keithley 2400 VISA:").grid(row=10, column=0, columnspan=3, sticky='w'); self.keithley_combobox = ttk.Combobox(grid, font=self.FONT_BASE, state='readonly', width=20); self.keithley_combobox.grid(row=11, column=0, columnspan=3, sticky='ew', pady=(0, 10))

        button_grid = ttk.Frame(frame); button_grid.pack(padx=10, pady=5, fill='x'); button_grid.grid_columnconfigure((0,1), weight=1)
        self.scan_button = ttk.Button(button_grid, text="Scan Instruments", command=self._scan_for_visa_instruments); self.scan_button.grid(row=0, column=0, sticky='ew', padx=(0,5))
        self.file_location_button = ttk.Button(button_grid, text="Save Location...", command=self._browse_file_location); self.file_location_button.grid(row=0, column=1, sticky='ew')

        bf = ttk.Frame(frame); bf.pack(padx=10, pady=10, fill='x'); bf.grid_columnconfigure((0,1), weight=1)
        self.start_button = ttk.Button(bf, text="Start", command=self.start_measurement, style='Start.TButton'); self.start_button.grid(row=0, column=0, sticky='ew', padx=(0,5))
        self.stop_button = ttk.Button(bf, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled'); self.stop_button.grid(row=0, column=1, sticky='ew')

        self.progress_bar = ttk.Progressbar(frame, orient='horizontal', mode='determinate'); self.progress_bar.pack(padx=10, pady=(5,10), fill='x')

    # --- NEW: Method to handle UI changes based on sweep type ---
    def _on_sweep_type_change(self, event=None):
        """Shows/hides UI elements based on the selected sweep type."""
        if not hasattr(self, 'sweep_type_var'): return # Avoid error during initialization

        selection = self.sweep_type_var.get()
        standard_sweep_entries = [self.entries["Max Current"], self.entries["Step Current"]]

        if selection == "Custom List":
            self.custom_list_label.grid()
            self.custom_list_text.grid()
            for entry in standard_sweep_entries:
                entry.config(state='disabled')
        else:
            self.custom_list_label.grid_remove()
            self.custom_list_text.grid_remove()
            for entry in standard_sweep_entries:
                entry.config(state='normal')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=('Consolas', 10), wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True, side='bottom')

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
        # --- MODIFIED: Animated=True for blitting ---
        self.figure = Figure(figsize=(8, 8), dpi=100, constrained_layout=True)
        self.ax_vi, self.ax_ri = self.figure.subplots(2, 1, sharex=True)

        # --- V-I Plot (Top) ---
        self.ax_vi.grid(True, linestyle='--', alpha=0.7)
        self.ax_vi.axhline(0, color='k', linestyle='--', linewidth=0.7, alpha=0.5)
        self.line_main, = self.ax_vi.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-', animated=True)
        self.ax_vi.set_title("Voltage vs. Current", fontweight='bold'); self.ax_vi.set_ylabel("Voltage (V)")

        # --- R-I Plot (Bottom) ---
        self.ax_ri.grid(True, linestyle='--', alpha=0.7)
        self.line_resistance, = self.ax_ri.plot([], [], color=self.CLR_ACCENT_GREEN, marker='o', markersize=4, linestyle='-', animated=True)
        self.ax_ri.set_title("Resistance vs. Current", fontweight='bold'); self.ax_ri.set_xlabel("Current (A)"); self.ax_ri.set_ylabel("Resistance (Ω)")
        self.ax_ri.set_yscale('log') # Resistance is often best viewed on a log scale

        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # --- MODIFIED: Connect draw event for blitting ---
        self.canvas.mpl_connect('draw_event', self._on_draw)


    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {message}\n"
        if hasattr(self, 'console_widget'):
            self.console_widget.config(state='normal'); self.console_widget.insert('end', log_line); self.console_widget.see('end'); self.console_widget.config(state='disabled')
        else:
            self.pre_init_logs.append(log_line)

    def start_measurement(self):
        try:
            # --- MODIFIED: New parameter collection logic ---
            sweep_type = self.sweep_type_var.get()
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'num_loops': int(self.entries["Num Loops"].get()),
                'compliance_v': float(self.entries["Compliance"].get()),
                'delay_s': float(self.entries["Delay"].get()),
                'sweep_type': sweep_type,
                'max_current': 0, 'step_current': 0, 'custom_list_str': ''
            }

            if sweep_type == "Custom List":
                params['custom_list_str'] = self.custom_list_text.get("1.0", tk.END)
                if not params['custom_list_str'].strip():
                    raise ValueError("Custom list cannot be empty.")
            else:
                params['max_current'] = float(self.entries["Max Current"].get()) * 1e-6
                params['step_current'] = float(self.entries["Step Current"].get()) * 1e-6

            visa_address = self.keithley_combobox.get()
            if not all([params['sample_name'], visa_address, self.file_location_path]):
                raise ValueError("Sample Name, VISA address, and Save Location are required.")

            self.backend.connect_and_configure(visa_address, params)
            self.sweep_points = self.backend.generate_sweep_points(params)
            self.log(f"Generated sweep with {len(self.sweep_points)} points.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); file_name = f"{params['sample_name']}_{ts}_IV.dat"; self.data_filepath = os.path.join(self.file_location_path, file_name)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f, delimiter='\t'); writer.writerow([f"# Sample: {params['sample_name']}", f"Compliance: {params['compliance_v']} V"]); writer.writerow(["Current (A)", "Voltage (V)", "Resistance (Ohm)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True; self.sweep_index = 0
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            
            # --- MODIFIED: Plot setup for blitting ---
            self.line_main.set_data([], []); self.line_resistance.set_data([], [])
            self.ax_vi.relim(); self.ax_vi.autoscale_view()
            self.ax_ri.relim(); self.ax_ri.autoscale_view()
            self.progress_bar['value'] = 0; self.progress_bar['maximum'] = len(self.sweep_points)
            self.figure.suptitle(f"Sample: {params['sample_name']}", fontweight='bold')
            self.canvas.draw_idle() # Use draw_idle to schedule a draw
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
            
            # Calculate resistance, handling division by zero
            if current != 0:
                resistance = voltage / current
            else:
                resistance = np.nan # Use Not-a-Number for plotting

            self.data_storage['current'].append(float(current)); self.data_storage['voltage'].append(voltage); self.data_storage['resistance'].append(resistance)
            with open(self.data_filepath, 'a', newline='') as f: csv.writer(f, delimiter='\t').writerow([f"{current:.8e}", f"{voltage:.8e}", f"{resistance:.8e}"])

            # --- MODIFIED: Efficient plotting with blitting ---
            self.line_main.set_data(self.data_storage['current'], self.data_storage['voltage'])
            self.line_resistance.set_data(self.data_storage['current'], self.data_storage['resistance'])

            # --- Autoscale axes and redraw background if limits change ---
            if self.ax_vi.get_xlim() != self.ax_ri.get_xlim() or self.ax_vi.get_ylim() != self.ax_vi.get_ylim():
                self.ax_vi.relim(); self.ax_vi.autoscale_view()
                self.ax_ri.relim(); self.ax_ri.autoscale_view()
                self.canvas.draw_idle() # Full redraw if axes change
            else:
                # --- Efficient blitting update ---
                if self.plot_bg:
                    self.canvas.restore_region(self.plot_bg)
                    self.ax_vi.draw_artist(self.line_main)
                    self.ax_ri.draw_artist(self.line_resistance)
                    self.canvas.blit(self.figure.bbox)
                    self.canvas.flush_events()

            self.progress_bar['value'] = self.sweep_index + 1

            self.sweep_index += 1
            self.root.after(10, self._run_sweep_step)
        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}"); messagebox.showerror("Runtime Error", "An error occurred during the sweep. Check console."); self.stop_measurement()

    # --- NEW: Blitting and resize handling methods ---
    def _on_draw(self, event):
        """Callback for draw events to cache the plot background."""
        if self.is_resizing: return
        self.plot_bg = self.canvas.copy_from_bbox(self.figure.bbox)

    def _on_resize(self, event):
        """Handle window resize events to trigger a full redraw."""
        self.is_resizing = True
        self.plot_bg = None # Invalidate background
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        
        # Redraw after a short delay to avoid excessive redraws during resizing
        self.resize_timer = self.root.after(300, self._finalize_resize)

    def _finalize_resize(self):
        """Finalize the resize by performing a full redraw."""
        self.is_resizing = False
        self.resize_timer = None
        if self.canvas:
            self.canvas.draw_idle()

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
