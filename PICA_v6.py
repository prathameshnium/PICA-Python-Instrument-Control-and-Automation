# BUILD VERSION: 13.3 (Active/Passive R-T Launchers)
'''
===============================================================================
 PROGRAM:      PICA Launcher

 PURPOSE:      A graphical dashboard for launching PICA measurement scripts.

 DESCRIPTION:  This application serves as the central graphical user interface (GUI)
               for the Python Instrument Control & Automation (PICA) suite. It
               provides a styled, user-friendly dashboard to launch various
               instrument automation scripts in isolated, stable processes using
               Python's multiprocessing library. Key features include a utility
               to test GPIB/VISA instrument connections, viewers for project
               documentation (README, LICENSE), quick access to script folders
               and instrument manuals, and a real-time logging console.

 AUTHOR:       Prathamesh K Deshmukh
 GUIDED BY:    Dr. Sudip Mukherjee
 INSTITUTE:    UGC-DAE Consortium for Scientific Research, Mumbai Centre
 
 VERSION HISTORY:
   13.3 (05/10/2025): Added distinct launchers for Active and Passive R-T modes.
   13.2 (05/10/2025): Integrated new K2400/2182 frontend GUIs.
   13.1 (04/10/2025): Resolved duplicate script paths and validated Delta Mode scripts.
 
===============================================================================
'''
import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, Text, Canvas, scrolledtext, font
import os, sys, subprocess, platform, threading, queue, re
from datetime import datetime
from html import unescape
import runpy
import multiprocessing
from multiprocessing import Process

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

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

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class PICALauncherApp:
    
    PROGRAM_VERSION = "5.3"
    CLR_BG_DARK = '#2B3D4F'
    CLR_FRAME_BG = '#3A506B'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_TEXT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_LINK = '#87CEEB' # Sky Blue, for better contrast
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE)
    FONT_INFO_ITALIC = ('Segoe UI', FONT_SIZE_BASE, 'italic')
    LOGO_FILE = resource_path("_assets/LOGO/UGC_DAE_CSR.jpeg")
    MANUAL_FILE = resource_path("_assets/Manuals")
    README_FILE = resource_path("PICA_README.md")
    LICENSE_FILE = resource_path("LICENSE")
    UPDATES_FILE = resource_path("Updates.md")
    LOGO_SIZE = 140

    SCRIPT_PATHS = {
        # Based on Updates.md, using the latest versions of scripts.
        "Delta Mode I-V Sweep": resource_path("Delta_mode_Keithley_6221_2182/IV_K6221_DC_Sweep_Frontend_V9.py"), # Correct from context
        "Delta Mode R-T": resource_path("Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_T_Control_Frontend_v4.py"),
        "Delta Mode R-T (T_Sensing)": resource_path("Delta_mode_Keithley_6221_2182/Delta_RT_K6221_K2182_L350_Sensing_Frontend_v3.py"),
        "K2400 I-V": resource_path("Keithley_2400/IV_K2400_Frontend_v4.py"),
        "K2400 R-T": resource_path("Keithley_2400/RT_K2400_L350_T_Control_Frontendv2.py"),
        "K2400 R-T (T_Sensing)": resource_path("Keithley_2400/RT_K2400_L350_T_Sensing_Frontend_v3.py"),
        "K2400_2182 I-V": resource_path("Keithley_2400_Keithley_2182/IV_K2400_K2182_Frontend_v2.py"),
        "K2400_2182 R-T": resource_path("Keithley_2400_Keithley_2182/RT_K2400_K2182_T_Control_Frontend_v2.py"),
        "K2400_2182 R-T (T_Sensing)": resource_path("Keithley_2400_Keithley_2182/RT_K2400_2182_L350_T_Sensing_Frontend_v1.py"),
        "K6517B I-V": resource_path("Keithley_6517B/High_Resistance/IV_K6517B_Frontend_v10.py"),
        "K6517B R-T": resource_path("Keithley_6517B/High_Resistance/6517B_high_resistance_lakeshore_RT_Frontend_V11p2_5Always.py"),
        "K6517B R-T (T_Sensing)": resource_path("Keithley_6517B/High_Resistance/RT_K6517B_L350_T_Sensing_Frontend_v13.py"),
        "Pyroelectric Current": resource_path("Keithley_6517B/Pyroelectricity/Pyroelectric_K6517B_L350_Frontend_v3.py"),
        "Lakeshore Temp Control": resource_path("Lakeshore_350_340/lakeshore350_temp_ramp_Frontend_V6.py"),
        "Lakeshore Temp Monitor": resource_path("Lakeshore_350_340/T_Sensing_L350_Frontend_v3.py"),
        "LCR C-V Measurement": resource_path("LCR_Keysight_E4980A/CV_KE4980A_Frontend_v2.py"),
        "Lock-in AC Measurement": resource_path("Lock_In_Amplifier/AC_Measurement_Frontend_V1.py"), # This file does not exist in the provided context, but the path is what the launcher expects.
        "PICA Help": resource_path("PICA_README.md"),
    }

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher v{self.PROGRAM_VERSION}")
        self.root.state('zoomed') # Launch in maximized/fullscreen state
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 780)
        self.logo_image = None
        self.console_widget = None
        self.setup_styles()
        self.create_widgets()
        self.log(f"PICA Launcher v{self.PROGRAM_VERSION} initialized.")
        self.log(f"PIL/Pillow (logo): {'Available' if PIL_AVAILABLE else 'Not found'}")
        self.log(f"PyVISA (GPIB test): {'Available' if PYVISA_AVAILABLE else 'Not found'}")
        self.log("Welcome to PICA. Check connections and run a GPIB test before starting.")
        
        # Auto-launch GPIB scanner after 1 second
        self.root.after(1000, self.run_gpib_test)
    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('TSeparator', background=self.CLR_FRAME_BG)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_BG_DARK, borderwidth=2, padding=12)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 8), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('App.TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Scan.TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_TEXT_DARK, background=self.CLR_ACCENT_GREEN)
        style.map('Scan.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Icon.TButton', font=('Segoe UI', 12), padding=(5, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG, borderwidth=0)
        style.map('Icon.TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure("Vertical.TScrollbar", troughcolor=self.CLR_BG_DARK, background=self.CLR_FRAME_BG, arrowcolor=self.CLR_ACCENT_GOLD, bordercolor=self.CLR_BG_DARK)
        style.map("Vertical.TScrollbar", background=[('active', self.CLR_ACCENT_GOLD)])

    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=0, minsize=380)
        self.root.grid_columnconfigure(1, weight=1)
        info_panel = self.create_resource_panel(self.root)
        info_panel.grid(row=0, column=0, sticky="nsew", padx=(15, 10), pady=15)
        launcher_container = self.create_launcher_panel(self.root)
        launcher_container.grid(row=0, column=1, sticky="nsew", padx=(10, 15), pady=15)

    def create_resource_panel(self, parent):
        info_frame = ttk.Frame(parent)
        info_frame.configure(padding=20)
        logo_canvas = Canvas(info_frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.pack(pady=(0, 15))
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(self.LOGO_FILE)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
        
        ttk.Label(info_frame, text="UGC-DAE Consortium for Scientific Research, Mumbai Centre", font=self.FONT_SUBTITLE, justify='center', anchor='center').pack(pady=(0, 15))
        
        ttk.Label(info_frame, text="PICA: Python Instrument\nControl & Automation", font=self.FONT_TITLE, foreground=self.CLR_ACCENT_GOLD, justify='center', anchor='center').pack(pady=(0, 15))
        
        desc_text = "A modular software suite for automating laboratory measurements in physics research."
        ttk.Label(info_frame, text=desc_text, font=self.FONT_INFO, wraplength=360, justify='center', anchor='center').pack(pady=(0, 10))
        
        # --- Create a bold font for names ---
        bold_font = font.Font(family='Segoe UI', size=self.FONT_SIZE_BASE, weight='bold')
        
        ttk.Label(info_frame, text="Developed by Prathamesh Deshmukh", font=bold_font, justify='center', anchor='center').pack(pady=(5, 0))
        ttk.Label(info_frame, text="Vision & Guidance by Dr. Sudip Mukherjee", font=bold_font, justify='center', anchor='center').pack(pady=(0, 15))
        
        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=10)
        util_frame = ttk.Frame(info_frame); util_frame.pack(fill='x', expand=False, pady=5)
        # --- Make the README button bigger by spanning two columns ---
        util_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        
        ttk.Button(util_frame, text="GPIB Utils", style='App.TButton', command=self.run_gpib_test).grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(util_frame, text="README", style='App.TButton', command=self.open_readme).grid(row=0, column=1, columnspan=2, sticky='ew', padx=4)
        ttk.Button(util_frame, text="Manuals", style='App.TButton', command=self.open_manual_folder).grid(row=0, column=3, sticky='ew', padx=(4, 0))

        
        bottom_frame = ttk.Frame(info_frame)
        bottom_frame.pack(side='bottom', fill='x', pady=(15, 0))
        
        license_font = font.Font(family='Segoe UI', size=9, underline=True)
        license_label = ttk.Label(bottom_frame, text="This project is licensed under the MIT License.",
                                  font=license_font, foreground=self.CLR_LINK, cursor="hand2")
        license_label.pack()
        license_label.bind("<Button-1>", lambda e: self.open_license())
        
        console_container = ttk.LabelFrame(info_frame, text="Console", padding=(5,10))
        console_container.pack(side='bottom', fill='x', pady=(20, 0))
        self.console_widget = scrolledtext.ScrolledText(console_container, state='disabled', bg=self.CLR_CONSOLE_BG,
                                                      fg=self.CLR_TEXT, font=self.FONT_CONSOLE,
                                                      wrap='word', bd=0, relief='flat', height=7)
        self.console_widget.pack(fill='both', expand=True)
        return info_frame

    def _create_launch_button(self, parent, text, script_key):
        return ttk.Button(parent, text=text, style='App.TButton',
                          command=lambda: self.launch_script(self.SCRIPT_PATHS[script_key]))

    def create_launcher_panel(self, parent):
        main_container = ttk.Frame(parent)
        main_container.grid_rowconfigure(0, weight=1)
        main_container.grid_columnconfigure(0, weight=1)
        
        # --- Container for the two columns, no scrolling needed ---
        button_container = ttk.Frame(main_container)
        button_container.grid(row=0, column=0, sticky="nsew", padx=15, pady=10)
        button_container.grid_columnconfigure((0, 1), weight=1)
        
        left_col = ttk.Frame(button_container); left_col.grid(row=0, column=0, sticky='new', padx=(0, 10))
        right_col = ttk.Frame(button_container); right_col.grid(row=0, column=1, sticky='new', padx=(10, 0))
        
        GROUP_PAD_Y = 15

        # --- Low Resistance ---
        low_res_frame = ttk.LabelFrame(left_col, text='Low Resistance (10⁻⁹ Ω to 10⁸ Ω)'); low_res_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        low_res_frame.columnconfigure(0, weight=1); ttk.Label(low_res_frame, text="Instruments: Keithley 6221/2182, Lakeshore 350", font=self.FONT_INFO).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        self._create_launch_button(low_res_frame, "I-V Sweep", "Delta Mode I-V Sweep").grid(row=1, column=0, sticky='ew', pady=(0, 4), padx=(0, 4));
        self._create_launch_button(low_res_frame, "R vs. T (T_Control)", "Delta Mode R-T").grid(row=2, column=0, sticky='ew', pady=(0, 4), padx=(0, 4));
        self._create_launch_button(low_res_frame, "R vs. T (T_Sensing)", "Delta Mode R-T (T_Sensing)").grid(row=3, column=0, sticky='ew', pady=(0, 4), padx=(0, 4)); ttk.Button(low_res_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Delta Mode I-V Sweep")).grid(row=1, column=1, rowspan=3, sticky='ns')
        
        # --- Mid Resistance (K2400) ---
        mid_res_frame1 = ttk.LabelFrame(left_col, text='Mid Resistance (10⁻³ Ω to 10⁹ Ω)'); mid_res_frame1.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        mid_res_frame1.columnconfigure(0, weight=1); ttk.Label(mid_res_frame1, text="Instruments: Keithley 2400, Lakeshore 350", font=self.FONT_INFO).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        self._create_launch_button(mid_res_frame1, "I-V Sweep", "K2400 I-V").grid(row=1, column=0, sticky='ew', pady=(0, 4), padx=(0, 4))
        self._create_launch_button(mid_res_frame1, "R vs. T (T_Control)", "K2400 R-T").grid(row=2, column=0, sticky='ew', pady=(0, 4), padx=(0, 4))
        self._create_launch_button(mid_res_frame1, "R vs. T (T_Sensing)", "K2400 R-T (T_Sensing)").grid(row=3, column=0, sticky='ew', pady=(0, 4), padx=(0, 4))
        ttk.Button(mid_res_frame1, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("K2400 I-V")).grid(row=1, column=1, rowspan=3, sticky='ns')
        
        # --- Mid Resistance (K2400/2182) ---
        mid_res_frame2 = ttk.LabelFrame(left_col, text='Mid Resistance, High Precision (10⁻⁶ Ω to 10⁹ Ω)'); mid_res_frame2.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        mid_res_frame2.columnconfigure(0, weight=1); ttk.Label(mid_res_frame2, text="Instruments: Keithley 2400/2182, Lakeshore 350", font=self.FONT_INFO).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        self._create_launch_button(mid_res_frame2, "I-V Sweep", "K2400_2182 I-V").grid(row=1, column=0, sticky='ew', pady=(0, 4), padx=(0, 4))
        self._create_launch_button(mid_res_frame2, "R vs. T (T_Control)", "K2400_2182 R-T").grid(row=2, column=0, sticky='ew', pady=(0, 4), padx=(0, 4))
        self._create_launch_button(mid_res_frame2, "R vs. T (T_Sensing)", "K2400_2182 R-T (T_Sensing)").grid(row=3, column=0, sticky='ew', pady=(0, 4), padx=(0, 4))
        ttk.Button(mid_res_frame2, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("K2400_2182 I-V")).grid(row=1, column=1, rowspan=3, sticky='ns')
        
        # --- High Resistance (moved to right column) ---
        high_res_frame = ttk.LabelFrame(right_col, text='High Resistance (10³ Ω to 10¹⁶ Ω)'); high_res_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        high_res_frame.columnconfigure(0, weight=1); ttk.Label(high_res_frame, text="Instruments: Keithley 6517B, Lakeshore 350", font=self.FONT_INFO).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        self._create_launch_button(high_res_frame, "I-V Sweep", "K6517B I-V").grid(row=1, column=0, sticky='ew', padx=(0, 4), pady=(0,4))
        self._create_launch_button(high_res_frame, "R vs. T (T_Control)", "K6517B R-T").grid(row=2, column=0, sticky='ew', padx=(0, 4), pady=(0,4))
        self._create_launch_button(high_res_frame, "R vs. T (T_Sensing)", "K6517B R-T (T_Sensing)").grid(row=3, column=0, sticky='ew', padx=(0, 4), pady=(0,4)); ttk.Button(high_res_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("K6517B I-V")).grid(row=1, column=1, rowspan=3, sticky='ns')
        
        # --- Other Utilities (right column) ---
        pyro_frame = ttk.LabelFrame(right_col, text='Pyroelectric Measurement (Keithley 6517B)'); pyro_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        pyro_frame.columnconfigure(0, weight=1)
        self._create_launch_button(pyro_frame, "PyroCurrent vs. T", "Pyroelectric Current").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(pyro_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Pyroelectric Current")).grid(row=0, column=1, sticky='ns')
        
        lakeshore_frame = ttk.LabelFrame(right_col, text='Temperature Utilities (Lakeshore 350)'); lakeshore_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        lakeshore_frame.columnconfigure(0, weight=1)
        self._create_launch_button(lakeshore_frame, "Temperature Ramp", "Lakeshore Temp Control").grid(row=0, column=0, sticky='ew', padx=(0, 4), pady=(0, 4))
        self._create_launch_button(lakeshore_frame, "Temperature Monitor", "Lakeshore Temp Monitor").grid(row=1, column=0, sticky='ew', padx=(0, 4), pady=(0, 4))
        ttk.Button(lakeshore_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Lakeshore Temp Control")).grid(row=0, column=1, rowspan=2, sticky='ns')
        
        lcr_frame = ttk.LabelFrame(right_col, text='Capacitance (Keysight E4980A)'); lcr_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        lcr_frame.columnconfigure(0, weight=1)
        self._create_launch_button(lcr_frame, "C-V Measurement", "LCR C-V Measurement").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(lcr_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("LCR C-V Measurement")).grid(row=0, column=1, sticky='ns')
        
        lockin_frame = ttk.LabelFrame(right_col, text='AC Measurements (Lock-in)'); lockin_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        lockin_frame.columnconfigure(0, weight=1)
        self._create_launch_button(lockin_frame, "AC Measurement", "Lock-in AC Measurement").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(lockin_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Lock-in AC Measurement")).grid(row=0, column=1, sticky='ns')

        return main_container

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
            messagebox.showwarning("Path Not Found", f"The specified path does not exist:\n\n{abs_path}")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(abs_path)
            elif platform.system() == "Darwin":
                subprocess.run(['open', abs_path], check=True)
            else:
                subprocess.run(['xdg-open', abs_path], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open path: {path}\n\nError: {e}")

    # =========================================================================
    # === THIS FUNCTION IS NOW UPDATED WITH THE MARKDOWN PARSER ================
    # =========================================================================
    def _show_file_in_window(self, file_path, title):
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: File not found: {abs_path}")
            messagebox.showerror("File Not Found", f"The specified file does not exist:\n\n{abs_path}")
            return
        try:
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Error Reading File", f"Could not read the file:\n\n{e}")
            return
            
        win = Toplevel(self.root)
        win.title(title)
        win.geometry("800x600")
        win.configure(bg=self.CLR_BG_DARK)
        win.transient(self.root)
        win.grab_set()
        
        text_area = scrolledtext.ScrolledText(win, wrap='word', bg=self.CLR_CONSOLE_BG, fg=self.CLR_TEXT, font=self.FONT_BASE, bd=0, padx=15, pady=10)
        
        # --- Define styles for rendering ---
        text_area.tag_configure("h1", font=('Segoe UI', 20, 'bold'), foreground=self.CLR_ACCENT_GOLD, spacing3=15)
        text_area.tag_configure("h3", font=('Segoe UI', 13, 'bold'), foreground=self.CLR_TEXT, spacing3=10)
        text_area.tag_configure("p", spacing3=8)
        text_area.tag_configure("list_l1", lmargin1=25, lmargin2=40, spacing3=4)
        text_area.tag_configure("bold", font=('Segoe UI', self.FONT_SIZE_BASE, 'bold'))
        text_area.tag_configure("hr", justify='center', spacing1=15, spacing3=15, foreground=self.CLR_FRAME_BG)
        
        is_markdown = file_path.lower().endswith('.md')
        
        if is_markdown:
            # Simple parser to apply styles line by line
            for line in content.split('\n'):
                stripped = line.strip()
                if stripped.startswith('### '):
                    text_area.insert('end', f"{stripped[4:]}\n", "h3")
                elif stripped.startswith('# '):
                    text_area.insert('end', f"{stripped[2:]}\n", "h1")
                elif stripped.startswith('* '):
                    # Apply bold tags within list items
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
                    text_area.insert('end', f"{'─'*120}\n", "hr")
                else:
                    # Apply bold tags within paragraphs
                    parts = re.split(r'(\*\*.*?\*\*)', line)
                    for part in parts:
                        if part.startswith('**') and part.endswith('**'):
                            text_area.insert('end', part[2:-2], ("p", "bold"))
                        else:
                            text_area.insert('end', part, "p")
                    text_area.insert('end', '\n')
        else: # For non-markdown files like LICENSE
            text_area.insert('1.0', content)
            
        text_area.pack(expand=True, fill='both')
        text_area.config(state='disabled')
        ttk.Button(win, text="Close", style='App.TButton', command=win.destroy).pack(pady=10, padx=10, fill='x')

    def open_script_folder(self, script_key):
        """Opens the directory containing the script associated with the given key."""
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path:
            self.log(f"ERROR: Script key '{script_key}' not found in SCRIPT_PATHS.")
            messagebox.showwarning("Key Not Found", f"The script key '{script_key}' is not defined.")
            return

        folder_path = os.path.dirname(os.path.abspath(script_path))
        if os.path.exists(folder_path):
            self._open_path(folder_path)
        else:
            self.log(f"ERROR: Folder path does not exist: {folder_path}")
            messagebox.showwarning("Path Not Found", f"The folder for '{script_key}' could not be found.")
    def open_readme(self):
        self._show_file_in_window(self.README_FILE, "README")

    def open_updates(self):
        self._show_file_in_window(self.UPDATES_FILE, "Change Log")

    def open_manual_folder(self):
        self._open_path(self.MANUAL_FILE)

    def open_license(self):
        self._show_file_in_window(self.LICENSE_FILE, "MIT License")

    def launch_script(self, script_path):
        self.log(f"Launching: {os.path.basename(script_path)}")
        abs_path = os.path.abspath(script_path)
        if not os.path.exists(abs_path):
            self.log(f"ERROR: Script not found at {abs_path}")
            messagebox.showerror("File Not Found", f"Script not found:\n\n{abs_path}")
            return
        try:
            proc = Process(target=run_script_process, args=(abs_path,))
            proc.start()
            self.log(f"Successfully launched '{os.path.basename(script_path)}' in a new process.")
        except Exception as e:
            self.log(f"ERROR: Failed to launch script. Reason: {e}")
            messagebox.showerror("Launch Error", f"An error occurred while launching the script:\n\n{e}")

    def run_gpib_test(self):
        if not PYVISA_AVAILABLE:
            self.log("ERROR: GPIB test failed, PyVISA is not available.")
            messagebox.showerror("Dependency Missing", "The 'pyvisa' library is required.\n\nInstall via pip:\npip install pyvisa pyvisa-py")
            return
        test_win = Toplevel(self.root)
        test_win.title("GPIB/VISA Instrument Scanner")

        # --- Position the window to the top-right of the screen ---
        win_width = 500
        win_height = 400
        test_win.update_idletasks() # Ensure winfo methods work correctly
        screen_width = test_win.winfo_screenwidth()
        x_pos = screen_width - win_width - 50  # Position on the right with padding
        y_pos = 50                             # Position from the top
        test_win.geometry(f"{win_width}x{win_height}+{x_pos}+{y_pos}")

        test_win.configure(bg=self.CLR_BG_DARK)
        test_win.minsize(500, 350)
        test_win.transient(self.root)
        # test_win.grab_set() # Removed to allow interaction with the main window
        result_queue = queue.Queue()
        main_frame = ttk.Frame(test_win, padding=15)
        main_frame.pack(fill='both', expand=True)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        controls_frame = ttk.Frame(main_frame)
        controls_frame.grid(row=0, column=0, sticky='ew', pady=(0, 15))
        controls_frame.columnconfigure((0, 1, 2), weight=1) # Updated for three columns

        console_area = scrolledtext.ScrolledText(main_frame, state='disabled', bg=self.CLR_CONSOLE_BG,
                                               fg=self.CLR_TEXT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        console_area.grid(row=1, column=0, sticky='nsew')
        def log_to_scanner(message, add_timestamp=True):
            console_area.config(state='normal')
            if add_timestamp:
                timestamp = datetime.now().strftime("%H:%M:%S")
                console_area.insert('end', f"[{timestamp}] {message}\n")
            else:
                console_area.insert('end', message)
            console_area.see('end')
            console_area.config(state='disabled')
        def _gpib_scan_worker():
            try:
                rm = pyvisa.ResourceManager()
                resources = rm.list_resources()
                if not resources:
                    result_queue.put("-> No instruments found. Check connections and retry.\n")
                else:
                    result_queue.put(f"-> Found {len(resources)} instrument(s). Querying...\n\n")
                    for address in resources:
                        try:
                            with rm.open_resource(address) as instrument:
                                instrument.timeout = 2000
                                idn = instrument.query('*IDN?').strip()
                                result = f"Address: {address}\n    ID: {idn}\n\n"
                                result_queue.put(result)
                        except Exception as e:
                            result = f"Address: {address}\n    Error: Could not get ID. {e}\n\n"
                            result_queue.put(result)
            except Exception as e:
                error_msg = (f"A critical VISA error occurred: {e}\n"
                             "Please ensure a VISA backend (e.g., NI-VISA) is installed correctly.\n")
                result_queue.put(error_msg)
            finally:
                result_queue.put("SCAN_COMPLETE")
        def _process_gpib_queue():
            try:
                while True:
                    message = result_queue.get_nowait()
                    if message == "SCAN_COMPLETE":
                        scan_button.config(state='normal')
                        log_to_scanner("Scan complete.")
                    else:
                        log_to_scanner(message, add_timestamp=False)
            except queue.Empty:
                pass
            finally:
                test_win.after(100, _process_gpib_queue)
        def start_scan():
            scan_button.config(state='disabled')
            log_to_scanner("Starting scan...")
            threading.Thread(target=_gpib_scan_worker, daemon=True).start()
        def clear_log():
            console_area.config(state='normal')
            console_area.delete(1.0, 'end')
            console_area.config(state='disabled')
            log_to_scanner("Log cleared.")

        def show_address_guide():
            guide_text = """\n--- Common PICA Instrument GPIB Addresses ---\n
Temperature Controllers
  • Lakeshore 340:  12
  • Lakeshore 350: 15

Source-Measure Units (SMU) & Electrometers
  • Keithley 2400:      4
  • Keithley 6221:      13
  • Keithley 6517B:     27

Nanovoltmeters & LCR Meters
  • Keithley 2182: 
  • Keysight E4980A:    
  • SRS SR830 Lock-in:  8
  • SRS PS365 HV:  14

\n---------------------------------------------\n"""
            log_to_scanner(guide_text, add_timestamp=False)

        scan_button = ttk.Button(controls_frame, text="Scan Instruments", command=start_scan, style='Scan.TButton')
        scan_button.grid(row=0, column=0, padx=(0, 5), sticky='ew')
        guide_button = ttk.Button(controls_frame, text="Address Guide", command=show_address_guide, style='App.TButton')
        guide_button.grid(row=0, column=1, padx=5, sticky='ew') # This button shows a hardcoded guide
        clear_button = ttk.Button(controls_frame, text="Clear Log", command=clear_log, style='App.TButton')
        clear_button.grid(row=0, column=2, padx=(5, 0), sticky='ew')
        ttk.Button(main_frame, text="Close", style='App.TButton', command=test_win.destroy).grid(row=2, column=0, sticky='ew', pady=(15, 0))
        log_to_scanner("Welcome to the GPIB/VISA Instrument Scanner.")
        log_to_scanner("Auto-scanning for instruments in 1 second...")
        self.log("GPIB/VISA scanner window opened. Auto-scan will begin shortly.")
        test_win.after(100, _process_gpib_queue)  # Start the queue processor
        test_win.after(1000, start_scan)          # Auto-start the scan after 1 second

def main():
    """Initializes and runs the main application."""
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    # This is ESSENTIAL for multiprocessing to work in a bundled executable
    multiprocessing.freeze_support()
    main()