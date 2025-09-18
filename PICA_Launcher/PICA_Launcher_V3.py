# -------------------------------------------------------------------------------
# Name:           PICA Launcher - Python Instrument Control & Automation
# Purpose:        A central meta front end to launch various measurement GUIs.
# Author:         Prathamesh Deshmukh
# Created:        10/09/2025
# Version:        5.1 (Path Correction)
# Last Edit:      19/09/2025
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, Text, Canvas, scrolledtext, font
import os
import sys
import subprocess
import platform
from datetime import datetime
import threading
import queue

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# --- PyVISA for GPIB Test ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False


class PICALauncherApp:
    """The main GUI application for the PICA Launcher."""
    PROGRAM_VERSION = "5.1"

    # --- Color and Font Palette ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_FRAME_BG = '#3A506B'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_TEXT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_LINK = '#61AFEF'

    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE)

    # --- Asset and File Paths (Corrected with ../ to go up one directory) ---
    LOGO_FILE = "../assets/LOGO/UGC_DAE_CSR.jpeg"
    MANUAL_FILE = "../assets/Manuals"
    README_FILE = "../README.md"
    LICENSE_FILE = "../LICENSE"
    LOGO_SIZE = 140

    # --- Script Definitions (Corrected with ../ to go up one directory) ---
    SCRIPT_PATHS = {
        "Delta Mode I-V": "../Delta_mode/Delta_V7.py",
        "Delta Mode R-T": "../Delta_mode/Delta_Lakeshore_Front_end_V7.py",
        "K2400 I-V": "../Keithley_2400/Frontend_IV_2400_v3.py",
        "K2400 R-T": "../Keithley_2400/Frontend_Keithley_2400_350_V_vs_T_V1.py",
        "K2400_2182 I-V": "../Keithley_2400_Keithley_2182/IV_Sweep_Keithley_2182.py",
        "K2400_2182 R-T": "../Keithley_2400_Keithley_2182/VT_Curve_IV_Sweep_Keithley_2400_2182_Lakeshore_350.py",
        "K6517B I-V": "../Keithley_6517B/High_Resistance/Keithley_6517B_IV_Frontend_V7.py",
        "K6517B Resistivity": "../Keithley_6517B/High_Resistance/6517B_high_resistance_lakeshore_RT_Frontend_V7.py",
        "Pyroelectric Current": "../Keithley_6517B/Pyroelectric/Pyroelectric_Measurement_GUI_V3.py",
        "Lakeshore Temp Control": "../Lakeshore_350_340/lakeshore350_temp_ramp_v4_Frontend_v1.py",
        "LCR C-V Measurement": "../LCR_Keysight_E4980A/LCR_CV.py",
        "Lock-in AC Measurement": "../Lock_in_amplifier/AC_Transport_GUI.py",
    }
    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher v{self.PROGRAM_VERSION}")
        self.root.geometry("1250x820")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 780)

        self.logo_image = None
        self.console_widget = None

        self.setup_styles()
        self.create_widgets()

        self.log(f"PICA Launcher v{self.PROGRAM_VERSION} initialized.")
        self.log(f"PIL/Pillow (for logo): {'Available' if PIL_AVAILABLE else 'Not found'}")
        self.log(f"PyVISA (for GPIB test): {'Available' if PYVISA_AVAILABLE else 'Not found'}")

    def setup_styles(self):
        """Configures all ttk styles for the application."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('TSeparator', background=self.CLR_FRAME_BG)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_FRAME_BG, padding=12)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('App.TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Scan.TButton', font=self.FONT_BASE, padding=(10, 8), foreground=self.CLR_TEXT_DARK, background=self.CLR_ACCENT_GREEN)
        style.map('Scan.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Icon.TButton', font=('Segoe UI', 12), padding=(5, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG, borderwidth=0)
        style.map('Icon.TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure("Vertical.TScrollbar", troughcolor=self.CLR_BG_DARK, background=self.CLR_FRAME_BG, arrowcolor=self.CLR_ACCENT_GOLD, bordercolor=self.CLR_BG_DARK)
        style.map("Vertical.TScrollbar", background=[('active', self.CLR_ACCENT_GOLD)])

    def create_widgets(self):
        """Creates and places the main panels of the application."""
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=0, minsize=380)
        self.root.grid_columnconfigure(1, weight=1)

        info_panel = self.create_resource_panel(self.root)
        info_panel.grid(row=0, column=0, sticky="nsew", padx=(15, 10), pady=15)

        launcher_container = self.create_launcher_panel(self.root)
        launcher_container.grid(row=0, column=1, sticky="nsew", padx=(10, 15), pady=15)

    def create_resource_panel(self, parent):
        """Creates the left-side panel with info, utilities, and the console."""
        info_frame = ttk.Frame(parent)
        info_frame.configure(padding=20)

        logo_canvas = Canvas(info_frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.pack(pady=(0, 20))

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(self.LOGO_FILE).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception:
                logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill=self.CLR_TEXT, justify='center')
        else:
            logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill=self.CLR_TEXT, justify='center')

        ttk.Label(info_frame, text="PICA: Python Instrument\nControl & Automation", font=self.FONT_TITLE, justify='center', anchor='center').pack(pady=(0, 15))
        desc_text = "A suite of Python scripts for automating laboratory instruments for materials science and physics research."
        ttk.Label(info_frame, text=desc_text, font=self.FONT_INFO, wraplength=360, justify='center', anchor='center').pack(pady=(0, 20))
        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=25)
        util_frame = ttk.Frame(info_frame)
        util_frame.pack(fill='x', expand=False, pady=5)
        ttk.Button(util_frame, text="Open README", style='App.TButton', command=self.open_readme).pack(fill='x', pady=4)
        ttk.Button(util_frame, text="Open Instrument Manuals", style='App.TButton', command=self.open_manual_folder).pack(fill='x', pady=4)
        ttk.Button(util_frame, text="Test GPIB Connection", style='App.TButton', command=self.run_gpib_test).pack(fill='x', pady=4)
        bottom_frame = ttk.Frame(info_frame)
        bottom_frame.pack(side='bottom', pady=(20, 0))
        author_text = ("Developed by Prathamesh Deshmukh | Vision & Guidance by Dr. Sudip Mukherjee\n"
                       "UGC-DAE Consortium for Scientific Research, Mumbai Centre")
        ttk.Label(bottom_frame, text=author_text, font=('Segoe UI', 9), justify='center', anchor='center').pack(pady=(0,10))
        license_font = font.Font(family='Segoe UI', size=9, underline=True)
        license_label = ttk.Label(bottom_frame, text="This project is licensed under the MIT License.",
                                  font=license_font, foreground=self.CLR_LINK, cursor="hand2")
        license_label.pack()
        license_label.bind("<Button-1>", lambda e: self.open_license())
        console_container = ttk.LabelFrame(info_frame, text="Console", padding=(5,10))
        console_container.pack(side='bottom', fill='x', pady=(25, 0))

        self.console_widget = scrolledtext.ScrolledText(console_container, state='disabled', bg=self.CLR_CONSOLE_BG,
                                                       fg=self.CLR_TEXT, font=self.FONT_CONSOLE,
                                                       wrap='word', bd=0, relief='flat', height=7)
        self.console_widget.pack(fill='both', expand=True)

        return info_frame

    def _create_launch_button(self, parent, text, script_key):
        """Factory function to create a single, simple launch button."""
        return ttk.Button(parent, text=text, style='App.TButton',
                          command=lambda: self.launch_script(self.SCRIPT_PATHS[script_key]))

    def create_launcher_panel(self, parent):
        """Creates the right-side panel with the scrollable list of script launchers."""
        main_container = ttk.Frame(parent)
        main_container.grid_rowconfigure(0, weight=1)
        main_container.grid_columnconfigure(0, weight=1)

        button_container = ttk.Frame(main_container)
        button_container.grid(row=0, column=0, sticky="nsew")

        canvas = Canvas(button_container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(button_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame.grid_columnconfigure(0, weight=1, uniform="group1")
        scrollable_frame.grid_columnconfigure(1, weight=1, uniform="group1")
        left_col = ttk.Frame(scrollable_frame); left_col.grid(row=0, column=0, sticky='new', padx=(15, 8), pady=10)
        right_col = ttk.Frame(scrollable_frame); right_col.grid(row=0, column=1, sticky='new', padx=(8, 15), pady=10)

        GROUP_PAD_Y = 15

        # --- Low Resistance Group ---
        low_res_frame = ttk.LabelFrame(left_col, text='Low Resistance (Delta Mode:Keithley 6221/2182A)'); low_res_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        low_res_frame.columnconfigure(0, weight=1)
        self._create_launch_button(low_res_frame, "I-V Measurement", "Delta Mode I-V").grid(row=0, column=0, sticky='ew', pady=(0, 2), padx=(0, 4))
        self._create_launch_button(low_res_frame, "R vs. T Measurement", "Delta Mode R-T").grid(row=1, column=0, sticky='ew', pady=(2, 0), padx=(0, 4))
        ttk.Button(low_res_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Delta Mode I-V")).grid(row=0, column=1, rowspan=2, sticky='ns')

        # --- Mid Resistance (K2400) Group ---
        mid_res_frame1 = ttk.LabelFrame(left_col, text='Mid Resistance (Keithley 2400)'); mid_res_frame1.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        mid_res_frame1.columnconfigure(0, weight=1)
        self._create_launch_button(mid_res_frame1, "I-V Measurement", "K2400 I-V").grid(row=0, column=0, sticky='ew', pady=(0, 2), padx=(0, 4))
        self._create_launch_button(mid_res_frame1, "R vs. T Measurement", "K2400 R-T").grid(row=1, column=0, sticky='ew', pady=(2, 0), padx=(0, 4))
        ttk.Button(mid_res_frame1, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("K2400 I-V")).grid(row=0, column=1, rowspan=2, sticky='ns')

        # --- Mid Resistance (K2400/K2182) Group ---
        mid_res_frame2 = ttk.LabelFrame(left_col, text='Mid Resistance (Keithley 2400 / 2182)'); mid_res_frame2.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        mid_res_frame2.columnconfigure(0, weight=1)
        self._create_launch_button(mid_res_frame2, "I-V Measurement", "K2400_2182 I-V").grid(row=0, column=0, sticky='ew', pady=(0, 2), padx=(0, 4))
        self._create_launch_button(mid_res_frame2, "R vs. T Measurement", "K2400_2182 R-T").grid(row=1, column=0, sticky='ew', pady=(2, 0), padx=(0, 4))
        ttk.Button(mid_res_frame2, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("K2400_2182 I-V")).grid(row=0, column=1, rowspan=2, sticky='ns')

        # --- High Resistance Group ---
        high_res_frame = ttk.LabelFrame(left_col, text='High Resistance (Keithley 6517B)'); high_res_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        high_res_frame.columnconfigure(0, weight=1)
        self._create_launch_button(high_res_frame, "I-V Measurement", "K6517B I-V").grid(row=0, column=0, sticky='ew', pady=(0, 2), padx=(0, 4))
        self._create_launch_button(high_res_frame, "Resistivity vs. Temp", "K6517B Resistivity").grid(row=1, column=0, sticky='ew', pady=(2, 0), padx=(0, 4))
        ttk.Button(high_res_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("K6517B I-V")).grid(row=0, column=1, rowspan=2, sticky='ns')

        # --- Pyroelectric Group ---
        pyro_frame = ttk.LabelFrame(right_col, text='Pyroelectric Measurement (Keithley 6517B)'); pyro_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        pyro_frame.columnconfigure(0, weight=1)
        self._create_launch_button(pyro_frame, "Pyro Current vs. Temp", "Pyroelectric Current").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(pyro_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Pyroelectric Current")).grid(row=0, column=1, sticky='ns')

        # --- Environmental Control Group ---
        lakeshore_frame = ttk.LabelFrame(right_col, text='Temperature Control (Lakeshore)'); lakeshore_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        lakeshore_frame.columnconfigure(0, weight=1)
        self._create_launch_button(lakeshore_frame, "Temperature Ramp", "Lakeshore Temp Control").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(lakeshore_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Lakeshore Temp Control")).grid(row=0, column=1, sticky='ns')

        # --- LCR Meter Group ---
        lcr_frame = ttk.LabelFrame(right_col, text='LCR Meter (Keysight E4980A)'); lcr_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        lcr_frame.columnconfigure(0, weight=1)
        self._create_launch_button(lcr_frame, "C-V Measurement", "LCR C-V Measurement").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(lcr_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("LCR C-V Measurement")).grid(row=0, column=1, sticky='ns')

        # --- Lock-in Amplifier Group ---
        lockin_frame = ttk.LabelFrame(right_col, text='Lock-in Amplifier'); lockin_frame.pack(fill='x', expand=True, pady=GROUP_PAD_Y)
        lockin_frame.columnconfigure(0, weight=1)
        self._create_launch_button(lockin_frame, "AC Measurement", "Lock-in AC Measurement").grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(lockin_frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder("Lock-in AC Measurement")).grid(row=0, column=1, sticky='ns')

        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        return main_container

    def log(self, message):
        """Adds a timestamped message to the main console widget."""
        if self.console_widget:
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {message}\n"
            self.console_widget.config(state='normal')
            self.console_widget.insert('end', log_entry)
            self.console_widget.see('end')
            self.console_widget.config(state='disabled')

    def _open_path(self, path):
        """Cross-platform function to open a file or directory path."""
        if path == self.LICENSE_FILE and not os.path.exists(path):
            if os.path.exists(path + ".md"): path += ".md"
            elif os.path.exists(path + ".txt"): path += ".txt"
        if not os.path.exists(path):
            self.log(f"ERROR: Path not found: {os.path.abspath(path)}")
            messagebox.showwarning("Path Not Found", f"The specified path does not exist:\n\n{os.path.abspath(path)}")
            return
        try:
            if platform.system() == "Windows": os.startfile(os.path.abspath(path))
            elif platform.system() == "Darwin": subprocess.run(['open', os.path.abspath(path)], check=True)
            else: subprocess.run(['xdg-open', os.path.abspath(path)], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open path: {path}\n\nError: {e}")

    def open_script_folder(self, script_key):
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path or not os.path.exists(script_path):
            self.log(f"ERROR: Path for '{script_key}' is invalid.")
            messagebox.showwarning("Path Invalid", f"Script path for '{script_key}' is invalid.")
            return
        folder_path = os.path.dirname(os.path.abspath(script_path))
        self._open_path(folder_path)

    def open_readme(self): self._open_path(self.README_FILE)
    def open_manual_folder(self): self._open_path(self.MANUAL_FILE)
    def open_license(self): self._open_path(self.LICENSE_FILE)

    def launch_script(self, script_path):
        """Launches a specified Python script in a new process."""
        self.log(f"Launching: {os.path.basename(script_path)}")
        if not os.path.exists(script_path):
            self.log(f"ERROR: Script not found at {os.path.abspath(script_path)}")
            messagebox.showerror("File Not Found", f"Script not found:\n\n{os.path.abspath(script_path)}")
            return
        try:
            # Popen requires the script path relative to the cwd.
            # The cwd should be the script's own directory to handle its internal relative paths.
            script_directory = os.path.dirname(os.path.abspath(script_path))
            script_filename = os.path.basename(script_path)
            subprocess.Popen([sys.executable, script_filename], cwd=script_directory)
            self.log(f"Successfully launched '{script_filename}'")
        except Exception as e:
            self.log(f"ERROR: Failed to launch script. Reason: {e}")
            messagebox.showerror("Launch Error", f"An error occurred while launching the script:\n\n{e}")

    def run_gpib_test(self):
        """Launches an advanced, non-blocking GPIB/VISA instrument scanner window."""
        if not PYVISA_AVAILABLE:
            self.log("ERROR: GPIB test failed, PyVISA is not available.")
            messagebox.showerror("Dependency Missing", "The 'pyvisa' library is required.\n\nInstall via pip:\npip install pyvisa pyvisa-py")
            return

        test_win = Toplevel(self.root)
        test_win.title("GPIB/VISA Instrument Scanner")
        test_win.geometry("750x550")
        test_win.configure(bg=self.CLR_BG_DARK)
        test_win.minsize(600, 400)
        test_win.transient(self.root)
        test_win.grab_set()

        result_queue = queue.Queue()
        main_frame = ttk.Frame(test_win, padding=15)
        main_frame.pack(fill='both', expand=True)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        controls_frame = ttk.Frame(main_frame)
        controls_frame.grid(row=0, column=0, sticky='ew', pady=(0, 15))
        controls_frame.columnconfigure(0, weight=1)
        controls_frame.columnconfigure(1, weight=1)
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
            """Backend VISA scan logic that runs in a separate thread."""
            try:
                rm = pyvisa.ResourceManager()
                resources = rm.list_resources()
                if not resources:
                    result_queue.put("-> No instruments found. Check connections and VISA installation.\n")
                else:
                    result_queue.put(f"-> Found {len(resources)} instrument(s). Querying them now...\n\n")
                    for address in resources:
                        try:
                            with rm.open_resource(address) as instrument:
                                instrument.timeout = 2000
                                idn = instrument.query('*IDN?').strip()
                                result = f"Address: {address}\n   ID: {idn}\n\n"
                                result_queue.put(result)
                        except Exception as e:
                            result = f"Address: {address}\n   Error: Could not get ID. {e}\n\n"
                            result_queue.put(result)
            except Exception as e:
                error_msg = (f"A critical VISA error occurred: {e}\n"
                             "Please ensure a VISA backend (e.g., NI-VISA) is installed correctly.\n")
                result_queue.put(error_msg)
            finally:
                result_queue.put("SCAN_COMPLETE")

        def _process_gpib_queue():
            """Checks the queue for messages from the worker thread."""
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
            log_to_scanner("Starting scan... The GUI will remain responsive.")
            threading.Thread(target=_gpib_scan_worker, daemon=True).start()

        def clear_log():
            console_area.config(state='normal')
            console_area.delete(1.0, 'end')
            console_area.config(state='disabled')
            log_to_scanner("Log cleared.")

        scan_button = ttk.Button(controls_frame, text="Scan for Instruments", command=start_scan, style='Scan.TButton')
        scan_button.grid(row=0, column=0, padx=(0, 5), sticky='ew')
        clear_button = ttk.Button(controls_frame, text="Clear Log", command=clear_log, style='App.TButton')
        clear_button.grid(row=0, column=1, padx=(5, 0), sticky='ew')
        ttk.Button(main_frame, text="Close", style='App.TButton', command=test_win.destroy).grid(row=2, column=0, sticky='ew', pady=(15, 0))

        log_to_scanner("Welcome to the GPIB/VISA Instrument Scanner.")
        log_to_scanner("Click 'Scan for Instruments' to begin.")
        self.log("GPIB/VISA scanner window opened.")
        test_win.after(100, _process_gpib_queue)


def main():
    """Initializes and runs the main application."""
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
