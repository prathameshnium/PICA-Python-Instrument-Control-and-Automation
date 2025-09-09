# -------------------------------------------------------------------------------
# Name:         Temperature Dependent Resistance GUI
# Purpose:      Perform a temperature-dependent Delta mode measurement with a
#               Keithley 6221/2182A and Lakeshore 350.
# Author:       Prathamesh & Gemini
# Created:      09/09/2025
# Version:      10.1 (Final Layout & Aesthetic Polish)
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
except ImportError:
    pyvisa = None

class Combined_Backend:
    """
    A dedicated class to handle backend instrument communication.
    (NO CHANGES MADE TO THIS CLASS)
    """
    def __init__(self):
        self.params = {}
        self.keithley = None
        self.lakeshore = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None

    def initialize_instruments(self, parameters):
        """Receives all parameters from the GUI and configures the instruments."""
        print("\n--- [Backend] Initializing Instruments ---")
        self.params = parameters
        # ... (backend code remains unchanged)
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            print("  Connecting to Keithley 6221...")
            self.keithley = self.rm.open_resource(self.params['keithley_visa'])
            self.keithley.timeout = 25000
            print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
            self.keithley.write("*rst; status:preset; *cls")
            self.keithley.write(f"SOUR:DELT:HIGH {self.params['apply_current']}")
            self.keithley.write("SOUR:DELT:ARM")
            time.sleep(1)
            self.keithley.write("INIT:IMM")
            print("  Keithley 6221/2182A Configured and Armed for Delta Mode.")
            print("  Connecting to Lakeshore 350...")
            self.lakeshore = self.rm.open_resource(self.params['lakeshore_visa'])
            print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
            self.lakeshore.write('*RST'); time.sleep(0.5); self.lakeshore.write('*CLS')
            self.lakeshore.write('RANGE 0')
            print("  Lakeshore 350 Configured (Heater OFF).")
            print("--- [Backend] Instrument Initialization Complete ---")
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure an instrument. {e}")
            raise e

    def get_measurement(self):
        """Performs a single measurement and returns all relevant data."""
        if not self.keithley or not self.lakeshore:
            raise ConnectionError("One or more instruments are not connected.")
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        data_points = raw_data.strip().split(',')
        voltage = float(data_points[0])
        resistance = voltage / self.params['apply_current']
        temp_str = self.lakeshore.query('KRDG? A').strip()
        temperature = float(temp_str)
        return resistance, voltage, temperature

    def close_instruments(self):
        """Safely shuts down and disconnects from all instruments."""
        print("--- [Backend] Closing instrument connections. ---")
        if self.keithley:
            try:
                self.keithley.write("SOUR:CLE"); self.keithley.write("*RST"); self.keithley.close()
                print("  Keithley 6221 connection closed.")
            except pyvisa.errors.VisaIOError: pass
            finally: self.keithley = None
        if self.lakeshore:
            try:
                self.lakeshore.write("RANGE 0"); self.lakeshore.close()
                print("  Lakeshore 350 connection closed.")
            except pyvisa.errors.VisaIOError: pass
            finally: self.lakeshore = None

class MeasurementAppGUI:
    """The main GUI application class (Front End)."""
    # --- Theming and Styling ---
    PROGRAM_VERSION = "10.1"
    # Colors
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B' # Harmonious blue-grey header
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_GRAPH_BG = '#F5F5F5'
    # Fonts
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Temperature Dependent Resistance Measurement")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running, self.start_time = False, None
        self.backend = Combined_Backend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'voltage': [], 'resistance': [], 'temperature': []}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles and Matplotlib for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')

        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)

        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8))
        style.map('TButton', foreground=[('!active', self.CLR_BG_DARK), ('active', self.CLR_FG_LIGHT)],
                  background=[('!active', self.CLR_ACCENT_BLUE), ('active', self.CLR_BG_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)

        style.configure('Toggle.TRadiobutton', indicatoron=False, font=('Segoe UI', 10), padding=(10, 4), relief='flat', width=7)
        style.map('Toggle.TRadiobutton',
                  background=[('selected', self.CLR_ACCENT_BLUE), ('!selected', 'white')],
                  foreground=[('selected', self.CLR_FG_LIGHT), ('!selected', self.CLR_BG_DARK)])

        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 6
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=480)
        main_pane.add(left_panel, weight=1)

        right_panel = tk.Frame(main_pane, bg='white')
        main_pane.add(right_panel, weight=3)

        top_controls_pane = ttk.Frame(left_panel)
        left_panel.add(top_controls_pane, weight=1)

        self.create_info_frame(top_controls_pane)
        self.create_input_frame(top_controls_pane)

        console_pane = self.create_console_frame(left_panel)
        left_panel.add(console_pane, weight=2)
        self.create_graph_frame(right_panel)

    def create_header(self):
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Temperature Dependent Resistance Measurement", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', bd=2, relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(frame, width=120, height=120, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, padx=15, pady=15)

        logo_path = "logo_hd.png"
        if PIL_AVAILABLE and os.path.exists(logo_path):
            try:
                img = Image.open(logo_path).resize((120, 120), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(60, 60, image=self.logo_image)
            except Exception as e:
                logo_canvas.create_text(60, 60, text="LOGO\n(Error)", font=self.FONT_BASE, fill=self.CLR_FG_LIGHT, justify='center')
                print(f"Logo Error: {e}")
        else:
            logo_canvas.create_text(60, 60, text="LOGO", font=self.FONT_TITLE, fill=self.CLR_FG_LIGHT)

        info_text_frame = ttk.Frame(frame, style='TFrame')
        info_text_frame.grid(row=0, column=1, padx=10, sticky='ns')
        info_text_frame.grid_rowconfigure(0, weight=1)

        info_text = ("Institute: UGC DAE CSR, Mumbai\n"
                     "Measurement: Temp. Dependent 4-Probe Resistance\n"
                     "(Delta Mode)\n\n"
                     "Instruments:\n  • Keithley 6221/2182A\n  • Lakeshore 350")
        ttk.Label(info_text_frame, text=info_text, background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE, justify='left').grid(row=0, column=0, sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', bd=2, relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        self.entries = {}
        Label(frame, text="Sample Name:", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=0, column=0, padx=10, pady=10, sticky='w')
        self.entries["Sample Name"] = Entry(frame, width=25, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=0, column=1, columnspan=2, padx=10, pady=10, sticky='ew')

        Label(frame, text="Apply Current (A):", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=1, column=0, padx=10, pady=(10,0), sticky='w')
        self.entries["Apply Current (A)"] = Entry(frame, width=25, font=self.FONT_BASE)
        self.entries["Apply Current (A)"].grid(row=1, column=1, columnspan=2, padx=10, pady=(10,0), sticky='ew')
        Label(frame, text="(Limits: 100pA to 105mA)", font=self.FONT_SUB_LABEL, fg=self.CLR_ACCENT_BLUE, bg=self.CLR_BG_DARK).grid(row=2, column=1, columnspan=2, padx=10, pady=(0,5), sticky='w')

        Label(frame, text="Keithley 6221 VISA:", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=3, column=0, padx=10, pady=(10,0), sticky='w')
        self.keithley_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_combobox.grid(row=3, column=1, columnspan=2, padx=10, pady=(10,0), sticky='ew')
        Label(frame, text="(Default VISA: 13)", font=self.FONT_SUB_LABEL, fg=self.CLR_ACCENT_BLUE, bg=self.CLR_BG_DARK).grid(row=4, column=1, columnspan=2, padx=10, pady=(0,5), sticky='w')

        Label(frame, text="Lakeshore 350 VISA:", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=5, column=0, padx=10, pady=(10,0), sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_combobox.grid(row=5, column=1, columnspan=2, padx=10, pady=(10,0), sticky='ew')
        Label(frame, text="(Default VISA: 15)", font=self.FONT_SUB_LABEL, fg=self.CLR_ACCENT_BLUE, bg=self.CLR_BG_DARK).grid(row=6, column=1, columnspan=2, padx=10, pady=(0,5), sticky='w')

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=7, column=0, columnspan=3, padx=10, pady=(15,5), sticky='ew')

        Label(frame, text="Save Location:", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=8, column=0, padx=10, pady=10, sticky='w')
        self.file_location_button = ttk.Button(frame, text="Browse...", command=self._browse_file_location)
        self.file_location_button.grid(row=8, column=1, columnspan=2, padx=10, pady=10, sticky='ew')

        self.start_button = ttk.Button(frame, text="Start", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=9, column=0, padx=10, pady=20, sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=9, column=1, padx=10, pady=20, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', bd=2, relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0, highlightthickness=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Please scan for instruments.")
        if not PIL_AVAILABLE:
            self.log("WARNING: Pillow not found. Logo cannot be displayed. Run 'pip install Pillow'.")
        if not os.path.exists("logo_hd.png"):
             self.log("WARNING: 'logo_hd.png' not found. Please run the logo processor script.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', bd=5, relief='groove', bg='white', fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)

        controls_frame = tk.Frame(graph_container, bg='white')
        controls_frame.pack(fill='x', pady=10, padx=10)

        self.x_scale_var, self.y_scale_var = tk.StringVar(value="linear"), tk.StringVar(value="linear")
        Label(controls_frame, text="X-Axis:", font=self.FONT_BASE, bg='white', fg=self.CLR_BG_DARK).pack(side='left', padx=(10, 5))
        ttk.Radiobutton(controls_frame, text="Linear", variable=self.x_scale_var, value="linear", command=self._update_plot_scales, style='Toggle.TRadiobutton').pack(side='left')
        ttk.Radiobutton(controls_frame, text="Log", variable=self.x_scale_var, value="log", command=self._update_plot_scales, style='Toggle.TRadiobutton').pack(side='left', padx=2)

        Label(controls_frame, text="Y-Axis:", font=self.FONT_BASE, bg='white', fg=self.CLR_BG_DARK).pack(side='left', padx=(30, 5))
        ttk.Radiobutton(controls_frame, text="Linear", variable=self.y_scale_var, value="linear", command=self._update_plot_scales, style='Toggle.TRadiobutton').pack(side='left')
        ttk.Radiobutton(controls_frame, text="Log", variable=self.y_scale_var, value="log", command=self._update_plot_scales, style='Toggle.TRadiobutton').pack(side='left', padx=2)

        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        # Use a simpler GridSpec for better layout control
        gs = gridspec.GridSpec(2, 2, figure=self.figure, height_ratios=[3, 2])

        self.ax_main = self.figure.add_subplot(gs[0, :]) # Main plot spans the entire top row
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0])
        self.ax_sub2 = self.figure.add_subplot(gs[1, 1])

        for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
            ax.set_facecolor(self.CLR_GRAPH_BG); ax.grid(True, linestyle='--', alpha=0.7, color='white')

        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=5, linestyle='')
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_BLUE, marker='.', markersize=5)
        self.line_sub2, = self.ax_sub2.plot([], [], color=self.CLR_ACCENT_GREEN, marker='.', markersize=5)

        self.ax_main.set_title("Resistance vs. Temperature", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)"); self.ax_main.set_ylabel("Resistance (Ohms)")
        self.ax_sub1.set_xlabel("Temperature (K)"); self.ax_sub1.set_ylabel("Voltage (V)")
        self.ax_sub2.set_xlabel("Time (s)"); self.ax_sub2.set_ylabel("Temperature (K)")

        self.figure.tight_layout(pad=2.5) # Adjust padding
        self.canvas = FigureCanvasTkAgg(self.figure, graph_container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def start_measurement(self):
        try:
            params = {}
            params['sample_name'] = self.entries["Sample Name"].get()
            params['apply_current'] = float(self.entries["Apply Current (A)"].get())
            params['keithley_visa'] = self.keithley_combobox.get()
            params['lakeshore_visa'] = self.lakeshore_combobox.get()
            if not all(params.values()) or not self.file_location_path:
                raise ValueError("All fields, VISA addresses, and a save location are required.")
            self.backend.initialize_instruments(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_R_vs_T.dat"
            self.data_filepath = os.path.join(self.file_location_path, file_name)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample Name: {params['sample_name']}", f"# Applied Current (A): {params['apply_current']}"])
                writer.writerow(["Time (s)", "Voltage (V)", "Resistance (Ohms)", "Temperature (K)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")
            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.ax_main.set_title(f"Sample: {params['sample_name']} | Current: {params['apply_current']} A", fontweight='bold')
            self.canvas.draw()
            self.log("Measurement loop started.")
            self.root.after(1000, self._update_measurement_loop)
        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n{e}")

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False
            self.log("Measurement loop stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instruments()
            self.log("Instrument connections closed.")
            messagebox.showinfo("Info", "Measurement stopped and instruments disconnected.")

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            r, v, t = self.backend.get_measurement()
            elapsed_time = time.time() - self.start_time
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow([f"{elapsed_time:.3f}", f"{v:.8f}", f"{r:.8f}", f"{t:.4f}"])
            self.data_storage['time'].append(elapsed_time); self.data_storage['voltage'].append(v)
            self.data_storage['resistance'].append(r); self.data_storage['temperature'].append(t)
            self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])
            self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['voltage'])
            self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature'])
            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]:
                ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=2.5) # Re-apply tight layout
            self.canvas.draw()
        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "An error occurred. Check console.")
        if self.is_running:
            self.root.after(1000, self._update_measurement_loop)

    def _update_plot_scales(self):
        self.ax_main.set_xscale(self.x_scale_var.get()); self.ax_main.set_yscale(self.y_scale_var.get())
        self.canvas.draw()

    def _scan_for_visa_instruments(self):
        if pyvisa is None:
            self.log("ERROR: PyVISA not found. Run 'pip install pyvisa'."); return
        if self.backend.rm is None:
            self.log("ERROR: VISA manager failed. Is NI-VISA installed?"); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources; self.lakeshore_combobox['values'] = resources
                for res in resources:
                    if "13" in res: self.keithley_combobox.set(res)
                    if "15" in res or "12" in res: self.lakeshore_combobox.set(res)
                if not self.keithley_combobox.get() and resources: self.keithley_combobox.set(resources[0])
                if not self.lakeshore_combobox.get() and resources: self.lakeshore_combobox.set(resources[-1])
            else: self.log("No VISA instruments found.")
        except Exception as e:
            self.log(f"ERROR during scan: {e}")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.file_location_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement is running. Stop and exit?"):
                self.stop_measurement(); self.root.destroy()
        else: self.root.destroy()

def main():
    root = tk.Tk()
    app = MeasurementAppGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()

