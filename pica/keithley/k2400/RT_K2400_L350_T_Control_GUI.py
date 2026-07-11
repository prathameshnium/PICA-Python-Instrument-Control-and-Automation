"""
Module: RT_K2400_L350_T_Control_GUI.py
Purpose: GUI module for RT K2400 L350 T Control GUI v3.
"""

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

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley2400
    PYMEASURE_AVAILABLE = True
except ImportError:
    pyvisa, Keithley2400 = None, None
    PYMEASURE_AVAILABLE = False

try:
    # Dynamically find the project root and add it to the path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
    if project_root not in sys.path:
        sys.path.append(project_root)
except Exception:
    # Path manipulation can fail in some environments (e.g., frozen
    # executables)
    pass

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
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: k2400 -> keithley -> pica
        plotter_path = os.path.join(
            script_dir,
            "..", "..", "utils", "PlotterUtil_GUI.py")
        if not os.path.exists(plotter_path):
            messagebox.showerror(
                "File Not Found",
                f"Plotter utility not found at expected path:\n{plotter_path}")
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 2 levels: k2400 -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------


class RT_Backend_Active:
    """ Manages communication with the K2400 and Lakeshore 350. """

    def __init__(self):
        self.k2400, self.lakeshore = None, None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None

    def connect(self, k2400_visa, ls_visa):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure is not available.")
        self.k2400 = Keithley2400(k2400_visa)
        print(f"  K2400 Connected: {self.k2400.id}")
        self.lakeshore = self.rm.open_resource(ls_visa)
        print(
            f"  Lakeshore Connected: {self.lakeshore.query('*IDN?').strip()}")

    def configure_instruments(self, current_ma, compliance_v):
        # Lakeshore setup
        self.lakeshore.write('*RST')
        time.sleep(0.5)
        self.lakeshore.write('*CLS')
        self.lakeshore.write('HTRSET 1,1,2,0,1')  # 25Ω heater, 1A max

        # Keithley 2400 setup
        self.k2400.reset()
        self.k2400.use_front_terminals()
        self.k2400.apply_current()
        self.k2400.source_current_range = abs(current_ma * 1e-3) * 1.05
        self.k2400.compliance_voltage = compliance_v
        self.k2400.source_current = current_ma * 1e-3
        self.k2400.measure_voltage()
        self.k2400.enable_source()

    def get_temperature(self):
        if not self.lakeshore:
            return 0.0
        return float(self.lakeshore.query('KRDG? A').strip())

    def set_heater_range(self, output, heater_range):
        range_map = {'off': 0, 'low': 2, 'medium': 4, 'high': 5}
        range_code = range_map.get(heater_range.lower())
        if range_code is None:
            raise ValueError("Invalid heater range.")
        self.lakeshore.write(f'RANGE {output},{range_code}')

    def set_setpoint(self, output, temperature_k):
        self.lakeshore.write(f'SETP {output},{temperature_k}')

    def start_ramp(self, end_temp, rate_k_min):
        self.lakeshore.write(f'SETP 1,{end_temp}')
        self.lakeshore.write(f'RAMP 1,1,{rate_k_min}')
        self.lakeshore.write('RANGE 1,5')  # Heater High for ramp

    def get_measurement(self):
        voltage = self.k2400.voltage
        temperature = float(self.lakeshore.query('KRDG? A').strip())
        return temperature, voltage

    def shutdown(self):
        if self.k2400:
            try:
                self.k2400.shutdown()
            except BaseException:
                pass
        if self.lakeshore:
            try:
                self.lakeshore.write("RANGE 1,0")
                self.lakeshore.close()
            except BaseException:
                pass
        print("  Instruments shut down and disconnected.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class RT_GUI_Active:
    PROGRAM_VERSION = "3.1"  # UI/UX Update
    CLR_BG = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#B68B6E', '#BA6B5E', '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_TEXT_DARK = '#1A1A1A'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')

    LEFT_PANEL_WIDTH = 500  # default sash position so the left panel starts fully visible

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"K2400 & L350: R-T Sweep (T-Control) v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x950")
        self.root.minsize(1400, 800)
        self.root.configure(bg=self.CLR_BG)
        self.experiment_state = 'idle'
        self.logo_image = None
        self.backend = RT_Backend_Active()
        self.data_storage = {
            'temperature': [],
            'voltage': [],
            'resistance': []}
        # Plot updates are decoupled from data acquisition: data callbacks
        # only set this flag; _refresh_plot redraws on a fixed cadence.
        self._plot_dirty = False
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG,
            foreground=self.CLR_FG,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG)
        style.configure('TPanedWindow', background=self.CLR_BG)
        style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG,
            insertcolor=self.CLR_FG)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Start.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])
        # --- NEW: Style for the Browse button ---
        style.configure(
            'Browse.TButton',
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_ACCENT_BLUE)
        style.map(
            'Browse.TButton', background=[
                ('active', '#7C899E'), ('hover', '#7C899E')])
        style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_ACCENT_BLUE)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG,
            font=self.FONT_TITLE)
        # --- NEW: Style for Comboboxes to make them more visible ---
        style.configure('TCombobox',
                        fieldbackground=self.CLR_INPUT_BG,
                        foreground=self.CLR_FG,
                        arrowcolor=self.CLR_FG,
                        selectbackground=self.CLR_ACCENT_BLUE,
                        selectforeground=self.CLR_FG)
        mpl.rcParams.update({'font.family': 'Segoe UI',
                             'font.size': 11,
                             'axes.titlesize': 15,
                             'axes.labelsize': 13})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(
            header,
            text="K2400 & L350: R-T Sweep (T-Control)",
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left',
            padx=20,
            pady=10)

        # --- Plotter Launch Button ---
        plotter_button = ttk.Button(
            header,
            text="📈",
            command=launch_plotter_utility,
            width=3)
        plotter_button.pack(side='right', padx=10, pady=5)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        # FIX: pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel_container = ttk.Frame(self.main_pane)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)
        right_panel = ttk.Frame(self.main_pane, padding=5)
        self.main_pane.add(right_panel, weight=1)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding=10)
        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window(
            (0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._populate_left_panel(scrollable_frame)
        self._populate_right_panel(right_panel)

        # sashpos() has no effect until the PanedWindow is actually mapped and
        # laid out — an early call fails SILENTLY. So we (a) wait for the
        # window to be drawn, (b) measure the real required width of the
        # left-panel content instead of guessing, and (c) retry until the
        # sash position verifiably sticks.
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()  # force geometry to be computed

            # Measure the actual content width: inner scrollable frame +
            # vertical scrollbar + a little breathing room. Falls back to
            # LEFT_PANEL_WIDTH if measurement isn't ready yet.
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            if content_w > 1:
                target = content_w + 30  # scrollbar (~15px) + padding
            else:
                target = self.LEFT_PANEL_WIDTH

            self.main_pane.sashpos(0, target)

            # Verify it stuck; if not (widget not mapped yet), retry.
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        panel.grid_columnconfigure(0, weight=1)
        self._create_info_panel(panel).pack(
            fill='x', expand=True, pady=(0, 10))
        self._create_params_panel(panel).pack(fill='x', expand=True, pady=10)
        self._create_control_panel(panel).pack(fill='x', expand=True, pady=10)
        self._create_console_panel(panel).pack(
            fill='both', expand=True, pady=(10, 0))

    def _create_info_panel(self, parent):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 110
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        try:  # Use a more robust relative path
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir,
                "..",
                "..",
                "assets",
                "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")  # This path is correct
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1], 'bold')
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                15,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_FRAME_BG).grid(
            row=1,
            column=1,
            padx=10,
            pady=(
                0,
                5),
            sticky='nw')
        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)
        details_text = ("Program Name: R vs. T (T-Control)\n"
                        "Instruments: Keithley 2400, Lakeshore 350\n"
                        "Measurement Range: 100 µΩ to 200 MΩ\n"
                        "Heater Range: 5 (Active)")
        ttk.Label(
            frame,
            text=details_text,
            justify='left',
            background=self.CLR_FRAME_BG).grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')
        return frame

    def _populate_right_panel(self, panel):
        container = ttk.LabelFrame(panel, text='Live R-T Curve')
        container.pack(fill='both', expand=True)
        self.figure = Figure(
            dpi=100,
            facecolor='white',
            constrained_layout=True)

        # --- MODIFIED: Create two subplots sharing the x-axis ---
        self.ax_main, self.ax_sub = self.figure.subplots(2, 1, sharex=True)

        # Top plot: Resistance vs. Temperature
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Live R-T and V-T Curves", fontweight='bold')
        self.ax_main.set_ylabel("Resistance (Ω)")
        self.ax_main.set_yscale('log')
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        # Bottom plot: Voltage vs. Temperature
        self.line_sub, = self.ax_sub.plot(
            [], [], color=self.CLR_ACCENT_BLUE, marker='o', markersize=4, linestyle='-')
        self.ax_sub.set_xlabel("Temperature (K)")
        self.ax_sub.set_ylabel("Voltage (V)")
        self.ax_sub.grid(True, linestyle='--', alpha=0.6)

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

    def _create_params_panel(self, parent):
        container = ttk.Frame(parent)
        container.grid_columnconfigure(0, weight=1)
        self.entries = {}

        temp_frame = ttk.LabelFrame(container, text='Temperature Control')
        temp_frame.pack(fill='x', expand=True, pady=(0, 10))
        temp_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(temp_frame, "Start Temp (K)", "300", 0)
        self._create_entry(temp_frame, "End Temp (K)", "310", 1)
        self._create_entry(temp_frame, "Ramp Rate (K/min)", "2", 2)
        self._create_entry(temp_frame, "Safety Cutoff (K)", "320", 3)

        iv_frame = ttk.LabelFrame(container, text='Measurement Settings')
        iv_frame.pack(fill='x', expand=True, pady=(0, 10))
        iv_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(iv_frame, "Source Current (mA)", "1", 0)
        self._create_entry(iv_frame, "Compliance (V)", "10", 1)
        self._create_entry(iv_frame, "Logging Delay (s)", "1", 2)

        visa_frame = ttk.LabelFrame(container, text='Instrument Addresses')
        visa_frame.pack(fill='x', expand=True)
        visa_frame.grid_columnconfigure(1, weight=1)
        self.ls_cb = self._create_combobox(visa_frame, "Lakeshore VISA", 0)
        self.k2400_cb = self._create_combobox(
            visa_frame, "Keithley 2400 VISA", 1)
        return container

    def _create_control_panel(self, parent):
        frame = ttk.LabelFrame(parent, text='File & Control')
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_RT_Active", 0)
        self._create_entry(frame, "Save Location", "", 1, browse=True)
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=4, sticky='ew', pady=5)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.start_button = ttk.Button(
            button_frame,
            text="Start",
            style='Start.TButton',
            command=self.start_experiment)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=5)
        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            style='Stop.TButton',
            state='disabled',
            command=self.stop_experiment)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=5)
        ttk.Button(
            button_frame,
            text="Scan",
            command=self._scan_for_visa).grid(
            row=0,
            column=2,
            sticky='ew',
            padx=5)
        return frame

    def _create_console_panel(self, parent):
        frame = ttk.LabelFrame(parent, text='Console')
        self.console = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG, font=(
                'Consolas', 10), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        return frame

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    def start_experiment(self):
        try:
            self.params = self._validate_and_get_params()
            self.log("Connecting to instruments...")
            self.backend.connect(
                self.params['k2400_visa'],
                self.params['ls_visa'])
            self.backend.configure_instruments(
                self.params['current_ma'], self.params['compliance_v'])
            self.log("All instruments connected and configured.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.params['name']}_{ts}_RT_Active.csv"
            self.data_filepath = os.path.join(
                self.params['save_path'], filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Temperature (K)", "Voltage (V)",
                                "Resistance (Ohm)", "Elapsed Time (s)"])

            self.set_ui_state(running=True)
            self.experiment_state = 'stabilizing'
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.line_sub.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {self.params['name']}")
            self.ax_main.set_yscale('log')
            self._plot_dirty = False
            self.canvas.draw_idle()

            self.log(
                f"Starting stabilization at {self.params['start_temp']} K...")
            self.root.after(100, self._experiment_loop)
            self.root.after(250, self._refresh_plot)

        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Start Failed", f"{e}")
            self.backend.shutdown()

    def stop_experiment(self, reason=""):
        if self.experiment_state == 'idle':
            return
        self.log(
            f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.experiment_state = 'idle'
        self.backend.shutdown()
        self.set_ui_state(running=False)
        # Final flush: state is already 'idle', so this won't reschedule.
        self._refresh_plot()
        self.ax_main.set_title("Experiment stopped.")
        self.canvas.draw()
        if reason:
            messagebox.showinfo("Experiment Finished", f"Reason: {reason}")

    # --- NON-BLOCKING HEATER LOGIC (from 6517B scripts) ---
    def _stabilization_loop(self):
        if self.experiment_state != 'stabilizing':
            return
        try:
            current_temp = self.backend.get_temperature()
            start_temp = self.params['start_temp']

            if current_temp > start_temp + 5.0:
                self.log(
                    f"Cooling... Current: {current_temp:.4f} K > Target: {start_temp} K")
                self.backend.set_heater_range(1, 'off')
            else:
                self.log(
                    f"Heating... Current: {current_temp:.4f} K <= Target: {start_temp} K")
                self.backend.set_heater_range(1, 'high')
                self.backend.set_setpoint(1, start_temp)

            if abs(current_temp - start_temp) < 5.0:
                self.log(
                    f"Stabilized at {current_temp:.4f} K. Waiting 5s before starting ramp...")
                self.experiment_state = 'ramping_setup'
                # Transition to next state
                self.root.after(5000, self._experiment_loop)
            else:
                # Continue stabilizing
                self.root.after(2000, self._stabilization_loop)
        except Exception as e:
            self.log(f"ERROR during stabilization: {e}")
            self.stop_experiment("Stabilization Error")

    def _experiment_loop(self):
        if self.experiment_state == 'idle':
            return
        try:
            if self.experiment_state == 'stabilizing':
                self._stabilization_loop()
                return  # Let the after() calls manage the flow

            elif self.experiment_state == 'ramping_setup':
                self.backend.start_ramp(
                    self.params['end_temp'], self.params['rate'])
                self.log(f"Ramp started towards {self.params['end_temp']} K.")
                self.experiment_state = 'ramping'
                self.start_time = time.time()
                # Transition to measurement
                self.root.after(100, self._experiment_loop)
                return

            elif self.experiment_state == 'ramping':
                temp, voltage = self.backend.get_measurement()
                resistance = voltage / \
                    (self.params['current_ma'] * 1e-3) if self.params['current_ma'] != 0 else float('inf')
                elapsed = time.time() - self.start_time
                self.log(f"T: {temp:.3f} K | R: {resistance:.4e} Ω")

                self.data_storage['temperature'].append(temp)
                self.data_storage['voltage'].append(voltage)
                self.data_storage['resistance'].append(resistance)
                with open(self.data_filepath, 'a', newline='') as f:
                    csv.writer(f).writerow(
                        [f"{temp:.4f}", f"{voltage:.6e}", f"{resistance:.6e}", f"{elapsed:.2f}"])

                self._plot_dirty = True

                # Check end conditions
                if temp >= self.params['cutoff']:
                    self.stop_experiment(
                        f"Safety cutoff reached at {temp:.2f} K.")
                elif (self.params['rate'] > 0 and temp >= self.params['end_temp']) or \
                     (self.params['rate'] < 0 and temp <= self.params['end_temp']):
                    self.stop_experiment("End temperature reached.")
                else:
                    self.root.after(
                        int(self.params['delay_s'] * 1000), self._experiment_loop)

        except Exception as e:
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            messagebox.showerror("Runtime Error", f"{e}")
            self.stop_experiment("Runtime Error")

    def _validate_and_get_params(self):
        try:
            params = {
                'name': self.entries["Sample Name"].get(),
                'save_path': self.entries["Save Location"].get(),
                'start_temp': float(
                    self.entries["Start Temp (K)"].get()),
                'end_temp': float(
                    self.entries["End Temp (K)"].get()),
                'rate': float(
                    self.entries["Ramp Rate (K/min)"].get()),
                'cutoff': float(
                    self.entries["Safety Cutoff (K)"].get()),
                'ls_visa': self.ls_cb.get(),
                'current_ma': float(
                    self.entries["Source Current (mA)"].get()),
                'compliance_v': float(
                    self.entries["Compliance (V)"].get()),
                'delay_s': float(
                    self.entries["Logging Delay (s)"].get()),
                'k2400_visa': self.k2400_cb.get()}
            if not all([p for k, p in params.items()
                       if k not in ['rate', 'cutoff']]):
                raise ValueError("A required field is empty.")
            if params['rate'] > 0 and not (
                    params['start_temp'] < params['end_temp'] < params['cutoff']):
                raise ValueError(
                    "For heating, temperatures must be in order: start < end < cutoff.")
            if params['rate'] < 0 and not (
                    params['start_temp'] > params['end_temp'] > params['cutoff']):
                raise ValueError(
                    "For cooling, temperatures must be in order: start > end > cutoff.")
            return params
        except Exception as e:
            raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values():
            w.config(state=state)
        for cb in [self.ls_cb, self.k2400_cb]:
            cb.config(state=state if state == 'normal' else 'readonly')
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
            self.k2400_cb['values'] = resources
            for r in resources:
                if 'GPIB1::15' in r:
                    self.ls_cb.set(r)
                if 'GPIB1::4' in r:
                    self.k2400_cb.set(r)
        else:
            self.log("No VISA instruments found.")

    def _browse_file_location(self):
        path = filedialog.askdirectory()
        if path:
            self.entries["Save Location"].config(state='normal')
            self.entries["Save Location"].delete(0, 'end')
            self.entries["Save Location"].insert(0, path)
            self.entries["Save Location"].config(state='disabled')

    def _create_entry(
            self,
            parent,
            label_text,
            default_value,
            row,
            browse=False):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=30)
        entry.grid(
            row=row,
            column=1,
            sticky='ew',
            padx=10,
            pady=3,
            columnspan=3 if browse else 1)
        entry.insert(0, default_value)
        self.entries[label_text] = entry
        if browse:  # Special handling for the save location entry
            btn = ttk.Button(
                parent,
                text="Browse...",
                style='Browse.TButton',
                command=self._browse_file_location)
            btn.grid(row=row, column=4, sticky='e', padx=(0, 10))
            entry.config(state='disabled')

    def _create_combobox(self, parent, label_text, row):
        ttk.Label(
            parent,
            text=f"{label_text}:").grid(
            row=row,
            column=0,
            sticky='w',
            padx=10,
            pady=3)
        cb = ttk.Combobox(
            parent,
            font=self.FONT_BASE,
            state='readonly',
            style='TCombobox',
            height=5)
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _refresh_plot(self):
        """Redraws the plots at a fixed cadence, independent of data rate.

        A normal (non-blitted) draw is used so that the axes — ticks,
        limits, gridlines and scale — always stay in sync with the data.
        """
        if self._plot_dirty:
            self._plot_dirty = False

            temps = self.data_storage['temperature']
            res = self.data_storage['resistance']
            volts = self.data_storage['voltage']

            self.line_main.set_data(temps, res)
            self.line_sub.set_data(temps, volts)

            # Recompute and apply limits on every axis.
            self._autoscale_axis(self.ax_main, x=temps, y=res, log_y=True)
            self._autoscale_axis(self.ax_sub, x=temps, y=volts)

            # Full redraw keeps ticks/labels/gridlines correct and is
            # resize-proof. draw_idle() coalesces redraws efficiently.
            self.canvas.draw_idle()

        if self.experiment_state != 'idle':
            self.root.after(250, self._refresh_plot)

    def _autoscale_axis(self, ax, x, y, log_y=False, margin=0.05):
        """Rescale an axis, ignoring non-finite and (for log) non-positive
        values so the axis never collapses or freezes."""
        import math

        xs = [v for v in x if v is not None and math.isfinite(v)]
        if log_y:
            ys = [v for v in y if v is not None and math.isfinite(v) and v > 0]
        else:
            ys = [v for v in y if v is not None and math.isfinite(v)]

        if not xs or not ys:
            return

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)

        # X padding (linear)
        xpad = (xmax - xmin) * margin or 0.5
        ax.set_xlim(xmin - xpad, xmax + xpad)

        # Y padding
        if log_y:
            # pad multiplicatively in log space
            ax.set_ylim(ymin / (1 + margin), ymax * (1 + margin))
        else:
            ypad = (ymax - ymin) * margin or abs(ymax) * margin or 1e-12
            ax.set_ylim(ymin - ypad, ymax + ypad)

    def _on_closing(self):
        if self.experiment_state != 'idle' and messagebox.askyesno(
                "Exit", "Experiment is running. Stop and exit?"):
            self.stop_experiment("Application closed by user.")
            self.root.destroy()
        elif self.experiment_state == 'idle':
            self.root.destroy()


if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror(
            "Dependency Error",
            "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk()
        app = RT_GUI_Active(root)
        root.mainloop()