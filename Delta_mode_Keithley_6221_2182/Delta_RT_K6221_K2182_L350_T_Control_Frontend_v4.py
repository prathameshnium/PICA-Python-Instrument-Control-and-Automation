# -------------------------------------------------------------------------------
# Name:         Advanced Delta Mode R-T Measurement
# Purpose:      Perform a temperature-dependent Delta mode measurement with a
#               Keithley 6221/2182 and Lakeshore 350, using an advanced GUI
#               and temperature control logic.
#
# Author:       Prathamesh Deshmukh
# Created:      03/10/2025
#
# Version:      2.0 (Merged with 6517B GUI)
#
# Description:  This program integrates the advanced GUI, hardware ramp, and
#               stabilization logic from the Keithley 6517B R-T script with the
#               core Delta Mode measurement backend. The Delta Mode backend
#               remains unchanged, while the frontend is now significantly more
#               robust and user-friendly.
# -------------------------------------------------------------------------------

# --- Packages ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import os
import sys
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Active_Delta_Backend:
    """ Manages both Keithley 6221 and Lakeshore 350 for active measurements. """
    def __init__(self):
        self.keithley = None
        self.lakeshore = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, keithley_visa, lakeshore_visa):
        """ Connects to both instruments. """
        print("\n--- [Backend] Initializing Instruments ---")
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        # Connect to Keithley
        print(f"  Connecting to Keithley 6221 at {keithley_visa}...")
        self.keithley = self.rm.open_resource(keithley_visa)
        self.keithley.timeout = 25000
        print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
        # Connect to Lakeshore
        print(f"  Connecting to Lakeshore 350 at {lakeshore_visa}...")
        self.lakeshore = self.rm.open_resource(lakeshore_visa)
        self.lakeshore.write('*RST'); time.sleep(0.5); self.lakeshore.write('*CLS')
        print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
        print("--- [Backend] Instrument Initialization Complete ---")

    def setup_keithley_delta(self, current, compliance):
        """ Configures the Keithley for a Delta Mode measurement. """
        if not self.keithley: return
        print("  Configuring Keithley for Delta Mode...")
        self.keithley.write("*rst; status:preset; *cls")
        self.keithley.write(f"SOUR:DELT:HIGH {current}")
        self.keithley.write(f"SOUR:DELT:PROT {compliance}")
        self.keithley.write("SOUR:DELT:ARM")
        time.sleep(1)
        self.keithley.write("INIT:IMM")
        print("  Keithley Armed for Delta Measurement.")

    # --- NEW HELPER METHODS to support advanced GUI logic ---
    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None: raise ValueError("Invalid heater range.")
        self.lakeshore.write(f'RANGE {output},{range_code}')
        
    def set_setpoint(self, output, temperature_k):
        self.lakeshore.write(f'SETP {output},{temperature_k}')

    def setup_ramp(self, output, rate_k_per_min, ramp_on=True):
        self.lakeshore.write(f'RAMP {output},{1 if ramp_on else 0},{rate_k_per_min}')
        time.sleep(0.5)
        
    def get_heater_output(self, output):
        return float(self.lakeshore.query(f'HTR? {output}').strip())
    # --- END of new helper methods ---

    def get_temperature(self):
        if not self.lakeshore: return 0.0
        return float(self.lakeshore.query('KRDG? A').strip())

    def get_delta_measurement(self):
        if not self.keithley: return 0.0
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        voltage = float(raw_data.strip().split(',')[0])
        return voltage

    def close_instruments(self):
        """ CRITICAL: Turns off heater and closes all connections. """
        print("--- [Backend] Closing instrument connections. ---")
        try:
            if self.lakeshore:
                print("  SAFETY: Setting Lakeshore heater to OFF (Range 0).")
                self.set_heater_range(1, 'off')
            if self.keithley:
                print("  Clearing Keithley source.")
                self.keithley.write("SOUR:CLE"); self.keithley.write("*RST")
        except Exception as e:
            print(f"  WARNING: A non-critical error occurred during shutdown: {e}")
        finally:
            if self.keithley: self.keithley.close(); self.keithley = None; print("  Keithley connection closed.")
            if self.lakeshore: self.lakeshore.close(); self.lakeshore = None; print("  Lakeshore connection closed.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) - ADAPTED FROM 6517B SCRIPT ---
# -------------------------------------------------------------------------------
class Advanced_Delta_GUI:
    PROGRAM_VERSION = "2.0"
    LOGO_SIZE = 110
    try:
        # Robust path finding for assets relative to the script's location
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = resource_path("../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg")

    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'; CLR_ACCENT_GOLD = '#FFC107'; CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'; CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11; FONT_BASE = ('Segoe UI', FONT_SIZE_BASE); FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("K6221/2182 & L350: Delta Mode R-T (T-Control)")
        self.root.geometry("1550x950"); self.root.minsize(1200, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.is_stabilizing = False
        self.start_time = None
        self.data_file_handle = None
        self.plot_backgrounds = None
        self.backend = Active_Delta_Backend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': [], 'voltage': [], 'resistance': []}
        self.log_scale_var = tk.BooleanVar(value=True)
        self.current_heater_range = 'off'
        self.logo_image = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK); style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TCheckbutton', background=self.CLR_GRAPH_BG, foreground=self.CLR_TEXT_DARK, font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_GRAPH_BG)])
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': self.FONT_SIZE_BASE, 'axes.titlesize': self.FONT_SIZE_BASE + 4, 'axes.labelsize': self.FONT_SIZE_BASE + 2})

    def create_widgets(self):
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal'); main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500); main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG); main_pane.add(right_panel, weight=3)
        top_controls_frame = ttk.Frame(left_panel); left_panel.add(top_controls_frame, weight=0)
        console_pane = self.create_console_frame(left_panel)
        self.create_info_frame(top_controls_frame)
        self.create_input_frame(top_controls_frame)
        left_panel.add(console_pane, weight=1)
        self.create_graph_frame(right_panel)

    def create_header(self):
        font_title_italic = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold italic')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="K6221/2182 & L350: Delta Mode R-T (T-Control)", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=font_title_italic).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0); logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)
        # Defer logo loading to improve startup time
        self.root.after(50, lambda: self._load_logo(logo_canvas, frame))

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)
 
        details_text = ("Program Name: Delta Mode R vs. T (T-Control)\n"
                        "Instruments: Keithley 6221/2182, Lakeshore 350\n"
                        "Measurement Range: 10⁻⁹ Ω to 10⁸ Ω")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def _load_logo(self, canvas, frame):
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e: self.log(f"ERROR: Failed to load logo. {e}")

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}; pady_val = (5, 5); padx_val = 10
        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE); self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=padx_val, pady=(0, 10), sticky='ew')
        Label(frame, text="Start Temp (K):").grid(row=2, column=0, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE); self.entries["Start Temp"].grid(row=3, column=0, padx=(padx_val,5), pady=(0,5), sticky='ew')
        Label(frame, text="End Temp (K):").grid(row=2, column=1, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE); self.entries["End Temp"].grid(row=3, column=1, padx=(5,padx_val), pady=(0,5), sticky='ew')
        Label(frame, text="Ramp Rate (K/min):").grid(row=4, column=0, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE); self.entries["Rate"].grid(row=5, column=0, padx=(padx_val,5), pady=(0,10), sticky='ew')
        Label(frame, text="Safety Cutoff (K):").grid(row=4, column=1, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Cutoff"] = Entry(frame, font=self.FONT_BASE); self.entries["Cutoff"].grid(row=5, column=1, padx=(5,padx_val), pady=(0,10), sticky='ew')
        
        Label(frame, text="Apply Current (A):").grid(row=6, column=0, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Apply Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Apply Current"].grid(row=7, column=0, padx=(padx_val,5), pady=(0,5), sticky='ew')
        self.entries["Apply Current"].insert(0, "1E-6")
        Label(frame, text="Compliance (V):").grid(row=6, column=1, padx=padx_val, pady=pady_val, sticky='w')
        self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].grid(row=7, column=1, padx=(5,padx_val), pady=(0,5), sticky='ew')
        self.entries["Compliance"].insert(0, "10")

        Label(frame, text="Lakeshore VISA:").grid(row=8, column=0, padx=padx_val, pady=pady_val, sticky='w')
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.lakeshore_cb.grid(row=9, column=0, padx=(padx_val,5), pady=(0,10), sticky='ew')
        Label(frame, text="Keithley VISA:").grid(row=8, column=1, padx=padx_val, pady=pady_val, sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.keithley_cb.grid(row=9, column=1, padx=(5,padx_val), pady=(0,10), sticky='ew')
        
        ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments).grid(row=10, column=0, columnspan=2, padx=padx_val, pady=4, sticky='ew')
        ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location).grid(row=11, column=0, columnspan=2, padx=padx_val, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Measurement", command=self.start_measurement, style='Start.TButton'); self.start_button.grid(row=13, column=0, padx=padx_val, pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled'); self.stop_button.grid(row=13, column=1, padx=padx_val, pady=(10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0); self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE); graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        # Use a standard tk.Frame and set its background explicitly to match the graph
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG); top_bar.pack(side='top', fill='x', pady=(0, 5))
        ttk.Checkbutton(top_bar, text="Log Resistance Axis", variable=self.log_scale_var, command=self._update_y_scale).pack(side='right', padx=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG); self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])
        
        # Set animated=True for blitting
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-', animated=True)
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold'); self.ax_main.set_ylabel("Resistance (Ω)")
        self._update_y_scale(); self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)
        
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-', animated=True)
        self.ax_sub1.set_xlabel("Temperature (K)"); self.ax_sub1.set_ylabel("Voltage (V)"); self.ax_sub1.grid(True, linestyle='--', alpha=0.6)
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=3, linestyle='-', animated=True)
        self.ax_sub2.set_xlabel("Time (s)"); self.ax_sub2.set_ylabel("Temperature (K)"); self.ax_sub2.grid(True, linestyle='--', alpha=0.6)
        
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _update_y_scale(self):
        self.ax_main.set_yscale('log' if self.log_scale_var.get() else 'linear')
        # A full redraw will be triggered by other actions, so this is often redundant.

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S"); self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{ts}] {message}\n"); self.console_widget.see('end'); self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            self.params = {
                'sample_name': self.entries["Sample Name"].get(),
                'start_temp': float(self.entries["Start Temp"].get()),
                'end_temp': float(self.entries["End Temp"].get()),
                'rate': float(self.entries["Rate"].get()),
                'cutoff': float(self.entries["Cutoff"].get()),
                'current': float(self.entries["Apply Current"].get()),
                'compliance': float(self.entries["Compliance"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'keithley_visa': self.keithley_cb.get()
            }
            if not all(self.params.values()) or not self.file_location_path: raise ValueError("All fields, VISA addresses, and save location are required.")
            if not (self.params['start_temp'] < self.params['end_temp'] < self.params['cutoff']): raise ValueError("Temperatures must be in order: start < end < cutoff.")

            self.backend.initialize_instruments(self.params['keithley_visa'], self.params['lakeshore_visa'])
            self.backend.setup_keithley_delta(self.params['current'], self.params['compliance'])
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); file_name = f"{self.params['sample_name']}_{ts}_Delta_RT.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)
            self.data_file_handle = open(self.data_filepath, 'w', newline='')
            writer = csv.writer(self.data_file_handle)
            writer.writerow([f"# Sample: {self.params['sample_name']}", f"Applied Current: {self.params['current']}A"])
            writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)", "Measured Voltage (V)", "Resistance (Ohm)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_stabilizing, self.is_running = True, False
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {self.params['sample_name']}", fontweight='bold')
            # Perform a single full redraw to clear plots and set the new title
            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]: ax.relim(); ax.autoscale_view()
            self.canvas.draw()

            self.log("Starting stabilization process..."); self.root.after(1000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}"); messagebox.showerror("Initialization Error", f"{e}")

    def stop_measurement(self):
        if self.is_running or self.is_stabilizing:
            self.is_running, self.is_stabilizing = False, False
            self.log("Measurement stopped by user.")
            self.backend.close_instruments()
            if self.data_file_handle:
                self.data_file_handle.close()
                self.data_file_handle = None
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            messagebox.showinfo("Info", "Measurement stopped and instruments disconnected.")

    def _stabilization_loop(self):
        if not self.is_stabilizing: return
        try:
            current_temp = self.backend.get_temperature()
            if current_temp > self.params['start_temp'] + 0.2:
                self.log(f"Stabilizing (Cooling)... Current: {current_temp:.4f} K > Target: {self.params['start_temp']} K")
                self.backend.set_heater_range(1, 'off')
            else:
                self.log(f"Stabilizing (Heating)... Current: {current_temp:.4f} K <= Target: {self.params['start_temp']} K")
                self.backend.set_heater_range(1, 'medium')
                self.backend.set_setpoint(1, self.params['start_temp'])

            if abs(current_temp - self.params['start_temp']) < 0.1:
                self.log(f"Stabilized at {current_temp:.4f} K. Waiting 5s before starting ramp...")
                self.is_stabilizing = False; self.root.after(5000, self._start_hardware_ramp) # Move to next stage
            else:
                self.root.after(2000, self._stabilization_loop)
        except Exception as e: self.log(f"ERROR during stabilization: {e}"); self.stop_measurement()

    def _start_hardware_ramp(self):
        self.backend.set_setpoint(1, self.params['end_temp']); self.backend.setup_ramp(1, self.params['rate'])
        self.current_heater_range = 'high'; self.backend.set_heater_range(1, self.current_heater_range)
        self.log(f"Hardware ramp started towards {self.params['end_temp']} K at {self.params['rate']} K/min.")
        self.is_running = True; self.start_time = time.time(); self.root.after(1000, self._update_measurement_loop)
        
        # --- Performance Improvement: Capture static background for blitting ---
        self.canvas.draw()
        self.plot_backgrounds = [self.canvas.copy_from_bbox(self.ax_main.bbox),
                                 self.canvas.copy_from_bbox(self.ax_sub1.bbox),
                                 self.canvas.copy_from_bbox(self.ax_sub2.bbox)]
        # -----------------------------------------------------------------------

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            temp = self.backend.get_temperature()
            htr = self.backend.get_heater_output(1)
            voltage = self.backend.get_delta_measurement()
            res = voltage / self.params['current'] if self.params['current'] != 0 else float('inf')
            elapsed = time.time() - self.start_time

            self.log(f"T:{temp:.3f}K | R:{res:.3e}Ω | Htr:{htr:.1f}% ({self.current_heater_range})")
            if self.data_file_handle:
                csv.writer(self.data_file_handle).writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}", f"{temp:.4f}", f"{htr:.2f}", f"{voltage:.4e}", f"{res:.4e}"])
            
            self.data_storage['time'].append(elapsed); self.data_storage['temperature'].append(temp); self.data_storage['voltage'].append(voltage); self.data_storage['resistance'].append(res)
            
            # --- Performance Improvement: Use blitting for fast graph updates ---
            # Restore the clean background
            for bg in self.plot_backgrounds: self.canvas.restore_region(bg)
            
            # Update data and redraw only the artists
            self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])
            self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['voltage'])
            self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature'])
            
            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]: 
                ax.relim()
                ax.autoscale_view()
            
            for ax, line in [(self.ax_main, self.line_main), (self.ax_sub1, self.line_sub1), (self.ax_sub2, self.line_sub2)]:
                ax.draw_artist(line)
            self.canvas.blit(self.figure.bbox)
            # --------------------------------------------------------------------
            
            if temp >= self.params['cutoff']: self.log(f"!!! SAFETY CUTOFF REACHED at {temp:.4f} K !!!"); self.stop_measurement()
            elif temp >= self.params['end_temp']: self.log(f"Target temperature reached. Measurement complete."); self.stop_measurement()
            else: self.root.after(950, self._update_measurement_loop) # Slightly less than 1s to prevent drift
        except Exception as e: self.log(f"RUNTIME ERROR: {traceback.format_exc()}"); self.stop_measurement()

    def _scan_for_visa_instruments(self):
        if not pyvisa: self.log("ERROR: PyVISA is not installed."); return
        try:
            rm = pyvisa.ResourceManager(); self.log("Scanning for VISA instruments..."); resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}"); self.lakeshore_cb['values'] = resources; self.keithley_cb['values'] = resources
                for res in resources:
                    if "GPIB1::15" in res: self.lakeshore_cb.set(res)
                    if "GPIB0::13" in res: self.keithley_cb.set(res)
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running or self.is_stabilizing:
            if messagebox.askyesno("Exit", "Measurement running. Stop and exit?"): self.stop_measurement(); self.root.destroy()
        else: self.root.destroy()

def main():
    root = tk.Tk()
    app = Advanced_Delta_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
