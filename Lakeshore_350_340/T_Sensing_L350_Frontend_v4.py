# -------------------------------------------------------------------------------
# Name:           Lakeshore 350 Passive Temperature Monitor
# Purpose:        Provide a GUI to passively monitor and record temperature
#                 from a Lakeshore 350 controller. This version DOES NOT
#                 control the temperature.
# Author:         Prathamesh Deshmukh
# Created:        28/09/2025
# Version:        V: 2.0 (Professional UI Refresh)
# -------------------------------------------------------------------------------


# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import traceback
import threading
import queue
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
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

import runpy
from multiprocessing import Process

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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # The plotter is in the Utilities folder, which is one level up from the script's parent directory
    plotter_path = os.path.join(script_dir, "..", "Utilities", "PlotterUtil_Frontend_v3.py")
    Process(target=run_script_process, args=(plotter_path,)).start()

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


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Lakeshore350_Backend:
    """A class to passively monitor the Lakeshore Model 350 Temperature Controller."""
    def __init__(self, visa_address):
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        print(f"Lakeshore Connected: {self.instrument.query('*IDN?').strip()}")

    def configure_for_monitoring(self):
        """Resets the instrument's event registers without changing settings."""
        self.instrument.write('*RST'); time.sleep(0.5)
        self.instrument.write('*CLS'); time.sleep(1)
        # The following line is commented out to ensure the heater state is not changed.
        # self.instrument.write('RANGE 1,0')
        print("Lakeshore connected for passive monitoring. Heater state is unchanged.")

    def get_temperature(self, sensor='A'):
        """Reads the temperature from a specified sensor."""
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def close(self):
        """Closes the connection to the instrument."""
        if self.instrument:
            try:
                # The following line is commented out to leave the heater in its current state.
                # self.instrument.write('RANGE 1,0')
                time.sleep(0.5)
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class TempMonitorGUI:
    PROGRAM_VERSION = "2.0"
    LOGO_SIZE = 110

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"
    
    # --- Modern Dark Theme (PICA Standard) ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_STATUS = ('Segoe UI', 28, 'bold')


    def __init__(self, root):
        self.root = root
        self.root.title("Lakeshore 350 Passive Temperature Monitor")
        self.root.state('zoomed') # Launch maximized
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = None
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': []}
        self.logo_image = None
        self.data_queue = queue.Queue()

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)

        # Custom style for the large status label
        style.configure('Status.TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_STATUS)

        style.configure('TEntry', fieldbackground='#4C566A', foreground=self.CLR_FG_LIGHT, insertcolor=self.CLR_FG_LIGHT, borderwidth=0)
        style.configure('TCombobox', fieldbackground='#4C566A', foreground=self.CLR_FG_LIGHT, arrowcolor=self.CLR_FG_LIGHT, selectbackground=self.CLR_ACCENT_GOLD, selectforeground=self.CLR_TEXT_DARK)

        style.configure('TButton',
                        font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton',
                        font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton',
                        font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])

        # Style for LabelFrames
        style.configure('TLabelframe', background=self.CLR_BG_DARK, bordercolor=self.CLR_HEADER, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.CLR_BG_DARK, foreground=self.CLR_ACCENT_GOLD, font=self.FONT_TITLE)

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel_container = ttk.Frame(main_pane, width=500)
        main_pane.add(left_panel_container, weight=0)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(left_panel_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_panel_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.create_info_frame(scrollable_frame)
        self.create_input_frame(scrollable_frame)
        self.create_status_frame(scrollable_frame)
        self.create_console_frame(scrollable_frame)

        self.create_graph_frame(right_panel)

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(header_frame, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(header_frame, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        Label(header_frame, text="Passive Temperature Monitor", bg=self.CLR_HEADER, fg=self.CLR_ACCENT_GOLD, font=font_title_main).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')

        # Institute Name (larger font)
        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_BG_DARK).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_BG_DARK).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)
 
        # Program details
        details_text = ("Program Name: Temperature Monitor\n"
                        "Instrument: Lakeshore 350 Controller\n"
                        "Measurement Range: 1.4 K to 800 K (Sensor Dependent)")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')

    def create_input_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Experiment Parameters')
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        Label(frame, text="Log File Name:").grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        ttk.Label(frame, text="Logging Delay (s):").grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Delay"] = ttk.Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(row=3, column=0, padx=10, pady=(0,5), sticky='ew')
        self.entries["Delay"].insert(0, "1.0")

        ttk.Label(frame, text="Lakeshore VISA:").grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(row=5, column=0, padx=10, pady=(0,10), sticky='ew')
        
        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=6, column=0, padx=10, pady=4, sticky='ew') # Changed row
        self.file_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=7, column=0, padx=10, pady=4, sticky='ew')

        control_frame = ttk.Frame(frame)
        control_frame.grid(row=8, column=0, padx=10, pady=(10, 10), sticky='ew')
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(control_frame, text="Start Logging", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=0, column=0, sticky='ew', padx=(0,5))
        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=(5,0))

    def create_status_frame(self, parent):
        """Creates the frame for displaying live temperature."""
        frame = ttk.LabelFrame(parent, text='Live Status')
        frame.pack(pady=5, padx=10, fill='x')

        status_inner_frame = ttk.Frame(frame, style='TFrame') # This frame inherits the dark background
        status_inner_frame.pack(fill='x', expand=True, padx=5, pady=5)

        self.temp_label_var = tk.StringVar(value="--.---- K")
        # The label's style gives it the dark background and gold text
        status_label = ttk.Label(status_inner_frame, textvariable=self.temp_label_var, style='Status.TLabel', anchor='center', padding=(0, 10))
        status_label.pack(pady=10, fill='x')

    def create_console_frame(self, parent):
        frame = ttk.LabelFrame(parent, text='Console Output')
        frame.pack(pady=5, padx=10, fill='x', expand=True)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0, relief='flat')
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        if not PYVISA_AVAILABLE: self.log("CRITICAL: PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = ttk.LabelFrame(parent, text='Live Graph')
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)

        self.ax_main = self.figure.add_subplot(1, 1, 1)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Temperature vs. Time", fontweight='bold')
        self.ax_main.set_xlabel("Elapsed Time (s)")
        self.ax_main.set_ylabel("Temperature (K)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {
                'sample_name': self.entries["Sample Name"].get(),
                'delay': float(self.entries["Delay"].get()),
                'lakeshore_visa': self.lakeshore_cb.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError("All fields, VISA address, and save location are required.")

            self.backend = Lakeshore350_Backend(params['lakeshore_visa'])
            self.backend.configure_for_monitoring()
            self.log(f"Backend initialized for: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_Temp_passive.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Log File: {params['sample_name']}"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            self.is_running = True
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(f"Temperature Log: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()

            self.log("Starting passive data logging...")
            self.start_time = time.time()
            
            self.measurement_thread = threading.Thread(target=self._measurement_worker, daemon=True)
            self.measurement_thread.start()
            self.root.after(100, self._process_data_queue)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start logging.\n{e}")
            if self.backend:
                self.backend.close()

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            if self.backend:
                self.backend.close()
            messagebox.showinfo("Info", "Logging stopped and instrument disconnected.")

    def _measurement_worker(self):
        """Worker thread for handling blocking instrument calls."""
        delay_s = float(self.entries["Delay"].get())
        while self.is_running:
            try:
                temp = self.backend.get_temperature()
                elapsed = time.time() - self.start_time
                self.data_queue.put((elapsed, temp))
                time.sleep(delay_s)
            except Exception as e:
                self.data_queue.put(e)
                break

    def _process_data_queue(self):
        """Processes data from the worker thread to update the GUI."""
        try:
            while not self.data_queue.empty():
                data = self.data_queue.get_nowait()
                if isinstance(data, Exception):
                    self.log(f"RUNTIME ERROR in worker thread: {traceback.format_exc()}")
                    self.stop_measurement()
                    messagebox.showerror("Runtime Error", "A critical error occurred. Check console.")
                    return

                elapsed, temp = data
                self.temp_label_var.set(f"{temp:.4f} K")
                self.log(f"T:{temp:.3f} K")

                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}", f"{temp:.4f}"])

                self.data_storage['time'].append(elapsed)
                self.data_storage['temperature'].append(temp)

                self.line_main.set_data(self.data_storage['time'], self.data_storage['temperature'])
                self.ax_main.relim(); self.ax_main.autoscale_view()
                self.figure.tight_layout(pad=3.0)
                self.canvas.draw_idle()

        except queue.Empty:
            pass # This is normal

        if self.is_running:
            self.root.after(200, self._process_data_queue)

    def _scan_for_visa_instruments(self):
        if not PYVISA_AVAILABLE: self.log("ERROR: PyVISA not installed."); return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_cb['values'] = resources
                for res in resources:
                    if "GPIB1::15" in res: self.lakeshore_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            if self.backend:
                self.backend.close()
            self.root.destroy()

def main():
    root = tk.Tk()
    app = TempMonitorGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()