"""
Module: T_Control_L340_Step_GUI.py
Purpose: Step-sequence temperature control on a Lake Shore Model 340
         (Threaded & Multi-plot).  Port of T_Control_L350_Step_GUI.py to
         the Model 340 command set.

What is different from the Model 350 version, and why
-----------------------------------------------------
  * RANGE takes no loop number on a 340: "RANGE <0-5>" sets the Loop 1
    heater range, "RANGE?" reads it back.  The 350 form "RANGE 1,<n>" is a
    syntax error on a 340.  Every RANGE write is verified with RANGE?.
                                                 (340 manual, printed 9-40/9-41)
  * HTR? takes no argument and always reports Loop 1 in percent.  (9-33)
  * Loop 1 is DISABLED from the factory on a 340: the heater stays off
    until "CSET 1,<input>,1,1" is sent.  The worker sends it at the start
    of the sequence and verifies with CSET? 1.                     (9-31)
  * "CMODE 1,1" (Manual PID) is sent and verified so nothing inside the
    instrument (zone table, AutoTune) overrides the PID this GUI sends.
                                                                   (9-29)
  * There is no TLIMIT on a 340.  "CLIMIT? 1" is read at start and its
    setpoint limit, max current code and max range are logged; a step above
    the setpoint limit or a heater range above the max range is refused.
                                                                   (9-28)
  * A 340 ramps from the CURRENT SETPOINT, not from the temperature.  Each
    step therefore pins the setpoint to the present reading with the ramp
    off (RAMP 1,0,0; SETP 1,<T now>) before enabling the ramp and sending
    the target, exactly as T_Control_L340_RangeControl_GUI.py does.
  * PID limits are 0-1000 for P, I and D (350: P<=9999, D<=200); the ramp
    rate is 0.1-100 K/min (350: 0.001-100).                        (9-40)
  * HTRST? is read on every poll; a change is logged once, a non-zero code
    (05 open heater load, 06 load < 10 ohm) beeps and shows in the banner.
    RDGST? is read with every sample; a non-zero status is logged and that
    sample is not used for the stability test.                     (9-41)
  * The control/sensor input is selectable (A or B on a base 340; C and D
    only with the 3462 option card).  Default A, as the 350 file fixed it.
  * No *RST anywhere: on a 340 "*RST sets controller parameters to
    power-up settings" (9-24), which would wipe loop, setpoint and ramp.
    Only *CLS is sent.
  * The connection is accepted only if *IDN? contains MODEL340.  Scan
    pre-selects the lab address (::19::) and never auto-picks 12 or 15.
  * MODE is not sent (the 350 file did not send it either).       (9-38)

CHANGELOG (this revision):
  1. Plot: added user-adjustable X-axis minimum + window-scoped Y auto-scaling.
  2. UI: added large live-temperature readout; fixed left-panel default sizing.
  3. Dynamic step management: sequence can be edited (add/remove) while running.
  4. Bug fixes: pad-lock buttons now stay usable during a run; stabilization now
     requires a continuous dwell (timer + consecutive in-band samples) instead of
     declaring "stable" on the first sample that clips the tolerance band.
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
            messagebox.showerror(
                "File Not Found", f"Plotter utility not found at:\n{plotter_path}"
            )
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def launch_gpib_scanner():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(script_dir, "..", "utils", "GPIB_Instrument_Scanner_GUI.py")
        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found", f"GPIB Scanner not found at:\n{scanner_path}"
            )
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


def explain_visa_error(exc):
    """Plain-language hint for the VISA errors seen on the lab PCs."""
    text = str(exc)
    if 'VI_ERROR_ALLOC' in text:
        return ("VI_ERROR_ALLOC comes from the VISA driver before any "
                "command is sent: the VISA library cannot open a session on "
                "that GPIB interface. Most often the interface is a STALE entry "
                "cached by Keysight Connection Expert (an adapter that is no "
                "longer plugged in): list_resources() still reports it, and "
                "opening it fails. Remove the dead interface in Connection "
                "Expert and rescan; the live adapter usually is the other "
                "board (GPIB1::..). If Connection Expert itself cannot talk "
                "to the instrument either, PyVISA may be loading NI-VISA for a "
                "Keysight adapter: tick 'Keysight VISA as primary VISA' in "
                "Connection Expert settings, or set "
                "PYVISA_LIBRARY=C:\\Windows\\System32\\ktvisa32.dll and restart "
                "PICA. Then use 'Identify' to see which address answers as "
                "MODEL340.")
    if 'VI_ERROR_TMO' in text:
        return ("Timeout: the address exists but nothing answered *IDN?. "
                "Check the 340 is powered, its IEEE address matches, and no "
                "other programme holds the session.")
    return ""


class Lakeshore_Backend:
    """Model 340 control backend (Loop 1 only).  No *RST is ever sent."""

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
        self.idn = self.lakeshore.query("*IDN?").strip()
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

    def configure_ramp(self, setpoint, rate, heater_range, current_temperature):
        """Range first, then setpoint pinned to now (ramp off), then ramp on
        and the target.  A 340 ramps from the CURRENT SETPOINT, so without
        the pin a leftover setpoint would make the ramp start far away."""
        self.set_heater_range(heater_range)  # ensure heater on at desired range
        if not (0.1 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self._write("RAMP 1,0,0")
        self._write(f"SETP 1,{current_temperature:.3f}")
        time.sleep(0.2)
        self._write(f"RAMP 1,1,{rate}")  # enable ramp FIRST
        time.sleep(0.1)
        self._write(f"SETP 1,{setpoint}")  # now the change is ramped

    def set_ramp_rate(self, rate):
        if not (0.1 <= rate <= 100):
            raise ValueError(f"Ramp rate must be 0.1-100 K/min on a Model 340, got {rate}")
        self._write(f"RAMP 1,1,{rate}")

    @staticmethod
    def range_code(heater_range):
        """Heater range code from a string ('High', '5 (Max)') or number (5)."""
        try:
            return int(str(heater_range).split()[0])
        except (ValueError, TypeError, IndexError):
            range_map = {"off": 0, "low": 2, "medium": 4, "high": 5}
            if str(heater_range).lower() in range_map:
                return range_map[str(heater_range).lower()]
        raise ValueError(f"Heater range must be 0-5. Got: {heater_range}")

    def set_heater_range(self, heater_range):
        """RANGE <0-5>: Loop 1 only, no loop number on a 340.  Verified."""
        range_code = self.range_code(heater_range)
        if not (0 <= range_code <= 5):
            raise ValueError(f"Heater range must be 0-5. Got: {heater_range}")
        self._write(f"RANGE {range_code}")
        time.sleep(0.1)
        back = self.get_heater_range()
        if back != range_code:
            raise RuntimeError(
                f"RANGE {range_code} did not stick: RANGE? = {back}. The CLIMIT "
                "max range may be lower, or the loop is disabled.")

    def get_heater_range(self):
        return int(float(self._query("RANGE?")))

    # -- readings --

    def get_status(self, control_input):
        """-> (temperature K, sensor units, heater %, reading-status text)."""
        temp = float(self._query(f"KRDG? {control_input}"))
        resistance = float(self._query(f"SRDG? {control_input}"))
        htr_output = float(self._query("HTR?"))
        code = int(float(self._query(f"RDGST? {control_input}")))
        names = [n for bit, n in self.RDGST_BITS if code & bit]
        return temp, resistance, htr_output, ", ".join(names)

    def get_temperature(self, control_input):
        return float(self._query(f"KRDG? {control_input}"))

    def get_heater_status(self):
        """HTRST? -> (code, text).  05 = open heater load."""
        code = int(float(self._query("HTRST?")))
        return code, self.HEATER_ERRORS.get(code, f"unknown code {code}")

    # -- stop --

    def stop_ramp(self):
        """RAMP off and heater off.  Loop stays enabled (harmless at range 0)."""
        if self.lakeshore:
            try:
                self._write("RAMP 1,0,0")
                self._write("RANGE 0")
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

    def set_pid(self, loop, p, i, d):
        """PID 1,P,I,D.  On a Model 340 P, I and D are each 0-1000."""
        if not (0 <= p <= 1000 and 0 <= i <= 1000 and 0 <= d <= 1000):
            raise ValueError("PID values must be 0-1000 on a Model 340.")
        self._write(f"PID {loop},{p},{i},{d}")

    def get_pid(self, loop):
        """Query current PID values. Returns (P, I, D) as floats."""
        parts = self._query(f"PID? {loop}").split(",")
        return float(parts[0]), float(parts[1]), float(parts[2])


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------

class TempControlGUI:
    PROGRAM_VERSION = "9.4-Step"
    CLR_BG_DARK = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG_LIGHT = "#2C2825"
    CLR_FRAME_BG = "#E5DCD3"
    CLR_INPUT_BG = "#F4EFEA"
    CLR_TEXT_DARK = "#1A1A1A"
    CLR_ACCENT_GREEN = "#8AB845"
    CLR_ACCENT_RED = "#BA6B5E"
    CLR_ACCENT_GOLD = "#B68B6E"
    CLR_STABLE_WAIT = "#D4A373"
    CLR_CONSOLE_BG = "#E5DCD3"
    CLR_GRAPH_BG = "#F4EFEA"
    FONT_BASE = ("Segoe UI", 10)
    FONT_TITLE = ("Segoe UI", 12, "bold")
    FONT_CONSOLE = ("Consolas", 9)

    LEFT_PANEL_WIDTH = 540  # default sash position so the left panel starts fully visible

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"Lakeshore 340 Step Sequence Control v{self.PROGRAM_VERSION}"
        )
        self.root.geometry("1450x850")
        self.root.minsize(1200, 750)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.measurement_thread = None
        self.gui_queue = queue.Queue()
        self.proceed_event = threading.Event()

        self.logo_image = None
        self.backend = Lakeshore_Backend()
        self.resource_labels = {}   # combobox label -> VISA address
        self.climit = None          # CLIMIT? 1 dict, read at sequence start

        self.data_storage = {
            "time": [],
            "temperature": [],
            "target": [],
            "resistance": [],
            "heater": [],
        }

        # --- Live update flags (set by GUI, consumed by worker) ---
        self.live_heater_update = None
        self.live_param_update = None
        self.live_pid_update = None

        # --- Dynamic step-sequence state (feature 3) ---
        # setpoint_floats is now the single "live" source of truth for the
        # sequence, mutated by the GUI thread and read by the worker thread
        # under setpoint_lock. current_step_index marks how far the worker
        # has progressed so completed/active steps can't be edited away.
        self.setpoint_lock = threading.Lock()
        self.setpoint_floats = []
        self.current_step_index = 0

        # --- PID Presets ---
        self.PID_PRESETS = {
            "Slow (P=0.5, I=4, D=0)": (0.5, 4.0, 0),
            "Medium (P=20, I=15, D=0)": (20.0, 15.0, 0),
            "Fast (P=50, I=20, D=0)": (50.0, 20.0, 0),
        }

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".",
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE,
        )
        style.configure("TFrame", background=self.CLR_BG_DARK)
        style.configure("TPanedWindow", background=self.CLR_BG_DARK)
        style.configure(
            "TLabel", background=self.CLR_FRAME_BG, foreground=self.CLR_FG_LIGHT
        )
        style.configure("Header.TLabel", background=self.CLR_HEADER)

        style.configure(
            "TButton",
            font=self.FONT_BASE,
            padding=(8, 6),
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor="none",
        )
        style.map(
            "TButton",
            background=[("active", self.CLR_ACCENT_GOLD), ("hover", self.CLR_ACCENT_GOLD)],
        )

        style.configure("Start.TButton", background=self.CLR_ACCENT_GREEN)
        style.configure(
            "Stop.TButton", background=self.CLR_ACCENT_RED, foreground=self.CLR_FRAME_BG
        )
        style.configure(
            "Proceed.TButton",
            font=("Segoe UI", 12, "bold"),
            background=self.CLR_ACCENT_GREEN,
        )

        style.configure(
            "TLabelframe", background=self.CLR_FRAME_BG, bordercolor="#BA6B5E"
        )
        style.configure(
            "TLabelframe.Label",
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE,
        )
        style.configure(
            "TEntry", fieldbackground=self.CLR_GRAPH_BG, foreground=self.CLR_TEXT_DARK
        )

        mpl.rcParams.update(
            {
                "font.family": "Segoe UI",
                "font.size": 10,
                "axes.titlesize": 12,
                "axes.labelsize": 11,
            }
        )

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side="top", fill="x")
        font_title_main = ("Segoe UI", self.FONT_BASE[1] + 4, "bold")
        ttk.Label(
            header,
            text="Lakeshore 340 Step Measurement Sequence Utility",
            style="Header.TLabel",
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD,
        ).pack(side="left", padx=20, pady=10)

        ttk.Button(
            header, text="📈", command=launch_plotter_utility, width=3
        ).pack(side="right", padx=10, pady=5)
        ttk.Button(
            header, text="📟", command=launch_gpib_scanner, width=3
        ).pack(side="right", padx=(0, 5), pady=5)

        self.main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        # FIX (2b): pack_propagate(False) makes the requested width stick;
        # weight=0 keeps the left panel from being squeezed as the window
        # resizes, while the right (plot) panel absorbs all extra space.
        left_panel = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel.pack_propagate(False)
        self.main_pane.add(left_panel, weight=0)
        right_panel = ttk.Frame(self.main_pane)
        self.main_pane.add(right_panel, weight=1)

        self._populate_left_panel(left_panel)
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
        canvas = tk.Canvas(panel, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width),
        )
        self.left_scrollable_frame = scrollable_frame

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        scrollable_frame.grid_columnconfigure(0, weight=1)
        scrollable_frame.grid_rowconfigure(4, weight=1)

        self._create_info_panel(scrollable_frame, 0)
        self._create_sequence_panel(scrollable_frame, 1)
        self._create_settings_panel(scrollable_frame, 2)
        self._create_pid_panel(scrollable_frame, 3)
        self._create_console_panel(scrollable_frame, 4)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Information")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)

        LOGO_SIZE = 90
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0,
        )
        logo_canvas.grid(row=0, column=0, rowspan=2, padx=10, pady=10)

        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg"
            )
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception:
            pass

        institute_font = ("Segoe UI", self.FONT_BASE[1] + 2, "bold")
        ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_FRAME_BG,
        ).grid(row=0, column=1, padx=5, pady=(15, 0), sticky="sw")
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_FRAME_BG,
        ).grid(row=1, column=1, padx=5, sticky="nw")
        ttk.Label(
            frame,
            text="Lake Shore Model 340 | Loop 1 heater | CSET/CMODE/CLIMIT at start",
            font=("Segoe UI", 9),
            background=self.CLR_FRAME_BG,
        ).grid(row=2, column=1, padx=5, pady=(0, 8), sticky="nw")

    def _create_sequence_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Measurement Sequence Builder")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=10, pady=5)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(
            list_frame,
            height=6,
            selectmode=tk.EXTENDED,
            font=self.FONT_BASE,
            bg=self.CLR_INPUT_BG,
            fg=self.CLR_TEXT_DARK,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.listbox.yview)

        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(frame, text="Start(K):").grid(row=1, column=0, sticky="e", padx=2)
        self.entry_start = ttk.Entry(frame, width=6)
        self.entry_start.grid(row=1, column=1, sticky="w", padx=2)

        ttk.Label(frame, text="End(K):").grid(row=1, column=2, sticky="e", padx=2)
        self.entry_end = ttk.Entry(frame, width=6)
        self.entry_end.grid(row=1, column=3, sticky="w", padx=2)

        ttk.Label(frame, text="Step(K):").grid(row=2, column=0, sticky="e", padx=2)
        self.entry_step = ttk.Entry(frame, width=6)
        self.entry_step.grid(row=2, column=1, sticky="w", padx=2)

        self.btn_generate_steps = ttk.Button(
            frame, text="Generate Steps", command=self._generate_steps
        )
        self.btn_generate_steps.grid(row=2, column=2, columnspan=2, sticky="ew", padx=5, pady=2)

        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=5, padx=10
        )

        ttk.Label(frame, text="Order:").grid(row=4, column=0, sticky="e", padx=2)
        self.sort_var = tk.StringVar(value="Ascending")
        self.sort_cb = ttk.Combobox(
            frame,
            textvariable=self.sort_var,
            values=["Ascending", "Descending"],
            state="readonly",
            width=10,
        )
        self.sort_cb.grid(row=4, column=1, sticky="w", padx=2)
        self.sort_cb.bind("<<ComboboxSelected>>", lambda e: self._sort_listbox())

        ttk.Label(frame, text="Rows:").grid(row=4, column=2, sticky="e", padx=2)
        self.list_size_var = tk.IntVar(value=6)
        size_spin = ttk.Spinbox(
            frame,
            from_=3,
            to=25,
            textvariable=self.list_size_var,
            width=5,
            command=self._update_list_size,
        )
        size_spin.grid(row=4, column=3, sticky="w", padx=2)
        size_spin.bind("<Return>", self._update_list_size)
        size_spin.bind("<FocusOut>", self._update_list_size)

        ttk.Label(frame, text="Manual(K):").grid(
            row=5, column=0, sticky="e", padx=2, pady=5
        )
        self.entry_manual = ttk.Entry(frame, width=6)
        self.entry_manual.grid(row=5, column=1, sticky="w", padx=2, pady=5)

        ttk.Button(frame, text="Add", command=self._add_manual_step).grid(
            row=5, column=2, sticky="ew", padx=2, pady=5
        )
        ttk.Button(frame, text="Remove", command=self._remove_step).grid(
            row=5, column=3, sticky="ew", padx=2, pady=5
        )

        ttk.Button(frame, text="Clear All", command=self._clear_listbox).grid(
            row=6, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 5)
        )

        # Live status note shown only while a sequence is running, to make it
        # clear the list is now editable in place (feature 3).
        self.lbl_seq_hint = ttk.Label(
            frame, text="", foreground=self.CLR_ACCENT_GOLD, font=("Segoe UI", 8, "italic")
        )
        self.lbl_seq_hint.grid(row=7, column=0, columnspan=4, sticky="w", padx=10)

    def _create_settings_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Instrument & Stability Settings")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        for i in range(6):
            frame.grid_columnconfigure(i, weight=1 if i in [1, 4] else 0)

        self.entries = {}

        self._create_grid_entry(frame, "Tolerance (±K):", "tol", "0.5", 0, 0)
        # Dwell: the minimum continuous time the temperature must remain
        # inside the tolerance band before the program is allowed to
        # declare "Stabilized".
        self._create_grid_entry(frame, "Dwell (s):", "dwell", "60", 0, 3)
        self._create_grid_entry(frame, "Ramp Rate (K/min):", "rate", "2.0", 1, 0)
        self._create_grid_entry(frame, "Poll Delay (s):", "delay", "1", 1, 3)

        ttk.Label(frame, text="Heater Range:").grid(
            row=2, column=0, sticky="w", padx=10, pady=5
        )
        self.heater_range_var = tk.StringVar(value="5")
        self.heater_cb = ttk.Combobox(
            frame,
            textvariable=self.heater_range_var,
            values=["0 (Off)", "1", "2", "3", "4", "5 (Max)"],
            state="readonly",
            width=10,
        )
        self.heater_cb.grid(row=2, column=1, columnspan=2, sticky="ew", padx=5)
        self.heater_cb.bind("<<ComboboxSelected>>", self._on_heater_range_changed)

        # Control input: A or B on a base Model 340 (C, D need the 3462 card).
        ttk.Label(frame, text="Control Input:").grid(
            row=2, column=3, sticky="w", padx=10, pady=5
        )
        self.input_var = tk.StringVar(value="A")
        self.input_cb = ttk.Combobox(
            frame,
            textvariable=self.input_var,
            values=["A", "B", "C", "D"],
            state="readonly",
            width=5,
        )
        self.input_cb.grid(row=2, column=4, sticky="w", padx=5)

        ttk.Label(frame, text="VISA Addr:").grid(
            row=3, column=0, sticky="w", padx=5, pady=5
        )
        self.ls_cb = ttk.Combobox(frame, state="readonly", width=15)
        self.ls_cb.grid(row=3, column=1, columnspan=2, sticky="ew", padx=5)
        self.identify_btn = ttk.Button(
            frame, text="Identify", command=self._identify_visa
        )
        self.identify_btn.grid(row=3, column=3, columnspan=2, sticky="ew", padx=5)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=6, sticky="ew", pady=10, padx=10)
        button_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.start_button = ttk.Button(
            button_frame,
            text="Start Sequence",
            style="Start.TButton",
            command=self.start_sequence,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)

        self.stop_button = ttk.Button(
            button_frame,
            text="Stop All",
            style="Stop.TButton",
            state="disabled",
            command=self.stop_ramp,
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=2)

        ttk.Button(
            button_frame, text="Scan VISA", command=self._scan_for_visa
        ).grid(row=0, column=2, sticky="ew", padx=2)

        ttk.Button(
            button_frame, text="Send Updates", command=self._send_live_updates
        ).grid(row=0, column=3, sticky="ew", padx=2)

    def _create_pid_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Live PID Tuning (Loop 1, P/I/D 0-1000)")
        frame.grid(row=grid_row, column=0, sticky="new", pady=5, padx=5)
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="Preset:").grid(
            row=0, column=0, sticky="w", padx=10, pady=5
        )
        self.pid_preset_var = tk.StringVar()
        pid_preset_cb = ttk.Combobox(
            frame,
            textvariable=self.pid_preset_var,
            values=list(self.PID_PRESETS.keys()) + ["Custom"],
            state="readonly",
        )
        pid_preset_cb.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        pid_preset_cb.bind("<<ComboboxSelected>>", self._on_pid_preset_change)

        self.pid_p_entry = self._create_grid_entry(
            frame, "P:", "pid_p", "50.0", 1, 0, lockable=False
        )
        self.pid_i_entry = self._create_grid_entry(
            frame, "I:", "pid_i", "30.0", 1, 3, lockable=False
        )
        self.pid_d_entry = self._create_grid_entry(
            frame, "D:", "pid_d", "0.0", 2, 0, lockable=False
        )

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=6, sticky="ew", pady=5)
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        ttk.Button(btn_frame, text="Send PID", command=self._send_pid).grid(
            row=0, column=0, sticky="ew", padx=5
        )
        ttk.Button(btn_frame, text="Read PID", command=self._read_pid).grid(
            row=0, column=1, sticky="ew", padx=5
        )

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text="Console Log")
        frame.grid(row=grid_row, column=0, sticky="nsew", pady=5, padx=5)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.console = scrolledtext.ScrolledText(
            frame,
            state="disabled",
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap="word",
            borderwidth=0,
        )
        self.console.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.log("Console initialized. Build sequence and start.")

    def _populate_right_panel(self, panel):
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        status_frame = ttk.Frame(panel)
        status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        status_frame.grid_columnconfigure(0, weight=1)

        self.lbl_status = tk.Label(
            status_frame,
            text="READY TO START",
            font=("Segoe UI", 16, "bold"),
            bg=self.CLR_FRAME_BG,
            fg=self.CLR_TEXT_DARK,
            pady=10,
        )
        self.lbl_status.grid(row=0, column=0, sticky="ew")

        # --- Large live temperature readout (feature 2a) ---
        self.lbl_current_temp = tk.Label(
            status_frame,
            text="--- K",
            font=("Segoe UI", 26, "bold"),
            bg=self.CLR_FRAME_BG,
            fg=self.CLR_ACCENT_RED,
            padx=20,
        )
        self.lbl_current_temp.grid(row=0, column=1, sticky="e", padx=10)

        self.btn_proceed = ttk.Button(
            status_frame,
            text="Measurement Complete - Proceed ➔",
            style="Proceed.TButton",
            state="disabled",
            command=self._on_proceed,
        )
        self.btn_proceed.grid(row=0, column=2, sticky="ew", padx=10, ipady=5)

        container = ttk.LabelFrame(panel, text="Live Temperature Monitoring")
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)  # canvas row expands; toolbar row (0) doesn't

        # --- Plot window controls (feature 1a) ---
        plot_ctrl = ttk.Frame(container)
        plot_ctrl.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))

        ttk.Label(plot_ctrl, text="X-axis min (s):").pack(side="left", padx=(5, 2))
        self.xmin_var = tk.StringVar(value="0")
        xmin_entry = ttk.Entry(plot_ctrl, textvariable=self.xmin_var, width=8)
        xmin_entry.pack(side="left")
        xmin_entry.bind("<Return>", lambda e: self._put_gui_msg("plot"))

        ttk.Button(
            plot_ctrl, text="Apply", command=lambda: self._put_gui_msg("plot")
        ).pack(side="left", padx=4)
        ttk.Button(
            plot_ctrl,
            text="Full View",
            command=lambda: (self.xmin_var.set("0"), self._put_gui_msg("plot")),
        ).pack(side="left", padx=4)

        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp = self.figure.add_subplot(211)
        self.ax_heater = self.figure.add_subplot(212, sharex=self.ax_temp)

        self.line_target = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_GREEN, marker="", linestyle="--", label="Target Setpoint"
        )[0]
        self.line_temp = self.ax_temp.plot(
            [], [], color=self.CLR_ACCENT_RED, marker="o", markersize=3, linestyle="-", label="Actual Temp"
        )[0]
        self.ax_temp.set_ylabel("Temperature (K)")
        self.ax_temp.grid(True, linestyle="--", alpha=0.6)
        self.ax_temp.legend(loc="best", frameon=True, facecolor=self.CLR_GRAPH_BG)
        self.ax_temp.tick_params(axis="x", which="both", bottom=False, top=False, labelbottom=False)

        self.line_heater = self.ax_heater.plot(
            [], [], color=self.CLR_ACCENT_GOLD, marker=".", markersize=3, linestyle="-"
        )[0]
        self.ax_heater.set_xlabel("Time (s)")
        self.ax_heater.set_ylabel("Heater Output (%)")
        self.ax_heater.grid(True, linestyle="--", alpha=0.6)

        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

    # --- UI HELPERS ---
    def _create_grid_entry(
        self, parent, label, key, default_value, row, col, lockable=True
    ):
        """Creates a label, entry, and optional lock button."""
        ttk.Label(parent, text=label).grid(
            row=row, column=col, sticky="w", padx=(10, 2), pady=2
        )
        entry = ttk.Entry(parent, font=self.FONT_BASE, width=10)
        entry.grid(row=row, column=col + 1, sticky="ew", padx=2, pady=2)
        entry.insert(0, default_value)

        if lockable:
            lock_btn = ttk.Button(
                parent,
                text="🔓",
                width=2,
                command=lambda k=key: self._toggle_entry_lock(k),
            )
            lock_btn.grid(row=row, column=col + 2, sticky="w", padx=(0, 10), pady=2)
            self.entries[key] = {"entry": entry, "lock": lock_btn, "locked": False}
        else:
            self.entries[key] = {"entry": entry, "lock": None, "locked": False}

        return entry

    def _update_list_size(self, event=None):
        try:
            val = self.list_size_var.get()
            if 3 <= val <= 25:
                self.listbox.config(height=val)
        except Exception:
            pass

    def _sort_listbox(self):
        # FIX (3b): sorting mid-run could silently reorder steps that have
        # already executed, corrupting the worker's index-based progress.
        if self.is_running:
            self.log("Sort disabled while a sequence is running.")
            return
        items = list(self.listbox.get(0, tk.END))
        if not items:
            return
        try:
            floats = [float(x) for x in items]
            is_desc = self.sort_var.get() == "Descending"
            floats.sort(reverse=is_desc)
            self.listbox.delete(0, tk.END)
            for val in floats:
                self.listbox.insert(tk.END, f"{val:.2f}")
        except Exception:
            pass

    def _generate_steps(self):
        if self.is_running:
            messagebox.showwarning(
                "Not Available", "Bulk-generate is disabled while a sequence is running.\n"
                "Use 'Manual(K) → Add' to append steps live."
            )
            return
        try:
            start = float(self.entry_start.get())
            end = float(self.entry_end.get())
            step = float(self.entry_step.get())
            if step <= 0:
                raise ValueError("Step must be positive")

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
            messagebox.showerror(
                "Input Error",
                "Please enter valid numeric values for Start, End, and Step.",
            )

    def _add_manual_step(self):
        try:
            val = float(self.entry_manual.get())
            if self.is_running:
                # Append to the end only; do not re-sort (would disturb
                # already-executed steps at the front of the list).
                self.listbox.insert(tk.END, f"{val:.2f}")
            else:
                self.listbox.insert(tk.END, f"{val:.2f}")
                self._sort_listbox()
            self.entry_manual.delete(0, tk.END)
            self._sync_setpoints_from_listbox()
        except ValueError:
            messagebox.showerror("Input Error", "Enter a valid numeric temperature.")

    def _remove_step(self):
        selection = self.listbox.curselection()
        if self.is_running:
            # Guard against removing a step that has already executed or is
            # currently active — _sync_setpoints_from_listbox will validate
            # and reject/rollback if the protected prefix was touched.
            for index in reversed(selection):
                if index <= self.current_step_index:
                    messagebox.showwarning(
                        "Cannot Remove",
                        "That step has already completed or is currently active "
                        "and cannot be removed.",
                    )
                    return
        for index in reversed(selection):
            self.listbox.delete(index)
        self._sync_setpoints_from_listbox()

    def _clear_listbox(self):
        if self.is_running:
            messagebox.showwarning(
                "Not Available", "Cannot clear the sequence while it is running."
            )
            return
        self.listbox.delete(0, tk.END)

    def _sync_setpoints_from_listbox(self):
        """Push listbox contents into the worker's shared setpoint list.

        Only steps at or after the current step index may be changed while a
        sequence is running; completed/active steps must remain a prefix of
        the new list, or the edit is rejected and the listbox is restored to
        the authoritative state (feature 3 + safety guard).
        """
        if not self.is_running:
            return
        try:
            new_list = [float(x) for x in self.listbox.get(0, tk.END)]
        except ValueError:
            return

        with self.setpoint_lock:
            idx = self.current_step_index
            protected = self.setpoint_floats[: idx + 1]
            if new_list[: idx + 1] == protected:
                self.setpoint_floats = new_list
                remaining = new_list[idx + 1:]
                self.log(f"Sequence updated live. Remaining steps: {remaining}")
            else:
                self.log("WARN: edit touched a completed/active step; change rejected.")
                restore = list(self.setpoint_floats)

        if new_list[: idx + 1] != protected:
            # Restore listbox outside the lock to avoid blocking the worker.
            self.listbox.delete(0, tk.END)
            for v in restore:
                self.listbox.insert(tk.END, f"{v:.2f}")

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state="normal")
        self.console.insert("end", f"[{ts}] {message}\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def _update_status_ui(self, text, color):
        self.lbl_status.config(text=text, bg=color)

    def _on_proceed(self):
        self.log("User confirmed measurement. Moving to next setpoint.")
        self.btn_proceed.config(state="disabled")
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
                if platform.system() == "Windows":
                    import winsound

                    winsound.Beep(1000, 500)
                else:
                    self.root.bell()
            except Exception:
                pass

        threading.Thread(target=_ring, daemon=True).start()

    def _toggle_entry_lock(self, key):
        """Toggle the lock state of a single parameter entry."""
        if key in self.entries:
            widget_map = self.entries[key]
            if widget_map["locked"]:
                widget_map["entry"].config(state="normal")
                widget_map["lock"].config(text="🔓")
                widget_map["locked"] = False
            else:
                widget_map["entry"].config(state="disabled")
                widget_map["lock"].config(text="🔒")
                widget_map["locked"] = True

    def _on_pid_preset_change(self, event=None):
        """Update P/I/D entries when a preset is selected."""
        preset = self.pid_preset_var.get()
        if preset in self.PID_PRESETS:
            p, i, d = self.PID_PRESETS[preset]
            self.pid_p_entry.delete(0, "end")
            self.pid_p_entry.insert(0, str(p))
            self.pid_i_entry.delete(0, "end")
            self.pid_i_entry.insert(0, str(i))
            self.pid_d_entry.delete(0, "end")
            self.pid_d_entry.insert(0, str(d))

    def _send_pid(self):
        """Queue a request to send PID values to the worker thread."""
        if not self.is_running:
            messagebox.showwarning(
                "Not Running", "PID can only be sent while a sequence is active."
            )
            return
        try:
            p = float(self.pid_p_entry.get())
            i = float(self.pid_i_entry.get())
            d = float(self.pid_d_entry.get())
            self.live_pid_update = {"action": "send", "values": (p, i, d)}
            self.log(f"Queued PID SEND: P={p}, I={i}, D={d}")
        except ValueError:
            messagebox.showerror("Invalid Input", "P, I, D must be numeric.")

    def _read_pid(self):
        """Queue a request to read PID values from the worker thread."""
        if not self.is_running:
            messagebox.showwarning(
                "Not Running", "PID can only be read while a sequence is active."
            )
            return
        self.live_pid_update = {"action": "read"}
        self.log("Queued PID READ request.")

    def _send_live_updates(self):
        """Validate unlocked fields and queue a parameter update."""
        if not self.is_running:
            messagebox.showwarning(
                "Not Running",
                "Parameters can only be updated while a sequence is active.",
            )
            return

        updates = {}
        try:
            for key, widgets in self.entries.items():
                if (
                    "lock" in widgets
                    and widgets["lock"]
                    and not widgets["locked"]
                ):
                    updates[key] = float(widgets["entry"].get())

            if updates:
                self.live_param_update = updates
                self.log(f"Queued live parameter update: {updates}")
            else:
                self.log("No unlocked parameters to update.")
        except ValueError:
            messagebox.showerror(
                "Invalid Input", "All unlocked parameter values must be numeric."
            )

    def _close_data_file(self):
        f = getattr(self, "data_file", None)
        if f:
            try:
                f.flush()
                f.close()
                self._put_gui_msg("log", text=f"Data file closed: {self.data_filepath}")
            except Exception:
                pass
            finally:
                self.data_file = None

    # --- MAIN LOGIC ---
    def start_sequence(self):
        setpoints = list(self.listbox.get(0, tk.END))
        if not setpoints:
            messagebox.showwarning(
                "Empty Sequence",
                "Please add at least one target temperature to the list.",
            )
            return

        try:
            self.params = self._validate_and_get_params()
            with self.setpoint_lock:
                self.setpoint_floats = [float(x) for x in setpoints]
                self.current_step_index = 0
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
            return

        self.set_ui_state(running=True)
        self.is_running = True
        self.live_heater_update = None

        for key in self.data_storage:
            self.data_storage[key].clear()
        self.line_target.set_data([], [])
        self.line_temp.set_data([], [])
        self.line_heater.set_data([], [])
        self.xmin_var.set("0")
        self.canvas.draw()

        self.start_time = time.time()
        self.proceed_event.clear()

        os.makedirs("data", exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_filepath = os.path.join("data", f"TStep_{stamp}.csv")
        self.data_file = open(self.data_filepath, "w", newline="")
        self.csv_writer = csv.writer(self.data_file)
        self.csv_writer.writerow(
            [
                "Timestamp",
                "Elapsed_s",
                "Target_K",
                "Temperature_K",
                "Resistance_Ohm",
                "Heater_pct",
            ]
        )
        self.data_file.flush()
        self.log(f"Logging data to: {self.data_filepath}")

        self.root.after(100, self._process_gui_queue)

        self.measurement_thread = threading.Thread(
            target=self._hardware_worker_loop, daemon=True
        )
        self.measurement_thread.start()

    def stop_ramp(self):
        if not self.is_running:
            return
        self.log("ABORT INITIATED BY USER.")
        self.is_running = False
        self.proceed_event.set()
        # The hardware stop happens on the WORKER thread (its finally
        # block): the pyvisa session is not thread-safe, so writing to it
        # here could collide with an in-flight worker query.
        self.set_ui_state(running=False)
        self._update_status_ui("SEQUENCE ABORTED", self.CLR_ACCENT_RED)
        messagebox.showinfo(
            "Ramp Stopped", "Hardware ramp stopped and sequence aborted."
        )

    def _validate_and_get_params(self):
        params = {
            "tol": float(self.entries["tol"]["entry"].get()),
            "rate": float(self.entries["rate"]["entry"].get()),
            "delay": float(self.entries["delay"]["entry"].get()),
            "dwell": float(self.entries["dwell"]["entry"].get()),
            "heater_range": self.heater_range_var.get().split()[0],
            "input": self.input_var.get(),
            "ls_visa": self._selected_address(),
        }
        if not params["ls_visa"]:
            raise ValueError("Please select a VISA address.")
        if not (0.1 <= params["rate"] <= 100):
            raise ValueError("Ramp rate must be 0.1-100 K/min on a Model 340.")
        if int(params["heater_range"]) == 0:
            raise ValueError("Heater range 0 (Off) cannot ramp. Pick 1-5.")
        if params["tol"] <= 0:
            raise ValueError("Tolerance must be positive.")
        if params["dwell"] < 0:
            raise ValueError("Dwell time cannot be negative.")
        if params["delay"] <= 0:
            raise ValueError("Poll delay must be positive.")
        return params

    def set_ui_state(self, running: bool):
        state = "disabled" if running else "normal"
        self.start_button.config(state=state)
        self.stop_button.config(state="normal" if running else "disabled")

        # entries are now dicts: {'entry': widget, 'lock': button, 'locked': bool}
        for w in self.entries.values():
            entry = w["entry"]
            if running:
                # Keep lockable params editable during run unless individually locked
                if w.get("lock") is not None:
                    entry.config(
                        state="disabled" if w.get("locked") else "normal"
                    )
                else:
                    # PID entries — always editable during run
                    entry.config(state="normal")
            else:
                entry.config(
                    state="disabled" if w.get("locked") else "normal"
                )
            # FIX (4a): lock buttons must stay clickable at all times.
            # Previously this line set the lock button's state to `state`
            # (== "disabled" while running), so users could never toggle a
            # lock mid-run — defeating the entire live-update workflow.
            if w.get("lock") is not None:
                w["lock"].config(state="normal")

        # FIX (3b): sequence-builder controls that support *dynamic* editing
        # stay enabled during a run; bulk-generation/sort/clear remain locked
        # because they could disturb already-executed steps.
        self.entry_manual.config(state="normal")
        self.listbox.config(state="normal")
        self.btn_generate_steps.config(state=state)
        self.entry_start.config(state=state)
        self.entry_end.config(state=state)
        self.entry_step.config(state=state)
        self.sort_cb.config(state=("disabled" if running else "readonly"))

        self.lbl_seq_hint.config(
            text="Sequence is live: you may Add/Remove upcoming steps below."
            if running else ""
        )

        self.ls_cb.config(state=state if state == "normal" else "readonly")
        self.input_cb.config(state="disabled" if running else "readonly")
        self.identify_btn.config(state=state)
        self.btn_proceed.config(state="disabled")

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
            self.ls_cb["values"] = resources
            # The lab's 340 is set to address 19 (3 Sep 2026); pre-select
            # it when present. Identify confirms by *IDN?.
            preferred = [r for r in resources if LAKESHORE340_ADDRESS_HINT in r]
            if preferred:
                self.ls_cb.set(preferred[0])
                self.log(f"Pre-selected {preferred[0]} (lab address 19). "
                         "Press Identify to confirm it answers as MODEL340.")
            elif len(resources) == 1:
                self.ls_cb.set(resources[0])
            else:
                self.ls_cb.set("")
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
            self.log("Nothing to identify: press Scan VISA first.")
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
        self.ls_cb["values"] = list(labels.keys())
        if chosen:
            self.ls_cb.set(chosen)
            self.log(f"Selected the Model 340 at {labels[chosen]}.")
        else:
            self.ls_cb.set("")
            self.log("No address answered as a Model 340.")

    def _put_gui_msg(self, msg_type, **kwargs):
        payload = {"type": msg_type}
        payload.update(kwargs)
        self.gui_queue.put(payload)

    def _process_gui_queue(self):
        try:
            while True:
                msg = self.gui_queue.get_nowait()
                msg_type = msg["type"]

                if msg_type == "log":
                    self.log(msg["text"])

                elif msg_type == "status":
                    self._update_status_ui(msg["text"], msg["color"])

                elif msg_type == "temp_display":
                    # Feature 2a: large live-temperature readout
                    self.lbl_current_temp.config(text=f"{msg['value']:.3f} K")

                elif msg_type == "plot":
                    self._redraw_plot()

                elif msg_type == "handshake_ready":
                    self.btn_proceed.config(state="normal")
                    self._beep()

                elif msg_type == "beep":
                    self._beep()

                elif msg_type == "pid_read_result":
                    p, i, d = msg["values"]
                    self.pid_p_entry.delete(0, "end")
                    self.pid_p_entry.insert(0, str(p))
                    self.pid_i_entry.delete(0, "end")
                    self.pid_i_entry.insert(0, str(i))
                    self.pid_d_entry.delete(0, "end")
                    self.pid_d_entry.insert(0, str(d))
                    self.pid_preset_var.set("Custom")

                elif msg_type == "sequence_complete":
                    self.set_ui_state(running=False)
                    messagebox.showinfo(
                        "Sequence Complete",
                        "All setpoints measured successfully.",
                    )

        except queue.Empty:
            pass

        # Keep polling while the worker thread is still alive so messages
        # queued from its except/finally blocks are never dropped.
        if (self.is_running or not self.gui_queue.empty()
                or (self.measurement_thread
                    and self.measurement_thread.is_alive())):
            self.root.after(100, self._process_gui_queue)

    def _redraw_plot(self):
        """Feature 1: redraw with a user-adjustable x_min and y-axis limits
        that auto-scale using only the data inside the visible x window."""
        n = min(
            len(self.data_storage["time"]),
            len(self.data_storage["temperature"]),
            len(self.data_storage["heater"]),
            len(self.data_storage["target"]),
        )
        t = self.data_storage["time"][:n]

        self.line_target.set_data(t, self.data_storage["target"][:n])
        self.line_temp.set_data(t, self.data_storage["temperature"][:n])
        self.line_heater.set_data(t, self.data_storage["heater"][:n])

        if not t:
            self.canvas.draw_idle()
            return

        # --- Parse x_min safely; fall back to 0 on bad/empty input ---
        try:
            x_min = max(0.0, float(self.xmin_var.get()))
        except (ValueError, tk.TclError):
            x_min = 0.0
        x_max = t[-1]
        if x_min >= x_max:  # window collapsed or user typed something huge
            x_min = max(0.0, x_max - 60)

        vis = [k for k, tv in enumerate(t) if tv >= x_min]

        def _ylim_from(*series_list):
            vals = [s[k] for s in series_list for k in vis]
            if not vals:
                return None
            lo, hi = min(vals), max(vals)
            pad = max((hi - lo) * 0.05, 0.05)  # 5% margin, never zero-height
            return lo - pad, hi + pad

        margin = max((x_max - x_min) * 0.02, 1)
        for ax in (self.ax_temp, self.ax_heater):
            ax.set_xlim(x_min, x_max + margin)

        yl = _ylim_from(self.data_storage["temperature"][:n], self.data_storage["target"][:n])
        if yl:
            self.ax_temp.set_ylim(*yl)

        yl = _ylim_from(self.data_storage["heater"][:n])
        if yl:
            self.ax_heater.set_ylim(*yl)

        self.canvas.draw_idle()

    def _hardware_worker_loop(self):
        try:
            self._put_gui_msg("log", text="Connecting to Lakeshore...")
            idn = self.backend.connect(self.params["ls_visa"])
            self._put_gui_msg("log", text=f"Connected: {idn}")
            if not self.backend.is_model_340():
                raise RuntimeError(
                    f"'{idn}' is not a Lake Shore Model 340. Refusing to send "
                    "340-only commands (CSET, RANGE n) to it.")

            # --- Model 340 loop setup: CSET / CMODE / CLIMIT? (verified) ---
            inp = self.params["input"]
            cset, cmode, limits = self.backend.prepare_loop(inp)
            self.climit = limits
            cur = self.backend.MAX_CURRENT_CODES.get(
                limits["max_current"], f"code {limits['max_current']}")
            self._put_gui_msg(
                "log",
                text=f"Loop 1 enabled on input {cset['input']} (kelvin), Manual PID "
                     f"(CMODE {cmode}). CLIMIT: setpoint <= {limits['sp_limit']:g} K, "
                     f"max current {cur}, max range {limits['max_range']}.",
            )
            with self.setpoint_lock:
                planned = list(self.setpoint_floats)
            too_hot = [t for t in planned if t > limits["sp_limit"]]
            if too_hot:
                raise ValueError(
                    f"Step(s) {too_hot} K are above the 340's setpoint limit of "
                    f"{limits['sp_limit']:g} K (CLIMIT). Remove them or raise the "
                    "limit in the Direct Control module first.")
            wanted_range = self.backend.range_code(self.params["heater_range"])
            if wanted_range > limits["max_range"]:
                raise ValueError(
                    f"Heater range {wanted_range} exceeds the 340's max range "
                    f"{limits['max_range']} (CLIMIT). Lower it or raise the limit.")
            code, text = self.backend.get_heater_status()
            last_htr_error = code
            if code != 0:
                raise RuntimeError(
                    f"Heater error HTRST? {code:02d}: {text}. "
                    "Fix the heater circuit before ramping.")

            # --- Feature 3: index-based loop over the SHARED setpoint list,
            # so the GUI can append/remove upcoming steps mid-run. ---
            step_index = 0
            while self.is_running:
                with self.setpoint_lock:
                    total = len(self.setpoint_floats)
                    if step_index >= total:
                        break
                    target = self.setpoint_floats[step_index]
                    self.current_step_index = step_index

                self._put_gui_msg(
                    "log",
                    text=f"--- Sequence Step {step_index+1}/{total}: Target {target} K ---",
                )
                self._put_gui_msg(
                    "status",
                    text=f"RAMPING TO {target} K",
                    color=self.CLR_ACCENT_RED,
                )

                # Steps added live are checked here against the CLIMIT
                # setpoint limit read at start.
                if target > self.climit["sp_limit"]:
                    raise ValueError(
                        f"Step target {target} K is above the 340's setpoint "
                        f"limit of {self.climit['sp_limit']:g} K (CLIMIT).")

                # Pin the setpoint to the present temperature before enabling
                # the ramp: a 340 ramps from the current setpoint.
                temp_now = self.backend.get_temperature(inp)
                self.backend.configure_ramp(
                    target, self.params["rate"],
                    self.params["heater_range"].split()[0], temp_now,
                )
                self._put_gui_msg(
                    "log",
                    text=f"Ramp started from {temp_now:.3f} K (setpoint pinned "
                         f"first) to {target} K at {self.params['rate']} K/min, "
                         f"RANGE {wanted_range}.",
                )

                # FIX (4b): stabilization now requires BOTH a continuous
                # wall-clock dwell AND a minimum number of consecutive
                # in-band polls, so a single transient overshoot/undershoot
                # that merely clips the tolerance band cannot trigger a
                # premature "Stabilized" declaration.
                stable_start_time = None
                consecutive_in_band = 0
                min_consecutive = max(3, int(self.params["dwell"] / self.params["delay"] * 0.9))
                phase = "RAMPING"

                self.proceed_event.clear()

                while self.is_running:
                    # 1. Process Live Heater Updates (Mid-Run adjustments)
                    if self.live_heater_update is not None:
                        new_range = self.live_heater_update
                        self.live_heater_update = None
                        try:
                            new_code = self.backend.range_code(new_range)
                            if new_code > self.climit["max_range"]:
                                raise ValueError(
                                    f"range {new_code} exceeds CLIMIT max range "
                                    f"{self.climit['max_range']}")
                            self.backend.set_heater_range(new_code)
                            wanted_range = new_code
                            self._put_gui_msg(
                                "log",
                                text=f"Heater successfully switched to: {new_range} "
                                     f"(RANGE {new_code}, verified)",
                            )
                        except Exception as e:
                            self._put_gui_msg(
                                "log",
                                text=f"Failed to switch heater range: {e}",
                            )

                    # 1b. Process Live Parameter Updates
                    if self.live_param_update is not None:
                        updates = self.live_param_update
                        self.live_param_update = None
                        self.params.update(updates)
                        self._put_gui_msg(
                            "log", text=f"Live parameters applied: {updates}"
                        )
                        if "dwell" in updates or "delay" in updates:
                            min_consecutive = max(
                                3, int(self.params["dwell"] / self.params["delay"] * 0.9)
                            )
                        if "rate" in updates:
                            try:
                                self.backend.set_ramp_rate(float(updates["rate"]))
                                self._put_gui_msg(
                                    "log",
                                    text=f"Ramp rate updated to {updates['rate']} K/min.",
                                )
                            except Exception as e:
                                self._put_gui_msg(
                                    "log",
                                    text=f"Failed to update ramp rate: {e}",
                                )

                    # 1c. Process PID Updates
                    if self.live_pid_update is not None:
                        req = self.live_pid_update
                        self.live_pid_update = None
                        if req["action"] == "send":
                            p, i, d = req["values"]
                            self.backend.set_pid(1, p, i, d)
                            self._put_gui_msg(
                                "log", text=f"PID sent: P={p}, I={i}, D={d}"
                            )
                        elif req["action"] == "read":
                            p, i, d = self.backend.get_pid(1)
                            self._put_gui_msg(
                                "pid_read_result", values=(p, i, d)
                            )

                    # 2. Get hardware status (KRDG?, SRDG?, HTR?, RDGST?)
                    temp, resistance, htr, rd_status = self.backend.get_status(inp)
                    elapsed = time.time() - self.start_time
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if rd_status:
                        self._put_gui_msg(
                            "log",
                            text=f"T:{temp:.3f} K   [RDGST {rd_status}] "
                                 "- sample not used for stability",
                        )

                    # 2b. Heater error register: logged once on change.
                    code, text = self.backend.get_heater_status()
                    if code != last_htr_error:
                        last_htr_error = code
                        if code != 0:
                            self._put_gui_msg(
                                "log", text=f"HEATER ERROR HTRST? {code:02d}: {text}")
                            self._put_gui_msg(
                                "status",
                                text=f"HEATER ERROR {code:02d}: {text} (see console)",
                                color=self.CLR_ACCENT_RED,
                            )
                            self._put_gui_msg("beep")
                        else:
                            self._put_gui_msg(
                                "log", text="Heater error cleared (HTRST? 00).")

                    # 3. Store and commit data
                    self.data_storage["time"].append(elapsed)
                    self.data_storage["temperature"].append(temp)
                    self.data_storage["target"].append(target)
                    self.data_storage["resistance"].append(resistance)
                    self.data_storage["heater"].append(htr)

                    try:
                        self.csv_writer.writerow(
                            [
                                now_str,
                                f"{elapsed:.2f}",
                                f"{target:.4f}",
                                f"{temp:.4f}",
                                f"{resistance:.6g}",
                                f"{htr:.2f}",
                            ]
                        )
                        self.data_file.flush()
                        os.fsync(self.data_file.fileno())
                    except Exception as e:
                        self._put_gui_msg(
                            "log", text=f"WARN: data write failed: {e}"
                        )

                    self._put_gui_msg("plot")
                    self._put_gui_msg("temp_display", value=temp)

                    # 4. State Machine Logic (dwell-gated stabilization)
                    in_band = abs(temp - target) <= self.params["tol"]

                    if rd_status:
                        # An invalid/old/over-range reading is neither in
                        # nor out of band: it must not advance or reset the
                        # dwell.  Only the poll delay below runs.
                        pass
                    elif phase in ("RAMPING", "SOAKING"):
                        if in_band:
                            consecutive_in_band += 1
                            if phase == "RAMPING":
                                stable_start_time = time.time()
                                phase = "SOAKING"
                                self._put_gui_msg(
                                    "log",
                                    text=f"Entered tolerance band (±{self.params['tol']}K). "
                                         f"Starting dwell timer...",
                                )
                                self._put_gui_msg(
                                    "status",
                                    text=f"STABILIZING AT {target} K...",
                                    color=self.CLR_STABLE_WAIT,
                                )
                            elif phase == "SOAKING":
                                dwell_ok = (
                                    time.time() - stable_start_time >= self.params["dwell"]
                                )
                                samples_ok = consecutive_in_band >= min_consecutive
                                if dwell_ok and samples_ok:
                                    self._put_gui_msg(
                                        "log",
                                        text=f"Stable inside window for "
                                             f"{self.params['dwell']}s "
                                             f"({consecutive_in_band} consecutive samples). "
                                             f"Ready for external measurement.",
                                    )
                                    self._put_gui_msg(
                                        "status",
                                        text=f"STABLE AT {target} K | AWAITING MEASUREMENT",
                                        color=self.CLR_ACCENT_GREEN,
                                    )
                                    self._put_gui_msg("handshake_ready")
                                    phase = "WAITING"
                        else:
                            # Any single excursion fully resets the dwell —
                            # this is what prevents a transient overshoot
                            # from counting toward stabilization.
                            consecutive_in_band = 0
                            if phase == "SOAKING":
                                self._put_gui_msg(
                                    "log",
                                    text="Drifted outside tolerance band. Restarting dwell timer.",
                                )
                                self._put_gui_msg(
                                    "status",
                                    text=f"RAMPING TO {target} K",
                                    color=self.CLR_ACCENT_RED,
                                )
                                stable_start_time = None
                                phase = "RAMPING"

                    elif phase == "WAITING":
                        # Optional additional hold after stabilization, if
                        # the user still wants a post-stable soak period.
                        if self.proceed_event.is_set():
                            self.proceed_event.clear()
                            break

                    # 5. Delay before next poll
                    time.sleep(self.params["delay"])

                step_index += 1

            if self.is_running:
                self._put_gui_msg("log", text="Measurement Sequence Complete.")
                self._put_gui_msg(
                    "status", text="READY TO START", color=self.CLR_HEADER
                )
                self._put_gui_msg("sequence_complete")
                self.backend.stop_ramp()
                self.is_running = False

        except Exception as e:
            self._put_gui_msg(
                "log",
                text=f"CRITICAL ERROR IN HARDWARE THREAD: {e}\n{traceback.format_exc()}",
            )
            hint = explain_visa_error(e)
            if hint:
                self._put_gui_msg("log", text=f"HINT: {hint}")
            self._put_gui_msg(
                "status", text="ERROR: SEQUENCE STOPPED, HEATER OFF (see console)",
                color=self.CLR_ACCENT_RED,
            )
            self._put_gui_msg("beep")
            # Queue the completion message BEFORE flipping is_running, so
            # the GUI poller cannot stop between the two and drop it
            # (which would leave the Start button disabled forever).
            self._put_gui_msg("sequence_complete")
            self.is_running = False
        finally:
            # Hardware stop always runs here, on the worker thread — this
            # also covers the user-Stop path (stop_ramp no longer touches
            # the VISA session from the GUI thread).
            self.backend.stop_ramp()
            self._close_data_file()

    def _on_closing(self):
        if self.is_running and messagebox.askyesno(
            "Exit", "A sequence is active. Stop hardware and exit?"
        ):
            self.stop_ramp()
            time.sleep(0.5)
            if self.measurement_thread and self.measurement_thread.is_alive():
                self.measurement_thread.join(timeout=2.0)
            if self.measurement_thread and self.measurement_thread.is_alive():
                # Worker stuck (e.g. slow VISA read): its finally block may
                # never run, so force the heater off before the window dies.
                self.backend.stop_ramp()
            self.root.destroy()
        elif not self.is_running:
            self.root.destroy()


if __name__ == "__main__":
    if not pyvisa:
        messagebox.showerror(
            "Dependency Error",
            "PyVISA is not installed. Please run 'pip install pyvisa'.",
        )
    else:
        root = tk.Tk()
        app = TempControlGUI(root)
        root.mainloop()