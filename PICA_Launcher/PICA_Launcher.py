# -------------------------------------------------------------------------------
# Name:         PICA Launcher - Python Instrument Control & Automation
# Purpose:      A central meta front end to launch various measurement GUIs.
# Author:       Prathamesh Deshmukh
# Created:      10/09/2025
# Version:      1.5 (Professional Redesign)
# Last Edit:    17/09/2025
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, Label, LabelFrame, Button, messagebox, Toplevel, Text
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
    PROGRAM_VERSION = "1.5"
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#2B3D4F', '#3A506B', '#EDF2F4'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 4, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE - 1)
    LOGO_FILE = "UGC_DAE_CSR.jpeg"
    MANUAL_FILE = "PICA_User_Manual.pdf" # This can point to a directory too
    README_FILE = "README.md"
    LOGO_SIZE = 150

    # ---------------------------------------------------------------------------
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    #
    #                           !!! IMPORTANT !!!
    #      YOU MUST EDIT THE PATHS IN THIS DICTIONARY TO MATCH YOUR SCRIPT FILES
    #
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
    # ---------------------------------------------------------------------------
    SCRIPT_PATHS = {
        # Delta Mode (Low Resistance)
        "Delta Mode R-T": "Delta_mode/Delta_Mode_RT_GUI.py",
        "Delta Mode I-V": "Delta_mode/Delta_Mode_IV_GUI.py",

        # Keithley 2400 (Mid Resistance)
        "K2400 I-V": "Keithley_2400/IV_GUI.py",
        "K2400 Time-Current": "Keithley_2400/Time_Current_GUI.py",

        # K2400 + K2182 (Mid Resistance / 4-Probe)
        "K2400_2182 R-T": "Keithley_2400_Keithley_2182/Four_Probe_RT_GUI.py",
        "K2400_2182 I-V": "Keithley_2400_Keithley_2182/Four_Probe_IV_GUI.py",

        # Keithley 6517B (High Resistance)
        "K6517B Resistivity": "Keithley_6517B/High_Res_GUI.py",

        # Pyroelectric Measurement (using K6517B)
        "Pyroelectric Current": "Keithley_6517B/Pyro_GUI.py",

        # Environmental Control
        "Lakeshore Temp Control": "Lakeshore_350_340/Temp_Control_GUI.py", # <-- EDIT THIS PATH
    }

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher v{self.PROGRAM_VERSION}")
        self.root.geometry("1200x850")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1100, 800)

        self.logo_image = None
        self.setup_styles()
        self.create_widgets()

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton', font=self.FONT_BASE, padding=(12, 10))
        style.map('TButton', foreground=[('!active', self.CLR_BG_DARK), ('active', self.CLR_FG_LIGHT)],
                  background=[('!active', '#8D99AE'), ('active', self.CLR_BG_DARK)])
        style.configure('Icon.TButton', font=('Segoe UI', 12), padding=(5, 9))

    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1, minsize=400) # Resource Panel
        self.root.grid_columnconfigure(1, weight=2)             # Action Panel

        info_panel = self.create_resource_panel(self.root)
        info_panel.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)

        launcher_panel = self.create_launcher_panel(self.root)
        launcher_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)

    def create_resource_panel(self, parent):
        info_frame = ttk.Frame(parent, padding=20)

        logo_canvas = tk.Canvas(info_frame, width=self.LOGO_SIZE, height=self.LOGO_SIZE, bg=self.CLR_BG_DARK, highlightthickness=0)
        logo_canvas.pack(pady=(0, 20))
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE):
            try:
                img = Image.open(self.LOGO_FILE).resize((self.LOGO_SIZE, self.LOGO_SIZE), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(self.LOGO_SIZE/2, self.LOGO_SIZE/2, image=self.logo_image)
            except Exception:
                logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nERROR", font=self.FONT_BASE, fill="white", justify='center')
        else:
            logo_canvas.create_text(self.LOGO_SIZE/2, self.LOGO_SIZE/2, text="LOGO\nMISSING", font=self.FONT_BASE, fill="white", justify='center')

        Label(info_frame, text="PICA: Python Instrument\nControl & Automation", bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE, justify='center').pack(pady=(0, 15))

        desc_text = ("A suite of Python scripts using PyVISA to control and automate laboratory "
                     "instruments (Keithley, Lakeshore, Keysight) for materials science and physics research.")
        Label(info_frame, text=desc_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_INFO, wraplength=380, justify='center').pack(pady=(0, 20))

        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=20)
        util_frame = ttk.Frame(info_frame)
        util_frame.pack(fill='x', expand=True)
        Button(util_frame, text="Open README", command=self.open_readme).pack(fill='x', pady=4)
        Button(util_frame, text="Open Instrument Manuals", command=self.open_manual).pack(fill='x', pady=4)
        Button(util_frame, text="Test GPIB Connection", command=self.run_gpib_test).pack(fill='x', pady=4)

        author_text = ("This software was developed by Prathamesh Deshmukh.\n"
                       "The work was conducted at the Mumbai Centre of the\n"
                       "UGC-DAE Consortium for Scientific Research (CSR)\n"
                       "within the Sudip Mukherjee Materials Physics Lab.")
        Label(info_frame, text=author_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=('Segoe UI', 10), justify='center').pack(side='bottom', pady=(20,0))
        return info_frame

    def _create_launch_button(self, parent, text, script_key):
        """Helper method to create a row with a launch button and a folder button."""
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        Button(frame, text=text, command=lambda: self.launch_script(self.SCRIPT_PATHS[script_key])).grid(row=0, column=0, sticky='ew')
        ttk.Button(frame, text="📁", style='Icon.TButton', command=lambda: self.open_script_folder(script_key)).grid(row=0, column=1, padx=(5,0))
        return frame

    def create_launcher_panel(self, parent):
        launcher_frame = ttk.Frame(parent, padding=10)

        # Electrical Characterization Frames
        low_res_frame = LabelFrame(launcher_frame, text='Low Resistance (Delta Mode)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        low_res_frame.pack(fill='x', padx=10, pady=5)
        self._create_launch_button(low_res_frame, "R vs. T Measurement", "Delta Mode R-T").pack(fill='x', padx=5, pady=4)
        self._create_launch_button(low_res_frame, "I-V Measurement", "Delta Mode I-V").pack(fill='x', padx=5, pady=4)

        mid_res_frame1 = LabelFrame(launcher_frame, text='Mid Resistance (Keithley 2400)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        mid_res_frame1.pack(fill='x', padx=10, pady=5)
        self._create_launch_button(mid_res_frame1, "I-V Measurement", "K2400 I-V").pack(fill='x', padx=5, pady=4)
        self._create_launch_button(mid_res_frame1, "Time vs. Current", "K2400 Time-Current").pack(fill='x', padx=5, pady=4)

        mid_res_frame2 = LabelFrame(launcher_frame, text='Mid Resistance (K2400 / K2182)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        mid_res_frame2.pack(fill='x', padx=10, pady=5)
        self._create_launch_button(mid_res_frame2, "R vs. T Measurement", "K2400_2182 R-T").pack(fill='x', padx=5, pady=4)
        self._create_launch_button(mid_res_frame2, "I-V Measurement", "K2400_2182 I-V").pack(fill='x', padx=5, pady=4)

        high_res_frame = LabelFrame(launcher_frame, text='High Resistance (Keithley 6517B)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        high_res_frame.pack(fill='x', padx=10, pady=5)
        self._create_launch_button(high_res_frame, "Resistivity Measurement", "K6517B Resistivity").pack(fill='x', padx=5, pady=4)

        # Specialized & Thermal Systems Frames
        pyro_frame = LabelFrame(launcher_frame, text='Pyroelectric Measurement (K6517B)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        pyro_frame.pack(fill='x', padx=10, pady=5)
        self._create_launch_button(pyro_frame, "Pyro Current vs. Temp", "Pyroelectric Current").pack(fill='x', padx=5, pady=4)

        lakeshore_frame = LabelFrame(launcher_frame, text='Environmental Control', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        lakeshore_frame.pack(fill='x', padx=10, pady=5)
        self._create_launch_button(lakeshore_frame, "Temperature Control (Lakeshore)", "Lakeshore Temp Control").pack(fill='x', padx=5, pady=4)

        return launcher_frame

    def _open_path(self, path):
        """Generic opener for files or folders."""
        if not os.path.exists(path):
            messagebox.showwarning("Path Not Found", f"The specified file or folder does not exist:\n\n{path}")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.run(['open', path], check=True)
            else:
                subprocess.run(['xdg-open', path], check=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the path.\nPath: {path}\n\nError: {e}")

    def open_script_folder(self, script_key):
        script_path = self.SCRIPT_PATHS.get(script_key)
        if not script_path or not os.path.exists(script_path):
            messagebox.showwarning("Path Invalid", f"The script path for '{script_key}' is invalid. Please check the SCRIPT_PATHS dictionary.")
            return
        folder_path = os.path.dirname(os.path.abspath(script_path))
        self._open_path(folder_path)

    def open_readme(self):
        self._open_path(self.README_FILE)

    def open_manual(self):
        self._open_path(self.MANUAL_FILE)

    def launch_script(self, script_path):
        if not os.path.exists(script_path):
            messagebox.showerror("File Not Found", f"The script could not be found:\n\n{script_path}\n\nPlease edit SCRIPT_PATHS in the launcher.")
            return
        try:
            script_directory = os.path.dirname(script_path) or '.'
            subprocess.Popen([sys.executable, script_path], cwd=script_directory)
        except Exception as e:
            messagebox.showerror("Launch Error", f"An error occurred while launching the script:\n\n{e}")

    def run_gpib_test(self):
        if not PYVISA_AVAILABLE:
            messagebox.showerror("Dependency Missing", "The 'pyvisa' library is required.\n\nPlease install it via pip:\npip install pyvisa pyvisa-py")
            return
        test_win = Toplevel(self.root)
        test_win.title("Connected VISA Instruments")
        test_win.geometry("600x400")
        test_win.configure(bg=self.CLR_BG_DARK)
        Label(test_win, text="Found VISA Resources:", font=self.FONT_SUBTITLE, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT).pack(pady=10)
        text_area = Text(test_win, bg='#1E2D3B', fg='white', font=self.FONT_BASE, relief='flat', height=10, width=70)
        text_area.pack(padx=10, pady=5, expand=True, fill='both')
        try:
            rm = pyvisa.ResourceManager()
            resources = rm.list_resources()
            text_area.insert('1.0', "\n".join(resources) if resources else "No VISA instruments found.")
        except Exception as e:
            text_area.insert('1.0', f"An error occurred while scanning for instruments:\n\n{e}")
        text_area.config(state='disabled')
        Button(test_win, text="Close", command=test_win.destroy).pack(pady=10)

def main():
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
