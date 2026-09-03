"""
Module: T_Control_L340_RangeControl_GUI.py
Purpose: Single temperature ramp on a Lake Shore Model 340 with a software
         PID zone table.  Port of T_Control_L350_RangeControl_GUI.py.

What this does
--------------
  1. Enables control Loop 1 on the chosen input (CSET), puts it in Manual
     PID mode (CMODE 1,1) so nothing in the instrument overrides the PID
     this programme sends, and reads the loop limits (CLIMIT?) so a target
     above the 340's setpoint limit is refused before the ramp starts.
  2. Sets the setpoint to the present temperature with the ramp off, then
     turns the ramp on at the requested rate and sends the target.  A 340
     ramps from the CURRENT SETPOINT, not from the temperature; without
     this step a leftover 300 K setpoint would make a ramp to 3 K start
     with the heater at full power.
  3. Polls the control input every "Logging Delay" seconds, logs to a CSV
     file, plots temperature and heater output, and, when zoned PID is on,
     sends the PID + heater range of the zone the MEASURED temperature is
     in whenever the zone changes (no hysteresis, as asked).  Every switch
     is written to the console and the CSV.
  4. Declares the ramp complete when the reading has stayed within the
     tolerance band for the dwell time.  By default the heater is then
     turned off (RANGE 0), as the Model 350 version did.  Tick "Hold at
     target" to keep the loop controlling instead.
  5. Stop = RAMP off + RANGE 0.  Closing the window while idle leaves the
     instrument exactly as it is.

No modal dialog appears during or after a run: a ramp can run for hours
unattended, so completion and errors go to the console, the status banner
and a beep.  Only a refused Start opens a dialog.

Model 340 commands used (User's Manual, Chapter 9):
  CSET 1,<in>,1,1      enable Loop 1 on input, kelvin        (printed 9-31)
  CMODE 1,1            Manual PID                            (9-29)
  CLIMIT? 1            SP limit, slopes, max current, max range (9-28)
  RAMP 1,<on>,<rate>   0.1-100 K/min                         (9-40)
  SETP 1,<K>                                                 (9-42)
  RANGE <0-5>          Loop 1 heater range, no loop number   (9-40)
  PID 1,<P>,<I>,<D>    P 0-1000, I 0-1000, D 0-1000          (9-40)
  KRDG? <in>, RDGST? <in>, HTR?, HTRST?, SETP? 1, RANGE?, PID? 1
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, Canvas
import os
import csv
import time
import traceback
from datetime import datetime
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
    """Execute a script using runpy in its own directory (child process)."""
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
        scanner_path = os.path.join(
            script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# -------------------------------------------------------------------------------
# --- PID ZONES ---
# -------------------------------------------------------------------------------

# (upper bound K, P, I, D, heater range).  The first zone runs from 0 K up
# to its bound; a temperature above the last bound uses the last zone.
#
# 100-310 K is the lab's tested setting for the CCR (P 0.5, I 4, D 0,
# range 5).  Below that the heater range is dropped one step per zone and P
# is raised by about 3x per step (one range step is 10x in power, 3.16x in
# current, and the PID output is a percentage of full-scale current), which
# is the rule the lab uses: "reduce the range at low temperature, then
# increase P; same again further down".  Starting points, edit freely.
ZONE_DEFAULTS = (
    (20.0, 5.0, 4.0, 0.0, 3),
    (50.0, 1.5, 4.0, 0.0, 4),
    (100.0, 1.5, 4.0, 0.0, 4),
    (310.0, 0.5, 4.0, 0.0, 5),
)
ZONE_T_MIN = 3.0
ZONE_T_MAX = 310.0


def select_zone(zones, temperature):
    """Index of the zone a temperature falls in (no hysteresis).

    zones: sequence of (upper_bound, P, I, D, range), sorted ascending.
    The first zone whose upper bound is >= temperature wins; anything above
    the last bound uses the last zone.
    """
    if not zones:
        raise ValueError("zone table is empty")
    for idx, zone in enumerate(zones):
        if temperature <= zone[0]:
            return idx
    return len(zones) - 1


def generate_equal_zones(n_segments, t_min=ZONE_T_MIN, t_max=ZONE_T_MAX,
                         template=ZONE_DEFAULTS):
    """Split [t_min, t_max] into n equal segments, PID/range from template."""
    n_segments = int(n_segments)
    if n_segments < 1:
        raise ValueError("at least one segment is required")
    if t_max <= t_min:
        raise ValueError("t_max must be above t_min")
    width = (t_max - t_min) / n_segments
    out = []
    for k in range(1, n_segments + 1):
        ub = t_min + width * k
        if k == n_segments:
            ub = t_max
        tpl = template[select_zone(template, ub)]
        out.append((round(ub, 3), tpl[1], tpl[2], tpl[3], tpl[4]))
    return out


def explain_visa_error(exc):
    """Plain-language hint for the VISA errors seen on the lab PCs."""
    text = str(exc)
    if 'VI_ERROR_ALLOC' in text:
        return ("VI_ERROR_ALLOC comes from the VISA driver before any "
                "command is sent: this VISA library cannot open a session on "
                "that GPIB board (typically GPIB0 is a USB adapter whose own "
                "driver, e.g. Keysight IO Libraries, is not installed or is "
                "not the primary VISA). Try the same address on the other "
                "board (GPIB1::..). If Keysight Connection Expert can talk to the "
                "instrument, PyVISA is loading NI-VISA for a Keysight adapter: "
                "tick 'Keysight VISA as primary VISA' in Connection Expert "
                "settings, or set PYVISA_LIBRARY=C:\Windows\System32\ktvisa32.dll "
                "and restart PICA. Then use 'Identify' to see which address "
                "answers as MODEL340.")
    if 'VI_ERROR_TMO' in text:
        return ("Timeout: the address exists but nothing answered *IDN?. "
                "Check the 340 is powered, its IEEE address matches, and no "
                "other programme holds the session.")
    return ""


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Lakeshore340_Backend:
    MODEL_TOKENS = ("MODEL340", "MODEL 340")
    HEATER_ERRORS = {
        0: "No error",
        1: "Power supply over voltage",
        2: "Power supply under voltage",
        3: "Output DAC error",
        4: "Current limit DAC error",
        5: "OPEN HEATER LOAD",
        6: "Heater load < 10 ohm",
    }
    MAX_CURRENT_CODES = {1: "0.25 A", 2: "0.5 A", 3: "1.0 A", 4: "2.0 A",
                         5: "User (CLIMI)"}
    RDGST_BITS = (
        (1, "invalid reading"), (2, "old reading"), (16, "temp underrange"),
        (32, "temp overrange"), (64, "units zero"), (128, "units overrange"),
    )

    def __init__(self):
        self.lakeshore = None
        self.idn = ""
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None
        else:
            self.rm = None

    # -- session --

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        self.lakeshore = self.rm.open_resource(visa_address)
        self.lakeshore.timeout = 10000
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        self.idn = self.lakeshore.query('*IDN?').strip()
        print(f"  Lakeshore Connected: {self.idn}")
        return self.idn

    def is_model_340(self):
        idn = self.idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def _write(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        self.lakeshore.write(cmd)

    def _query(self, cmd):
        if not self.lakeshore:
            raise ConnectionError("Not connected to instrument.")
        return self.lakeshore.query(cmd).strip()

    def identify_resources(self, addresses, timeout_ms=2000):
        """Send *IDN? to each address (user-triggered). {addr: reply|'ERROR: ..'}"""
        out = {}
        if not self.rm:
            return out
        for addr in addresses:
            try:
                inst = self.rm.open_resource(addr)
                try:
                    inst.timeout = timeout_ms
                    inst.read_termination = '\n'
                    inst.write_termination = '\n'
                    out[addr] = inst.query('*IDN?').strip()
                finally:
                    inst.close()
            except Exception as e:
                out[addr] = f"ERROR: {e}"
        return out

    # -- loop setup --

    def prepare_loop(self, control_input):
        """*CLS; enable Loop 1 on <input> in kelvin; Manual PID mode.

        Returns (CSET? dict, CMODE? code, CLIMIT? dict) for logging.
        """
        self._write('*CLS')
        time.sleep(0.2)
        self._write(f'CSET 1,{control_input},1,1')
        self._write('CMODE 1,1')
        time.sleep(0.2)
        cset = self.get_control_loop(1)
        if cset['input'].upper() != str(control_input).upper() or not cset['enabled']:
            raise RuntimeError(
                f"CSET 1,{control_input},1,1 did not stick: CSET? 1 reads "
                f"{cset}. Check the front panel (Remote/Local) and retry.")
        cmode = int(float(self._query('CMODE? 1')))
        if cmode != 1:
            raise RuntimeError(f"CMODE 1,1 did not stick: CMODE? 1 = {cmode}.")
        return cset, cmode, self.get_control_limits(1)

    def get_control_loop(self, loop=1):
        parts = [p.strip() for p in self._query(f'CSET? {loop}').split(',')]
        if len(parts) < 4:
            raise ValueError(f"unexpected CSET? reply '{','.join(parts)}'")
        return {'input': parts[0], 'units': int(float(parts[1])),
                'enabled': int(float(parts[2])), 'powerup': int(float(parts[3]))}

    def get_control_limits(self, loop=1):
        parts = [p.strip() for p in self._query(f'CLIMIT? {loop}').split(',')]
        if len(parts) < 5:
            raise ValueError(f"unexpected CLIMIT? reply '{','.join(parts)}'")
        return {'sp_limit': float(parts[0]), 'pos_slope': float(parts[1]),
                'neg_slope': float(parts[2]), 'max_current': int(float(parts[3])),
                'max_range': int(float(parts[4]))}

    # -- ramp --

    def start_ramp(self, target, rate, current_temperature):
        """Setpoint = now (ramp off), then ramp on and target.

        The 340 ramps from the current setpoint; pinning it to the present
        temperature first makes the ramp start from where the sample is.
        """
        if not (0.1 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self._write('RAMP 1,0,0')
        self._write(f'SETP 1,{current_temperature:.3f}')
        time.sleep(0.2)
        self._write(f'RAMP 1,1,{rate}')
        self._write(f'SETP 1,{target}')

    def set_ramp_rate(self, rate):
        if not (0.1 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self._write(f'RAMP 1,1,{rate}')

    def set_heater_range(self, range_code):
        """RANGE <0-5>: Loop 1 only, no loop number on a 340. Verified."""
        range_code = int(range_code)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5, got {range_code}")
        self._write(f'RANGE {range_code}')
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != range_code:
            raise RuntimeError(
                f"RANGE {range_code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        return int(float(self._query('RANGE?')))

    def set_pid(self, p, i, d):
        if not (0 <= p <= 1000 and 0 <= i <= 1000 and 0 <= d <= 1000):
            raise ValueError("PID values must be 0-1000 on a Model 340.")
        self._write(f'PID 1,{p},{i},{d}')

    def get_pid(self):
        parts = self._query('PID? 1').split(',')
        return float(parts[0]), float(parts[1]), float(parts[2])

    # -- readings --

    def get_status(self, control_input):
        """-> (temperature K, heater %, setpoint K, reading-status text)."""
        temp = float(self._query(f'KRDG? {control_input}'))
        htr = float(self._query('HTR?'))
        setp = float(self._query('SETP? 1'))
        code = int(float(self._query(f'RDGST? {control_input}')))
        names = [n for bit, n in self.RDGST_BITS if code & bit]
        return temp, htr, setp, ", ".join(names)

    def get_temperature(self, control_input):
        return float(self._query(f'KRDG? {control_input}'))

    def get_heater_status(self):
        code = int(float(self._query('HTRST?')))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    # -- stop --

    def stop_ramp(self):
        """RAMP off and heater off.  Loop stays enabled (harmless at range 0)."""
        if self.lakeshore:
            try:
                self._write('RAMP 1,0,0')
                self._write('RANGE 0')
                print("  Lakeshore ramp stopped and heater turned off.")
            except Exception as e:
                print(f"  Warning: Could not fully stop ramp. {e}")

    def close(self):
        """Close the session only; the instrument keeps its state."""
        if self.lakeshore:
            try:
                self.lakeshore.close()
            except Exception as e:
                print(f"  Warning: Error closing Lakeshore session. {e}")
            finally:
                self.lakeshore = None

    def shutdown(self):
        if self.lakeshore:
            self.stop_ramp()
            self.close()


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class TempControlGUI:
    PROGRAM_VERSION = "1.0"
    LEFT_PANEL_WIDTH = 520
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_GOLD = '#B68B6E', '#BA6B5E', '#BA6B5E'
    CLR_STABLE_WAIT = '#D4A373'
    CLR_OK_GREEN = '#8AB845'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Lakeshore 340 Temperature Control v{self.PROGRAM_VERSION}")
        self.root.geometry("1450x850")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.logo_image = None
        self.backend = Lakeshore340_Backend()
        self.data_storage = {'time': [], 'temperature': [], 'setpoint': [],
                             'heater': []}
        self.resource_labels = {}
        self.params = {}
        self.data_file = None
        self.csv_writer = None
        self.data_filepath = None
        self.active_zone = None       # index into self.zones, or None
        self.zones = []
        self.stable_since = None
        self.reached = False
        self.last_htr_error = 0
        self.poll_job = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TButton', font=self.FONT_BASE, padding=(8, 7),
                        foreground=self.CLR_ACCENT_GOLD,
                        background=self.CLR_HEADER, borderwidth=0,
                        focusthickness=0, focuscolor='none')
        style.map('TButton',
                  background=[('active', self.CLR_ACCENT_GOLD),
                              ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK),
                              ('hover', self.CLR_TEXT_DARK)])
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN,
                        foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton',
                  background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED,
                        foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton',
                  background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor='#BA6B5E')
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('TEntry', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK,
                        insertcolor=self.CLR_TEXT_DARK)
        style.configure('TCombobox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        style.configure('TSpinbox', fieldbackground=self.CLR_GRAPH_BG,
                        foreground=self.CLR_TEXT_DARK)
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11,
                             'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(header, text="Lakeshore 340 Temperature Ramp Utility",
                  style='Header.TLabel', font=font_title_main,
                  foreground=self.CLR_ACCENT_GOLD).pack(
            side='left', padx=20, pady=10)
        ttk.Button(header, text="📈", command=launch_plotter_utility,
                   width=3).pack(side='right', padx=10, pady=5)
        ttk.Button(header, text="📟", command=launch_gpib_scanner,
                   width=3).pack(side='right', padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
        self._populate_right_panel(right_panel)
        self.root.after(50, self._set_default_sash_position)

    def _set_default_sash_position(self, attempt=0):
        try:
            self.root.update_idletasks()
            content_w = self.left_scrollable_frame.winfo_reqwidth()
            target = content_w + 30 if content_w > 1 else self.LEFT_PANEL_WIDTH
            target = max(target, self.LEFT_PANEL_WIDTH)
            self.main_pane.sashpos(0, target)
            if abs(self.main_pane.sashpos(0) - target) > 5 and attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))
        except tk.TclError:
            if attempt < 10:
                self.root.after(100, lambda: self._set_default_sash_position(attempt + 1))

    def _populate_left_panel(self, panel):
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = scrollable_frame
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        canvas.bind('<Enter>',
                    lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        scrollable_frame.grid_columnconfigure(0, weight=1)
        scrollable_frame.grid_rowconfigure(4, weight=1)
        self._create_info_panel(scrollable_frame, 0)
        self._create_control_panel(scrollable_frame, 1)
        self._create_zone_panel(scrollable_frame, 2)
        self._create_pid_panel(scrollable_frame, 3)
        self._create_console_panel(scrollable_frame, 4)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 100
        logo_canvas = Canvas(frame, width=LOGO_SIZE, height=LOGO_SIZE,
                             bg=self.CLR_FRAME_BG, highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "..", "assets", "LOGO",
                                     "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE / 2, LOGO_SIZE / 2,
                                         image=self.logo_image)
        except Exception as e:
            print(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 2, 'bold')
        ttk.Label(frame, text="UGC-DAE Consortium for Scientific Research",
                  font=institute_font, background=self.CLR_FRAME_BG).grid(
            row=0, column=1, padx=10, pady=(20, 0), sticky='sw')
        ttk.Label(frame, text="Mumbai Centre", font=institute_font,
                  background=self.CLR_FRAME_BG).grid(
            row=1, column=1, padx=10, pady=(0, 5), sticky='nw')
        ttk.Label(frame,
                  text="Lake Shore Model 340 | Loop 1 heater | CCR 3-310 K",
                  justify='left', background=self.CLR_FRAME_BG).grid(
            row=2, column=1, padx=10, pady=(0, 10), sticky='w')

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Ramp Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(1, weight=1)
        self.entries = {}

        self._create_entry(frame, "Target Temp (K)", "310", 0)
        self._create_entry(frame, "Ramp Rate (K/min)", "2", 1)
        self._create_entry(frame, "Logging Delay (s)", "1", 2)
        self._create_entry(frame, "Tolerance (±K)", "0.5", 3)
        self._create_entry(frame, "Dwell at target (s)", "60", 4)

        ttk.Label(frame, text="Control Input:").grid(
            row=5, column=0, sticky='w', padx=10, pady=5)
        self.input_var = tk.StringVar(value='A')
        ttk.Combobox(frame, textvariable=self.input_var,
                     values=['A', 'B', 'C', 'D'], state='readonly',
                     width=6).grid(row=5, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(frame, text="Heater Range (manual):").grid(
            row=6, column=0, sticky='w', padx=10, pady=5)
        self.heater_range_var = tk.StringVar(value='5')
        self.heater_cb = ttk.Combobox(
            frame, textvariable=self.heater_range_var,
            values=['0 (Off)', '1', '2', '3', '4', '5'], state='readonly')
        self.heater_cb.grid(row=6, column=1, sticky='ew', padx=10, pady=5)
        self.heater_cb.bind('<<ComboboxSelected>>', self._on_heater_range_changed)

        self.hold_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Hold at target after arrival (heater stays on; default: heater OFF)",
            variable=self.hold_var).grid(
            row=7, column=0, columnspan=2, sticky='w', padx=10, pady=(2, 4))

        ttk.Label(frame, text="Lakeshore VISA:").grid(
            row=8, column=0, sticky='w', padx=10, pady=5)
        self.ls_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.ls_cb.grid(row=8, column=1, sticky='ew', padx=10, pady=5)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=9, column=0, columnspan=2, sticky='ew', pady=10)
        button_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.start_button = ttk.Button(button_frame, text="Start Ramp",
                                       style='Start.TButton',
                                       command=self.start_ramp)
        self.start_button.grid(row=0, column=0, sticky='ew', padx=4)
        self.stop_button = ttk.Button(button_frame, text="Stop",
                                      style='Stop.TButton', state='disabled',
                                      command=self.stop_ramp)
        self.stop_button.grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Button(button_frame, text="Scan",
                   command=self._scan_for_visa).grid(
            row=0, column=2, sticky='ew', padx=4)
        self.identify_btn = ttk.Button(button_frame, text="Identify",
                                       command=self._identify_visa)
        self.identify_btn.grid(row=0, column=3, sticky='ew', padx=4)

    def _create_zone_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='PID Zones (by measured temperature)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure(0, weight=1)

        self.zones_enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Auto-select PID + heater range from the zone the reading is in",
            variable=self.zones_enabled_var,
            command=self._on_zones_toggled).grid(
            row=0, column=0, sticky='w', padx=10, pady=(5, 2))

        ttk.Label(frame,
                  text=("'Upper Bound' is the top of a segment; the first one\n"
                        "starts at 0 K, the last also covers anything above it.\n"
                        "Switches happen at the boundary, no hysteresis, and\n"
                        "are logged. Edit rows during a run, then 'Apply'."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(row=1, column=0, sticky='w', padx=10)

        sel = ttk.Frame(frame)
        sel.grid(row=2, column=0, sticky='ew', padx=10, pady=4)
        ttk.Label(sel, text=f"Segments ({ZONE_T_MIN:g}-{ZONE_T_MAX:g} K):",
                  background=self.CLR_FRAME_BG).pack(side='left')
        self.zone_segments_var = tk.IntVar(value=len(ZONE_DEFAULTS))
        ttk.Spinbox(sel, from_=1, to=20, width=4,
                    textvariable=self.zone_segments_var).pack(side='left', padx=5)
        ttk.Button(sel, text="Generate equal",
                   command=self._generate_zone_rows).pack(side='left', padx=4)
        ttk.Button(sel, text="Reset defaults",
                   command=self._reset_zone_rows).pack(side='left', padx=4)

        self.zone_table = ttk.Frame(frame)
        self.zone_table.grid(row=3, column=0, sticky='ew', padx=10, pady=4)
        for col, h in enumerate(['Upper Bound (K)', 'P', 'I', 'D', 'Range']):
            ttk.Label(self.zone_table, text=h, background=self.CLR_FRAME_BG,
                      font=('Segoe UI', 9, 'bold')).grid(
                row=0, column=col, padx=4, pady=2)
        self.zone_rows = []
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)

        btns = ttk.Frame(frame)
        btns.grid(row=4, column=0, sticky='ew', padx=10, pady=(2, 6))
        btns.grid_columnconfigure((0, 1, 2), weight=1)
        ttk.Button(btns, text="+ Add Zone",
                   command=lambda: self._add_zone_row()).grid(
            row=0, column=0, sticky='ew', padx=4)
        ttk.Button(btns, text="- Remove Zone",
                   command=self._remove_zone_row).grid(
            row=0, column=1, sticky='ew', padx=4)
        ttk.Button(btns, text="Apply (live)",
                   command=self._apply_zone_table).grid(
            row=0, column=2, sticky='ew', padx=4)

        self.lbl_zone_status = ttk.Label(
            frame, text="Active zone: --", background=self.CLR_FRAME_BG,
            foreground=self.CLR_ACCENT_GOLD, font=('Segoe UI', 10, 'bold'))
        self.lbl_zone_status.grid(row=5, column=0, sticky='w', padx=10, pady=(0, 6))

        self._on_zones_toggled()

    def _create_pid_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Manual PID (Loop 1)')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5, padx=10)
        frame.grid_columnconfigure((1, 3, 5), weight=1)

        ttk.Label(frame,
                  text=("Sent as typed. With zones ON a manual send lasts until\n"
                        "the reading crosses the next zone boundary."),
                  background=self.CLR_FRAME_BG, font=('Segoe UI', 9),
                  justify='left').grid(row=0, column=0, columnspan=6,
                                       sticky='w', padx=10, pady=(4, 2))
        self.pid_entries = {}
        for col, (key, label, default) in enumerate(
                (('p', 'P:', '0.5'), ('i', 'I:', '4'), ('d', 'D:', '0'))):
            ttk.Label(frame, text=label).grid(row=1, column=2 * col, sticky='e',
                                              padx=(10, 2), pady=4)
            e = ttk.Entry(frame, width=8, font=self.FONT_BASE)
            e.insert(0, default)
            e.grid(row=1, column=2 * col + 1, sticky='ew', padx=(0, 6), pady=4)
            self.pid_entries[key] = e
        btns = ttk.Frame(frame)
        btns.grid(row=2, column=0, columnspan=6, sticky='ew', padx=10, pady=(2, 6))
        btns.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(btns, text="Send PID", command=self._send_pid).grid(
            row=0, column=0, sticky='ew', padx=4)
        ttk.Button(btns, text="Read PID", command=self._read_pid).grid(
            row=0, column=1, sticky='ew', padx=4)

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5, padx=10)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.console = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word',
            borderwidth=0, height=10)
        self.console.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.log("Console initialized. Set parameters and start ramp.")

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        status_frame = ttk.Frame(panel)
        status_frame.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        status_frame.grid_columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(
            status_frame, text="READY TO START", font=('Segoe UI', 16, 'bold'),
            bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK, pady=10)
        self.lbl_status.grid(row=0, column=0, sticky='ew')
        self.lbl_current_temp = tk.Label(
            status_frame, text="--- K", font=('Segoe UI', 26, 'bold'),
            bg=self.CLR_FRAME_BG, fg=self.CLR_ACCENT_RED, padx=20)
        self.lbl_current_temp.grid(row=0, column=1, sticky='e', padx=10)

        container = ttk.LabelFrame(panel, text='Live Temperature')
        container.grid(row=1, column=0, sticky='nsew')
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)

        self.line_setp = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_GREEN, linestyle='--', label='Setpoint')[0]
        self.line_temp = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=3,
            linestyle='-', label='Temperature')[0]
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle='--', alpha=0.6)
        self.ax_temp.legend(loc='best', frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis='x', which='both', bottom=False, top=False,
                                 labelbottom=False)

        self.line_heater = self.ax_heater.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker='.', markersize=3,
            linestyle='-')[0]
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle='--', alpha=0.6)

        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

    # --- ZONE TABLE HELPERS ---

    def _add_zone_row(self, upper=ZONE_T_MAX, p=0.5, i=4.0, d=0.0, rng=5):
        r = len(self.zone_rows) + 1
        widgets, entries = [], []
        for col, val, width in ((0, upper, 10), (1, p, 7), (2, i, 7), (3, d, 7)):
            e = ttk.Entry(self.zone_table, width=width, font=self.FONT_BASE)
            e.insert(0, f"{val:g}")
            e.grid(row=r, column=col, padx=4, pady=2)
            widgets.append(e)
            entries.append(e)
        rng_var = tk.StringVar(value=str(int(rng)))
        rng_cb = ttk.Combobox(self.zone_table, textvariable=rng_var,
                              values=['0', '1', '2', '3', '4', '5'],
                              state='readonly', width=5)
        rng_cb.grid(row=r, column=4, padx=4, pady=2)
        widgets.append(rng_cb)
        self.zone_rows.append({'ub': entries[0], 'p': entries[1], 'i': entries[2],
                               'd': entries[3], 'range': rng_var,
                               'widgets': widgets})

    def _remove_zone_row(self):
        if len(self.zone_rows) <= 1:
            self.log("At least one zone is required.")
            return
        row = self.zone_rows.pop()
        for w in row['widgets']:
            w.destroy()

    def _clear_zone_rows(self):
        while self.zone_rows:
            row = self.zone_rows.pop()
            for w in row['widgets']:
                w.destroy()

    def _reset_zone_rows(self):
        self._clear_zone_rows()
        for z in ZONE_DEFAULTS:
            self._add_zone_row(*z)
        self.zone_segments_var.set(len(ZONE_DEFAULTS))
        self.log("Zone table reset to the CCR defaults (not yet applied "
                 "to a running ramp: press Apply).")

    def _generate_zone_rows(self):
        try:
            n = int(self.zone_segments_var.get())
            zones = generate_equal_zones(n)
        except (ValueError, tk.TclError) as e:
            self.log(f"ERROR: cannot generate segments: {e}")
            return
        self._clear_zone_rows()
        for z in zones:
            self._add_zone_row(*z)
        self.log(f"Generated {n} equal segment(s) from {ZONE_T_MIN:g} K to "
                 f"{ZONE_T_MAX:g} K with default PID/range per segment.")

    def _collect_zones(self):
        """(ub, P, I, D, range) rows sorted by bound; raises ValueError."""
        zones = []
        for r in self.zone_rows:
            ub = float(r['ub'].get())
            p = float(r['p'].get())
            i = float(r['i'].get())
            d = float(r['d'].get())
            rng = int(r['range'].get())
            if not (0 <= p <= 1000 and 0 <= i <= 1000 and 0 <= d <= 1000):
                raise ValueError(f"zone <= {ub:g} K: P, I, D must be 0-1000")
            zones.append((ub, p, i, d, rng))
        if not zones:
            raise ValueError("the zone table is empty")
        zones.sort(key=lambda z: z[0])
        bounds = [z[0] for z in zones]
        if len(set(bounds)) != len(bounds):
            raise ValueError("two zones share the same upper bound")
        return zones

    def _on_zones_toggled(self):
        # With zones on, the manual heater-range box is informational only.
        self.heater_cb.config(
            state='disabled' if self.zones_enabled_var.get() else 'readonly')
        if self.is_running:
            if self.zones_enabled_var.get():
                self.active_zone = None   # force a (re)send on the next poll
                self.log("Zones enabled: PID/range follow the reading from the next sample.")
            else:
                self.log("Zones disabled: PID/range stay as they are until you send them.")

    def _apply_zone_table(self):
        try:
            self.zones = self._collect_zones()
        except ValueError as e:
            self.log(f"ERROR in zone table: {e}")
            return
        self.active_zone = None   # re-evaluate on the next poll
        self.log("Zone table applied: " + "; ".join(
            f"<={ub:g} K: P{p:g}/I{i:g}/D{d:g}/R{r}" for ub, p, i, d, r in self.zones))

    def _apply_zone_for_temperature(self, temp, force=False):
        """Send the PID + range of the zone <temp> is in if it changed."""
        if not self.zones_enabled_var.get() or not self.zones:
            return
        idx = select_zone(self.zones, temp)
        if idx == self.active_zone and not force:
            return
        ub, p, i, d, rng = self.zones[idx]
        lo = self.zones[idx - 1][0] if idx > 0 else 0.0
        self.backend.set_pid(p, i, d)
        self.backend.set_heater_range(rng)
        self.active_zone = idx
        self.lbl_zone_status.config(
            text=f"Active zone {idx + 1}: {lo:g}-{ub:g} K  "
                 f"P={p:g} I={i:g} D={d:g} range {rng}")
        self.heater_range_var.set(str(rng))
        for key, val in (('p', p), ('i', i), ('d', d)):
            self.pid_entries[key].delete(0, 'end')
            self.pid_entries[key].insert(0, f"{val:g}")
        self.log(f"ZONE {idx + 1} ({lo:g}-{ub:g} K) at T={temp:.3f} K: "
                 f"PID 1,{p:g},{i:g},{d:g}; RANGE {rng}")
        self._csv_note(f"zone {idx + 1}: PID {p:g}/{i:g}/{d:g} range {rng}")

    # --- MANUAL PID ---

    def _send_pid(self):
        if not self.is_running:
            self.log("PID can only be sent while a ramp is active.")
            return
        try:
            p = float(self.pid_entries['p'].get())
            i = float(self.pid_entries['i'].get())
            d = float(self.pid_entries['d'].get())
            self.backend.set_pid(p, i, d)
            self.log(f"Manual PID sent: PID 1,{p:g},{i:g},{d:g}")
            self._csv_note(f"manual PID {p:g}/{i:g}/{d:g}")
        except ValueError as e:
            self.log(f"ERROR: {e}")
        except Exception as e:
            self.log(f"ERROR sending PID: {e}")

    def _read_pid(self):
        if not self.is_running:
            self.log("PID can only be read while a ramp is active.")
            return
        try:
            p, i, d = self.backend.get_pid()
            self.log(f"PID? 1 -> P={p}, I={i}, D={d}")
            for key, val in (('p', p), ('i', i), ('d', d)):
                self.pid_entries[key].delete(0, 'end')
                self.pid_entries[key].insert(0, f"{val:g}")
        except Exception as e:
            self.log(f"ERROR reading PID: {e}")

    def _on_heater_range_changed(self, event=None):
        if not self.is_running or self.zones_enabled_var.get():
            return
        code = int(self.heater_range_var.get().split()[0])
        try:
            self.backend.set_heater_range(code)
            self.log(f"Heater range set to {code} (RANGE {code}).")
            self._csv_note(f"manual range {code}")
        except Exception as e:
            self.log(f"ERROR setting heater range: {e}")

    # --- LOGGING ---

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _beep(self):
        try:
            self.root.bell()
        except Exception:
            pass

    def _set_status(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _open_data_file(self):
        os.makedirs("data", exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_filepath = os.path.join("data", f"TRamp_L340_{stamp}.csv")
        self.data_file = open(self.data_filepath, 'w', newline='')
        self.csv_writer = csv.writer(self.data_file)
        self.csv_writer.writerow([f"# {self.backend.idn}"])
        self.csv_writer.writerow([
            f"# target {self.params['setpoint']} K, rate {self.params['rate']} K/min, "
            f"input {self.params['input']}, tolerance {self.params['tol']} K, "
            f"dwell {self.params['dwell']} s, zones "
            f"{'on' if self.zones_enabled_var.get() else 'off'}"])
        self.csv_writer.writerow(["Timestamp", "Elapsed_s", "Setpoint_K",
                                  "Temperature_K", "Heater_pct", "Range",
                                  "Zone", "Note"])
        self.data_file.flush()
        self.log(f"Logging data to: {self.data_filepath}")

    def _csv_row(self, elapsed, setp, temp, htr, rng, note=""):
        if not self.csv_writer:
            return
        zone = "" if self.active_zone is None else str(self.active_zone + 1)
        try:
            self.csv_writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}",
                f"{setp:.4f}", f"{temp:.4f}", f"{htr:.2f}", rng, zone, note])
            self.data_file.flush()
            os.fsync(self.data_file.fileno())
        except Exception as e:
            self.log(f"WARN: data write failed: {e}")

    def _csv_note(self, note):
        """A row that carries only a note (events between samples)."""
        if self.csv_writer and self.is_running:
            elapsed = time.time() - self.start_time
            try:
                self.csv_writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed:.2f}",
                    "", "", "", "", "", note])
                self.data_file.flush()
            except Exception:
                pass

    def _close_data_file(self):
        if self.data_file:
            try:
                self.data_file.flush()
                self.data_file.close()
                self.log(f"Data file closed: {self.data_filepath}")
            except Exception:
                pass
            finally:
                self.data_file = None
                self.csv_writer = None

    # --- RUN CONTROL ---

    def start_ramp(self):
        try:
            self.params = self._validate_and_get_params()
            if self.zones_enabled_var.get():
                self.zones = self._collect_zones()
            else:
                self.zones = []
            self.log(f"Connecting to {self.params['ls_visa']}...")
            idn = self.backend.connect(self.params['ls_visa'])
            self.log(f"Connected: {idn}")
            if not self.backend.is_model_340():
                self.backend.close()
                raise RuntimeError(
                    f"'{idn}' is not a Lake Shore Model 340. Refusing to send "
                    "340-only commands (CSET, RANGE n) to it.")

            cset, cmode, limits = self.backend.prepare_loop(self.params['input'])
            cur = self.backend.MAX_CURRENT_CODES.get(
                limits['max_current'], f"code {limits['max_current']}")
            self.log(f"Loop 1 enabled on input {cset['input']} (kelvin), "
                     f"Manual PID. Limits: setpoint <= {limits['sp_limit']:g} K, "
                     f"max current {cur}, max range {limits['max_range']}.")
            if self.params['setpoint'] > limits['sp_limit']:
                raise ValueError(
                    f"Target {self.params['setpoint']} K is above the 340's "
                    f"setpoint limit of {limits['sp_limit']:g} K (CLIMIT). Raise "
                    "the limit in the Direct Control module first.")
            wanted = ({z[4] for z in self.zones} if self.zones
                      else {self.params['heater_range']})
            too_high = sorted(r for r in wanted if r > limits['max_range'])
            if too_high:
                raise ValueError(
                    f"Heater range(s) {too_high} exceed the 340's max range "
                    f"{limits['max_range']} (CLIMIT). Lower them or raise the limit.")

            temp_now = self.backend.get_temperature(self.params['input'])
            self.direction = 'up' if self.params['setpoint'] >= temp_now else 'down'
            self.log(f"Present temperature {temp_now:.3f} K; ramping "
                     f"{self.direction} to {self.params['setpoint']} K at "
                     f"{self.params['rate']} K/min.")

            code, text = self.backend.get_heater_status()
            if code != 0:
                raise RuntimeError(f"Heater error HTRST? {code:02d}: {text}. "
                                   "Fix the heater circuit before ramping.")

            # PID and range first, then the ramp, so the loop never runs on
            # stale gains.
            self.active_zone = None
            if self.zones:
                self._apply_zone_for_temperature(temp_now, force=True)
            else:
                p = float(self.pid_entries['p'].get())
                i = float(self.pid_entries['i'].get())
                d = float(self.pid_entries['d'].get())
                self.backend.set_pid(p, i, d)
                self.backend.set_heater_range(self.params['heater_range'])
                self.log(f"Manual mode: PID 1,{p:g},{i:g},{d:g}; "
                         f"RANGE {self.params['heater_range']}")
                self.lbl_zone_status.config(text="Active zone: -- (zones off)")

            self.backend.start_ramp(self.params['setpoint'], self.params['rate'],
                                    temp_now)
            self.log("Ramp started (setpoint pinned to present temperature first).")

            self.is_running = True
            self.reached = False
            self.stable_since = None
            self.last_htr_error = 0
            self.set_ui_state(running=True)
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_temp.set_data([], [])
            self.line_setp.set_data([], [])
            self.line_heater.set_data([], [])
            self.ax_temp.set_title(f"Ramping to {self.params['setpoint']} K")
            self.canvas.draw()
            self._set_status(f"RAMPING TO {self.params['setpoint']} K",
                             self.CLR_ACCENT_RED)

            self.start_time = time.time()
            self._open_data_file()
            self.poll_job = self.root.after(100, self._monitoring_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            hint = explain_visa_error(e)
            if hint:
                self.log(f"HINT: {hint}")
            # Start is attended, so a dialog is acceptable here.
            messagebox.showerror("Start Failed", f"{e}" + (f"\n\n{hint}" if hint else ""))
            self.backend.shutdown()

    def stop_ramp(self, reason="Stopping ramp by user request.", heater_off=True):
        if not self.is_running:
            return
        self.log(reason)
        self.is_running = False
        if self.poll_job is not None:
            try:
                self.root.after_cancel(self.poll_job)
            except Exception:
                pass
            self.poll_job = None
        if heater_off:
            self.backend.stop_ramp()
            self.log("RAMP 1,0,0 and RANGE 0 sent: heater is OFF.")
        else:
            self.log("Heater left ON, loop still controlling at the setpoint.")
        self.backend.close()
        self._close_data_file()
        self.set_ui_state(running=False)
        self.canvas.draw_idle()

    def _monitoring_loop(self):
        if not self.is_running:
            return
        try:
            b = self.backend
            inp = self.params['input']
            temp, htr, setp, rd_status = b.get_status(inp)
            rng = b.get_heater_range()
            elapsed = time.time() - self.start_time

            self.lbl_current_temp.config(text=f"{temp:.3f} K")
            line = f"T: {temp:.3f} K | SP: {setp:.3f} K | Heater: {htr:.1f}% | R{rng}"
            if rd_status:
                line += f" | RDGST: {rd_status}"
            self.log(line)

            code, text = b.get_heater_status()
            if code != self.last_htr_error:
                self.last_htr_error = code
                if code != 0:
                    self.log(f"HEATER ERROR HTRST? {code:02d}: {text}")
                    self._beep()
                    self._csv_note(f"heater error {code:02d} {text}")
                else:
                    self.log("Heater error cleared (HTRST? 00).")

            if rd_status:
                # An invalid/over-range reading must not drive the zone
                # table or the arrival test.
                self._csv_row(elapsed, setp, temp, htr, rng, f"reading: {rd_status}")
            else:
                self._apply_zone_for_temperature(temp)
                self._csv_row(elapsed, setp, temp, htr, rng)

            self.data_storage['time'].append(elapsed)
            self.data_storage['temperature'].append(temp)
            self.data_storage['setpoint'].append(setp)
            self.data_storage['heater'].append(htr)
            self.line_temp.set_data(self.data_storage['time'],
                                    self.data_storage['temperature'])
            self.line_setp.set_data(self.data_storage['time'],
                                    self.data_storage['setpoint'])
            self.line_heater.set_data(self.data_storage['time'],
                                      self.data_storage['heater'])
            for ax in (self.ax_temp, self.ax_heater):
                ax.relim()
                ax.autoscale_view()
            self.canvas.draw_idle()

            # Arrival: in band for the dwell time, both directions.
            if not self.reached and not rd_status:
                in_band = abs(temp - self.params['setpoint']) <= self.params['tol']
                if in_band:
                    if self.stable_since is None:
                        self.stable_since = time.time()
                        self._set_status(
                            f"IN BAND AT {self.params['setpoint']} K, DWELLING...",
                            self.CLR_STABLE_WAIT)
                        self.log(f"Entered ±{self.params['tol']} K band; dwell "
                                 f"{self.params['dwell']} s started.")
                    elif time.time() - self.stable_since >= self.params['dwell']:
                        self.reached = True
                        self._beep()
                        if self.hold_var.get():
                            self._set_status(
                                f"AT {self.params['setpoint']} K | HOLDING (heater on)",
                                self.CLR_OK_GREEN)
                            self.ax_temp.set_title(
                                f"Holding at {self.params['setpoint']} K")
                            self.log("Target reached and held for the dwell. "
                                     "Holding: loop keeps controlling, logging "
                                     "continues until Stop.")
                            self._csv_note("target reached, holding")
                        else:
                            self.ax_temp.set_title(
                                f"Reached {self.params['setpoint']} K, heater off")
                            self._csv_note("target reached, heater off")
                            self.stop_ramp(
                                reason="Target reached and held for the dwell. "
                                       "Ramp complete.", heater_off=True)
                            self._set_status(
                                f"RAMP COMPLETE | HEATER OFF ({self.params['setpoint']} K)",
                                self.CLR_OK_GREEN)
                            return
                else:
                    if self.stable_since is not None:
                        self.log("Left the tolerance band; dwell restarted.")
                        self._set_status(f"RAMPING TO {self.params['setpoint']} K",
                                         self.CLR_ACCENT_RED)
                    self.stable_since = None

            self.poll_job = self.root.after(
                int(self.params['delay_s'] * 1000), self._monitoring_loop)

        except Exception as e:
            # Unattended-safe: no dialog. Heater off, banner, beep, console.
            self.log(f"CRITICAL ERROR: {traceback.format_exc()}")
            self._beep()
            self.stop_ramp(reason="Ramp stopped after a runtime error.",
                           heater_off=True)
            self._set_status("ERROR: RAMP STOPPED, HEATER OFF (see console)",
                             self.CLR_ACCENT_RED)

    def _validate_and_get_params(self):
        try:
            params = {
                'setpoint': float(self.entries["Target Temp (K)"].get()),
                'rate': float(self.entries["Ramp Rate (K/min)"].get()),
                'delay_s': float(self.entries["Logging Delay (s)"].get()),
                'tol': float(self.entries["Tolerance (±K)"].get()),
                'dwell': float(self.entries["Dwell at target (s)"].get()),
                'heater_range': int(self.heater_range_var.get().split()[0]),
                'input': self.input_var.get(),
                'ls_visa': self._selected_address(),
            }
        except ValueError as e:
            raise ValueError(f"Invalid parameter input: {e}")
        if not params['ls_visa']:
            raise ValueError("Please scan and select the Lakeshore VISA address.")
        if not (0.1 <= params['rate'] <= 100):
            raise ValueError("Ramp rate must be 0.1-100 K/min on a Model 340.")
        if params['delay_s'] <= 0:
            raise ValueError("Logging delay must be positive.")
        if params['tol'] <= 0:
            raise ValueError("Tolerance must be positive.")
        if params['dwell'] < 0:
            raise ValueError("Dwell cannot be negative.")
        if params['setpoint'] <= 0:
            raise ValueError("Target temperature must be positive.")
        if not self.zones_enabled_var.get() and params['heater_range'] == 0:
            raise ValueError("Heater range 0 (Off) cannot ramp. Pick 1-5 or "
                             "enable zones.")
        return params

    def set_ui_state(self, running: bool):
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values():
            w.config(state=state)
        self.ls_cb.config(state=state if state == 'normal' else 'readonly')
        self.identify_btn.config(state=state)
        self.stop_button.config(state='normal' if running else 'disabled')
        # Heater range stays live in manual mode; zones own it otherwise.
        self.heater_cb.config(
            state='disabled' if self.zones_enabled_var.get() else 'readonly')

    # --- VISA DISCOVERY ---

    def _selected_address(self):
        label = self.ls_cb.get()
        return self.resource_labels.get(label, label)

    def _scan_for_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        self.log("Scanning for VISA instruments...")
        try:
            resources = list(self.backend.rm.list_resources())
        except Exception as e:
            self.log(f"Scan error: {e}")
            return
        if resources:
            self.log(f"Found: {resources}")
            self.resource_labels = {r: r for r in resources}
            self.ls_cb['values'] = resources
            if len(resources) == 1:
                self.ls_cb.set(resources[0])
            else:
                self.ls_cb.set('')
                self.log("Several addresses. Press Identify to find the "
                         "MODEL340, or pick it yourself.")
        else:
            self.log("No VISA instruments found.")

    def _identify_visa(self):
        if self.backend.rm is None:
            self.log("ERROR: PyVISA library missing.")
            return
        if self.is_running:
            return
        addresses = list(self.resource_labels.values())
        if not addresses:
            self.log("Nothing to identify: press Scan first.")
            return
        self.log(f"Identifying {len(addresses)} address(es) with *IDN? "
                 "(2 s timeout each)...")
        self.root.update_idletasks()
        replies = self.backend.identify_resources(addresses)
        labels, chosen = {}, None
        for addr in addresses:
            reply = replies.get(addr, "no reply")
            if reply.startswith("ERROR:"):
                hint = explain_visa_error(reply)
                short = reply.split('):')[0] + ')' if '):' in reply else reply
                self.log(f"  {addr}: {short}")
                if hint:
                    self.log(f"      {hint}")
                labels[f"{addr}  (no answer)"] = addr
            else:
                self.log(f"  {addr}: {reply}")
                label = f"{addr}  ({reply[:32]})"
                labels[label] = addr
                if 'MODEL340' in reply.upper().replace(' ', '') and chosen is None:
                    chosen = label
        self.resource_labels = labels
        self.ls_cb['values'] = list(labels.keys())
        if chosen:
            self.ls_cb.set(chosen)
            self.log(f"Selected the Model 340 at {labels[chosen]}.")
        else:
            self.ls_cb.set('')
            self.log("No address answered as a Model 340.")

    # --- WIDGET HELPERS ---

    def _create_entry(self, parent, label_text, default_value, row):
        ttk.Label(parent, text=f"{label_text}:").grid(
            row=row, column=0, sticky='w', padx=10, pady=4)
        entry = ttk.Entry(parent, font=self.FONT_BASE)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=4)
        entry.insert(0, default_value)
        self.entries[label_text] = entry

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "A ramp is active. Stop (heater off) and exit?"):
                self.stop_ramp()
                self.root.destroy()
        else:
            self.backend.close()
            self.root.destroy()


if __name__ == '__main__':
    if not pyvisa:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed. Please run 'pip install pyvisa'.")
        root.destroy()
    else:
        root = tk.Tk()
        app = TempControlGUI(root)
        root.mainloop()
