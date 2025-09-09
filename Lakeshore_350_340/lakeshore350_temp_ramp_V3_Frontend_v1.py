# -------------------------------------------------------------------------------
# Name:         Lakeshore 350 Temperature Ramp GUI
# Purpose:      Provide a user-friendly interface for running a temperature
#               ramp experiment with a Lakeshore 350 controller.
# Author:       UI Expert (Gemini)
# Created:      10/09/2025
# Version:      1.0
# Inspired by:  Delta_Lakeshore_Front_end_V7.py by Prathamesh
# -------------------------------------------------------------------------------

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
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
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
except ImportError:
    pyvisa = None

#===============================================================================
# BACKEND CLASS - Instrument Control Logic
#===============================================================================
class LakeshoreBackend:
    """
    A dedicated class to handle backend communication with the Lakeshore 350.
    This encapsulates the logic from the original lakeshore350_temp_ramp_V3.py script.
    """
    def __init__(self):
        self.instrument = None
        self.params = {}
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None

    def initialize_instrument(self, parameters):
        """Receives all parameters from the GUI and configures the instrument."""
        print("\n--- [Backend] Initializing Lakeshore 350 ---")
        self.params = parameters
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        try:
            print(f"  Connecting to Lakeshore 350 at {self.params['lakeshore_visa']}...")
            self.instrument = self.rm.open_resource(self.params['lakeshore_visa'])
            self.instrument.timeout = 10000
            print(f"    Connected to: {self.instrument.query('*IDN?').strip()}")

            # Reset and clear status
            self.instrument.write('*RST'); time.sleep(0.5)
            self.instrument.write('*CLS')

            # Configure heater (using fixed hardware values from original script)
            # resistance=1 (25Ω), max_current=2 (1A)
            self.instrument.write('HTRSET 1,1,2,0,1')
            print("  Heater configured (25Ω, 1A max).")
            print("--- [Backend] Instrument Initialization Complete ---")
            return True
        except pyvisa.errors.VisaIOError as e:
            print(f"  ERROR: Could not connect/configure the instrument. {e}")
            raise e

    def start_stabilization(self):
        """Begins the process of moving to the start temperature."""
        print(f"  Moving to start temperature: {self.params['start_temp']} K")
        self.instrument.write(f"SETP 1,{self.params['start_temp']}")
        self.instrument.write("RANGE 1,2") # Set heater to 'low' range to start
        print("  Heater range set to 'low'.")

    def start_ramp(self):
        """Configures and starts the temperature ramp."""
        print(f"  Ramp starting towards {self.params['end_temp']} K at {self.params['rate']} K/min.")
        # Ramp command: RAMP <output>,<on/off>,<rate>
        self.instrument.write(f"RAMP 1,1,{self.params['rate']}")
        self.instrument.write(f"SETP 1,{self.params['end_temp']}")
        self.instrument.write("RANGE 1,4") # Set heater to 'medium' for the ramp
        print("  Ramp configured and setpoint updated. Heater range set to 'medium'.")

    def get_measurement(self):
        """Performs a single measurement and returns temperature and heater output."""
        if not self.instrument:
            raise ConnectionError("Instrument is not connected.")
        try:
            temp_str = self.instrument.query('KRDG? A').strip()
            heater_str = self.instrument.query('HTR? 1').strip()
            return float(temp_str), float(heater_str)
        except (pyvisa.errors.VisaIOError, ValueError):
            return float('nan'), float('nan') # Return NaN on error

    def close_instrument(self):
        """Safely shuts down the heater and disconnects from the instrument."""
        print("--- [Backend] Closing instrument connection. ---")
        if self.instrument:
            try:
                print("  Turning off heater...")
                self.instrument.write("RANGE 1,0") # Set heater range to OFF
                time.sleep(0.5)
                self.instrument.close()
                print("  Lakeshore 350 connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning: Error during instrument shutdown. {e}")
            finally:
                self.instrument = None

#===============================================================================
# FRONTEND CLASS - The Main GUI Application
#===============================================================================
class MeasurementAppGUI:
    """The main GUI application class (Front End)."""
    # --- Theming and Styling (from Delta_Lakeshore_Front_end_V7) ---
    PROGRAM_VERSION = "1.0"
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Lakeshore 350 Temperature Ramp Control")
        self.root.geometry("1600x950")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1300, 850)

        self.is_running, self.start_time = False, None
        self.experiment_state = 'idle' # Can be 'idle', 'stabilizing', 'ramping'
        self.backend = LakeshoreBackend()
        self.file_location_path = ""
        self.data_storage = {'time': [], 'temperature': [], 'heater': []}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles for a modern look."""
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
        mpl.rcParams['font.family'] = 'Segoe UI'
        mpl.rcParams['font.size'] = self.FONT_SIZE_BASE
        mpl.rcParams['axes.titlesize'] = self.FONT_SIZE_BASE + 6
        mpl.rcParams['axes.labelsize'] = self.FONT_SIZE_BASE + 2

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500)
        main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg='white')
        main_pane.add(right_panel, weight=3)
        top_controls_frame = ttk.Frame(left_panel)
        left_panel.add(top_controls_frame, weight=0)
        self.create_info_frame(top_controls_frame)
        self.create_input_frame(top_controls_frame)
        console_pane = self.create_console_frame(left_panel)
        left_panel.add(console_pane, weight=1)
        self.create_graph_frame(right_panel)

    def create_header(self):
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        Label(header_frame, text="Lakeshore 350 Temperature Ramp Control", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='right', padx=20, pady=10)

    def _process_logo_image(self, input_path, size=120):
        """Dynamically processes the input jpeg to a circular, transparent-background image."""
        if not (PIL_AVAILABLE and os.path.exists(input_path)): return None
        try:
            with Image.open(input_path) as img:
                w, h = img.size
                d = min(w, h) * 0.8
                l, t, r, b = (w - d) / 2, (h - d) / 2, (w + d) / 2, (h + d) / 2
                img_cropped = img.crop((l, t, r, b))
                mask = Image.new('L', img_cropped.size, 0)
                ImageDraw.Draw(mask).ellipse((0, 0) + img_cropped.size, fill=255)
                img_cropped.putalpha(mask)
                return ImageTk.PhotoImage(img_cropped.resize((size, size), Image.Resampling.LANCZOS))
        except Exception as e:
            print(f"ERROR: Could not process logo image '{input_path}'. Reason: {e}")
            return None

    def create_info_frame(self, parent):
        frame = LabelFrame(parent, text='Information', bd=2, relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)
        logo_canvas = Canvas(frame, width=120, height=120, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, padx=15, pady=15)
        self.logo_image = self._process_logo_image("UGC_DAE_CSR.jpeg")
        if self.logo_image:
            logo_canvas.create_image(60, 60, image=self.logo_image)
        else:
            logo_canvas.create_text(60, 60, text="LOGO", font=self.FONT_TITLE, fill=self.CLR_FG_LIGHT)
        info_text_frame = ttk.Frame(frame, style='TFrame')
        info_text_frame.grid(row=0, column=1, padx=10, sticky='ns')
        info_text_frame.grid_rowconfigure(0, weight=1)
        info_text = ("Institute: UGC DAE CSR, Mumbai\n"
                     "Measurement: Temperature vs. Time Ramp\n\n"
                     "Instrument:\n  • Lakeshore Model 350")
        ttk.Label(info_text_frame, text=info_text, background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE, justify='left').grid(row=0, column=0, sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', bd=2, relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)

        self.entries = {}
        pady_val = (5, 5)

        # Row 0: Sample Name
        Label(frame, text="Sample Name:", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=0, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky='ew')

        # Row 1: Start Temp & End Temp
        Label(frame, text="Start Temperature (K):", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=2, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Start Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Start Temp"].grid(row=3, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')

        Label(frame, text="End Temperature (K):", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=2, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["End Temp"] = Entry(frame, font=self.FONT_BASE)
        self.entries["End Temp"].grid(row=3, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')

        # Row 2: Rate & Safety Cutoff
        Label(frame, text="Ramp Rate (K/min):", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=4, column=0, padx=10, pady=pady_val, sticky='w')
        self.entries["Rate"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Rate"].grid(row=5, column=0, padx=(10, 5), pady=(0, 10), sticky='ew')

        Label(frame, text="Safety Cutoff (K):", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=4, column=1, padx=10, pady=pady_val, sticky='w')
        self.entries["Safety Cutoff"] = Entry(frame, font=self.FONT_BASE)
        self.entries["Safety Cutoff"].grid(row=5, column=1, padx=(5, 10), pady=(0, 10), sticky='ew')

        # Row 3: Instrument Selection
        Label(frame, text="Lakeshore 350 VISA:", font=self.FONT_BASE, fg=self.CLR_FG_LIGHT, bg=self.CLR_BG_DARK).grid(row=6, column=0, columnspan=2, padx=10, pady=pady_val, sticky='w')
        self.lakeshore_combobox = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.lakeshore_combobox.grid(row=7, column=0, columnspan=2, padx=10, pady=(0, 0), sticky='ew')
        self.scan_button = ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa_instruments)
        self.scan_button.grid(row=8, column=0, columnspan=2, padx=10, pady=10, sticky='ew')

        # Row 4 & 5: Controls
        self.file_location_button = ttk.Button(frame, text="Browse Save Location...", command=self._browse_file_location)
        self.file_location_button.grid(row=9, column=0, columnspan=2, padx=10, pady=5, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=10, column=0, padx=(10,5), pady=15, sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=10, column=1, padx=(5,10), pady=15, sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', bd=2, relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0, highlightthickness=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Please configure parameters and scan for instruments.")
        if not PIL_AVAILABLE: self.log("WARNING: Pillow not found. Logo cannot be displayed. Run 'pip install Pillow'.")
        if not os.path.exists("UGC_DAE_CSR.jpeg"): self.log("WARNING: 'UGC_DAE_CSR.jpeg' not found in script directory.")
        return frame

    def create_graph_frame(self, parent):
        graph_container = LabelFrame(parent, text='Live Graphs', bd=5, relief='groove', bg='white', fg=self.CLR_BG_DARK, font=self.FONT_TITLE)
        graph_container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        gs = gridspec.GridSpec(2, 1, figure=self.figure, height_ratios=[3, 1])
        self.ax_main = self.figure.add_subplot(gs[0, 0])
        self.ax_sub1 = self.figure.add_subplot(gs[1, 0], sharex=self.ax_main) # Share X-axis
        for ax in [self.ax_main, self.ax_sub1]:
            ax.set_facecolor('#EAEAEA'); ax.grid(True, linestyle='--', alpha=0.7, color='white')
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.line_sub1, = self.ax_sub1.plot([], [], color=self.CLR_ACCENT_BLUE, marker='.', markersize=4, linestyle='-')
        self.ax_main.set_title("Temperature vs. Time", fontweight='bold')
        self.ax_main.set_ylabel("Temperature (K)")
        self.ax_main.tick_params(axis='x', labelbottom=False) # Hide x-axis labels on top plot
        self.ax_sub1.set_xlabel("Elapsed Time (s)")
        self.ax_sub1.set_ylabel("Heater Output (%)")
        self.figure.tight_layout(pad=2.5)
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
            params['start_temp'] = float(self.entries["Start Temp"].get())
            params['end_temp'] = float(self.entries["End Temp"].get())
            params['rate'] = float(self.entries["Rate"].get())
            params['safety_cutoff'] = float(self.entries["Safety Cutoff"].get())
            params['lakeshore_visa'] = self.lakeshore_combobox.get()

            # --- Validation ---
            if not all([params['sample_name'], params['lakeshore_visa'], self.file_location_path]):
                raise ValueError("Sample Name, VISA address, and Save Location are all required.")
            if not (params['start_temp'] < params['end_temp'] < params['safety_cutoff']):
                raise ValueError("Temperatures must be in ascending order (Start < End < Cutoff).")
            if params['rate'] <= 0:
                raise ValueError("Ramp rate must be a positive number.")

            # --- Initialization ---
            self.backend.initialize_instrument(params)
            self.log(f"Backend initialized for sample: {params['sample_name']}")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"{params['sample_name']}_{timestamp}_T_vs_Time.csv"
            self.data_filepath = os.path.join(self.file_location_path, file_name)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {params['sample_name']}", f"Rate: {params['rate']} K/min"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Heater Output (%)"])
            self.log(f"Output file created: {os.path.basename(self.data_filepath)}")

            # --- Start UI and State Machine ---
            self.is_running = True
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1]: line.set_data([], [])
            self.ax_main.set_title(f"Ramp for Sample: {params['sample_name']}", fontweight='bold')
            self.canvas.draw()
            self.log("Moving to start temperature for stabilization...")
            self.experiment_state = 'stabilizing'
            self.backend.start_stabilization()
            self.root.after(2000, self._update_measurement_loop) # Start loop after 2s

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            messagebox.showerror("Initialization Error", f"Could not start measurement.\n\n{e}")

    def stop_measurement(self, reason=""):
        if self.is_running:
            self.is_running = False
            self.experiment_state = 'idle'
            if reason:
                self.log(f"Measurement stopped: {reason}")
            else:
                self.log("Measurement stopped by user.")
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            self.backend.close_instrument()
            self.log("Instrument connection closed.")
            if reason:
                 messagebox.showinfo("Info", f"Measurement finished.\nReason: {reason}")
            else:
                 messagebox.showinfo("Info", "Measurement stopped and instrument disconnected.")

    def _update_measurement_loop(self):
        if not self.is_running: return
        try:
            current_temp, heater_output = self.backend.get_measurement()
            params = self.backend.params

            # --- STATE: STABILIZING ---
            if self.experiment_state == 'stabilizing':
                self.log(f"Stabilizing... Current Temp: {current_temp:.4f} K (Target: {params['start_temp']} K)")
                if abs(current_temp - params['start_temp']) < 0.1: # Stabilization tolerance
                    self.log(f"Stabilized at {params['start_temp']} K. Starting ramp.")
                    self.experiment_state = 'ramping'
                    self.backend.start_ramp()
                    self.start_time = time.time() # Start timer now
            # --- STATE: RAMPING ---
            elif self.experiment_state == 'ramping':
                elapsed_time = time.time() - self.start_time
                # Log data to file
                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                     f"{elapsed_time:.2f}", f"{current_temp:.4f}", f"{heater_output:.2f}"])
                # Update data for plotting
                self.data_storage['time'].append(elapsed_time)
                self.data_storage['temperature'].append(current_temp)
                self.data_storage['heater'].append(heater_output)
                self.log(f"Time: {elapsed_time:7.1f}s | Temp: {current_temp:8.4f}K | Heater: {heater_output:5.1f}%")

                # Update plots
                self.line_main.set_data(self.data_storage['time'], self.data_storage['temperature'])
                self.line_sub1.set_data(self.data_storage['time'], self.data_storage['heater'])
                for ax in [self.ax_main, self.ax_sub1]:
                    ax.relim(); ax.autoscale_view()
                self.figure.tight_layout(pad=2.5)
                self.canvas.draw()

                # Check for end conditions
                if current_temp >= params['safety_cutoff']:
                    self.stop_measurement(f"SAFETY CUTOFF REACHED at {current_temp:.4f} K!")
                    return
                if current_temp >= params['end_temp']:
                    self.stop_measurement(f"Target temperature of {params['end_temp']} K reached.")
                    return

        except Exception:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
            self.stop_measurement("A critical error occurred.")
            messagebox.showerror("Runtime Error", "An error occurred during measurement. Check console.")
            return

        if self.is_running:
            self.root.after(2000, self._update_measurement_loop) # Loop every 2 seconds

    def _scan_for_visa_instruments(self):
        if pyvisa is None: self.log("ERROR: PyVISA not found. Run 'pip install pyvisa'."); return
        if self.backend.rm is None: self.log("ERROR: VISA manager failed. Is NI-VISA installed?"); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.lakeshore_combobox['values'] = resources
                for res in resources:
                    if "12" in res or "13" in res or "15" in res:
                        self.lakeshore_combobox.set(res)
                        break
                if not self.lakeshore_combobox.get(): self.lakeshore_combobox.set(resources[0])
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"ERROR during scan: {e}")

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