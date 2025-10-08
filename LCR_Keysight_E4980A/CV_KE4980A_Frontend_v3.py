'''
===============================================================================
 PROGRAM:      Keysight E4980A C-V Measurement GUI

 PURPOSE:      Provide a user-friendly interface for automating C-V sweeps.

 DESCRIPTION:  This program provides a graphical user interface (GUI) for
               automating a Capacitance-Voltage (C-V) sweep using a Keysight
               E4980A LCR Meter. It allows users to define sweep parameters,
               visualize the C-V curve in real-time, and save the data
               automatically.

 AUTHOR:       Prathamesh Deshmukh 

 VERSION:      1.0
 LAST EDITED:  05/10/2025
===============================================================================
'''

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas, font
import os
import time
import traceback
from datetime import datetime
import csv
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    from pymeasure.instruments.agilent import AgilentE4980
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa, AgilentE4980 = None, None
    PYMEASURE_AVAILABLE = False


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
        # Assumes the plotter is in a standard location relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(script_dir, "..", "Utilities", "PlotterUtil_Frontend_v2.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")

def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        # Assumes the scanner is in a standard location relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(script_dir, "..", "Utilities", "GPIB_Instrument_Scanner_Frontend_v4.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")

#===============================================================================
# BACKEND CLASS - Instrument Control Logic
#===============================================================================
class LCR_Backend:
    """A dedicated class to handle backend communication with the Keysight E4980A."""
    def __init__(self):
        self.instrument = None
        self.lcr = None
        self.params = {}
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None

    def initialize_instrument(self, parameters):
        """Receives all parameters from the GUI and configures the instrument."""
        print("\n--- [Backend] Initializing Keysight E4980A ---")
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure library is required. Please run 'pip install pymeasure'.")

        try:
            print(f"  Connecting to E4980A at {self.params['lcr_visa']}...")
            self.instrument = self.rm.open_resource(self.params['lcr_visa'])
            self.lcr = AgilentE4980(self.params['lcr_visa'])

            self.instrument.timeout = 100000
            self.instrument.read_termination = '\n'
            self.instrument.write_termination = '\n'

            self.instrument.write('*RST; *CLS')
            self.instrument.write(':DISP:ENAB')
            time.sleep(2)
            self.instrument.write(':INIT:CONT')
            self.instrument.write(':TRIG:SOUR EXT')
            time.sleep(2)
            self.instrument.write(':APER MED')
            self.instrument.write(':FUNC:IMP:RANGE:AUTO ON')
            time.sleep(2)
            self.instrument.write(f":FREQ {self.params['freq']}")
            self.instrument.write(f":VOLT:LEVEL {self.params['v_ac']}")
            self.instrument.write(':BIAS:STATe ON')
            time.sleep(2)

            print(f"    Connected to: {self.instrument.query('*IDN?').strip()}")
            print("--- [Backend] Instrument Initialization Complete ---")
            return True
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure the instrument. {e}")
            raise e

    def perform_measurement(self, voltage):
        """Sets a voltage and performs a single C-V measurement."""
        if not self.instrument or not self.lcr:
            raise ConnectionError("Instrument is not connected.")

        self.instrument.write(f':BIAS:VOLTage:LEVel {voltage}')
        time.sleep(1) # Settling time
        self.instrument.write(':INITiate[:IMMediate]')
        time.sleep(1) # Measurement time

        # Using pymeasure's values method is cleaner
        values = self.lcr.values(":FETCh:IMPedance:FORMatted?")
        capacitance = values[0]
        
        # Query the actual voltage back for verification
        actual_voltage_str = self.instrument.query(':BIAS:VOLTage:LEVel?')
        actual_voltage = float(actual_voltage_str)

        return actual_voltage, capacitance

    def close_instrument(self):
        """Safely shuts down the instrument."""
        print("--- [Backend] Closing instrument connection. ---")
        if self.instrument:
            try:
                print("  Turning off bias and resetting display...")
                self.instrument.write(':BIAS:STATe OFF')
                self.instrument.write(':DISP:PAGE MEAS')
                time.sleep(1)
                self.instrument.close()
                print("  E4980A connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning: Error during instrument shutdown. {e}")
            finally:
                self.instrument = None
                self.lcr = None

#===============================================================================
# FRONTEND CLASS - The Main GUI Application
#===============================================================================
class LCR_CV_GUI:
    """The main GUI application class for C-V measurements."""
    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    # --- Modern Dark Theme ---
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'; CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#FFC107'; CLR_ACCENT_GREEN = '#A7C957'; CLR_ACCENT_RED = '#E74C3C'
    CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11; FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Keysight E4980A C-V Measurement")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running = False
        self.backend = LCR_Backend()
        self.file_location_path = ""
        self.data_storage = {'voltage': [], 'capacitance': [], 'loop': [], 'protocol': []}
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
        style.configure('TLabelframe', background=self.CLR_BG_DARK, bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)
        
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])

        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])

        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])

        # Style for Progress Bar
        style.configure('green.Horizontal.TProgressbar', background=self.CLR_ACCENT_GREEN)

        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': self.FONT_SIZE_BASE, 'axes.titlesize': self.FONT_SIZE_BASE + 4, 'axes.labelsize': self.FONT_SIZE_BASE + 2, 'figure.facecolor': self.CLR_GRAPH_BG})

    def create_widgets(self):
        font_title_italic = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold', 'italic')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(header_frame, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(header_frame, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(header_frame, text="Keysight E4980A: C-V Measurement", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=font_title_italic).pack(side='left', padx=20, pady=10)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        
        # --- Left Panel using Grid for better control ---
        left_panel_container = ttk.Frame(main_pane)
        main_pane.add(left_panel_container, weight=0)
        
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=500) # Set a width for the scrollable area
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Populate Left Panel ---
        info_frame = self.create_info_frame(scrollable_frame); info_frame.pack(fill='x', expand=True, padx=10, pady=5)
        input_frame = self.create_input_frame(scrollable_frame); input_frame.pack(fill='x', expand=True, padx=10, pady=5)
        console_frame = self.create_console_frame(scrollable_frame); console_frame.pack(fill='both', expand=True, padx=10, pady=5)

        # --- Populate Right Panel ---
        self.create_graph_frame(right_panel)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        # frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo: {e}")

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')
        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8) 
        
        details_text = ("Program Name: C-V Measurement\n"
                        "Instrument: Keysight E4980A LCR Meter\n"
                        "Measurement Range: 20 Hz to 2 MHz")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')
        return frame

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        # frame.pack(pady=10, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady = (5, 5); padx = 10

        self._add_entry(frame, "Sample Name", 0, 0, colspan=2, default="Sample_CV")
        self._add_entry(frame, "Max Voltage (V)", 2, 0, default="2")
        self._add_entry(frame, "Voltage Step (V)", 2, 1, default="0.2")
        self._add_entry(frame, "Frequency (Hz)", 4, 0, default="1000")
        self._add_entry(frame, "AC Voltage (V)", 4, 1, default="0.5")
        self._add_entry(frame, "Number of Loops", 6, 0, default="1")

        Label(frame, text="LCR Meter VISA:", font=self.FONT_BASE).grid(row=8, column=0, columnspan=2, padx=padx, pady=pady, sticky='w')
        self.lcr_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lcr_combobox.grid(row=9, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky='ew')

        ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa).grid(row=10, column=0, columnspan=2, padx=padx, pady=5, sticky='ew')
        ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location).grid(row=11, column=0, columnspan=2, padx=padx, pady=5, sticky='ew')
        
        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_sweep, style='Start.TButton')
        self.start_button.grid(row=12, column=0, padx=(padx,5), pady=15, sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_sweep, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=12, column=1, padx=(5,padx), pady=15, sticky='ew')

        self.progress_bar = ttk.Progressbar(frame, orient='horizontal', mode='determinate', style='green.Horizontal.TProgressbar')
        self.progress_bar.grid(row=13, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky='ew')
        return frame

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        # frame.pack(pady=10, padx=10, fill='x')
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live C-V Curve')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot([], [], color='#C00000', marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Capacitance vs. Voltage", fontweight='bold')
        self.ax_main.set_xlabel("Bias Voltage (V)")
        self.ax_main.set_ylabel("Capacitance (F)")
        self.ax_main.grid(True, linestyle='--', alpha=0.7)
        self.figure.tight_layout(pad=2.5)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_sweep(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name:"].get(),
                'v_max': float(self.entries["Max Voltage (V):"].get()),
                'v_step': float(self.entries["Voltage Step (V):"].get()),
                'freq': float(self.entries["Frequency (Hz):"].get()),
                'v_ac': float(self.entries["AC Voltage (V):"].get()),
                'loops': int(self.entries["Number of Loops:"].get()),
                'lcr_visa': self.lcr_combobox.get()
            }
            if not all([params['sample_name'], params['lcr_visa'], self.file_location_path]):
                raise ValueError("Sample Name, VISA address, and Save Location are required.")
            if params['v_step'] <= 0 or params['loops'] <= 0:
                raise ValueError("Voltage Step and Number of Loops must be positive.")

            self.backend.initialize_instrument(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_CV.csv"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}", f"Freq: {params['freq']} Hz"])
                writer.writerow(["Voltage (V)", "Capacitance (F)", "Loop", "Protocol"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(f"C-V Curve for: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()
            
            self.progress_bar['value'] = 0
            self.progress_bar['maximum'] = self._get_total_sweep_points(params)

            self.log("Starting C-V sweep...")
            self.root.after(100, self._sweep_loop)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start sweep.\n\n{e}")

    def stop_sweep(self, reason=""):
        if self.is_running:
            self.is_running = False
            if reason: self.log(f"Sweep stopped: {reason}")
            else: self.log("Sweep stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instrument()
            self.log("Instrument connection closed.")
            if not reason: messagebox.showinfo("Info", "Sweep stopped and instrument disconnected.")

    def _get_total_sweep_points(self, params):
        """Calculates the total number of points in a full sweep."""
        v_max, v_step = params['v_max'], params['v_step']
        points_per_segment = len(np.arange(0, v_max + v_step, v_step))
        return (points_per_segment * 4 - 4) * params['loops']

    def _sweep_loop(self):
        """Generator-based loop to avoid freezing the GUI."""
        if not self.is_running: return
        
        params = self.backend.params
        v_max, v_step = params['v_max'], params['v_step']

        def sweep_generator():
            for loop_num in range(1, params['loops'] + 1):
                # Protocol A: 0 to V
                for v_ind in np.arange(0, v_max + v_step, v_step): yield (v_ind, loop_num, "A")
                # Protocol B: V to 0
                for v_ind in np.arange(v_max, 0 - v_step, -v_step): yield (v_ind, loop_num, "B")
                # Protocol C: 0 to -V
                for v_ind in np.arange(0, -v_max - v_step, -v_step): yield (v_ind, loop_num, "C")
                # Protocol D: -V to 0
                for v_ind in np.arange(-v_max, 0 + v_step, v_step): yield (v_ind, loop_num, "D")

        if not hasattr(self, 'sweep_gen'):
            self.sweep_gen = sweep_generator()

        try:
            target_v, loop_n, proto = next(self.sweep_gen)
            
            if not self.is_running: return

            actual_v, cap = self.backend.perform_measurement(target_v)
            self.log(f"V: {actual_v:.3f}V | C: {cap:.4e}F | Loop: {loop_n} ({proto})")

            # Store and save data
            self.data_storage['voltage'].append(actual_v)
            self.data_storage['capacitance'].append(cap)
            self.data_storage['loop'].append(loop_n)
            self.data_storage['protocol'].append(proto)
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow([f"{actual_v:.6f}", f"{cap:.6e}", loop_n, proto])

            # Update plot
            self.line_main.set_data(self.data_storage['voltage'], self.data_storage['capacitance'])
            self.ax_main.relim(); self.ax_main.autoscale_view()
            self.figure.tight_layout(pad=2.5)
            self.canvas.draw()
            self.progress_bar.step()

            self.root.after(50, self._sweep_loop) # Short delay before next point

        except StopIteration:
            self.log("Sweep finished successfully.")
            messagebox.showinfo("Finished", "C-V sweep is complete.")
            self.stop_sweep("Sweep complete.")
            del self.sweep_gen
        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_sweep("A critical error occurred.")
            messagebox.showerror("Runtime Error", "An error occurred during the sweep. Check console.")
            if hasattr(self, 'sweep_gen'): del self.sweep_gen

    def _scan_for_visa(self):
        if not PYMEASURE_AVAILABLE: self.log("ERROR: PyVISA/Pymeasure not found."); return
        if self.backend.rm is None: self.log("ERROR: VISA manager failed. Is NI-VISA installed?"); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lcr_combobox['values'] = resources
                for res in resources:
                    if "GPIB0::17" in res: # Common GPIB for E4980A
                        self.lcr_combobox.set(res)
                        break
                if not self.lcr_combobox.get(): self.lcr_combobox.set(resources[0])
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Sweep is running. Stop and exit?"):
                self.stop_sweep(); self.root.destroy()
        else: self.root.destroy()
    
    def _add_entry(self, parent, text, r, c, colspan=1, default=""):
        Label(parent, text=f"{text}:", font=self.FONT_BASE).grid(row=r, column=c, padx=10, pady=(5,0), sticky='w')
        entry = Entry(parent, font=self.FONT_BASE)
        entry.grid(row=r+1, column=c, columnspan=colspan, padx=10, pady=(0, 10), sticky='ew')
        entry.insert(0, default)
        self.entries[text] = entry

def main():
    """Initializes and runs the main application."""
    if not PYMEASURE_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Dependency Error", "Pymeasure or PyVISA is not installed.\n\nPlease run:\npip install pymeasure")
        return

    root = tk.Tk()
    app = LCR_CV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
