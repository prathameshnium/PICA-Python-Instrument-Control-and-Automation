# -------------------------------------------------------------------------------
# Name:         Lakeshore 350 Temperature Ramp Utility
# Purpose:      Provide a professional GUI for setting a temperature ramp on
#               the Lakeshore 350, with heater range control and live monitoring.
# Author:       Prathamesh Deshmukh
# Created:      05/10/2025
# Version:      8.0 (Feature Update: Added Plotter & GPIB Utilities)
# -------------------------------------------------------------------------------

# --- GUI and System Packages ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import sys
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process

# --- Optional Packages ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

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
class Lakeshore_Backend:
    def __init__(self):
        self.lakeshore = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            self.rm = None

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        idn = self.lakeshore.query('*IDN?').strip()
        print(f"  Lakeshore Connected: {idn}")
        return idn

    def configure_ramp(self, setpoint, rate, heater_range):
        self.lakeshore.write('*RST'); time.sleep(0.5); self.lakeshore.write('*CLS')
        self.set_heater_range(1, heater_range)
        self.lakeshore.write(f'SETP 1,{setpoint}')
        self.lakeshore.write(f'RAMP 1,1,{rate}') # Ramp ON

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None:
            raise ValueError("Invalid heater range.")
        self.lakeshore.write(f'RANGE {output},{range_code}')

    def get_status(self):
        temp = float(self.lakeshore.query('KRDG? A').strip())
        htr_output = float(self.lakeshore.query('HTR? 1').strip())
        return temp, htr_output

    def stop_ramp(self):
        if self.lakeshore:
            try:
                self.lakeshore.write('RAMP 1,0,0') # Ramp OFF
                self.set_heater_range(1, 'off')
                print("  Lakeshore ramp stopped and heater turned off.")
            except Exception as e:
                print(f"  Warning: Could not fully stop ramp. {e}")

    def shutdown(self):
        if self.lakeshore:
            try:
                self.stop_ramp()
                self.lakeshore.close()
            except Exception as e:
                print(f"  Warning: Error during Lakeshore shutdown. {e}")
            finally:
                self.lakeshore = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class TempControlGUI:
    PROGRAM_VERSION = "8.0"
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_FRAME_BG = '#3A506B'; CLR_INPUT_BG = '#4C566A'; CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_GOLD = '#A7C957', '#E74C3C', '#FFC107'
    CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(f"Lakeshore 350 Temperature Control v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x800")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.logo_image = None
        self.backend = Lakeshore_Backend()
        self.data_storage = {'time': [], 'temperature': [], 'heater': []}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor='#8D99AE')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11, 'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(header, text="Lakeshore 350 Temperature Ramp Utility", style='Header.TLabel', font=font_title_main, foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)

        plotter_button = ttk.Button(header, text="📈", command=launch_plotter_utility, width=3)
        plotter_button.pack(side='right', padx=10, pady=5)
        gpib_button = ttk.Button(header, text="📟", command=launch_gpib_scanner, width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(main_pane, width=450)
        main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(main_pane)
        main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        self._create_info_panel(panel, 0)
        self._create_control_panel(panel, 1)
        self._create_console_panel(panel, 2)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 110
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE, bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE/2, LOGO_SIZE/2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1], 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, background=self.CLR_FRAME_BG).grid(row=0, column=1, padx=10, pady=(25,0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, background=self.CLR_FRAME_BG).grid(row=1, column=1, padx=10, pady=(0,10), sticky='nw')

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Ramp Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)
        self.entries = {}

        self._create_entry(frame, "Target Temp (K)", "310", 0)
        self._create_entry(frame, "Ramp Rate (K/min)", "2", 1)
        self._create_entry(frame, "Logging Delay (s)", "1", 2)

        ttk.Label(frame, text="Heater Range:").grid(row=3, column=0, sticky='w', padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value='High')
        heater_cb = ttk.Combobox(frame, textvariable=self.heater_range_var, values=['Off', 'Low', 'Medium', 'High'], state='readonly')
        heater_cb.grid(row=3, column=1, sticky='ew', padx=10, pady=5)

        self.ls_cb = self._create_combobox(frame, "Lakeshore VISA", 4)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=10)
        button_frame.grid_columnconfigure((0,1,2), weight=1)
        self.start_button = ttk.Button(button_frame, text="Start Ramp", style='Start.TButton', command=self.start_ramp)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(button_frame, text="Stop", style='Stop.TButton', state='disabled', command=self.stop_ramp)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(button_frame, text="Scan", command=self._scan_for_visa).grid(row=0, column=2, sticky='ew', padx=5)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', borderwidth=0)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.log("Console initialized. Set parameters and start ramp.")

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        container = ttk.LabelFrame(panel, text='Live Temperature')
        container.grid(row=0, column=0, sticky='nsew')
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)

        self.line_temp, = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-')
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle='--', alpha=0.6)
        self.ax_temp.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)

        self.line_heater, = self.ax_heater.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-')
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    def start_ramp(self):
        try:
            self.params = self._validate_and_get_params()
            self.log("Connecting to Lakeshore...")
            self.backend.connect(self.params['ls_visa'])
            self.log("Configuring ramp...")
            self.backend.configure_ramp(self.params['setpoint'], self.params['rate'], self.params['heater_range'])
            self.log(f"Ramp started towards {self.params['setpoint']} K at {self.params['rate']} K/min.")

            self.set_ui_state(running=True)
            for key in self.data_storage: self.data_storage[key].clear()
            self.line_temp.set_data([], [])
            self.line_heater.set_data([], [])
            self.ax_temp.set_title(f"Ramping to {self.params['setpoint']} K")
            self.canvas.draw()

            self.start_time = time.time()
            self.root.after(100, self._monitoring_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Start Failed", f"{e}")
            self.backend.shutdown()

    def stop_ramp(self):
        if not self.is_running: return
        self.log("Stopping ramp by user request.")
        self.is_running = False
        self.backend.stop_ramp()
        self.set_ui_state(running=False)
        self.ax_temp.set_title("Ramp stopped.")
        self.canvas.draw_idle()
        messagebox.showinfo("Ramp Stopped", "The temperature ramp has been stopped and the heater is off.")

    def _monitoring_loop(self):
        if not self.is_running: return
        try:
            temp, htr_output = self.backend.get_status()
            elapsed = time.time() - self.start_time
            self.log(f"T: {temp:.3f} K | Heater: {htr_output:.1f}%")

            self.data_storage['time'].append(elapsed)
            self.data_storage['temperature'].append(temp)
            self.data_storage['heater'].append(htr_output)

            self.line_temp.set_data(self.data_storage['time'], self.data_storage['temperature'])
            self.line_heater.set_data(self.data_storage['time'], self.data_storage['heater'])

            for ax in [self.ax_temp, self.ax_heater]:
                ax.relim()
                ax.autoscale_view()

            self.canvas.draw_idle()

            # Check end condition
            if (self.params['rate'] > 0 and temp >= self.params['setpoint']) or \
               (self.params['rate'] < 0 and temp <= self.params['setpoint']):
                self.log("Target temperature reached. Ramp complete.")
                self.stop_ramp()
                messagebox.showinfo("Ramp Complete", f"Target temperature of {self.params['setpoint']} K has been reached.")
            else:
                self.root.after(int(self.params['delay_s'] * 1000), self._monitoring_loop)

        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            messagebox.showerror("Runtime Error", f"{e}")
            self.stop_ramp()

    def _validate_and_get_params(self):
        try:
            params = {
                'setpoint': float(self.entries["Target Temp (K)"].get()),
                'rate': float(self.entries["Ramp Rate (K/min)"].get()),
                'delay_s': float(self.entries["Logging Delay (s)"].get()),
                'heater_range': self.heater_range_var.get(),
                'ls_visa': self.ls_cb.get()
            }
            if not all(params.values()):
                raise ValueError("All fields must be filled.")
            if params['rate'] <= 0:
                raise ValueError("Ramp rate must be a positive number.")
            return params
        except Exception as e:
            raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        self.is_running = running
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values(): w.config(state=state)
        self.ls_cb.config(state=state if state == 'normal' else 'readonly')
        self.stop_button.config(state='normal' if running else 'disabled')

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        resources = self.backend.rm.list_resources()
        if resources:
            self.log(f"Found: {resources}")
            self.ls_cb['values'] = resources
            for r in resources:
                if 'GPIB' in r and ('12' in r or '15' in r):
                    self.ls_cb.set(r)
                    break
        else:
            self.log("No VISA instruments found.")

    def _create_entry(self, parent, label_text, default_value, row):
        ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky='w', padx=10, pady=5)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=5)
        entry.insert(0, default_value)
        self.entries[label_text] = entry

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky='w', padx=10, pady=5)
        cb = ttk.Combobox(parent, font=self.FONT_BASE, state='readonly')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=5)
        return cb

    def _on_closing(self):
        if self.is_running and messagebox.askyesno("Exit", "A ramp is active. Stop and exit?"):
            self.stop_ramp()
            self.root.destroy()
        elif not self.is_running:
            self.root.destroy()

if __name__ == '__main__':
    if not pyvisa:
        messagebox.showerror("Dependency Error", "PyVISA is not installed. Please run 'pip install pyvisa'.")
    else:
        root = tk.Tk()
        app = TempControlGUI(root)
        root.mainloop()