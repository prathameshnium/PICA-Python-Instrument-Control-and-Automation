"""
Module: T_Control_L350_Step_GUI.py
Purpose: GUI module for T Control L350 Step Measurement GUI (Threaded & Multi-plot).
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas
import os
import time
import traceback
import threading
import queue
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib as mpl
import runpy
from multiprocessing import Process
import csv
import platform

# --- Optional Packages ---
try:
    import winsound
except ImportError:
    pass

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
    """Wrapper function to execute a script using runpy in its own directory."""
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_plotter_utility():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(script_dir, "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", f"Plotter utility not found at:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", f"GPIB Scanner not found at:\n{scanner_path}")
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
        self.lakeshore.write('*CLS')          # clear status once; do NOT *RST mid-run
        idn = self.lakeshore.query('*IDN?').strip()
        print(f"  Lakeshore Connected: {idn}")
        return idn

    def configure_ramp(self, setpoint, rate, heater_range):
        self.set_heater_range(1, heater_range)     # ensure heater on at desired range
        self.lakeshore.write(f'RAMP 1,1,{rate}')   # enable ramp FIRST
        time.sleep(0.1)
        self.lakeshore.write(f'SETP 1,{setpoint}') # now the change is ramped

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 1, 'medium': 3, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None:
            raise ValueError("Invalid heater range.")
        self.lakeshore.write(f'RANGE {output},{range_code}')

    def get_status(self):
        temp = float(self.lakeshore.query('KRDG? A').strip())       # Kelvin, input A
        resistance = float(self.lakeshore.query('SRDG? A').strip()) # sensor units (ohms)
        htr_output = float(self.lakeshore.query('HTR? 1').strip())  # heater %, output 1
        return temp, resistance, htr_output

    def stop_ramp(self):
        if self.lakeshore:
            try:
                self.lakeshore.write('RAMP 1,0,0')
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
    PROGRAM_VERSION = "9.3-Step"
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#8AB845'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_GOLD = '#B68B6E'
    CLR_STABLE_WAIT = '#D4A373'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 12, 'bold')
    FONT_CONSOLE = ('Consolas', 9)

    def __init__(self, root):
        self.root = root
        self.root.title(f"Lakeshore 350 Step Sequence Control v{self.PROGRAM_VERSION}")
        self.root.geometry("1450x850")
        self.root.minsize(1200, 750)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.measurement_thread = None
        self.gui_queue = queue.Queue()
        self.proceed_event = threading.Event()
        
        # Flag to safely pass live heater updates to the hardware thread
        self.live_heater_update = None 
        
        self.logo_image = None
        self.backend = Lakeshore_Backend()
        
        self.data_storage = {'time': [], 'temperature': [], 'target': [],
                             'resistance': [], 'heater': []}

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        
        style.configure('TButton', font=self.FONT_BASE, padding=(8, 6), foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)])
        
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FRAME_BG)
        style.configure('Proceed.TButton', font=('Segoe UI', 12, 'bold'), background=self.CLR_ACCENT_GREEN)
        
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG, foreground=self.CLR_TEXT_DARK)

        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 10, 'axes.titlesize': 12, 'axes.labelsize': 11})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(header, text="Lakeshore 350 Step Measurement Sequence Utility", style='Header.TLabel',
                  font=font_title_main, foreground=self.CLR_ACCENT_GOLD).pack(side='left', padx=20, pady=10)

        ttk.Button(header, text="📈", command=launch_plotter_utility, width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner, width=3).pack(side='right', padx=(0, 5), pady=5)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(main_pane, width=440)
        main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(main_pane)
        main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1) 
        
        self._create_info_panel(panel, 0)
        self._create_sequence_panel(panel, 1)
        self._create_settings_panel(panel, 2)
        self._create_console_panel(panel, 3)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        
        LOGO_SIZE = 90
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE, bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception:
            pass

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 2, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research", font=institute_font, 
                  background=self.CLR_FRAME_BG).grid(row=0, column=1, padx=5, pady=(15, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font, 
                  background=self.CLR_FRAME_BG).grid(row=1, column=1, padx=5, sticky='nw')

    def _create_sequence_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Measurement Sequence Builder')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=5)
        for i in range(4): frame.grid_columnconfigure(i, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, columnspan=4, sticky='nsew', padx=10, pady=5)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(list_frame, height=6, selectmode=tk.EXTENDED, 
                                  font=self.FONT_BASE, bg=self.CLR_INPUT_BG, fg=self.CLR_TEXT_DARK, 
                                  yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.listbox.yview)
        
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(frame, text="Start(K):").grid(row=1, column=0, sticky='e', padx=2)
        self.entry_start = ttk.Entry(frame, width=6)
        self.entry_start.grid(row=1, column=1, sticky='w', padx=2)
        
        ttk.Label(frame, text="End(K):").grid(row=1, column=2, sticky='e', padx=2)
        self.entry_end = ttk.Entry(frame, width=6)
        self.entry_end.grid(row=1, column=3, sticky='w', padx=2)
        
        ttk.Label(frame, text="Step(K):").grid(row=2, column=0, sticky='e', padx=2)
        self.entry_step = ttk.Entry(frame, width=6)
        self.entry_step.grid(row=2, column=1, sticky='w', padx=2)
        
        ttk.Button(frame, text="Generate Steps", command=self._generate_steps).grid(row=2, column=2, columnspan=2, sticky='ew', padx=5, pady=2)

        ttk.Separator(frame, orient='horizontal').grid(row=3, column=0, columnspan=4, sticky='ew', pady=5, padx=10)
        
        ttk.Label(frame, text="Order:").grid(row=4, column=0, sticky='e', padx=2)
        self.sort_var = tk.StringVar(value='Ascending')
        sort_cb = ttk.Combobox(frame, textvariable=self.sort_var, values=['Ascending', 'Descending'], state='readonly', width=10)
        sort_cb.grid(row=4, column=1, sticky='w', padx=2)
        sort_cb.bind('<<ComboboxSelected>>', lambda e: self._sort_listbox())

        ttk.Label(frame, text="Rows:").grid(row=4, column=2, sticky='e', padx=2)
        self.list_size_var = tk.IntVar(value=6)
        size_spin = ttk.Spinbox(frame, from_=3, to=25, textvariable=self.list_size_var, width=5, command=self._update_list_size)
        size_spin.grid(row=4, column=3, sticky='w', padx=2)
        size_spin.bind('<Return>', self._update_list_size)
        size_spin.bind('<FocusOut>', self._update_list_size)

        ttk.Label(frame, text="Manual(K):").grid(row=5, column=0, sticky='e', padx=2, pady=5)
        self.entry_manual = ttk.Entry(frame, width=6)
        self.entry_manual.grid(row=5, column=1, sticky='w', padx=2, pady=5)
        
        ttk.Button(frame, text="Add", command=self._add_manual_step).grid(row=5, column=2, sticky='ew', padx=2, pady=5)
        ttk.Button(frame, text="Remove", command=self._remove_step).grid(row=5, column=3, sticky='ew', padx=2, pady=5)
        
        ttk.Button(frame, text="Clear All", command=self._clear_listbox).grid(row=6, column=0, columnspan=4, sticky='ew', padx=10, pady=(0, 5))

    def _create_settings_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Instrument & Stability Settings')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(3, weight=1)

        self.entries = {}
        
        self._create_grid_entry(frame, "Tolerance (±K)", "0.5", 0, 0)
        self._create_grid_entry(frame, "Soak Time (s)", "120", 0, 2) # Updated to 120s
        self._create_grid_entry(frame, "Ramp Rate (K/min)", "2", 1, 0)
        self._create_grid_entry(frame, "Poll Delay (s)", "1", 1, 2)

        ttk.Label(frame, text="Heater Range:").grid(row=2, column=0, sticky='w', padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value='High')
        
        # Retain as self.heater_cb so we can easily access it. 
        self.heater_cb = ttk.Combobox(frame, textvariable=self.heater_range_var, values=['Off', 'Low', 'Medium', 'High'], state='readonly', width=10)
        self.heater_cb.grid(row=2, column=1, sticky='ew', padx=5)
        
        # Bind the event to handle live changes
        self.heater_cb.bind('<<ComboboxSelected>>', self._on_heater_range_changed)

        ttk.Label(frame, text="VISA Addr:").grid(row=2, column=2, sticky='w', padx=5, pady=5)
        self.ls_cb = ttk.Combobox(frame, state='readonly', width=15)
        self.ls_cb.grid(row=2, column=3, sticky='ew', padx=5)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=3, column=0, columnspan=4, sticky='ew', pady=10, padx=10)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        
        self.start_button = ttk.Button(button_frame, text="Start Sequence", style='Start.TButton', command=self.start_sequence)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=2)
        
        self.stop_button = ttk.Button(button_frame, text="Stop All", style='Stop.TButton', state='disabled', command=self.stop_ramp)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=2)
        
        ttk.Button(button_frame, text="Scan VISA", command=self._scan_for_visa).grid(row=0, column=2, sticky='ew', padx=2)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console Log')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5, padx=5)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', borderwidth=0)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.log("Console initialized. Build sequence and start.")

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        
        status_frame = ttk.Frame(panel)
        status_frame.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        status_frame.grid_columnconfigure(0, weight=1)
        
        self.lbl_status = tk.Label(status_frame, text="READY TO START", font=('Segoe UI', 16, 'bold'), bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK, pady=10)
        self.lbl_status.grid(row=0, column=0, sticky='ew')
        
        self.btn_proceed = ttk.Button(status_frame, text="Measurement Complete - Proceed ➔", style='Proceed.TButton', state='disabled', command=self._on_proceed)
        self.btn_proceed.grid(row=0, column=1, sticky='ew', padx=10, ipady=5)

        container = ttk.LabelFrame(panel, text='Live Temperature Monitoring')
        container.grid(row=1, column=0, sticky='nsew')
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)

        self.line_target = self.ax_temp.plot([], [], color=self.CLR_ACCENT_GREEN, marker='', linestyle='--', label='Target Setpoint')[0]
        self.line_temp = self.ax_temp.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3, linestyle='-', label='Actual Temp')[0]
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle='--', alpha=0.6)
        self.ax_temp.legend(loc='best', frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)

        self.line_heater = self.ax_heater.plot([], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3, linestyle='-')[0]
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

    # --- UI HELPERS ---
    def _create_grid_entry(self, parent, label_text, default_value, row, col):
        ttk.Label(parent, text=label_text).grid(row=row, column=col, sticky='w', padx=10, pady=5)
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=8)
        entry.grid(row=row, column=col+1, sticky='w', padx=5, pady=5)
        entry.insert(0, default_value)
        self.entries[label_text] = entry

    def _update_list_size(self, event=None):
        try:
            val = self.list_size_var.get()
            if 3 <= val <= 25:
                self.listbox.config(height=val)
        except Exception:
            pass

    def _sort_listbox(self):
        items = list(self.listbox.get(0, tk.END))
        if not items: return
        try:
            floats = [float(x) for x in items]
            is_desc = (self.sort_var.get() == 'Descending')
            floats.sort(reverse=is_desc)
            self.listbox.delete(0, tk.END)
            for val in floats:
                self.listbox.insert(tk.END, f"{val:.2f}")
        except Exception:
            pass 

    def _generate_steps(self):
        try:
            start = float(self.entry_start.get())
            end = float(self.entry_end.get())
            step = float(self.entry_step.get())
            if step <= 0: raise ValueError("Step must be positive")
            
            current = start
            if start < end:
                while current <= end:
                    self.listbox.insert(tk.END, f"{current:.2f}")
                    current += step
            else:
                while current >= end:
                    self.listbox.insert(tk.END, f"{current:.2f}")
                    current -= step
            self._sort_listbox() 
        except ValueError:
            messagebox.showerror("Input Error", "Please enter valid numeric values for Start, End, and Step.")

    def _add_manual_step(self):
        try:
            val = float(self.entry_manual.get())
            self.listbox.insert(tk.END, f"{val:.2f}")
            self.entry_manual.delete(0, tk.END)
            self._sort_listbox() 
        except ValueError:
            messagebox.showerror("Input Error", "Enter a valid numeric temperature.")

    def _remove_step(self):
        selection = self.listbox.curselection()
        for index in reversed(selection):
            self.listbox.delete(index)

    def _clear_listbox(self):
        self.listbox.delete(0, tk.END)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _update_status_ui(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _on_proceed(self):
        self.log("User confirmed measurement. Moving to next setpoint.")
        self.btn_proceed.config(state='disabled')
        self._update_status_ui("INITIATING NEXT RAMP...", self.CLR_HEADER)
        self.proceed_event.set()

    def _on_heater_range_changed(self, event=None):
        """Captures mid-run updates to the heater range dropdown."""
        if self.is_running:
            new_range = self.heater_range_var.get()
            self.log(f"Live heater update requested: {new_range}")
            self.live_heater_update = new_range

    def _beep(self):
        def _ring():
            try:
                if platform.system() == 'Windows':
                    import winsound
                    winsound.Beep(1000, 500)
                else:
                    self.root.bell()
            except Exception:
                pass
        threading.Thread(target=_ring, daemon=True).start()

    def _close_data_file(self):
        f = getattr(self, 'data_file', None)
        if f:
            try:
                f.flush()
                f.close()
                self._put_gui_msg('log', text=f"Data file closed: {self.data_filepath}")
            except Exception:
                pass
            finally:
                self.data_file = None

    # --- MAIN LOGIC ---
    def start_sequence(self):
        setpoints = list(self.listbox.get(0, tk.END))
        if not setpoints:
            messagebox.showwarning("Empty Sequence", "Please add at least one target temperature to the list.")
            return

        try:
            self.params = self._validate_and_get_params()
            self.setpoint_floats = [float(x) for x in setpoints]
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
            return

        self.set_ui_state(running=True)
        self.is_running = True
        self.live_heater_update = None # Reset flag
        
        for key in self.data_storage:
            self.data_storage[key].clear()
        self.line_target.set_data([], [])
        self.line_temp.set_data([], [])
        self.line_heater.set_data([], [])
        self.canvas.draw()
        
        self.start_time = time.time()
        self.proceed_event.clear()

        os.makedirs("data", exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_filepath = os.path.join("data", f"TStep_{stamp}.csv")
        self.data_file = open(self.data_filepath, 'w', newline='')
        self.csv_writer = csv.writer(self.data_file)
        self.csv_writer.writerow(
            ["Timestamp", "Elapsed_s", "Target_K", "Temperature_K",
             "Resistance_Ohm", "Heater_pct"])
        self.data_file.flush()
        self.log(f"Logging data to: {self.data_filepath}")

        self.root.after(100, self._process_gui_queue)

        self.measurement_thread = threading.Thread(target=self._hardware_worker_loop, daemon=True)
        self.measurement_thread.start()

    def stop_ramp(self):
        if not self.is_running: return
        self.log("ABORT INITIATED BY USER.")
        self.is_running = False
        self.proceed_event.set() # Unblocks if stuck waiting for user click
        self.backend.stop_ramp()
        self.set_ui_state(running=False)
        self._update_status_ui("SEQUENCE ABORTED", self.CLR_ACCENT_RED)
        messagebox.showinfo("Ramp Stopped", "Hardware ramp stopped and sequence aborted.")

    def _validate_and_get_params(self):
        params = {
            'tolerance': float(self.entries["Tolerance (±K)"].get()),
            'soak_time': float(self.entries["Soak Time (s)"].get()),
            'rate': float(self.entries["Ramp Rate (K/min)"].get()),
            'delay_s': float(self.entries["Poll Delay (s)"].get()),
            'heater_range': self.heater_range_var.get(),
            'ls_visa': self.ls_cb.get()
        }
        if not params['ls_visa']: raise ValueError("Please select a VISA address.")
        if params['rate'] <= 0: raise ValueError("Ramp rate must be positive.")
        if params['tolerance'] <= 0: raise ValueError("Tolerance must be positive.")
        return params

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        self.stop_button.config(state='normal' if running else 'disabled')
        for w in self.entries.values(): w.config(state=state)
        self.entry_start.config(state=state)
        self.entry_end.config(state=state)
        self.entry_step.config(state=state)
        self.entry_manual.config(state=state)
        self.sort_var.set(self.sort_var.get()) 
        self.ls_cb.config(state=state if state == 'normal' else 'readonly')
        self.btn_proceed.config(state='disabled')
        # Note: self.heater_cb is explicitly left as 'readonly' naturally so the user can interact mid-run.

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

    def _put_gui_msg(self, msg_type, **kwargs):
        payload = {'type': msg_type}
        payload.update(kwargs)
        self.gui_queue.put(payload)

    def _process_gui_queue(self):
        try:
            while True:
                msg = self.gui_queue.get_nowait()
                msg_type = msg['type']
                
                if msg_type == 'log':
                    self.log(msg['text'])
                
                elif msg_type == 'status':
                    self._update_status_ui(msg['text'], msg['color'])
                
                elif msg_type == 'plot':
                    n = min(len(self.data_storage['time']),
                            len(self.data_storage['temperature']),
                            len(self.data_storage['heater']),
                            len(self.data_storage['target']))
                    t = self.data_storage['time'][:n]
                    self.line_target.set_data(t, self.data_storage['target'][:n])
                    self.line_temp.set_data(t, self.data_storage['temperature'][:n])
                    self.line_heater.set_data(t, self.data_storage['heater'][:n])
                    for ax in [self.ax_temp, self.ax_heater]:
                        ax.relim()
                        ax.autoscale_view()
                    self.canvas.draw_idle()
                
                elif msg_type == 'handshake_ready':
                    self.btn_proceed.config(state='normal')
                    self._beep()
                
                elif msg_type == 'sequence_complete':
                    self.set_ui_state(running=False)
                    messagebox.showinfo("Sequence Complete", "All setpoints measured successfully.")
                    
        except queue.Empty:
            pass
        
        if self.is_running or not self.gui_queue.empty():
            self.root.after(100, self._process_gui_queue)

    def _hardware_worker_loop(self):
        try:
            self._put_gui_msg('log', text="Connecting to Lakeshore...")
            self.backend.connect(self.params['ls_visa'])
            
            for i, target in enumerate(self.setpoint_floats):
                if not self.is_running: break
                
                self._put_gui_msg('log', text=f"--- Sequence Step {i+1}/{len(self.setpoint_floats)}: Target {target} K ---")
                self._put_gui_msg('status', text=f"RAMPING TO {target} K", color=self.CLR_ACCENT_RED)
                
                self.backend.configure_ramp(target, self.params['rate'], self.params['heater_range'])
                
                stable_start_time = None
                phase = 'RAMPING' # Can be: RAMPING, SOAKING, or WAITING
                
                self.proceed_event.clear()
                
                while self.is_running:
                    # 1. Process Live Heater Updates (Mid-Run adjustments)
                    if self.live_heater_update is not None:
                        new_range = self.live_heater_update
                        self.live_heater_update = None
                        try:
                            self.backend.set_heater_range(1, new_range)
                            self._put_gui_msg('log', text=f"Heater successfully switched to: {new_range}")
                        except Exception as e:
                            self._put_gui_msg('log', text=f"Failed to switch heater range: {e}")

                    # 2. Get hardware status
                    temp, resistance, htr = self.backend.get_status()
                    elapsed = time.time() - self.start_time
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    # 3. Store and commit data
                    self.data_storage['time'].append(elapsed)
                    self.data_storage['temperature'].append(temp)
                    self.data_storage['target'].append(target)
                    self.data_storage['resistance'].append(resistance)
                    self.data_storage['heater'].append(htr)

                    try:
                        self.csv_writer.writerow(
                            [now_str, f"{elapsed:.2f}", f"{target:.4f}",
                             f"{temp:.4f}", f"{resistance:.6g}", f"{htr:.2f}"])
                        self.data_file.flush()
                        os.fsync(self.data_file.fileno())
                    except Exception as e:
                        self._put_gui_msg('log', text=f"WARN: data write failed: {e}")
                    
                    self._put_gui_msg('plot')
                    
                    # 4. State Machine Logic (Never breaks the loop until user clicks proceed)
                    if phase in ['RAMPING', 'SOAKING']:
                        if abs(temp - target) <= self.params['tolerance']:
                            if phase == 'RAMPING':
                                stable_start_time = time.time()
                                phase = 'SOAKING'
                                self._put_gui_msg('log', text=f"Entered tolerance band (±{self.params['tolerance']}K). Starting soak timer...")
                                self._put_gui_msg('status', text=f"STABILIZING AT {target} K...", color=self.CLR_STABLE_WAIT)
                                
                            elif phase == 'SOAKING' and (time.time() - stable_start_time >= self.params['soak_time']):
                                self._put_gui_msg('log', text=f"Stable inside window for {self.params['soak_time']}s. Ready for external measurement.")
                                self._put_gui_msg('status', text=f"STABLE AT {target} K | AWAITING MEASUREMENT", color=self.CLR_ACCENT_GREEN)
                                self._put_gui_msg('handshake_ready')
                                phase = 'WAITING'
                        else:
                            if phase == 'SOAKING':
                                self._put_gui_msg('log', text="Drifted outside tolerance band. Restarting soak timer.")
                                self._put_gui_msg('status', text=f"RAMPING TO {target} K", color=self.CLR_ACCENT_RED)
                                stable_start_time = None
                                phase = 'RAMPING'
                                
                    elif phase == 'WAITING':
                        # While waiting, we just monitor. Check if user clicked Proceed
                        if self.proceed_event.is_set():
                            self.proceed_event.clear()
                            break # Exits the while loop, moving to the next target in the sequence!
                        
                    # 5. Delay before next poll
                    time.sleep(self.params['delay_s'])
                
            if self.is_running:
                self._put_gui_msg('log', text="Measurement Sequence Complete.")
                self._put_gui_msg('status', text="READY TO START", color=self.CLR_HEADER)
                self._put_gui_msg('sequence_complete')
                self.backend.stop_ramp()
                self.is_running = False

        except Exception as e:
            self._put_gui_msg('log', text=f"CRITICAL ERROR IN HARDWARE THREAD: {e}\n{traceback.format_exc()}")
            self.is_running = False
            self._put_gui_msg('sequence_complete')
            self.backend.stop_ramp()
        finally:
            self._close_data_file()

    def _on_closing(self):
        if self.is_running and messagebox.askyesno("Exit", "A sequence is active. Stop hardware and exit?"):
            self.stop_ramp()
            time.sleep(0.5)
            if self.measurement_thread and self.measurement_thread.is_alive():
                self.measurement_thread.join(timeout=2.0)
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