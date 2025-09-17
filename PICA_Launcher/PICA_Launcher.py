# -------------------------------------------------------------------------------
# Name:         PICA Launcher - Python Instrument Control & Automation
# Purpose:      A central meta front end to launch various measurement GUIs.
# Author:       Prathamesh Deshmukh
# Created:      10/09/2025
# Version:      2.3 (Final Theme Synthesis)
# Last Edit:    17/09/2025
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, Text, Canvas
import os
import sys
import subprocess
import platform

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
    PROGRAM_VERSION = "2.3"

    # --- Synthesized Professional Color Palette ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_FRAME_BG = '#3A506B'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_TEXT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'

    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE)
    LOGO_FILE = "UGC_DAE_CSR.jpeg"
    MANUAL_FILE = "PICA_User_Manuals"
    README_FILE = "README.md"
    LICENSE_FILE = "LICENSE"

    LOGO_SIZE = 140

    SCRIPT_PATHS = {
        "Delta Mode R-T": "Delta_mode/Delta_Mode_RT_GUI.py", "Delta Mode I-V": "Delta_mode/Delta_Mode_IV_GUI.py",
        "K2400 I-V": "Keithley_2400/IV_GUI.py", "K2400 Time-Current": "Keithley_2400/Time_Current_GUI.py",
        "K2400_2182 R-T": "Keithley_2400_Keithley_2182/Four_Probe_RT_GUI.py", "K2400_2182 I-V": "Keithley_2400_Keithley_2182/Four_Probe_IV_GUI.py",
        "K6517B Resistivity": "Keithley_6517B/High_Res_GUI.py", "Pyroelectric Current": "Keithley_6517B/Pyro_GUI.py",
        "Lakeshore Temp Control": "Lakeshore_350_340/Temp_Control_GUI.py",
    }

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher v{self.PROGRAM_VERSION}")
        self.root.geometry("1250x820")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1200, 780)

        self.logo_image = None
        self.setup_styles()
        self.create_widgets()

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')

        # --- Base styling with the lighter blue-grey theme ---
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT, font=self.FONT_BASE)
        style.configure('TSeparator', background=self.CLR_FRAME_BG)

        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_FRAME_BG, padding=12)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)

        # --- High-contrast Gold Accent Button Theme ---
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 9),
                        foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG,
                        borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('App.TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])

        style.configure('Icon.TButton', font=('Segoe UI', 12), padding=(5, 9),
                        foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG,
                        borderwidth=0)
        style.map('Icon.TButton',
                  background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])

        style.configure("Vertical.TScrollbar", troughcolor=self.CLR_BG_DARK, background=self.CLR_FRAME_BG,
                        arrowcolor=self.CLR_ACCENT_GOLD, bordercolor=self.CLR_BG_DARK)
        style.map("Vertical.TScrollbar", background=[('active', self.CLR_ACCENT_GOLD)])

    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1, minsize=420)
        self.root.grid_columnconfigure(1, weight=2)

        info_panel = self.create_resource_panel(self.root)
        info_panel.grid(row=0, column=0, sticky="nsew", padx=(15, 10), pady=15)

        launcher_container = self.create_launcher_scrollable_panel(self.root)
        launcher_container.grid(row=0, column=1, sticky="nsew", padx=(10, 15), pady=15)

    def create_resource_panel(self, parent):
        info_frame = ttk.Frame(parent, padding=20)

        logo_canvas = Canvas(info_frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.pack(pady=(0, 20))
        # ... (logo loading logic)

        ttk.Label(info_frame, text="PICA: Python Instrument\nControl & Automation", font=self.FONT_TITLE, justify='center', anchor='center').pack(pady=(0, 15))
        desc_text = "A suite of Python scripts for automating laboratory instruments for materials science and physics research."
        ttk.Label(info_frame, text=desc_text, font=self.FONT_INFO, wraplength=400, justify='center', anchor='center').pack(pady=(0, 20))

        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=25)

        util_frame = ttk.Frame(info_frame)
        util_frame.pack(fill='x', pady=5)
        ttk.Button(util_frame, text="Open README", style='App.TButton', command=self.open_readme).pack(fill='x', pady=4)
        ttk.Button(util_frame, text="Open Instrument Manuals", style='App.TButton', command=self.open_manual_folder).pack(fill='x', pady=4)
        ttk.Button(util_frame, text="View License", style='App.TButton', command=self.open_license).pack(fill='x', pady=4)
        ttk.Button(util_frame, text="Test GPIB Connection", style='App.TButton', command=self.run_gpib_test).pack(fill='x', pady=4)

        bottom_frame = ttk.Frame(info_frame)
        bottom_frame.pack(side='bottom', pady=(20, 0))
        author_text = ("This software was developed by Prathamesh Deshmukh during his PhD tenure.\n"
                       "The work was conducted at the Mumbai Centre of the UGC-DAE CSR\n"
                       "within the Sudip Mukherjee Materials Physics Lab.")
        ttk.Label(bottom_frame, text=author_text, font=('Segoe UI', 10), justify='center', anchor='center').pack(pady=(0,10))

        license_text = "This project is licensed under the MIT License. See the LICENSE file for details."
        ttk.Label(bottom_frame, text=license_text, font=('Segoe UI', 9), justify='center', anchor='center').pack()

        return info_frame

    def _create_launch_button(self, parent, text, script_key):
        frame = ttk.Frame(parent, style='TLabelframe')
        frame.columnconfigure(0, weight=1)
        ttk.Button(frame, text=text, style='App.TButton', command=lambda: self.launch_script(self.SCRIPT_PATHS[script_key])).grid(row=0, column=0, sticky='ew')
        ttk.Button(frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder(script_key)).grid(row=0, column=1, padx=(8,0))
        return frame

    def create_launcher_scrollable_panel(self, parent):
        container = ttk.Frame(parent)
        canvas = Canvas(container, bg=self.CLR_BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame.grid_columnconfigure(0, weight=1, uniform="group1", pad=10)
        scrollable_frame.grid_columnconfigure(1, weight=1, uniform="group1", pad=10)

        left_col = ttk.Frame(scrollable_frame); left_col.grid(row=0, column=0, sticky='new')
        right_col = ttk.Frame(scrollable_frame); right_col.grid(row=0, column=1, sticky='new')

        # Populate columns...
        low_res_frame = ttk.LabelFrame(left_col, text='Low Resistance (Delta Mode)'); low_res_frame.pack(fill='x', expand=True, pady=10)
        self._create_launch_button(low_res_frame, "R vs. T Measurement", "Delta Mode R-T").pack(fill='x', pady=4)
        self._create_launch_button(low_res_frame, "I-V Measurement", "Delta Mode I-V").pack(fill='x', pady=4)
        mid_res_frame1 = ttk.LabelFrame(left_col, text='Mid Resistance (Keithley 2400)'); mid_res_frame1.pack(fill='x', expand=True, pady=10)
        self._create_launch_button(mid_res_frame1, "I-V Measurement", "K2400 I-V").pack(fill='x', pady=4)
        self._create_launch_button(mid_res_frame1, "Time vs. Current", "K2400 Time-Current").pack(fill='x', pady=4)
        mid_res_frame2 = ttk.LabelFrame(left_col, text='Mid Resistance (K2400 / K2182)'); mid_res_frame2.pack(fill='x', expand=True, pady=10)
        self._create_launch_button(mid_res_frame2, "R vs. T Measurement", "K2400_2182 R-T").pack(fill='x', pady=4)
        self._create_launch_button(mid_res_frame2, "I-V Measurement", "K2400_2182 I-V").pack(fill='x', pady=4)
        high_res_frame = ttk.LabelFrame(right_col, text='High Resistance (Keithley 6517B)'); high_res_frame.pack(fill='x', expand=True, pady=10)
        self._create_launch_button(high_res_frame, "Resistivity Measurement", "K6517B Resistivity").pack(fill='x', pady=4)
        pyro_frame = ttk.LabelFrame(right_col, text='Pyroelectric Measurement (K6517B)'); pyro_frame.pack(fill='x', expand=True, pady=10)
        self._create_launch_button(pyro_frame, "Pyro Current vs. Temp", "Pyroelectric Current").pack(fill='x', pady=4)
        lakeshore_frame = ttk.LabelFrame(right_col, text='Environmental Control'); lakeshore_frame.pack(fill='x', expand=True, pady=10)
        self._create_launch_button(lakeshore_frame, "Temperature Control (Lakeshore)", "Lakeshore Temp Control").pack(fill='x', pady=4)

        canvas.grid(row=0, column=0, sticky="nsew"); scrollbar.grid(row=0, column=1, sticky="ns")
        container.grid_rowconfigure(0, weight=1); container.grid_columnconfigure(0, weight=1)
        return container

    def _open_path(self, path):
        # (This logic remains unchanged)
        if path == self.LICENSE_FILE and not os.path.exists(path):
            if os.path.exists(path + ".md"): path += ".md"
            elif os.path.exists(path + ".txt"): path += ".txt"
        if not os.path.exists(path):
            messagebox.showwarning("Path Not Found", f"The specified path does not exist:\n\n{path}")
            return
        try:
            if platform.system() == "Windows": os.startfile(path)
            elif platform.system() == "Darwin": subprocess.run(['open', path], check=True)
            else: subprocess.run(['xdg-open', path], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open path: {path}\n\nError: {e}")

    def open_script_folder(self, script_key):
        # (This logic remains unchanged)
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path or not os.path.exists(script_path):
            messagebox.showwarning("Path Invalid", f"Script path for '{script_key}' is invalid.")
            return
        folder_path = os.path.dirname(os.path.abspath(script_path))
        self._open_path(folder_path)

    def open_readme(self): self._open_path(self.README_FILE)
    def open_manual_folder(self): self._open_path(self.MANUAL_FILE)
    def open_license(self): self._open_path(self.LICENSE_FILE)

    def launch_script(self, script_path):
        # (This logic remains unchanged)
        if not os.path.exists(script_path):
            messagebox.showerror("File Not Found", f"Script not found:\n\n{script_path}")
            return
        try:
            script_directory = os.path.dirname(script_path) or '.'
            subprocess.Popen([sys.executable, script_path], cwd=script_directory)
        except Exception as e:
            messagebox.showerror("Launch Error", f"An error occurred while launching the script:\n\n{e}")

    def run_gpib_test(self):
        # (This logic remains unchanged)
        if not PYVISA_AVAILABLE:
            messagebox.showerror("Dependency Missing", "The 'pyvisa' library is required.\n\nInstall via pip:\npip install pyvisa pyvisa-py")
            return
        test_win = Toplevel(self.root); test_win.title("Connected VISA Instruments"); test_win.geometry("600x400"); test_win.configure(bg=self.CLR_BG_DARK)
        ttk.Label(test_win, text="Found VISA Resources:", font=self.FONT_SUBTITLE, foreground=self.CLR_ACCENT_GOLD).pack(pady=10)
        text_area = Text(test_win, bg='#000000', fg=self.CLR_TEXT, font=self.FONT_BASE, relief='flat', bd=0)
        text_area.pack(padx=10, pady=5, expand=True, fill='both')
        try:
            rm = pyvisa.ResourceManager(); resources = rm.list_resources()
            text_area.insert('1.0', "\n".join(resources) if resources else "No VISA instruments found.")
        except Exception as e:
            text_area.insert('1.0', f"An error occurred while scanning:\n\n{e}")
        text_area.config(state='disabled')
        ttk.Button(test_win, text="Close", style='App.TButton', command=test_win.destroy).pack(pady=15, padx=10, fill='x')

def main():
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
