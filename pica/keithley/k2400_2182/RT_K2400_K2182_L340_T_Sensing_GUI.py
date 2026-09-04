"""
Module: RT_K2400_K2182_L340_T_Sensing_GUI.py
Purpose: R-T (Keithley 2400 source, 2182 nanovoltmeter) with passive
         temperature sensing from a Lake Shore Model 340.
         Port of RT_K2400_K2182_L350_T_Sensing_GUI.py to the Model 340
         command set.  The Keithley 2400/2182 logic is untouched; only the
         Lake Shore side changes.

What is different from the Model 350 version, and why
-----------------------------------------------------
  * *IDN? must contain MODEL340 or Start is refused.  The lab's 340, 350,
    Cryocon 34 and Keithley 6221 have all lived at IEEE address 12 at some
    point; sending RANGE 0 to the wrong box is not acceptable.
  * *CLS only.  Never *RST on the Lake Shore: on a 340 "*RST sets
    controller parameters to power-up settings" (340 manual, printed 9-24),
    loop, setpoint and ramp included.  A passive monitor has no business
    doing that.  (The Keithley 2182 still gets its own SCPI reset; that is
    a different instrument.)
  * Heater-off at start is a checkbox, ON by default.  When ticked the
    monitor sends "RANGE 0" once and "RANGE?" must read back 0
    (printed 9-40 / 9-41).  RANGE takes no loop argument on a 340; the 350
    form "RANGE 1,0" is a syntax error.  When unticked the heater is left
    exactly as found.  This checkbox replaces the unconditional
    "RANGE 1,0" the 350 version sent at start.
  * The heater is never touched at stop or close.  The 350 version sent
    "RANGE 1,0" again at shutdown; the 340 version only closes the session.
  * The sensor input is selectable (A or B on a base Model 340; C and D
    are only present with the 3462 dual-input option card).  The 350
    version was fixed to input A.
  * RDGST? <input> is read with every sample (printed 9-41).  A non-zero
    status (invalid, old, under/over range, units zero/overrange) is logged
    with the reading and written to the data file, so a 0.000 K row is
    never mistaken for a real reading.
  * The bus scan pre-selects the lab's 340 at IEEE address 19 ("::19::",
    since 3 Sep 2026).  Addresses 12 and 15 are never auto-picked.

Commands used on the Lake Shore (Model 340 User's Manual, Chapter 9):
  *IDN?          LSCI,MODEL340,<serial>,<firmware>
  *CLS           clear status registers
  RANGE 0        heater off (Loop 1), no loop argument   RANGE?  -> n
  KRDG? <input>  kelvin reading
  RDGST? <input> reading status bit weighting
"""

# -------------------------------------------------------------------------------
# Name:         R-T Sweep Passive GUI for K2400/2182 & LS340
# Purpose:      Provide a professional GUI for passively logging R vs T data.
#               This version does not control temperature.
# Author:       Prathamesh Deshmukh
# Created:      05/10/2025
# Version:      1.1
# -------------------------------------------------------------------------------

# --- GUI and Plotting Packages ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import sys
import os
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
        # Go up 2 levels: k2400_2182 -> keithley -> pica
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
        # Go up 2 levels: k2400_2182 -> keithley -> pica
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

# RDGST? bit weights, Model 340 manual printed 9-41.
RDGST_BITS = (
    (1, "invalid reading"),
    (2, "old reading"),
    (16, "temp underrange"),
    (32, "temp overrange"),
    (64, "units zero"),
    (128, "units overrange"),
)


def describe_reading_status(status):
    """Turn an RDGST? integer into readable text ('' when the reading is good)."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return f"unparseable status '{status}'"
    if status == 0:
        return ""
    names = [name for bit, name in RDGST_BITS if status & bit]
    if not names:
        names = [f"unknown bit(s) {status}"]
    return ", ".join(names)


# The lab's Model 340 was moved to IEEE address 19 on 3 Sep 2026 so that it
# no longer collides with the 350, the Cryocon 34 and the Keithley 6221, which
# all default to 12. A hint only: the IDN check decides.
LAKESHORE340_ADDRESS_HINT = "::19::"


class VT_Backend_Passive:
    """ Manages communication for passive monitoring.

    Lakeshore 340 side: *CLS, an optional verified RANGE 0 at start, then
    queries only.  Never *RST, never a heater command at stop.
    """

    MODEL_TOKENS = ("MODEL340", "MODEL 340")

    def __init__(self):
        self.k2400, self.k2182, self.lakeshore = None, None, None
        self.lakeshore_idn = ""
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA: {e}")
                self.rm = None

    def connect(self, k2400_visa, k2182_visa, ls_visa):
        if not self.rm:
            raise ConnectionError("PyVISA is not available.")
        if not PYMEASURE_AVAILABLE:
            raise ImportError("Pymeasure is not available.")
        self.k2400 = Keithley2400(k2400_visa)
        print(f"  K2400 Connected: {self.k2400.id}")
        self.k2182 = self.rm.open_resource(k2182_visa)
        print(f"  K2182 Connected: {self.k2182.query('*IDN?').strip()}")
        self.lakeshore = self.rm.open_resource(ls_visa)
        self.lakeshore.timeout = 10000
        # The 340 answers with <CR><LF> and EOI by default (IEEE command,
        # printed 9-33); '\n' as read terminator works on the lab's 340.
        self.lakeshore.read_termination = '\n'
        self.lakeshore.write_termination = '\n'
        self.lakeshore_idn = self.lakeshore.query('*IDN?').strip()
        print(f"  Lakeshore Connected: {self.lakeshore_idn}")
        if not self.is_model_340():
            raise RuntimeError(
                f"'{ls_visa}' answered '{self.lakeshore_idn}', which is not "
                "a Lake Shore Model 340. Refusing to send RANGE 0 to an "
                "unknown instrument. Pick the right address.")

    def is_model_340(self):
        idn = self.lakeshore_idn.upper().replace(' ', '')
        return any(tok.replace(' ', '') in idn for tok in self.MODEL_TOKENS)

    def get_heater_range(self):
        """RANGE? -> 0 (off) .. 5.  No output argument on a 340."""
        return int(float(self.lakeshore.query('RANGE?').strip()))

    def configure_instruments(self, current_ma, compliance_v, heater_off=True):
        # Lakeshore 340 setup for passive monitoring.  No *RST: on a 340
        # that resets loop, setpoint and ramp (printed 9-24).
        self.lakeshore.write('*CLS')
        time.sleep(0.2)
        if heater_off:
            self.lakeshore.write('RANGE 0')  # Loop 1 heater off (9-40)
            time.sleep(0.3)
            rng = self.get_heater_range()
            if rng != 0:
                raise RuntimeError(
                    f"Sent RANGE 0 but RANGE? reads back {rng}. Heater is "
                    "NOT off. Check the front panel (Remote/Local) and try "
                    "again.")
            print("  Lakeshore 340 heater turned OFF (RANGE 0 sent, RANGE? = 0).")
        else:
            rng = self.get_heater_range()
            print(f"  Lakeshore 340 heater left as found: RANGE? = {rng} "
                  f"({'off' if rng == 0 else 'ON'}).")

        # Keithley 2400/2182 setup
        self.k2400.reset()
        self.k2400.apply_current()
        self.k2400.source_current_range = abs(current_ma * 1e-3) * 1.05
        self.k2400.compliance_voltage = compliance_v
        self.k2400.source_current = current_ma * 1e-3
        self.k2400.enable_source()
        self.k2182.write("*rst; status:preset; *cls")
        time.sleep(1)

    def get_measurement(self, sensor='A'):
        # K2182 measurement sequence
        self.k2182.write("status:measurement:enable 512; *sre 1")
        self.k2182.write("sample:count 2")
        self.k2182.write("trigger:source bus")
        self.k2182.write("trigger:delay 0.1")
        self.k2182.write("trace:points 2")
        self.k2182.write("trace:feed sense1; feed:control next")
        self.k2182.write("initiate")
        self.k2182.assert_trigger()
        self.k2182.wait_for_srq(timeout=10)
        voltages = self.k2182.query_ascii_values("trace:data?")
        self.k2182.query("status:measurement?")
        self.k2182.write("trace:clear; feed:control next")
        voltage = sum(voltages) / len(voltages) if voltages else float('nan')

        # Lakeshore 340 temperature reading plus RDGST? status (printed
        # 9-41) so an out-of-range 0.000 K is never taken as a real reading.
        temperature = float(self.lakeshore.query(f'KRDG? {sensor}').strip())
        status = int(float(self.lakeshore.query(f'RDGST? {sensor}').strip()))
        return temperature, voltage, status

    def shutdown(self):
        if self.k2400:
            try:
                self.k2400.shutdown()
            except BaseException:
                pass
        if self.k2182:
            try:
                self.k2182.write("*rst")
                self.k2182.close()
            except BaseException:
                pass
        if self.lakeshore:
            try:
                # Heater state is not touched at close.
                self.lakeshore.close()
            except BaseException:
                pass
            finally:
                self.lakeshore = None
        print("  Instruments shut down and disconnected. "
              "Lakeshore heater state was not touched.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class VT_GUI_Passive:
    PROGRAM_VERSION = "1.3"  # Performance and UI update
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN, CLR_ACCENT_RED, CLR_ACCENT_BLUE = '#B68B6E', '#BA6B5E', '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_GRAPH_BG = '#F4EFEA'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    LEFT_PANEL_WIDTH = 480  # default sash position so the left panel starts fully visible

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"K2400/2182 & L340: R-T (T-Sensing) v{self.PROGRAM_VERSION}")
        self.root.geometry("1600x950")
        self.root.minsize(1400, 800)
        self.root.configure(bg=self.CLR_BG_DARK)
        self.is_running = False
        self.logo_image = None
        self.backend = VT_Backend_Passive()
        self.data_storage = {
            'temperature': [],
            'voltage': [],
            'resistance': []}
        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure(
            'TCheckbutton',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure(
            'TEntry',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG_LIGHT,
            insertcolor=self.CLR_FG_LIGHT)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER)
        style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_BG_DARK), ('hover', self.CLR_BG_DARK)])
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
            foreground=self.CLR_FG_LIGHT)
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
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        style.configure(
            'TCombobox',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG_LIGHT,
            arrowcolor=self.CLR_FG_LIGHT,
            selectbackground=self.CLR_ACCENT_BLUE,
            selectforeground=self.CLR_FG_LIGHT)
        mpl.rcParams.update({'font.family': 'Segoe UI',
                             'font.size': 11,
                             'axes.titlesize': 15,
                             'axes.labelsize': 13})

    def create_widgets(self):
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        ttk.Label(
            header,
            text="K2400/2182 & L340: R-T (T-Sensing)",
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
        left_panel_container = ttk.Frame(self.main_pane, width=self.LEFT_PANEL_WIDTH)
        left_panel_container.pack_propagate(False)
        self.main_pane.add(left_panel_container, weight=0)

        # --- Make the left panel scrollable ---
        canvas = Canvas(
            left_panel_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            left_panel_container,
            orient="vertical",
            command=canvas.yview)
        # This is now the scrollable_frame
        left_panel = ttk.Frame(canvas, padding=5)
        left_panel.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=left_panel, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Keep the inner frame exactly as wide as the canvas viewport, so
        # widgets are never clipped on the right edge (they reflow instead),
        # and remember the frame so the sash logic can measure its true width.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(window_id, width=e.width))
        self.left_scrollable_frame = left_panel

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        right_panel = self._create_right_panel(self.main_pane)
        self.main_pane.add(right_panel, weight=1)
        self._populate_left_panel(left_panel)

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
        panel.grid_rowconfigure(3, weight=1)
        self._create_info_panel(panel, 0)
        self._create_params_panel(panel, 1)
        self._create_control_panel(panel, 2)
        self._create_console_panel(panel, 3)

    def _create_info_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Information')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(1, weight=1)
        LOGO_SIZE = 110
        logo_canvas = Canvas(
            frame,
            width=LOGO_SIZE,
            height=LOGO_SIZE,
            bg=self.CLR_FRAME_BG,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(
                script_dir,
                "..",
                "..",
                "assets",
                "LOGO",
                "UGC_DAE_CSR_NBG.jpeg")
            if PIL_AVAILABLE and os.path.exists(logo_path):
                img = Image.open(logo_path).resize(
                    (LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    LOGO_SIZE / 2, LOGO_SIZE / 2, image=self.logo_image)
        except Exception as e:
            self.log(f"Warning: Could not load logo. {e}")

        institute_font = ('Segoe UI', self.FONT_BASE[1] + 6, 'bold')
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
        details_text = ("Program Name: R vs. T (T-Sensing)\n"
                        "Instruments: K2400, K2182, L340\n"
                        "Lakeshore: queries only, plus optional RANGE 0 at start\n"
                        "Measurement Range: 1 µΩ to 100 MΩ")
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

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent, padding=5)
        container = ttk.LabelFrame(panel, text='Live R-T Curve')
        container.pack(fill='both', expand=True)
        self.figure = Figure(dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot(
            [], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Waiting for logging...", fontweight='bold')
        self.ax_main.set_xlabel("Temperature (K)")
        self.ax_main.set_ylabel("Resistance (Ω)")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)
        return panel

    def _create_params_panel(self, parent, grid_row):
        container = ttk.Frame(parent)
        container.grid(row=grid_row, column=0, sticky='new', pady=5)
        container.grid_columnconfigure(0, weight=1)
        self.entries = {}

        settings_frame = ttk.LabelFrame(container, text='Measurement Settings')
        settings_frame.grid(row=0, column=0, sticky='nsew', pady=(0, 5))
        settings_frame.grid_columnconfigure(1, weight=1)
        self._create_entry(settings_frame, "Source Current (mA)", "1", 0)
        self._create_entry(settings_frame, "Compliance (V)", "10", 1)
        self._create_entry(settings_frame, "Logging Delay (s)", "1", 2)

        visa_frame = ttk.LabelFrame(container, text='Instrument Addresses')
        visa_frame.grid(row=1, column=0, sticky='nsew')
        visa_frame.grid_columnconfigure(1, weight=1)
        self.ls_cb = self._create_combobox(visa_frame, "Lakeshore VISA", 0)
        self.k2400_cb = self._create_combobox(
            visa_frame, "Keithley 2400 VISA", 1)
        self.k2182_cb = self._create_combobox(
            visa_frame, "Keithley 2182 VISA", 2)

        # --- Lake Shore 340 sensor input and heater-off option ---
        ttk.Label(
            visa_frame,
            text="Lakeshore Sensor Input:").grid(
            row=3, column=0, sticky='w', padx=10, pady=3)
        self.sensor_var = tk.StringVar(value='A')
        self.sensor_cb = ttk.Combobox(
            visa_frame, font=self.FONT_BASE, state='readonly',
            textvariable=self.sensor_var, values=['A', 'B', 'C', 'D'])
        self.sensor_cb.grid(
            row=3, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        # Heater-off at start. ON by default: a passive run is normally
        # started on a system that should be drifting freely, and the 340
        # keeps whatever heater range was last set through the keypad.
        self.heater_off_var = tk.BooleanVar(value=True)
        self.heater_off_cb = ttk.Checkbutton(
            visa_frame,
            text="Turn heater OFF at start (RANGE 0)",
            variable=self.heater_off_var)
        self.heater_off_cb.grid(
            row=4, column=0, columnspan=4, sticky='w', padx=10, pady=(3, 6))

    def _create_control_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='File Control')
        frame.grid(row=grid_row, column=0, sticky='new', pady=5)
        frame.grid_columnconfigure(0, weight=1)
        self._create_entry(frame, "Sample Name", "Sample_VT_Passive", 0)
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

    def _create_console_panel(self, parent, grid_row):
        frame = ttk.LabelFrame(parent, text='Console')
        frame.grid(row=grid_row, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

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
                self.params['k2182_visa'],
                self.params['ls_visa'])
            self.log(f"Lakeshore 340: {self.backend.lakeshore_idn}")
            self.backend.configure_instruments(
                self.params['current_ma'], self.params['compliance_v'],
                heater_off=self.params['heater_off'])
            if self.params['heater_off']:
                self.log("Lakeshore 340 heater turned OFF (RANGE 0 sent, RANGE? = 0).")
            else:
                self.log("Lakeshore 340 heater left as found (checkbox off).")
            self.log(
                "All instruments connected and configured for passive logging "
                f"(Lakeshore input {self.params['sensor']}).")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.params['name']}_{ts}_RT_Passive.csv"
            self.data_filepath = os.path.join(
                self.params['save_path'], filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Lakeshore 340: {self.backend.lakeshore_idn}",
                                 f"Sensor input: {self.params['sensor']}",
                                 f"Heater at start: "
                                 f"{'turned off' if self.params['heater_off'] else 'unchanged'}"])
                writer.writerow(["Temperature (K)", "Voltage (V)",
                                "Resistance (Ohm)", "Elapsed Time (s)",
                                "LS340 Reading Status"])

            self.set_ui_state(running=True)
            for key in self.data_storage:
                self.data_storage[key].clear()
            self.line_main.set_data([], [])
            self.ax_main.set_title(f"R-T Curve: {self.params['name']}")
            self.canvas.draw()
            self.log("Starting passive logging...")
            self.start_time = time.time()
            self.root.after(100, self._experiment_loop)
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Start Failed", f"{e}")
            self.backend.shutdown()

    def stop_experiment(self, reason=""):
        if not self.is_running:
            return
        self.log(
            f"Stopping... {reason}" if reason else "Stopping by user request.")
        self.is_running = False
        self.backend.shutdown()
        self.set_ui_state(running=False)
        self.ax_main.set_title("Logging stopped.")
        self.canvas.draw_idle()
        if reason:
            messagebox.showinfo("Experiment Finished", f"Reason: {reason}")

    def _experiment_loop(self):
        if not self.is_running:
            return
        try:
            temp, voltage, status = self.backend.get_measurement(
                self.params['sensor'])
            elapsed = time.time() - self.start_time
            resistance = voltage / \
                (self.params['current_ma'] * 1e-3) if self.params['current_ma'] != 0 else float('inf')
            status_text = describe_reading_status(status)
            if status_text:
                self.log(f"T: {temp:.3f} K | R: {resistance:.4e} Ω"
                         f"   [RDGST {status}: {status_text}]")
            else:
                self.log(f"T: {temp:.3f} K | R: {resistance:.4e} Ω")

            self.data_storage['temperature'].append(temp)
            self.data_storage['voltage'].append(voltage)
            self.data_storage['resistance'].append(resistance)
            with open(self.data_filepath, 'a', newline='') as f:
                csv.writer(f).writerow(
                    [f"{temp:.4f}", f"{voltage:.6e}", f"{resistance:.6e}", f"{elapsed:.2f}",
                     status])
            self.line_main.set_data(
                self.data_storage['temperature'],
                self.data_storage['resistance'])
            self.ax_main.relim()
            self.ax_main.autoscale_view()
            self.canvas.draw_idle()

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
                'ls_visa': self.ls_cb.get(),
                'current_ma': float(
                    self.entries["Source Current (mA)"].get()),
                'compliance_v': float(
                    self.entries["Compliance (V)"].get()),
                'delay_s': float(
                    self.entries["Logging Delay (s)"].get()),
                'k2400_visa': self.k2400_cb.get(),
                'k2182_visa': self.k2182_cb.get()}
            if not all(params.values()):
                raise ValueError("All fields must be filled.")
            # Added after the all() check: a False checkbox is a valid value.
            params['sensor'] = self.sensor_var.get()
            params['heater_off'] = bool(self.heater_off_var.get())
            return params
        except Exception as e:
            raise ValueError(f"Invalid parameter input: {e}")

    def set_ui_state(self, running: bool):
        self.is_running = running
        state = 'disabled' if running else 'normal'
        self.start_button.config(state=state)
        for w in self.entries.values():
            w.config(state=state)
        for cb in [self.ls_cb, self.k2400_cb, self.k2182_cb]:
            cb.config(state=state if state == 'normal' else 'readonly')
        self.sensor_cb.config(state='readonly' if state == 'normal' else 'disabled')
        self.heater_off_cb.config(state=state)
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
            self.k2182_cb['values'] = resources
            default_k2400_addr = 'GPIB1::4::INSTR'
            # Nothing is probed. The lab's 340 is at address 19 (3 Sep
            # 2026); 12 and 15 are never auto-picked because other
            # instruments live there.
            for r in resources:
                if LAKESHORE340_ADDRESS_HINT in r:
                    self.ls_cb.set(r)
                if 'GPIB0::7' in r:
                    self.k2182_cb.set(r)
            if default_k2400_addr in resources:
                self.k2400_cb.set(default_k2400_addr)
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
            columnspan=2)
        entry.insert(0, default_value)
        self.entries[label_text] = entry
        if browse:
            btn = ttk.Button(
                parent,
                text="Browse...",
                style='Browse.TButton',
                command=self._browse_file_location)
            btn.grid(row=row, column=3, sticky='e', padx=(0, 10))
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
            style='TCombobox')
        cb.grid(row=row, column=1, sticky='ew', padx=10, pady=3, columnspan=3)
        return cb

    def _on_closing(self):
        if self.is_running and messagebox.askyesno(
                "Exit", "Experiment is running. Stop and exit?"):
            self.stop_experiment("Application closed by user.")
            self.root.destroy()
        elif not self.is_running:
            self.root.destroy()


if __name__ == '__main__':
    if not PYMEASURE_AVAILABLE:
        messagebox.showerror(
            "Dependency Error",
            "Pymeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")
    else:
        root = tk.Tk()
        app = VT_GUI_Passive(root)
        root.mainloop()
