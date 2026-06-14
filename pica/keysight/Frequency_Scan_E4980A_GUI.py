'''
 PROGRAM:      Keysight E4980A Frequency Scan (Cp-G) GUI
 PURPOSE:      Provide a robust interface for automating Frequency sweeps with ALC, 
               Aperture control, Open/Short corrections, and Full Impedance calculations.
'''

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import math
import traceback
from datetime import datetime
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
from multiprocessing import Process
import runpy

def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")

def launch_plotter_utility():
    """Finds and launches the plotter utility script in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")

def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
    try:
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.LANCZOS
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# ===============================================================================
# BACKEND CLASS - Instrument Control Logic
# ===============================================================================

class LCR_Backend:
    """Handles all SCPI communication with the Keysight E4980A."""

    def __init__(self):
        self.instrument = None
        self.params = {}
        self.has_opt001 = False
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA init failed: {e}")

    def _check_errors(self, context=""):
        """Drain SCPI error queue; raise on any error."""
        errors = []
        for _ in range(20):
            err = self.instrument.query(':SYST:ERR?').strip()
            if err.startswith('0,') or err.startswith('+0,'):
                break
            errors.append(err)
        if errors:
            raise RuntimeError(f"SCPI errors after {context}: {errors}")

    def safe_ramp_dc_bias(self, target_v, step=0.5, dwell=0.1):
        """Safely ramps the DC bias to the target voltage."""
        current_v = float(self.instrument.query(':BIAS:VOLT?'))
        if abs(target_v - current_v) < 0.01:
            return

        direction = 1 if target_v > current_v else -1
        ramp_points = np.arange(current_v, target_v, direction * step)
        ramp_points = np.append(ramp_points, target_v)

        for v in ramp_points:
            self.instrument.write(f':BIAS:VOLT {v:.3f}')
            time.sleep(dwell)

    def initialize_instrument(self, p):
        """Configures the instrument for a Frequency sweep."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = p
        if not self.rm:
            raise ConnectionError("VISA Resource Manager unavailable.")
            
        inst = self.rm.open_resource(p['lcr_visa'])   
        inst.timeout = 15000 
        inst.read_termination = '\n'
        inst.write_termination = '\n'
        self.instrument = inst

        idn = inst.query('*IDN?').strip()
        if 'E4980' not in idn:
            inst.close()
            raise ConnectionError(f"Not an E4980A: {idn}")
        
        self.has_opt001 = '001' in inst.query('*OPT?')

        v_bias_max = 40.0 if self.has_opt001 else 2.0
        v_ac_max = 20.0 if self.has_opt001 else 2.0
        if abs(p['dc_bias']) > v_bias_max:
            raise ValueError(f"|DC Bias| > {v_bias_max} V limit.")
        if not (0 < p['ac_bias'] <= v_ac_max):
            raise ValueError(f"AC level outside 0–{v_ac_max} Vrms.")

        inst.write('*RST; *CLS')                 
        inst.write(':DISP:ENAB ON')              
        inst.write(':FUNC:IMP CPG')              
        inst.write(f":APER {p['aper']}")         
        inst.write(':FUNC:IMP:RANG:AUTO ON')     
        
        if p['alc_enabled']:
            inst.write(':AMPL:ALC ON')
        else:
            inst.write(':AMPL:ALC OFF')

        if p['corr_enabled']:
            inst.write(':CORR:OPEN:STAT ON')
            inst.write(':CORR:SHOR:STAT ON')
        else:
            inst.write(':CORR:OPEN:STAT OFF')
            inst.write(':CORR:SHOR:STAT OFF')

        inst.write(f":VOLT {p['ac_bias']}")
        inst.write(':TRIG:SOUR BUS')             
        inst.write(':INIT:CONT ON')              
        
        inst.write(':BIAS:VOLT 0')               
        inst.write(':BIAS:STAT ON')
        print(f"  Ramping DC Bias to {p['dc_bias']} V...")
        self.safe_ramp_dc_bias(p['dc_bias'])
        
        self._check_errors("configuration")
        print(f"  Connected & configured: {idn}")

    def perform_measurement(self, freq, delay):
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")

        self.instrument.write(f':FREQ {freq}')
        time.sleep(delay) 
        
        vals = self.instrument.query_ascii_values('*TRG')
        return vals[0], vals[1]  # Cp, G

    def close_instrument(self):
        print("--- [Backend] Closing instrument connection. ---")
        if not self.instrument:
            return
        try:
            print("  Ramping bias to zero and turning off...")
            self.safe_ramp_dc_bias(0.0)
            self.instrument.write(':BIAS:STAT OFF')
            self.instrument.write(':DISP:PAGE MEAS')
        except Exception as e:
            print(f"  Warning during shutdown: {e}")
        finally:
            try:
                self.instrument.close()
                print("  E4980A connection closed.")
            finally:
                self.instrument = None


# ===============================================================================
# FRONTEND CLASS - The Main GUI Application
# ===============================================================================

class LCR_Freq_GUI:
    """The main GUI application class for Frequency measurements."""
    LOGO_SIZE = 110
    
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Modern Dark Theme ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    # Required output format string
    DATA_HEADER = "Frequency\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\ttheta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\tCp''\tCs''"

    def __init__(self, root):
        self.root = root
        self.root.title("Keysight E4980A Frequency Scan (Cp-G)")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.backend = LCR_Backend()
        self.file_location_path = ""
        self.data_storage = {
            'freq': [],
            'cp': [],
            'g': []
        }
        self.logo_image = None
        self.sweep_index = 0
        
        self.sweep_frequencies = np.concatenate([
            np.arange(40, 1000, 10),           
            np.arange(1000, 10000, 100),       
            np.arange(10000, 100000, 1000),    
            np.arange(100000, 1000000, 10000), 
            np.arange(1000000, 2000001, 100000)
        ])

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TCheckbutton', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TLabelframe', background=self.CLR_BG_DARK, bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)

        style.configure(
            'TButton',
            font=self.FONT_BASE, padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER,
            borderwidth=0, focusthickness=0, focuscolor='none'
        )
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.configure('green.Horizontal.TProgressbar', background=self.CLR_ACCENT_GREEN)

        mpl.rcParams.update({
            'font.family': 'Segoe UI',
            'font.size': self.FONT_SIZE_BASE,
            'axes.titlesize': self.FONT_SIZE_BASE + 2,
            'axes.labelsize': self.FONT_SIZE_BASE,
            'figure.facecolor': self.CLR_GRAPH_BG
        })

    def create_widgets(self):
        font_title_italic = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold', 'italic')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        Label(header_frame, text="Keysight E4980A: Frequency Scan (Cp-G)", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=font_title_italic).pack(side='left', padx=20, pady=10)

        # --- Utility Launch Buttons ---
        ttk.Button(header_frame, text="📈", command=launch_plotter_utility, width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header_frame, text="📟", command=launch_gpib_scanner, width=3).pack(side='right', padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=0)

        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=480)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info_frame = self.create_info_frame(scrollable_frame)
        info_frame.pack(fill='x', expand=True, padx=10, pady=5)
        
        input_frame = self.create_input_frame(scrollable_frame)
        input_frame.pack(fill='x', expand=True, padx=10, pady=5)
        
        console_frame = self.create_console_frame(scrollable_frame)
        console_frame.pack(fill='both', expand=True, padx=10, pady=5)

        self.create_graph_frame(right_panel)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((self.LOGO_SIZE, self.LOGO_SIZE), RESAMPLE_FILTER)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE / 2, self.LOGO_SIZE / 2, image=self.logo_image)
            except Exception as e:
                pass

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        for i in range(2):
            frame.grid_columnconfigure(i, weight=1)
        
        self.entries = {}
        pady = (2, 5)
        padx = 10

        self._add_entry(frame, "Sample Name", 'sample_name', 0, 0, colspan=2, default="Sample_FreqScan")
        self._add_entry(frame, "AC Bias Voltage (V)", 'ac_bias', 2, 0, default="1.0")
        self._add_entry(frame, "DC Bias Voltage (V)", 'dc_bias', 2, 1, default="0.0")
        self._add_entry(frame, "Delay per step (s)", 'delay', 4, 0, default="0.5")
        
        Label(frame, text="Aperture (:APER):", font=self.FONT_BASE).grid(row=4, column=1, padx=padx, pady=pady, sticky='w')
        self.aper_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly', values=['SHOR', 'MED', 'LONG'])
        self.aper_combobox.set('MED')
        self.aper_combobox.grid(row=5, column=1, padx=padx, pady=(0, 10), sticky='ew')

        self.var_alc = tk.BooleanVar(value=True)
        self.var_corr = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Enable Auto Level Control (ALC)", variable=self.var_alc).grid(row=6, column=0, columnspan=2, padx=padx, pady=2, sticky='w')
        ttk.Checkbutton(frame, text="Enable Open/Short Corrections", variable=self.var_corr).grid(row=7, column=0, columnspan=2, padx=padx, pady=2, sticky='w')

        Label(frame, text="LCR Meter VISA:", font=self.FONT_BASE).grid(row=8, column=0, columnspan=2, padx=padx, pady=(10,2), sticky='w')
        self.lcr_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lcr_combobox.grid(row=9, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky='ew')

        ttk.Button(frame, text="Scan Instruments", command=self._scan_for_visa).grid(row=10, column=0, padx=padx, pady=5, sticky='ew')
        ttk.Button(frame, text="Browse Save Loc...", command=self._browse_file_location).grid(row=10, column=1, padx=padx, pady=5, sticky='ew')

        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_sweep, style='Start.TButton')
        self.start_button.grid(row=11, column=0, padx=(padx, 5), pady=15, sticky='ew')
        
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_sweep, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=11, column=1, padx=(5, padx), pady=15, sticky='ew')

        self.lbl_current_freq = ttk.Label(frame, text="Measuring: -- Hz", font=('Segoe UI', 12, 'bold'), foreground=self.CLR_ACCENT_RED)
        self.lbl_current_freq.grid(row=12, column=0, columnspan=2, pady=5)

        self.progress_bar = ttk.Progressbar(frame, orient='horizontal', mode='determinate', style='green.Horizontal.TProgressbar')
        self.progress_bar.grid(row=13, column=0, columnspan=2, padx=padx, pady=(5, 10), sticky='ew')
        
        return frame

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0, height=8)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Frequency Scan Initialized. Spanning 40 Hz to 2 MHz.")
        return frame

    def create_graph_frame(self, parent):
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        
        self.ax_cp = self.figure.add_subplot(2, 1, 1)
        self.line_cp, = self.ax_cp.plot([], [], color='#C00000', marker='o', markersize=3, linestyle='-')
        self.ax_cp.set_ylabel("Capacitance, Cp (F)")
        self.ax_cp.set_title("Cp vs. Frequency", fontweight='bold')
        self.ax_cp.set_xscale('log')
        self.ax_cp.grid(True, linestyle='--', alpha=0.7)

        self.ax_g = self.figure.add_subplot(2, 1, 2)
        self.line_g, = self.ax_g.plot([], [], color='#2A6B3A', marker='s', markersize=3, linestyle='-')
        self.ax_g.set_xlabel("Frequency (Hz)")
        self.ax_g.set_ylabel("Conductance, G (S)")
        self.ax_g.set_title("G vs. Frequency", fontweight='bold')
        self.ax_g.set_xscale('log')
        self.ax_g.grid(True, linestyle='--', alpha=0.7)
        
        self.figure.tight_layout(pad=2.5)
        
        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _add_entry(self, parent, text, dict_key, r, c, colspan=1, default=""):
        Label(parent, text=f"{text}:", font=self.FONT_BASE).grid(row=r, column=c, padx=10, pady=(2, 0), sticky='w')
        entry = Entry(parent, font=self.FONT_BASE)
        entry.grid(row=r + 1, column=c, columnspan=colspan, padx=10, pady=(0, 10), sticky='ew')
        entry.insert(0, default)
        self.entries[dict_key] = entry 

    def calculate_impedance_parameters(self, f, cp, g):
        """Calculates all 18 parameters requested based on Cp, G, and frequency."""
        omega = 2 * np.pi * f
        
        # Avoid division by zero
        G_safe = g if g != 0 else 1e-20
        omega_safe = omega if omega != 0 else 1e-20

        Rp = 1.0 / G_safe
        B = omega * cp
        B_safe = B if B != 0 else 1e-20
        
        D = G_safe / B_safe
        Q = 1.0 / D if D != 0 else 0.0

        Y_mag = np.sqrt(g**2 + B**2)
        Y_mag_safe = Y_mag if Y_mag != 0 else 1e-20
        Z_mag = 1.0 / Y_mag_safe

        Rs = g / (Y_mag_safe**2)
        Xs = -B / (Y_mag_safe**2)
        Xs_safe = Xs if Xs != 0 else 1e-20

        theta_rad = math.atan2(Xs, Rs)
        theta_deg = math.degrees(theta_rad)

        chi = Xs
        Cs = -1.0 / (omega_safe * Xs_safe)
        Ls = Xs / omega_safe
        Lp = -1.0 / (omega_safe * B_safe)

        # Complex capacitance C* = C' - jC''
        Cp_double_prime = g / omega_safe
        Cs_double_prime = Cp_double_prime

        return [Q, D, g, B, cp, Lp, Cs, Ls, Z_mag, theta_rad, chi, Rs, theta_deg, Rp, Y_mag, omega, Cp_double_prime, Cs_double_prime]

    def start_sweep(self):
        try:
            params = {
                'sample_name': self.entries['sample_name'].get(),
                'ac_bias': float(self.entries['ac_bias'].get()),
                'dc_bias': float(self.entries['dc_bias'].get()),
                'delay': float(self.entries['delay'].get()),
                'aper': self.aper_combobox.get(),
                'alc_enabled': self.var_alc.get(),
                'corr_enabled': self.var_corr.get(),
                'lcr_visa': self.lcr_combobox.get()
            }
            if not all([params['sample_name'], params['lcr_visa'], self.file_location_path]):
                raise ValueError("Sample Name, VISA address, and Save Location are required.")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_FreqScan.txt"
            self.data_filepath = os.path.join(self.file_location_path, file_name)
            
            # Format output file matching request
            with open(self.data_filepath, 'w', encoding='utf-8') as f:
                f.write(f"# Sample: {params['sample_name']} | AC: {params['ac_bias']}V | DC: {params['dc_bias']}V | APER: {params['aper']}\n")
                f.write(f"# ALC: {params['alc_enabled']} | Corrections: {params['corr_enabled']}\n")
                f.write(self.DATA_HEADER + "\n")

            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")
            
            if params['corr_enabled']:
                self.log("WARNING: Ensure physical Open/Short execution was performed prior to enabling corrections!")

            self.backend.initialize_instrument(params)
            
            try:
                self.is_running = True
                self.start_button.config(state='disabled')
                self.stop_button.config(state='normal')
                
                for key in self.data_storage:
                    self.data_storage[key].clear()
                
                self.line_cp.set_data([], [])
                self.line_g.set_data([], [])
                self.canvas.draw()

                self.sweep_index = 0
                self.progress_bar['value'] = 0
                self.progress_bar['maximum'] = len(self.sweep_frequencies)

                self.log("Starting Frequency sweep...")
                self.root.after(100, self._sweep_loop)

            except Exception as sweep_err:
                self.backend.close_instrument()
                raise sweep_err

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start sweep.\n\n{e}")

    def stop_sweep(self, reason=""):
        if self.is_running:
            self.is_running = False
            self.lbl_current_freq.config(text="Measuring: STOPPED")
            
            if reason:
                self.log(f"Sweep stopped: {reason}")
            else:
                self.log("Sweep stopped by user.")
                
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            
            self.backend.close_instrument()
            
            if not reason:
                messagebox.showinfo("Info", "Sweep stopped and instrument disconnected.")

    def _sweep_loop(self):
        if not self.is_running:
            return

        try:
            if self.sweep_index >= len(self.sweep_frequencies):
                self._handle_sweep_completion()
                return

            target_f = self.sweep_frequencies[self.sweep_index]
            self.lbl_current_freq.config(text=f"Measuring: {target_f:,.0f} Hz")
            
            delay_sec = float(self.entries['delay'].get())
            cp, g = self.backend.perform_measurement(target_f, delay_sec)
            self._process_sweep_point(target_f, cp, g)
            
            self.sweep_index += 1
            self._update_sweep_plot()

            if self.is_running:
                self.root.after(10, self._sweep_loop)

        except Exception as e:
            self._handle_sweep_error(e)

    def _scan_for_visa(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: PyVISA not found.")
            return
        
        backend = self.backend
        if backend.rm is None:
            self.log("ERROR: VISA manager failed.")
            return
            
        self.log("Scanning for VISA instruments...")
        try:
            resources = backend.rm.list_resources()
            if resources:
                self.lcr_combobox['values'] = resources
                for res in resources:
                    if "GPIB0::17" in res: 
                        self.lcr_combobox.set(res)
                        break
                if not self.lcr_combobox.get():
                    self.lcr_combobox.set(resources[0])
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
            if messagebox.askyesno("Exit", "Sweep is running. Stop and exit?"):
                self.stop_sweep("User closed application.")
                self.root.destroy()
        else:
            self.root.destroy()

    def _process_sweep_point(self, f, cp, g):
        self.log(f"f: {f} Hz | Cp: {cp:.4e} F | G: {g:.4e} S")
        self.data_storage['freq'].append(f)
        self.data_storage['cp'].append(cp)
        self.data_storage['g'].append(g)
        
        # Calculate full impedance parameters
        calc_vals = self.calculate_impedance_parameters(f, cp, g)
        row_vals = [f] + calc_vals
        
        # Format matching: 40.000000E+0	14.013077E+0
        row_str = "\t".join([f"{v:.6E}" for v in row_vals])
        
        with open(self.data_filepath, 'a', encoding='utf-8') as file:
            file.write(row_str + "\n")

    def _update_sweep_plot(self):
        self.line_cp.set_data(self.data_storage['freq'], self.data_storage['cp'])
        self.line_g.set_data(self.data_storage['freq'], self.data_storage['g'])
        
        self.ax_cp.relim()
        self.ax_cp.autoscale_view()
        self.ax_g.relim()
        self.ax_g.autoscale_view()
        
        self.figure.tight_layout(pad=2.5)
        self.canvas.draw()
        self.progress_bar['value'] = self.sweep_index

    def _handle_sweep_completion(self):
        self.lbl_current_freq.config(text="Measuring: DONE")
        self.log("Sweep finished successfully.")
        self.stop_sweep("Sweep naturally complete.")
        messagebox.showinfo("Finished", "Frequency sweep is complete.")

    def _handle_sweep_error(self, exception):
        self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        self.stop_sweep("A critical hardware or measurement error occurred.")
        messagebox.showerror("Runtime Error", "An error occurred during the sweep. Check console.")

def main():
    if not PYVISA_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Dependency Error", "PyVISA is not installed.\n\nPlease run:\npip install pyvisa")
        return

    root = tk.Tk()
    LCR_Freq_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()