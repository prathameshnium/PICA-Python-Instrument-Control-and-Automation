# -------------------------------------------------------------------------------
# Name:         Delta Mode I-V Sweep (Ambient)
# Purpose:      Perform an I-V sweep using a Keithley 6221/2182A in Delta Mode
#               at ambient temperature.
#
# Author:       Prathamesh Deshmukh
#
# Created:      03/10/2025
#
# Version:      1.0
#
# Description:  This program adapts the Delta Mode measurement logic into a
#               full I-V sweep application. It is inspired by the sweep
#               functionality of 6517B_high_resistance_IV_V8.py and the
#               GUI/backend of the previous Delta Mode passive logger.
# -------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
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
except ImportError:
    pyvisa = None


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Backend_IV_Delta:
    """
    A dedicated class to handle the Keithley 6221 for an I-V Sweep.
    """
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

    def initialize_instrument(self, visa_address, compliance_v):
        """Connects to and configures the Keithley 6221."""
        print("\n--- [Backend] Initializing Keithley 6221 ---")
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            self.keithley = self.rm.open_resource(visa_address)
            self.keithley.timeout = 25000
            print(f"  Connected to: {self.keithley.query('*IDN?').strip()}")
            self.keithley.write("*rst; status:preset; *cls")
            self.keithley.write(f"SOUR:DELT:PROT {compliance_v}")
            print(f"  Compliance set to {compliance_v} V.")
            print("--- [Backend] Initialization Complete ---")
            return True
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure the instrument. {e}")
            raise e

    def measure_one_point(self, current):
        """Performs a single Delta Mode measurement at the specified current."""
        if not self.keithley:
            raise ConnectionError("Keithley 6221 is not connected.")

        self.keithley.write(f"SOUR:DELT:HIGH {current}")
        self.keithley.write("SOUR:DELT:ARM")
        time.sleep(0.1) # Short delay to ensure the arm command is processed
        self.keithley.write("INIT:IMM")

        # Wait for measurement to complete. The timeout on open_resource handles this.
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        data_points = raw_data.strip().split(',')
        voltage = float(data_points[0])
        return voltage

    def close_instrument(self):
        """Safely shuts down and disconnects from the instrument."""
        print("--- [Backend] Closing instrument connection. ---")
        if self.keithley:
            try:
                self.keithley.write("SOUR:CLE"); self.keithley.write("*RST"); self.keithley.close()
                print("  Keithley 6221 source cleared and connection closed.")
            except pyvisa.errors.VisaIOError: pass
            finally: self.keithley = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Delta_IV_GUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "1.0"
    LOGO_SIZE = 110
    LOGO_FILE_PATH = "UGC_DAE_CSR.jpeg"

    # --- Theming and Styling ---
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
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Delta Mode I-V Sweep")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running, self.sweep_index = False, 0
        self.backend = Backend_IV_Delta()
        self.current_points = []
        self.data_storage = {'current': [], 'voltage': [], 'resistance': []}
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
        style.configure('TRadiobutton', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.map('TRadiobutton', background=[('active', self.CLR_BG_DARK)])
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams['font.family'] = 'Segoe UI'; mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 4; mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

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
        self.create_info_frame(top_controls_frame)
        self.create_input_frame(top_controls_frame)
        console_pane = self.create_console_frame(left_panel)
        left_panel.add(console_pane, weight=1)
        self.create_graph_frame(right_panel)

    def create_header(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        Label(header, text="Delta Mode I-V Sweep", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(5, 0), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=15, pady=10)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e: self.log(f"ERROR: Failed to load logo. {e}")
        info_text = "Measurement: I-V Sweep (Delta Mode)\nInstrument:\n  • Keithley 6221/2182A"
        ttk.Label(frame, text=info_text, justify='left').grid(row=0, column=1, rowspan=2, padx=10, sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Sweep Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady = (5, 5); padx = 10

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=padx, pady=pady, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky='ew')

        Label(frame, text="Start Current (A):").grid(row=2, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Start Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Start Current"].insert(0, "-1E-5")
        self.entries["Start Current"].grid(row=3, column=0, padx=(padx, 5), pady=(0, 5), sticky='ew')

        Label(frame, text="Stop Current (A):").grid(row=2, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Stop Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Stop Current"].insert(0, "1E-5")
        self.entries["Stop Current"].grid(row=3, column=1, padx=(5, padx), pady=(0, 5), sticky='ew')

        Label(frame, text="Number of Points:").grid(row=4, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Num Points"] = Entry(frame, font=self.FONT_BASE); self.entries["Num Points"].insert(0, "51")
        self.entries["Num Points"].grid(row=5, column=0, padx=(padx, 5), pady=(0, 5), sticky='ew')

        Label(frame, text="Compliance (V):").grid(row=4, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].insert(0, "10")
        self.entries["Compliance"].grid(row=5, column=1, padx=(5, padx), pady=(0, 5), sticky='ew')

        self.sweep_scale_var = tk.StringVar(value="Linear")
        Label(frame, text="Sweep Scale:").grid(row=6, column=0, columnspan=2, padx=padx, pady=pady, sticky='w')
        ttk.Radiobutton(frame, text="Linear", variable=self.sweep_scale_var, value="Linear").grid(row=7, column=0, padx=padx, sticky='w')
        ttk.Radiobutton(frame, text="Logarithmic", variable=self.sweep_scale_var, value="Logarithmic").grid(row=7, column=1, padx=padx, sticky='w')

        Label(frame, text="Keithley 6221 VISA:").grid(row=8, column=0, columnspan=2, padx=padx, pady=(10,5), sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(row=9, column=0, columnspan=2, padx=padx, pady=(0,10), sticky='ew')

        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=10, column=0, columnspan=2, padx=padx, pady=4, sticky='ew')
        self.file_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_button.grid(row=11, column=0, columnspan=2, padx=padx, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=12, column=0, padx=(padx,5), pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=12, column=1, padx=(5,padx), pady=(10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized.")
        return frame

    def create_graph_frame(self, parent):
        container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_TEXT_DARK, font=self.FONT_TITLE)
        container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        gs = gridspec.GridSpec(2, 1, figure=self.figure, height_ratios=[3, 2])
        self.ax_main = self.figure.add_subplot(gs[0])
        self.ax_sub = self.figure.add_subplot(gs[1])
        # Main Plot: V vs I
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("I-V Curve", fontweight='bold'); self.ax_main.set_xlabel("Current (A)"); self.ax_main.set_ylabel("Voltage (V)")
        self.ax_main.grid(True, which="both", linestyle='--', alpha=0.6)
        # Sub Plot: R vs I
        self.line_sub, = self.ax_sub.plot([], [], color=self.CLR_ACCENT_GREEN, marker='s', markersize=4, linestyle=':')
        self.ax_sub.set_xlabel("Current (A)"); self.ax_sub.set_ylabel("Resistance (Ω)")
        self.ax_sub.grid(True, which="both", linestyle='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def start_measurement(self):
        try:
            params = {
                'name': self.entries["Sample Name"].get(),
                'start_i': float(self.entries["Start Current"].get()),
                'stop_i': float(self.entries["Stop Current"].get()),
                'points': int(self.entries["Num Points"].get()),
                'compliance': float(self.entries["Compliance"].get()),
                'scale': self.sweep_scale_var.get(),
                'visa': self.keithley_cb.get()
            }
            if not all(params.values()) or not hasattr(self, 'save_path'):
                raise ValueError("All fields, VISA address, and save location are required.")
            if params['points'] < 2: raise ValueError("Number of points must be at least 2.")

            # Generate sweep points
            if params['scale'] == 'Linear':
                self.current_points = np.linspace(params['start_i'], params['stop_i'], params['points'])
            else: # Logarithmic
                if params['start_i'] * params['stop_i'] <= 0:
                    raise ValueError("Log sweep cannot cross or include zero.")
                start_log = np.log10(abs(params['start_i']))
                stop_log = np.log10(abs(params['stop_i']))
                # Create sweep in log space, then apply sign
                log_sweep = np.logspace(start_log, stop_log, params['points'])
                self.current_points = log_sweep * np.sign(params['start_i'])

            # --- Initialize Backend ---
            self.backend.initialize_instrument(params['visa'], params['compliance'])

            # --- Setup file ---
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{params['name']}_{ts}_IV_Delta.dat"
            self.data_filepath = os.path.join(self.save_path, filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['name']}", f"Sweep: {params['start_i']:.2e}A to {params['stop_i']:.2e}A, {params['points']} pts ({params['scale']})"])
                writer.writerow(["Set Current (A)", "Measured Voltage (V)", "Resistance (Ohm)"])
            self.log(f"Output file created: {filename}")

            # --- Reset GUI and start ---
            self.is_running, self.sweep_index = True, 0
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub]: line.set_data([], [])
            self.ax_main.set_title(f"I-V Curve: {params['name']}", fontweight='bold')
            self.canvas.draw()
            self.log("Starting I-V sweep...")
            self.root.after(100, self._update_sweep_loop)

        except Exception as e:
            self.log(f"ERROR on startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start sweep.\n{e}")
            self.backend.close_instrument()

    def stop_measurement(self, completed=False):
        if self.is_running or completed:
            self.is_running = False
            self.log("Sweep stopped by user." if not completed else "Sweep completed.")
            self.backend.close_instrument()
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            if not completed: messagebox.showinfo("Info", "Sweep stopped.")
            else: messagebox.showinfo("Info", "Sweep finished successfully.")

    def _update_sweep_loop(self):
        if not self.is_running: return
        try:
            if self.sweep_index >= len(self.current_points):
                self.stop_measurement(completed=True)
                return

            current = self.current_points[self.sweep_index]
            self.log(f"Point {self.sweep_index + 1}/{len(self.current_points)}: Setting I = {current:.4e} A")

            voltage = self.backend.measure_one_point(current)
            resistance = voltage / current if current != 0 else float('inf')

            # --- Store and Save Data ---
            self.data_storage['current'].append(current)
            self.data_storage['voltage'].append(voltage)
            self.data_storage['resistance'].append(resistance)
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow([f"{current:.6e}", f"{voltage:.6e}", f"{resistance:.6e}"])

            # --- Update Plots ---
            self.line_main.set_data(self.data_storage['current'], self.data_storage['voltage'])
            self.line_sub.set_data(self.data_storage['current'], self.data_storage['resistance'])
            for ax in [self.ax_main, self.ax_sub]:
                ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=3.0)
            self.canvas.draw()

            # --- Continue Loop ---
            self.sweep_index += 1
            self.root.after(100, self._update_sweep_loop)

        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement()
            messagebox.showerror("Runtime Error", "A critical error occurred. Sweep stopped.")

    def _scan_for_visa_instruments(self):
        if not pyvisa or self.backend.rm is None:
            self.log("ERROR: PyVISA not found or failed to initialize."); return
        self.log("Scanning for VISA instruments...")
        resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}")
            self.keithley_cb['values'] = resources
            for res in resources:
                if "13" in res: self.keithley_cb.set(res)
        else: self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path: self.save_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Sweep is running. Stop and exit?"):
                self.stop_measurement()
                self.root.destroy()
        else: self.root.destroy()

def main():
    root = tk.Tk()
    app = Delta_IV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
