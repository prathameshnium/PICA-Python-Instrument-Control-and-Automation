# -------------------------------------------------------------------------------
# Name:             Integrated R-T Passive Data Logger
# Purpose:          Provide a GUI to passively measure and record Resistance vs.
#                   Temperature from a Lakeshore 350 and Keithley 6517B.
#                   This version DOES NOT control the temperature.
# Author:           Prathamesh Deshmukh
# Created:          26/09/2025
# Version:          V: 4.1 (Logo Fix)
# -------------------------------------------------------------------------------


# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import os
import time
import traceback
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
    from pymeasure.instruments.keithley import Keithley6517B
    from pyvisa.errors import VisaIOError
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa = None
    Keithley6517B = None
    VisaIOError = None
    PYMEASURE_AVAILABLE = False

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Lakeshore350_Backend:
    """A class to control the Lakeshore Model 350 Temperature Controller."""
    def __init__(self, visa_address):
        self.instrument = None
        rm = pyvisa.ResourceManager()
        self.instrument = rm.open_resource(visa_address)
        self.instrument.timeout = 10000
        print(f"Lakeshore Connected: {self.instrument.query('*IDN?').strip()}")

    def reset_and_clear(self):
        self.instrument.write('*RST'); time.sleep(0.5)
        self.instrument.write('*CLS'); time.sleep(1)

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None: raise ValueError("Invalid heater range.")
        self.instrument.write(f'RANGE {output},{range_code}')

    def get_temperature(self, sensor):
        return float(self.instrument.query(f'KRDG? {sensor}').strip())

    def get_heater_output(self, output):
        return float(self.instrument.query(f'HTR? {output}').strip())

    def close(self):
        if self.instrument:
            try:
                self.set_heater_range(1, 'off')
                time.sleep(0.5)
                self.instrument.close()
            except Exception as e:
                print(f"Warning: Issue during Lakeshore shutdown: {e}")
            finally:
                self.instrument = None



class Combined_Backend:
    """Manages both the Lakeshore 350 and Keithley 6517B."""
    def __init__(self):
        self.lakeshore = None
        self.keithley = None
        self.params = {}

    def initialize_instruments(self, parameters):
        self.params = parameters
        print("\n--- [Backend] Initializing Instruments ---")
        self.lakeshore = Lakeshore350_Backend(self.params['lakeshore_visa'])
        self.lakeshore.reset_and_clear()
        # --- ENSURE HEATER IS OFF ---
        self.lakeshore.set_heater_range(1, 'off')
        print("Lakeshore heater set to OFF.")

        self.keithley = Keithley6517B(self.params['keithley_visa'])
        print(f"Keithley Connected: {self.keithley.id}")
        self._perform_keithley_zero_check()

        self.keithley.source_voltage = self.params['source_voltage']
        self.keithley.current_nplc = 1
        self.keithley.enable_source()
        print(f"Keithley source enabled: {self.params['source_voltage']} V")

    def _perform_keithley_zero_check(self):
        print("  --- Starting Keithley Zero Correction ---")
        self.keithley.reset()
        self.keithley.measure_resistance()
        print("  Step 1: Enabling Zero Check (shorts the input)...")
        self.keithley.write(':SYSTem:ZCHeck ON')
        time.sleep(2)
        print("  Step 2: Acquiring the zero correction value...")
        self.keithley.write(':SYSTem:ZCORrect:ACQuire')
        time.sleep(3)
        print("  Step 3: Disabling Zero Check...")
        self.keithley.write(':SYSTem:ZCHeck OFF')
        time.sleep(1)
        print("  Step 4: Enabling Zero Correction for all measurements...")
        self.keithley.write(':SYSTem:ZCORrect ON')
        time.sleep(1)
        print("  Zero Correction Complete.")

    def get_measurement(self):
        time.sleep(self.params['delay'])
        current_temp = self.lakeshore.get_temperature('A')
        heater_output = self.lakeshore.get_heater_output(1) # Will always be 0
        resistance = self.keithley.resistance
        if resistance != 0 and resistance != float('inf') and resistance == resistance:
            current = self.params['source_voltage'] / resistance
        else:
            current = 0.0
        return current_temp, heater_output, current, resistance

    def close_instruments(self):
        print("\n--- [Backend] Closing all instrument connections. ---")
        if self.keithley:
            self.keithley.shutdown()
            print("  Keithley connection closed and source OFF.")
        if self.lakeshore:
            self.lakeshore.close() # This already turns the heater off
            print("  Lakeshore connection closed and heater OFF.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Integrated_RT_GUI:
    PROGRAM_VERSION = "4.1"
    LOGO_SIZE = 110

    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        # Path is two directories up from the script location
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "..", "_assets", "LOGO", "UGC_DAE_CSR.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../../_assets/LOGO/UGC_DAE_CSR.jpeg"

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

    def __init__(self, root):
        self.root = root
        self.root.title("Integrated R-T Passive Data Logger (Lakeshore 350 + Keithley 6517B)")
        self.root.geometry("1550x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 850)

        self.is_running = False
        self.start_time = None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': [], 'current': [], 'resistance': []}
        self.log_scale_var = tk.BooleanVar(value=True)
        self.logo_image = None # Attribute to hold the logo image reference

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TCheckbutton', background=self.CLR_GRAPH_BG, foreground=self.CLR_TEXT_DARK, font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_GRAPH_BG)])
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
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500)
        main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG)
        main_pane.add(right_panel, weight=3)
        top_controls_frame = ttk.Frame(left_panel)
        left_panel.add(top_controls_frame, weight=0)
        console_pane = self.create_console_frame(left_panel)
        self.create_info_frame(top_controls_frame)
        self.create_input_frame(top_controls_frame)
        left_panel.add(console_pane, weight=1)
        self.create_graph_frame(right_panel)

    def create_header(self):
        # --- NEW: Define an italic font for the program name ---
        font_title_italic = ('Segoe UI', self.FONT_SIZE_BASE + 2, 'bold italic')

        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Resistance vs. Temperature (Passive Logger)", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=font_title_italic).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_SUB_LABEL).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=(15, 10), pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                # IMPORTANT: Keep a reference to the image to prevent it from being garbage collected
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
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font).grid(row=0, column=1, padx=10, pady=(10,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font).grid(row=1, column=1, padx=10, sticky='nw')

        ttk.Separator(frame, orient='horizontal').grid(row=2, column=1, sticky='ew', padx=10, pady=8)

        # Program details
        details_text = ("Program Mode: R vs. T (Passive Logger)\n"
                        "Instruments: Lakeshore 350, Keithley 6517B\n"
                        "Measurement Range: 10³ Ω to 10¹⁶ Ω")
        ttk.Label(frame, text=details_text, justify='left').grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 10), sticky='w')


    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady_val = (5, 5)

        # --- SIMPLIFIED INPUTS ---
        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        Label(frame, text="Source Voltage (V):").grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Source Voltage"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Source Voltage"].grid(row=3, column=0, padx=(10,5), pady=(0,5), sticky='ew')

        Label(frame, text="Logging Delay (s):").grid(row=2, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Delay"].grid(row=3, column=1, padx=(5,10), pady=(0,5), sticky='ew')
        self.entries["Delay"].insert(0, "1.0") # Default to 1 second

        Label(frame, text="Lakeshore VISA:").grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_cb.grid(row=5, column=0, padx=(10,5), pady=(0,10), sticky='ew')

        Label(frame, text="Keithley VISA:").grid(row=4, column=1, padx=10, pady=pady_val, sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(row=5, column=1, padx=(5,10), pady=(0,10), sticky='ew')

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=6, column=0, columnspan=2, padx=10, pady=4, sticky='ew')
        self.file_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=7, column=0, columnspan=2, padx=10, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Logging", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=8, column=0, padx=(10,5), pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=8, column=1, padx=(5,10), pady=(10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Configure parameters and scan for instruments.")
        if not PYMEASURE_AVAILABLE: self.log("CRITICAL: PyMeasure or PyVISA not found.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        # Use a standard tk.Frame and set its background to match the graph
        # to make the checkbox appear integrated with the graph area.
        top_bar = tk.Frame(graph_container, bg=self.CLR_GRAPH_BG)
        top_bar.pack(side='top', fill='x', pady=(0, 5))
        self.log_scale_cb = ttk.Checkbutton(top_bar, text="Logarithmic Resistance Axis",
                                              variable=self.log_scale_var, command=self._update_y_scale)
        self.log_scale_cb.pack(side='right', padx=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure)
        self.ax_main = self.figure.add_subplot(gs[0, :])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_ylabel("Resistance (Ω)")
        if self.log_scale_var.get(): self.ax_main.set_yscale('log')
        else: self.ax_main.set_yscale('linear')
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-')
        self.ax_sub1.set_xlabel("Temperature (K)")
        self.ax_sub1.set_ylabel("Current (A)")
        self.ax_sub1.grid(True, linestyle='--', alpha=0.6)
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=3, linestyle='-')
        self.ax_sub2.set_xlabel("Time (s)")
        self.ax_sub2.set_ylabel("Temperature (K)")
        self.ax_sub2.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _update_y_scale(self):
        if self.log_scale_var.get(): self.ax_main.set_yscale('log')
        else: self.ax_main.set_yscale('linear')
        self.canvas.draw()

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
                'source_voltage': float(self.entries["Source Voltage"].get()),
                'delay': float(self.entries["Delay"].get()),
                'lakeshore_visa': self.lakeshore_cb.get(),
                'keithley_visa': self.keithley_cb.get()
            }
            if not all(params.values()) or not self.file_location_path:
                raise ValueError("All fields, VISA addresses, and save location are required.")

            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{ts}_RT_passive.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)

            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}", f"Source V: {params['source_voltage']}V"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)", "Applied Voltage (V)", "Measured Current (A)", "Resistance (Ohm)"])

            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            # --- START LOGGING DIRECTLY ---
            self.is_running = True
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()

            self.log("Starting passive data logging...")
            self.start_time = time.time()
            self.root.after(1000, self._update_measurement_loop)

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            messagebox.showinfo("Info", "Measurement stopped and instruments disconnected.")

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            temp, htr, cur, res = self.backend.get_measurement()
            elapsed = time.time() - self.start_time

            # --- Logging and Data Storage ---
            self.log(f"T:{temp:.3f}K | R:{res:.3e}Ω | I:{cur:.3e}A")
            with open(self.data_filepath, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}", f"{temp:.4f}", f"{htr:.2f}", f"{self.backend.params['source_voltage']:.4e}", f"{cur:.4e}", f"{res:.4e}"])

            self.data_storage['time'].append(elapsed)
            self.data_storage['temperature'].append(temp)
            self.data_storage['current'].append(cur)
            self.data_storage['resistance'].append(res)

            # --- Update Plots ---
            self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])
            self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['current'])
            self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature'])
            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
                ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw()

            # --- Continuous Loop until Stopped ---
            loop_delay_ms = max(1000, int(self.backend.params['delay'] * 1000))
            self.root.after(loop_delay_ms, self._update_measurement_loop)

        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}"); self.stop_measurement()

    def _scan_for_visa_instruments(self):
        if not pyvisa: self.log("ERROR: PyVISA is not installed."); return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_cb['values'] = resources
                self.keithley_cb['values'] = resources
                for res in resources:
                    if "15" in res or "12" in res: self.lakeshore_cb.set(res)
                    if "27" in res or "26" in res: self.keithley_cb.set(res)
            else:
                self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = Integrated_RT_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
