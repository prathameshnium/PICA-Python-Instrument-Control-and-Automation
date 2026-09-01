'''
===============================================================================
 PROGRAM:      PICA Launcher

 PURPOSE:      A graphical dashboard for launching PICA measurement scripts.

 AUTHOR:       Prathamesh Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre
=========================================================================
'''
import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, Canvas, scrolledtext, font
import os
import sys
import subprocess
import platform
import re
from datetime import datetime
import runpy
import multiprocessing
from multiprocessing import Process

# Run this file directly (python pica/main.py) and sys.path[0] is the pica
# folder, not the repo root, so "import pica.utils..." can bind to an older
# copy installed in site-packages instead of the one sitting next to this
# file. Put the repo root first so the launcher always uses its own tree.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# IMPORT THE UTILS MODULE TO FIND ITS PATH AUTOMATICALLY
try:
    import pica.utils.GPIB_Instrument_Scanner_GUI as gpib_scanner_module
    import pica.utils.PlotterUtil_GUI as plotter_module
    UTILS_AVAILABLE = True
except ImportError:
    UTILS_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    import pyvisa.errors
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False


def run_script_process(script_path, argv=None):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    Note: os.chdir is acceptable here because this runs in a separate spawned process
    and does not affect the launcher's working directory.

    'argv' is an optional list of command-line arguments (e.g. data files to
    pre-load); it is placed in sys.argv exactly as a shell invocation would.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        sys.argv = [script_path] + list(argv or [])
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility using the package path."""
    if not UTILS_AVAILABLE:
        messagebox.showerror("Import Error", "Could not import pica.utils. Is the package installed?")
        return

    try:
        # Get the absolute path directly from the imported module
        scanner_path = os.path.abspath(gpib_scanner_module.__file__)
        
        if not os.path.exists(scanner_path):
            messagebox.showerror("File Not Found", f"Scanner not found at:\n{scanner_path}")
            return
            
        # Run it
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")


def launch_plotter_utility():
    """Finds and launches the plotter utility using the package path."""
    if not UTILS_AVAILABLE:
        messagebox.showerror("Import Error", "Could not import pica.utils. Is the package installed?")
        return

    try:
        # Get the absolute path directly from the imported module
        plotter_path = os.path.abspath(plotter_module.__file__)
        
        if not os.path.exists(plotter_path):
            messagebox.showerror("File Not Found", f"Plotter not found at:\n{plotter_path}")
            return
            
        # Run it
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch Plotter Utility: {e}")


def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller.
    """
    try:
        base_path = sys._MEIPASS
        return os.path.join(base_path, relative_path)
    except Exception:
        script_dir = os.path.dirname(os.path.abspath(__file__))

        installed_path = os.path.join(script_dir, relative_path)
        if os.path.exists(installed_path):
            return installed_path

        dev_path = os.path.join(script_dir, '..', relative_path)
        if os.path.exists(dev_path):
            return dev_path

        return installed_path


class PICALauncherApp:

    PROGRAM_VERSION = "1.0.3"
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    # Module family marker colors (thin bar at the left of launch buttons).
    # Small marks tolerate strong colors: pale sand / vivid terracotta /
    # dark espresso are unmistakable even at 5 px wide.
    CLR_FAMILY_SENSING = '#D8C0A8'   # T_Sensing  (pale sand, lightest)
    CLR_FAMILY_CONTROL = '#B04A38'   # T_Control  (vivid terracotta red)
    CLR_FAMILY_MASTER = '#4A3222'    # Master     (dark espresso, darkest)
    # Universal hover highlight: deep maroon (same as the EXPERIMENTAL badge),
    # darker and redder than any resting fill so "hovered" is unmistakable
    CLR_HOVER = '#8B3A2F'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_LINK = '#2E5266'  # Sky Blue, for better contrast
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 10, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')
    FONT_INSTITUTE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE)
    FONT_INFO_ITALIC = ('Segoe UI', FONT_SIZE_BASE, 'italic')
    
    LOGO_FILE = resource_path("assets/LOGO/UGC_DAE_CSR_NBG.jpeg")
    MANUAL_FILE = resource_path("docs/User_Manual.md")
    README_FILE = resource_path("README.md")
    LICENSE_FILE = resource_path("LICENSE")
    UPDATES_FILE = resource_path("CHANGELOG.md")
    REPO_URL = "https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/tree/main"
    LOGO_SIZE = 140

    SCRIPT_PATHS = {
        # Based on Updates.md, using the latest versions of scripts.
        "Delta Mode I-V Sweep": resource_path("keithley/delta_mode/IV_K6221_DC_Sweep_GUI.py"),
        "Delta Mode R-T": resource_path("keithley/delta_mode/Delta_RT_K6221_K2182_L350_T_Control_GUI.py"),
        "Delta Mode R-T (T_Sensing)": resource_path("keithley/delta_mode/Delta_RT_K6221_K2182_L350_Sensing_GUI.py"),
        "Delta Mode R-T (T_Sensing, CC34)": resource_path("keithley/delta_mode/Delta_RT_K6221_K2182_CC34_Sensing_GUI.py"),
        "K2400 I-V": resource_path("keithley/k2400/IV_K2400_GUI.py"),
        "K2400 R-T": resource_path("keithley/k2400/RT_K2400_L350_T_Control_GUI.py"),
        "K2400 R-T (T_Sensing)": resource_path("keithley/k2400/RT_K2400_L350_T_Sensing_GUI.py"),
        "K2400 R-T (T_Sensing, CC34)": resource_path("keithley/k2400/RT_K2400_CC34_T_Sensing_GUI.py"),
        "K2400_2182 I-V": resource_path("keithley/k2400_2182/IV_K2400_K2182_GUI.py"),
        "K2400_2182 R-T": resource_path("keithley/k2400_2182/RT_K2400_K2182_T_Control_GUI.py"),
        "K2400_2182 R-T (T_Sensing)": resource_path("keithley/k2400_2182/RT_K2400_K2182_L350_T_Sensing_GUI.py"),
        "K2400_2182 R-T (T_Sensing, CC34)": resource_path("keithley/k2400_2182/RT_K2400_K2182_CC34_T_Sensing_GUI.py"),
        "K6517B I-V": resource_path("keithley/k6517b/High_Resistance/IV_K6517B_GUI.py"),
        "K6517B R-T": resource_path("keithley/k6517b/High_Resistance/RT_K6517B_L350_T_Control_GUI.py"),
        "K6517B R-T (T_Sensing)": resource_path("keithley/k6517b/High_Resistance/RT_K6517B_L350_T_Sensing_GUI.py"),
        "K6517B R-T (T_Sensing, CC34)": resource_path("keithley/k6517b/High_Resistance/RT_K6517B_CC34_T_Sensing_GUI.py"),
        "K197A Monitor": resource_path("keithley/k197a/Monitor_K197A_GUI.py"),
        "TPG361 Pressure Log": resource_path("pfeiffer/Pressure_Log_TPG361_GUI.py"),
        "AFG3022B Function Generator": resource_path("tektronix/Function_Gen_AFG3022B_GUI.py"),
        "Pyroelectric Current": resource_path("keithley/k6517b/Pyroelectricity/Pyroelectric_K6517B_L350_GUI.py"),
        "K6517B Polling (Bias)": resource_path("keithley/k6517b/Pyroelectricity/Polling_K6517B_GUI.py"),
        "Lakeshore Temp Control": resource_path("lakeshore/T_Control_L350_RangeControl_GUI.py"),
        "Lakeshore Step Control": resource_path("lakeshore/T_Control_L350_Step_GUI.py"),
        "Lakeshore Step Control (Advanced)": resource_path("lakeshore/T_Control_L350_Step_GUI_advanced.py"),
        "Lakeshore Direct Control": resource_path("lakeshore/T_Control_L350_DirectControl_GUI.py"),
        "Lakeshore Temp Monitor": resource_path("lakeshore/T_Sensing_L350_GUI.py"),
        "Lakeshore Sensor Curve Loader": resource_path("lakeshore/Sensor_Curve_Loader_L340_L350_GUI.py"),
        "Cryocon Direct Control": resource_path("cryocon/T_Control_CC34_DirectControl_GUI.py"),
        "Cryocon Temp Monitor": resource_path("cryocon/T_Sensing_CC34_GUI.py"),
        "Cryocon Sensor Curve Loader": resource_path("cryocon/Sensor_Curve_Loader_CC34_GUI.py"),
        # Read-only companions to the loader: they browse and export the
        # curves an instrument already holds and cannot write to it.
        "Cryocon Sensor Curve Viewer": resource_path("cryocon/Sensor_Curve_Viewer_CC34_GUI.py"),
        "Lakeshore Sensor Curve Viewer": resource_path("lakeshore/Sensor_Curve_Viewer_L350_GUI.py"),
        "LCR C-V Measurement": resource_path("keysight/CV_KE4980A_GUI.py"),
        "LCR Frequency Scan": resource_path("keysight/Frequency_Scan_E4980A_GUI.py"), # Already present
        "LCR Temp. Step Freq. Scan (T_Control)": resource_path("keysight/Step_Frequency_Scan_E4980A_GUI.py"), # Already present
        "LCR Temp. Scan (T_Control)": resource_path("keysight/Temprature_Scan_E4980A_GUI.py"), # Already present
        "LCR Temp. Scan (T_Sensing)": resource_path("keysight/Temprature_Scan_Passive_E4980A_GUI.py"), # Already present
        "LCR Temp. Scan (T_Sensing, CC34)": resource_path("keysight/Temprature_Scan_Passive_CC34_E4980A_GUI.py"),
        "PPMS Sync Freq. Scan": resource_path("keysight/PPMS_Sync_Freq_Scan_E4980A_GUI.py"),
        "PPMS Sync Freq. Scan (CC34)": resource_path("keysight/PPMS_Sync_Freq_Scan_CC34_E4980A_GUI.py"),
        "PPMS Dielectric Master": resource_path("keysight/PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py"),
        "PPMS Dielectric Master (CC34)": resource_path("keysight/PPMS_Dielectric_Master_Tscan_Fscan_CC34_E4980A_GUI.py"),
        "SR830 Lock-in Comms": resource_path("lockin/sr830/Comms_SR830_GUI.py"),
        "SR830 AC Resistivity": resource_path("lockin/sr830/AC_Resistivity_K6221_SR830_GUI.py"),
        "SR830 AC I-V": resource_path("lockin/sr830/IV_AC_K6221_SR830_GUI.py"),
        "SR830 AC Freq. Scan": resource_path("lockin/sr830/Frequency_Scan_K6221_SR830_GUI.py"),
        "SR830 AC R-T": resource_path("lockin/sr830/RT_AC_K6221_SR830_L350_T_Control_GUI.py"),
        "SR830 AC R-T (T_Sensing)": resource_path("lockin/sr830/RT_AC_K6221_SR830_L350_T_Sensing_GUI.py"),
        "SR830 AC R-T (T_Sensing, CC34)": resource_path("lockin/sr830/RT_AC_K6221_SR830_CC34_T_Sensing_GUI.py"),
        "K197A AC I-V": resource_path("keithley/k6221_k197a/IV_AC_K6221_K197A_GUI.py"),
        "K197A AC Freq. Scan": resource_path("keithley/k6221_k197a/Frequency_Scan_K6221_K197A_GUI.py"),
        "K197A AC R-T": resource_path("keithley/k6221_k197a/RT_AC_K6221_K197A_L350_T_Control_GUI.py"),
        "K197A AC R-T (T_Sensing)": resource_path("keithley/k6221_k197a/RT_AC_K6221_K197A_L350_T_Sensing_GUI.py"),
        "K197A AC R-T (T_Sensing, CC34)": resource_path("keithley/k6221_k197a/RT_AC_K6221_K197A_CC34_T_Sensing_GUI.py"),
        "Alpha-AN Freq. Scan": resource_path("novocontrol/Frequency_Scan_AlphaAN_GUI.py"),
        "Alpha-AN Freq. Scan (32-bit)": resource_path("novocontrol/Frequency_Scan_AlphaAN_32bit_GUI.py"),
        "Plotter Utility": resource_path("utils/PlotterUtil_GUI.py"),
        "PPMS Plotter Utility": resource_path("PPMS/PPMS_Plotter_GUI.py"),
        "GPIB Scanner": resource_path("utils/GPIB_Instrument_Scanner_GUI.py"),
        "GPIB Scanner (32-bit)": resource_path("utils/GPIB_Scanner_32bit_GUI.py"),
        "SCPI Console": resource_path("utils/SCPI_Console_GUI.py"),
        "PICA Help": resource_path("README.md"),
        "PE Plotter": resource_path("utils/PE_plotter.py"),
        "Quick Calc": resource_path("utils/Quick_Calc_GUI.py"),
        "Time Utility": resource_path("utils/Time_Utility_GUI.py"),
        "Unit Converter": resource_path("utils/Unit_Converter_GUI.py"),
        "Sequence Visualizer": resource_path("PPMS/PPMS_SeqVisualizer_GUI.py"),
        "PPMS Time Estimator": resource_path("PPMS/PPMS_TimeEstimator_GUI.py"),
        "MD Ratio Calculator": resource_path("utils/MD_Ratio_Calculator_GUI.py"),
    }

    def __init__(self, root):
        """Constructor to initialize the main application window."""
        self.root = root
        self.root.title(f"PICA Launcher - v{self.PROGRAM_VERSION}")
        self.root.geometry("1100x750")
        try:
            self.root.state('zoomed')
        except tk.TclError:
            pass
        self.root.configure(bg=self.CLR_BG_DARK)
        
        # Initialize internal variables
        self.logo_image = None
        self.console_widget = None
        self._md_cache = {}
        
        # Setup UI
        self.setup_styles()
        self.create_widgets()
        
        # Background tasks
        self._pre_cache_markdown_files()
        self.log(f"PICA Launcher initialized (v{self.PROGRAM_VERSION})")
        # Auto-launch the GPIB/VISA scanner ~0.5 s after startup
        self.root.after(500, self._auto_launch_gpib)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            '.',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_TEXT,
            font=self.FONT_BASE)
        style.configure('TSeparator', background=self.CLR_FRAME_BG)
        style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_BG_DARK,
            borderwidth=2,
            padding=6)
        style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_TEXT,
            font=self.FONT_SUBTITLE)
        style.configure(
            'App.TButton',
            font=self.FONT_BASE,
            padding=(10, 5),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_FRAME_BG,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        # One shared hover color for all launch buttons: deep maroon, darker
        # than any resting fill, so "highlighted" is unmistakable.
        style.map(
            'App.TButton', background=[
                ('active', self.CLR_HOVER), ('hover', self.CLR_HOVER)], foreground=[
                ('active', '#FFFFFF'), ('hover', '#FFFFFF')])
        style.configure(
            'Scan.TButton',
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_ACCENT_GREEN)
        style.map(
            'Scan.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        # --- Launch buttons: cream body, left-aligned so the family marker
        # bars line up down each column ---
        style.configure(
            'Launch.TButton',
            font=self.FONT_BASE,
            padding=(10, 5),
            anchor='w',
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_FRAME_BG,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'Launch.TButton', background=[
                ('active', self.CLR_HOVER), ('hover', self.CLR_HOVER)], foreground=[
                ('active', '#FFFFFF'), ('hover', '#FFFFFF')])
        style.configure(
            'Icon.TButton',
            font=('Segoe UI', 12),
            padding=(5, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_FRAME_BG,
            borderwidth=0)
        style.map(
            'Icon.TButton', background=[
                ('active', self.CLR_HOVER), ('hover', self.CLR_HOVER)], foreground=[
                ('active', '#FFFFFF'), ('hover', '#FFFFFF')])
        style.configure(
            "Vertical.TScrollbar",
            troughcolor=self.CLR_BG_DARK,
            background=self.CLR_FRAME_BG,
            arrowcolor=self.CLR_ACCENT_GOLD,
            bordercolor=self.CLR_BG_DARK)
        style.map("Vertical.TScrollbar", background=[
                  ('active', self.CLR_ACCENT_GOLD)])
        # --- Styles for the "Additional Tools" pop-up ---
        style.configure(
            'ToolsPopup.TFrame',
            background=self.CLR_FRAME_BG)
        style.configure(
            'ToolsPopupHeader.TFrame',
            background=self.CLR_BG_DARK)
        style.configure(
            'ToolsPopupTitle.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_SUBTITLE)
        style.configure(
            'ToolsPopupSubtitle.TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_TEXT,
            font=self.FONT_INFO_ITALIC)

    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=0, minsize=380)
        self.root.grid_columnconfigure(1, weight=1)

        info_panel = self.create_resource_panel(self.root)
        info_panel.grid(row=0, column=0, sticky="nsew", padx=(15, 10), pady=15)
        launcher_container = self.create_launcher_panel(self.root)
        launcher_container.grid(
            row=0, column=1, sticky="nsew", padx=(
                10, 15), pady=15)

    def create_resource_panel(self, parent):
        info_frame = ttk.Frame(parent)
        info_frame.configure(padding=20)

        logo_canvas = Canvas(
            info_frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.pack(pady=(0, 15))
        # Defer image loading to make startup feel faster
        self.root.after(10, lambda: self._load_logo(logo_canvas))

        ttk.Label(
            info_frame,
            text="UGC-DAE Consortium for Scientific Research, Mumbai Centre",
            font=self.FONT_INSTITUTE,
            justify='center',
            anchor='center').pack(
            pady=(0, 15))

        ttk.Label(
            info_frame,
            text="PICA: Python Instrument\nControl & Automation",
            font=self.FONT_TITLE,
            foreground=self.CLR_ACCENT_GOLD,
            justify='center',
            anchor='center').pack(
            pady=(0, 15))

        desc_text = "A modular software suite for automating laboratory measurements in physics research."
        ttk.Label(
            info_frame,
            text=desc_text,
            font=self.FONT_INFO,
            wraplength=360,
            justify='center',
            anchor='center').pack(
            pady=(0, 10))

        # --- Create a bold font for names ---
        bold_font = font.Font(
            family='Segoe UI',
            size=self.FONT_SIZE_BASE,
            weight='bold')

        ttk.Label(
            info_frame,
            text="Developed by Prathamesh Deshmukh",
            font=bold_font,
            justify='center',
            anchor='center').pack(
            pady=(5, 0))
        ttk.Label(
            info_frame,
            text="Vision & Guidance by Dr. Sudip Mukherjee",
            font=bold_font,
            justify='center',
            anchor='center').pack(
            pady=(0, 15))

        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=10)
        util_frame = ttk.Frame(info_frame)
        util_frame.pack(fill='x', expand=False, pady=5)
        
        # --- VISA/GPIB and Plotter moved to top-right icon toolbar ---
        util_frame.grid_columnconfigure((0, 1, 2), weight=1)

        ttk.Button(
            util_frame,
            text="Tools",
            style='App.TButton',
            command=self.open_tools_popup).grid(row=0, column=0, sticky='ew', padx=(0, 2))

        ttk.Button(
            util_frame,
            text="README",
            style='App.TButton',
            command=self.open_readme).grid(row=0, column=1, sticky='ew', padx=2)

        ttk.Button(
            util_frame,
            text="Manuals",
            style='App.TButton',
            command=self.open_manual_file).grid(row=0, column=2, sticky='ew', padx=(2, 0))

        bottom_frame = ttk.Frame(info_frame)
        bottom_frame.pack(side='bottom', fill='x', pady=(15, 0))

        # --- Version Label (Clickable) ---
        version_font = font.Font(family='Segoe UI', size=9, underline=True)
        version_label = ttk.Label(
            bottom_frame,
            text=f"PICA Launcher Version: {self.PROGRAM_VERSION}",
            font=version_font,
            foreground=self.CLR_LINK,
            cursor="hand2")
        version_label.pack(pady=(0, 5))
        version_label.bind("<Button-1>", lambda e: self.open_updates())

        # --- License Label (Clickable) ---
        license_font = font.Font(family='Segoe UI', size=9, underline=True)
        license_label = ttk.Label(
            bottom_frame,
            text="This project is licensed under the MIT License.",
            font=license_font,
            foreground=self.CLR_LINK,
            cursor="hand2")
        license_label.pack()
        license_label.bind("<Button-1>", lambda e: self.open_license())

        # --- Repo Link (Clickable) ---
        repo_font = font.Font(family='Segoe UI', size=9, underline=True)
        repo_label = ttk.Label(
            bottom_frame,
            text="View Project on GitHub",
            font=repo_font,
            foreground=self.CLR_LINK,
            cursor="hand2")
        repo_label.pack(pady=(5, 0))
        repo_label.bind("<Button-1>", self.open_repo)

        console_container = ttk.LabelFrame(
            info_frame, text="Console", padding=(5, 10))
        console_container.pack(side='bottom', fill='x', pady=(20, 0))
        self.console_widget = scrolledtext.ScrolledText(
            console_container,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_TEXT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0,
            relief='flat',
            height=7)
        self.console_widget.pack(fill='both', expand=True)
        return info_frame

    def _load_logo(self, canvas):
        """Loads the logo image after the main window is drawn."""
        if not (PIL_AVAILABLE and os.path.exists(self.LOGO_FILE)):
            self.log("Logo not loaded: PIL not available or file not found.")
            return

        try:
            img = Image.open(self.LOGO_FILE)
            img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                          Image.Resampling.LANCZOS)
            # Keep a reference to the image to prevent garbage collection
            self.logo_image = ImageTk.PhotoImage(img)
            canvas.create_image(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                image=self.logo_image)
        except Exception as e:
            self.log(f"ERROR: Failed to load logo. {e}")

    # Maps a module family tag to its marker bar color; None -> transparent
    # spacer so unmarked labels still line up with marked ones.
    _FAMILY_COLORS = {
        'sensing': CLR_FAMILY_SENSING,
        'control': CLR_FAMILY_CONTROL,
        'master': CLR_FAMILY_MASTER,
    }

    def _get_family_marker(self, family):
        """Returns a small PhotoImage bar in the family color (cached).
        The image is 8px wide with a 5px colored bar and a transparent gap;
        an unknown/None family yields a fully transparent spacer."""
        if not hasattr(self, '_marker_images'):
            self._marker_images = {}
        if family not in self._marker_images:
            img = tk.PhotoImage(master=self.root, width=8, height=18)
            color = self._FAMILY_COLORS.get(family)
            if color:
                img.put(color, to=(0, 0, 5, 18))
            self._marker_images[family] = img
        return self._marker_images[family]

    def _create_launch_button(self, parent, text, script_key, family=None):
        return ttk.Button(
            parent,
            text=text,
            style='Launch.TButton',
            image=self._get_family_marker(family),
            compound='left',
            command=lambda: self.launch_script(
                self.SCRIPT_PATHS[script_key]))

    def _add_tooltip(self, widget, text):
        """Attach a lightweight hover tooltip to a widget."""
        tip = {'win': None}

        def show(_event):
            if tip['win'] is not None:
                return
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(
                win,
                text=text,
                bg=self.CLR_CONSOLE_BG,
                fg=self.CLR_TEXT,
                font=('Segoe UI', 9),
                bd=1,
                relief='solid',
                padx=6,
                pady=2).pack()
            tip['win'] = win

        def hide(_event):
            if tip['win'] is not None:
                tip['win'].destroy()
                tip['win'] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def create_launcher_panel(self, parent):
        main_container = ttk.Frame(parent)
        main_container.grid_rowconfigure(1, weight=1)
        main_container.grid_columnconfigure(0, weight=1)

        # --- Top-right utility toolbar (small icon buttons) ---
        toolbar = ttk.Frame(main_container)
        toolbar.grid(row=0, column=0, columnspan=2, sticky='e', pady=(0, 2))

        plotter_button = ttk.Button(
            toolbar,
            text="📈",
            width=3,
            style='Icon.TButton',
            command=launch_plotter_utility)
        plotter_button.pack(side='right', padx=(2, 0))
        self._add_tooltip(plotter_button, "Plotter Utility")

        gpib_button = ttk.Button(
            toolbar,
            text="📟",
            width=3,
            style='Icon.TButton',
            command=self.run_gpib_test)
        gpib_button.pack(side='right', padx=(0, 2))
        self._add_tooltip(gpib_button, "VISA/GPIB Scanner")

        # --- Create a scrollable area ---
        canvas = Canvas(
            main_container,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            main_container,
            orient="vertical",
            command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        def _on_mousewheel_windows(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_mousewheel_linux_macos(event):
            canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel_windows)
            canvas.bind_all("<Button-4>", _on_mousewheel_linux_macos)
            canvas.bind_all("<Button-5>", _on_mousewheel_linux_macos)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Bind scrolling only while the pointer is over this panel, so it
        # works anywhere over the scrollable content (not just bare canvas).
        main_container.bind("<Enter>", _bind_mousewheel)
        main_container.bind("<Leave>", _unbind_mousewheel)

        canvas.grid(row=1, column=0, sticky='nsew', pady=10)
        scrollbar.grid(row=1, column=1, sticky='ns', pady=10)

        # --- Make the scrollable frame expand to the width of the canvas ---
        def _configure_scrollable_frame(event):
            canvas.itemconfig('frame', width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", _configure_scrollable_frame)

        # --- Container for the two columns inside the scrollable frame ---
        scrollable_frame.grid_columnconfigure(
            (0, 1), weight=1, uniform="group1")
        left_col = ttk.Frame(scrollable_frame)
        left_col.grid(row=0, column=0, sticky='new', padx=(0, 5), pady=(0, 15))
        right_col = ttk.Frame(scrollable_frame)
        right_col.grid(
            row=0, column=1, sticky='new', padx=(
                5, 5), pady=(
                0, 15))

        # --- Left Column Suites ---
        self._create_suite_frame(left_col,
                                 'Low Resistance (10 nΩ to 100 MΩ)',
                                 "Current Driven",
                                 "Instruments: Keithley 6221/2182, Lakeshore 350 or Cryocon 34",
                                 [("Sweep Mode I-V ",
                                   "Delta Mode I-V Sweep"),
                                  ("Delta Mode R vs. T (T_Control)",
                                   "Delta Mode R-T", "control"),
                                     ("Delta Mode R vs. T (T_Sensing, L350)",
                                      "Delta Mode R-T (T_Sensing)", "sensing"),
                                     ("Delta Mode R vs. T (T_Sensing, Cryocon 34)",
                                      "Delta Mode R-T (T_Sensing, CC34)", "sensing"),
                                  ])
        self._create_suite_frame(left_col,
                                 'Mid Resistance (100 µΩ to 200 MΩ)',
                                 "Current Driven",
                                 "Instruments: Keithley 2400, Lakeshore 350 or Cryocon 34",
                                 [("I-V Sweep",
                                   "K2400 I-V"),
                                  ("R vs. T (T_Control)",
                                   "K2400 R-T", "control"),
                                     ("R vs. T (T_Sensing, L350)",
                                      "K2400 R-T (T_Sensing)", "sensing"),
                                     ("R vs. T (T_Sensing, Cryocon 34)",
                                      "K2400 R-T (T_Sensing, CC34)", "sensing"),
                                  ])
        self._create_suite_frame(left_col,
                                 'Mid Resistance (Precision) (1 µΩ to 100 MΩ)',
                                 "Current Driven",
                                 "Instruments: Keithley 2400/2182, Lakeshore 350 or Cryocon 34",
                                 [("I-V Sweep",
                                   "K2400_2182 I-V"),
                                  ("R vs. T (T_Control)",
                                   "K2400_2182 R-T", "control"),
                                     ("R vs. T (T_Sensing, L350)",
                                      "K2400_2182 R-T (T_Sensing)", "sensing"),
                                     ("R vs. T (T_Sensing, Cryocon 34)",
                                      "K2400_2182 R-T (T_Sensing, CC34)", "sensing"),
                                  ])

        # --- Right Column Suites ---
        self._create_suite_frame(right_col,
                                 'High Resistance (1 Ω to 10 PΩ)',
                                 "Voltage Driven",
                                 "Instruments: Keithley 6517B, Lakeshore 350 or Cryocon 34",
                                 [("I-V Sweep",
                                   "K6517B I-V"),
                                  ("R vs. T (T_Control)",
                                   "K6517B R-T", "control"),
                                     ("R vs. T (T_Sensing, L350)",
                                      "K6517B R-T (T_Sensing)", "sensing"),
                                     ("R vs. T (T_Sensing, Cryocon 34)",
                                      "K6517B R-T (T_Sensing, CC34)", "sensing"),
                                  ])
        self._create_suite_frame(
            right_col, 'Pyroelectric Measurement (Keithley 6517B)', "Current Sensing", None, [
                ("PyroCurrent vs. T", "Pyroelectric Current"),
                ("Voltage Polling (Bias)", "K6517B Polling (Bias)")])
        self._create_suite_frame(
            right_col, 'Bench Multimeter Logging (Keithley 197A)', "Passive Logging",
            "Requires a Model 1973A / 1972A IEEE-488 interface card", [
                ("197A Reading Monitor", "K197A Monitor", "sensing")])
        self._create_suite_frame(
            right_col, 'Vacuum Gauge Logging (Pfeiffer TPG 361)', "Passive Logging",
            "RS-232 serial (ASRL), not GPIB -- pick the COM port in the module", [
                ("Pressure vs. Time", "TPG361 Pressure Log", "sensing")])
        # A source, not a measurement: this module sets the drive another
        # module measures against, so it carries no data file of its own.
        self._create_suite_frame(
            right_col, 'Signal Generation (Tektronix AFG 3022B)',
            "Waveform Source", "2 channels, 25 MHz", [
                ("Function Generator Direct Control",
                 "AFG3022B Function Generator", "control")])
        self._create_suite_frame(right_col,
                                 'Temperature Utilities (Lakeshore 350 / Cryocon 34)',
                                 "Control Utility",
                                 None,
                                 [("Temperature Ramp (L350)",
                                   "Lakeshore Temp Control", "control"),
                                  ("Step-wise Control (Basic, L350)",
                                   "Lakeshore Step Control", "control"),
                                  ("Step-wise Control (Advanced, L350)",
                                   "Lakeshore Step Control (Advanced)", "control"),
                                  ("Direct Control (L350)",
                                   "Lakeshore Direct Control", "control"),
                                  ("Temperature Monitor (L350)",
                                   "Lakeshore Temp Monitor", "sensing"),
                                  ("Sensor Curve Loader (L340 / L350)",
                                   "Lakeshore Sensor Curve Loader",
                                   "control"),
                                  ("Sensor Curve Viewer (L350, read only)",
                                   "Lakeshore Sensor Curve Viewer", "sensing"),
                                  ("Direct Control (Cryocon 34)",
                                   "Cryocon Direct Control", "control"),
                                  ("Temperature Monitor (Cryocon 34)",
                                   "Cryocon Temp Monitor", "sensing"),
                                  ("Sensor Curve Loader (Cryocon 34)",
                                   "Cryocon Sensor Curve Loader", "control"),
                                  ("Sensor Curve Viewer (Cryocon 34, read only)",
                                   "Cryocon Sensor Curve Viewer", "sensing")])
        self._create_suite_frame(right_col,
                                 'Impedance Spectroscopy',
                                 "Voltage Driven",
                                 "Instrument: Keysight E4980A",
                                 [("C-V Measurement",
                                   "LCR C-V Measurement"),
                                  ("Dielectric Frequency Scan", "LCR Frequency Scan"), # Already present
                                  ("Temp. Step Freq. Scan (T_Control)", "LCR Temp. Step Freq. Scan (T_Control)", "control"), # Already present
                                  ("Temp. Step Freq. Scan (PPMS, T_Sensing, L350)", "PPMS Sync Freq. Scan", "sensing"),
                                  ("Temp. Step Freq. Scan (PPMS, T_Sensing, Cryocon 34)", "PPMS Sync Freq. Scan (CC34)", "sensing"),
                                  ("Dielectric Master Tscan+Fscan (PPMS, L350)", "PPMS Dielectric Master", "master"),
                                  ("Dielectric Master Tscan+Fscan (PPMS, Cryocon 34)", "PPMS Dielectric Master (CC34)", "master"),
                                  ("Dielectric Temp. Scan (T_Control)", "LCR Temp. Scan (T_Control)", "control"), # Already present
                                  ("Dielectric Temp. Scan (T_Sensing, L350)", "LCR Temp. Scan (T_Sensing)", "sensing"), # Already present
                                  ("Dielectric Temp. Scan (T_Sensing, Cryocon 34)", "LCR Temp. Scan (T_Sensing, CC34)", "sensing"),
                                  ])
        self._create_suite_frame(right_col,
                                 'Broadband Dielectric Spectroscopy',
                                 "Voltage Driven",
                                 "Instrument: Novocontrol Alpha-AN + ZG4",
                                 [("Frequency Scan (fixed T)",
                                   "Alpha-AN Freq. Scan"),
                                  ("Frequency Scan (fixed T, 32-bit GPIB)",
                                   "Alpha-AN Freq. Scan (32-bit)")],
                                 experimental=True)
        self._create_suite_frame(right_col,
                                 'Lock-in Amplifier',
                                 "AC Current Driven",
                                 "Instruments: Keithley 6221 + SRS SR830",
                                 [("Comms and Control",
                                   "SR830 Lock-in Comms"),
                                  ("AC Resistivity (4-probe)",
                                   "SR830 AC Resistivity"),
                                  ("AC I-V Sweep", "SR830 AC I-V"),
                                  ("AC Frequency Scan",
                                   "SR830 AC Freq. Scan"),
                                  ("AC R vs. T (T_Control)",
                                   "SR830 AC R-T", "control"),
                                  ("AC R vs. T (T_Sensing, L350)",
                                   "SR830 AC R-T (T_Sensing)", "sensing"),
                                  ("AC R vs. T (T_Sensing, Cryocon 34)",
                                   "SR830 AC R-T (T_Sensing, CC34)",
                                   "sensing")],
                                 experimental=True)
        # No reference and no phase: R is a magnitude and an upper bound.
        # Kept apart from the lock-in suite so the two are never confused.
        self._create_suite_frame(right_col,
                                 'AC Transport without a Lock-in',
                                 "AC Current Driven",
                                 "Instruments: Keithley 6221 + Keithley 197A (AC volts)",
                                 [("AC I-V Sweep", "K197A AC I-V"),
                                  ("AC Frequency Scan",
                                   "K197A AC Freq. Scan"),
                                  ("AC R vs. T (T_Control)",
                                   "K197A AC R-T", "control"),
                                  ("AC R vs. T (T_Sensing, L350)",
                                   "K197A AC R-T (T_Sensing)", "sensing"),
                                  ("AC R vs. T (T_Sensing, Cryocon 34)",
                                   "K197A AC R-T (T_Sensing, CC34)",
                                   "sensing")],
                                 experimental=True)

        return main_container

    def _create_suite_frame(
            self,
            parent,
            title,
            measurement_type,
            instruments_text,
            buttons,
            experimental=False):
        """Helper to create a measurement suite frame, reducing code duplication."""
        frame = ttk.LabelFrame(parent, text=title, style='TLabelframe')
        frame.pack(fill='x', expand=True, pady=5)
        frame.columnconfigure(0, weight=1)

        # --- Header area for badge, text, and folder icon ---
        header_container = ttk.Frame(frame, style='TFrame')
        header_container.grid(row=0, column=0, sticky='ew', pady=(0, 5))
        header_container.columnconfigure(
            0, weight=1)  # Allow text area to expand

        # Left part of the header (badge and instrument text)
        left_header = ttk.Frame(header_container, style='TFrame')
        left_header.grid(row=0, column=0, sticky='w')

        if measurement_type:
            badge_font = font.Font(family='Segoe UI', size=8, weight='bold')
            # Maroon badge: distinct from the lighter button family tints
            badge = tk.Label(
                left_header,
                text=measurement_type.upper(),
                bg=self.CLR_ACCENT_GOLD,
                fg=self.CLR_TEXT_DARK,
                font=badge_font,
                padx=6,
                pady=2)
            badge.pack(side='left', anchor='w')

        if instruments_text:
            instrument_label = ttk.Label(
                left_header,
                text=instruments_text,
                font=self.FONT_INFO,
                wraplength=200,
                justify='left')
            instrument_label.pack(
                side='left', anchor='w', padx=(
                    8, 0), fill='x', expand=True)

        # --- Folder Icon Button (moved to the top right) ---
        if buttons:
            first_script_key = buttons[0][1]
            folder_button = ttk.Button(
                header_container,
                text="📁",
                style='Icon.TButton',
                width=3,
                command=lambda: self.open_script_folder(first_script_key))
            folder_button.grid(row=0, column=1, sticky='e')

        # --- Experimental badge on its own row so it never widens the
        # header (uniform columns would push the whole module grid right) ---
        row_idx = 1
        if experimental:
            exp_font = font.Font(family='Segoe UI', size=8, weight='bold')
            exp_badge = tk.Label(
                frame,
                text="UNDER TEST · EXPERIMENTAL",
                bg='#8B3A2F',
                fg='#FFFFFF',
                font=exp_font,
                padx=6,
                pady=2)
            exp_badge.grid(row=row_idx, column=0, sticky='w', pady=(0, 4))
            row_idx += 1

        # --- Launch Buttons ---
        # Each entry is (label, script_key) or (label, script_key, family),
        # where family is 'sensing' | 'control' | 'master' for tinting.
        for i, entry in enumerate(buttons):
            btn_text, script_key = entry[0], entry[1]
            family = entry[2] if len(entry) > 2 else None
            self._create_launch_button(
                frame,
                btn_text,
                script_key,
                family).grid(
                row=row_idx + i,
                column=0,
                sticky='ew',
                pady=(
                    0,
                    2))

    def log(self, message):
        """Logs a message to the console widget with a timestamp."""
        if self.console_widget:
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {message}\n"
            self.console_widget.config(state='normal')
            self.console_widget.insert('end', log_entry)
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')

    def _open_path(self, path):
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Path not found: {abs_path}")
            messagebox.showwarning(
                "Path Not Found",
                f"The specified path does not exist:\n\n{abs_path}")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(abs_path)
            elif platform.system() == "Darwin":
                subprocess.run(['open', abs_path], check=True)
            else:
                subprocess.run(['xdg-open', abs_path], check=True)
        except Exception as e:
            messagebox.showerror(
                "Error", f"Could not open path: {path}\n\nError: {e}")

    def _parse_markdown(self, content):
        """
        Parses markdown content into a list of (text, tags) for rendering.
        """
        return content.split('\n')

    def _show_file_in_window(self, file_path, title):
        lines = self._get_file_content_for_display(file_path)
        if lines is None:
            return

        win = self._create_display_window(title)
        text_area = self._configure_text_area(win)

        is_markdown = file_path.lower().endswith('.md')
        if is_markdown:
            self._render_markdown_content(text_area, lines)
        else:
            text_area.insert('1.0', "\n".join(lines))

        text_area.pack(expand=True, fill='both')
        text_area.config(state='disabled')
        ttk.Button(
            win,
            text="Close",
            style='App.TButton',
            command=win.destroy).pack(
            pady=10,
            padx=10,
            fill='x')

    def open_script_folder(self, script_key):
        """Opens the directory containing the script associated with the given key."""
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path:
            self.log(
                f"ERROR: Script key '{script_key}' not found in SCRIPT_PATHS.")
            messagebox.showwarning(
                "Key Not Found",
                f"The script key '{script_key}' is not defined.")
            return

        folder_path = os.path.dirname(os.path.abspath(script_path))
        if os.path.exists(folder_path):
            self._open_path(folder_path)
        else:
            self.log(f"ERROR: Folder path does not exist: {folder_path}")
            messagebox.showwarning(
                "Path Not Found",
                f"The folder for '{script_key}' could not be found.")

    def open_readme(self):
        self._show_file_in_window(self.README_FILE, "README")

    def open_updates(self):
        self._show_file_in_window(self.UPDATES_FILE, "Change Log")

    def open_manual_file(self):
        self._show_file_in_window(self.MANUAL_FILE, "User Manual")

    def open_license(self):
        self._show_file_in_window(self.LICENSE_FILE, "MIT License")

    def _create_tool_row(self, parent, text, script_key):
        """Helper to create one launch button in the Tools pop-up."""
        ttk.Button(
            parent,
            text=text,
            style='App.TButton',
            command=lambda: self.launch_script(self.SCRIPT_PATHS[script_key])
        ).pack(fill='x', pady=(0, 8))

    def open_tools_popup(self):
        """Creates a pop-up window listing the standalone utility scripts."""
        tools_win = Toplevel(self.root)
        tools_win.title("Additional Tools")
        tools_win.configure(bg=self.CLR_BG_DARK)
        tools_win.transient(self.root)
        tools_win.resizable(False, False)
        tools_win.grab_set()

        # --- Header ---
        header = ttk.Frame(tools_win, style='ToolsPopupHeader.TFrame', padding=(24, 18, 24, 12))
        header.pack(fill='x')
        ttk.Label(
            header,
            text="Additional Tools",
            style='ToolsPopupTitle.TLabel'
        ).pack(anchor='w')
        ttk.Label(
            header,
            text="Standalone utilities bundled with PICA",
            style='ToolsPopupSubtitle.TLabel'
        ).pack(anchor='w', pady=(2, 0))

        # --- Card body ---
        card = ttk.Frame(tools_win, style='ToolsPopup.TFrame', padding=(20, 16, 20, 16))
        card.pack(fill='both', expand=True, padx=24, pady=(0, 16))
        card.grid_columnconfigure((0, 1), weight=1, uniform="tools")

        tool_groups = [
            ("Communication", [
                ("GPIB Scanner", "GPIB Scanner"),
                ("GPIB Scanner (32-bit)", "GPIB Scanner (32-bit)"),
                ("SCPI Console", "SCPI Console"),
            ]),
            ("PPMS Utilities", [
                ("PPMS Plotter Utility", "PPMS Plotter Utility"),
                ("Sequence Visualizer", "Sequence Visualizer"),
                ("PPMS Time Estimator", "PPMS Time Estimator"),
                ("MD Ratio Calculator", "MD Ratio Calculator"),
            ]),
            ("Mini Tools", [
                ("Quick Calc", "Quick Calc"),
                ("Time Utility", "Time Utility"),
                ("Unit Converter", "Unit Converter"),
            ]),
            ("P-E Measurement", [
                ("PE Plotter", "PE Plotter"),
            ]),
        ]
        for i, (group_title, tools) in enumerate(tool_groups):
            group_frame = ttk.LabelFrame(
                card, text=group_title, style='TLabelframe')
            group_frame.grid(
                row=i // 2,
                column=i % 2,
                sticky='new',
                padx=6,
                pady=6)
            for label, key in tools:
                self._create_tool_row(group_frame, label, key)

        # --- Footer ---
        footer = ttk.Frame(tools_win, style='ToolsPopupHeader.TFrame', padding=(24, 0, 24, 20))
        footer.pack(fill='x')
        ttk.Button(
            footer,
            text="Close",
            style='App.TButton',
            command=tools_win.destroy
        ).pack(fill='x')

        # Center the pop-up over the main window once its final size is known
        tools_win.update_idletasks()
        w, h = tools_win.winfo_width(), tools_win.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (w // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (h // 2)
        tools_win.geometry(f"+{x}+{y}")

    def open_repo(self, event=None):
        import webbrowser
        webbrowser.open(self.REPO_URL)

    def launch_script(self, script_path):
        self.log(f"Launching: {os.path.basename(script_path)}")
        abs_path = os.path.abspath(script_path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Script not found at {abs_path}")
            messagebox.showerror(
                "File Not Found",
                f"Script not found:\n\n{abs_path}")
            return
        try:
            proc = Process(target=run_script_process, args=(abs_path,))
            proc.start()
            self.log(
                f"Successfully launched '{os.path.basename(script_path)}' in a new process.")
        except Exception as e:
            self.log(f"ERROR: Failed to launch script. Reason: {e}")
            messagebox.showerror(
                "Launch Error",
                f"An error occurred while launching the script:\n\n{e}")

    def run_gpib_test(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: GPIB test failed, PyVISA is not available.")
            messagebox.showerror(
                "Dependency Missing",
                "The 'pyvisa' library is required.\n\nInstall via pip:\npip install pyvisa pyvisa-py")
            return
        launch_gpib_scanner()

    def _auto_launch_gpib(self):
        """Automatically opens the VISA/GPIB Utils module shortly after startup."""
        self.log("Auto-launching VISA/GPIB Utils...")
        self.run_gpib_test()

    def _pre_cache_markdown_files(self):
        """
        Reads and parses key markdown/text files in the background.
        """
        files_to_cache = [
            (self.README_FILE, "README"),
            (self.UPDATES_FILE, "Change Log"),
            (self.LICENSE_FILE, "License")
        ]
        for file_path, name in files_to_cache:
            if file_path not in self._md_cache and os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if file_path.lower().endswith('.md'):
                        self._md_cache[file_path] = content.split('\n')
                    else:
                        self._md_cache[file_path] = content.split(
                            '\n')  # Cache as list of lines
                    self.log(f"Pre-cached '{name}' for faster access.")
                except Exception as e:
                    self.log(f"Warning: Could not pre-cache '{name}'. {e}")

    def _get_file_content_for_display(self, file_path):
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: File not found: {abs_path}")
            messagebox.showerror(
                "File Not Found",
                f"The specified file does not exist:\n\n{abs_path}")
            return None
        try:
            if file_path in self._md_cache:
                return self._md_cache[file_path]
            else:
                with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                lines = self._parse_markdown(content)
                if file_path.lower().endswith('.md'):
                    self._md_cache[file_path] = lines
                return lines
        except Exception as e:
            messagebox.showerror(
                "Error Reading File",
                f"Could not read the file:\n\n{e}")
            return None

    def _create_display_window(self, title):
        win = Toplevel(self.root)
        win.title(title)
        win.geometry("800x600")
        win.configure(bg=self.CLR_BG_DARK)
        win.transient(self.root)
        win.grab_set()
        return win

    def _configure_text_area(self, win):
        text_area = scrolledtext.ScrolledText(
            win,
            wrap='word',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_TEXT,
            font=self.FONT_BASE,
            bd=0,
            padx=15,
            pady=10)
        text_area.tag_configure(
            "h1",
            font=(
                'Segoe UI',
                20,
                'bold'),
            foreground=self.CLR_ACCENT_GOLD,
            spacing3=15)
        text_area.tag_configure(
            "h3",
            font=(
                'Segoe UI',
                13,
                'bold'),
            foreground=self.CLR_TEXT,
            spacing3=10)
        text_area.tag_configure("p", spacing3=8)
        text_area.tag_configure(
            "list_l1",
            lmargin1=25,
            lmargin2=40,
            spacing3=4)
        text_area.tag_configure(
            "bold",
            font=(
                'Segoe UI',
                self.FONT_SIZE_BASE,
                'bold'))
        text_area.tag_configure(
            "hr",
            justify='center',
            spacing1=15,
            spacing3=15,
            foreground=self.CLR_FRAME_BG)
        return text_area

    def _render_markdown_content(self, text_area, lines):
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('### '):
                text_area.insert('end', f"{stripped[4:]}\n", "h3")
            elif stripped.startswith('# '):
                text_area.insert('end', f"{stripped[2:]}\n", "h1")
            elif stripped.startswith('* '):
                line_content = stripped[2:]
                parts = re.split(r'(\*\*.*?\*\*)', line_content)
                text_area.insert('end', "• ", "list_l1")
                for part in parts:
                    if part.startswith('**') and part.endswith('**'):
                        text_area.insert('end', part[2:-2], ("list_l1", "bold"))
                    else:
                        text_area.insert('end', part, "list_l1")
                text_area.insert('end', '\n')
            elif stripped in ('---', '***', '___'):
                text_area.insert('end', f"{'-'*120}\n", "hr")
            else:
                parts = re.split(r'(\*\*.*?\*\*)', line)
                for part in parts:
                    if part.startswith('**') and part.endswith('**'):
                        text_area.insert('end', part[2:-2], ("p", "bold"))
                    else:
                        text_area.insert('end', part, "p")
                text_area.insert('end', '\n')


def main():
    """Initializes and runs the main application."""
    root = tk.Tk()
    PICALauncherApp(root)
    root.mainloop()


if __name__ == '__main__':
    # This is ESSENTIAL for multiprocessing to work in a bundled executable
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()

    main()