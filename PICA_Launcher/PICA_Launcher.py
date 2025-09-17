# -------------------------------------------------------------------------------
# Name:         PICA Launcher - Python Instrument Control & Automation
# Purpose:      A central meta front end to launch various measurement GUIs.
# Author:       Prathamesh Deshmukh
# Created:      10/09/2025
# Version:      1.2
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
    PROGRAM_VERSION = "1.2"
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#2B3D4F', '#3A506B', '#EDF2F4'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 4, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE - 1)
    LOGO_FILE = "UGC_DAE_CSR.jpeg"
    MANUAL_FILE = "PICA_User_Manual.pdf"
    LOGO_SIZE = 150

    # ---------------------------------------------------------------------------
    # ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼
    #
    #                           !!! IMPORTANT !!!
    #      YOU MUST EDIT THE PATHS IN THIS DICTIONARY TO MATCH YOUR FILES
    #
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
    # ---------------------------------------------------------------------------
    SCRIPT_PATHS = {
        # Low Resistance
        "Delta Mode Resistivity (K6221/2182A)": "Delta_mode/Delta_Mode_GUI.py",

        # Mid Resistance
        "I-V Measurement (Keithley 2400)": "Keithley_2400/Frontend_IV_2400_V2.py",
        "4-Probe Measurement (K2400/2182)": "Keithley_2400_Keithley_2182/Four_Probe_GUI.py", # <-- EDIT THIS PATH

        # High Resistance
        "High Resistance Measurement (K6517B)": "Keithley_6517B/High_Res_GUI.py",

        # Other Instruments
        "C-V Measurement (Keysight E4980A)": "LCR_Keysight_E4980A/CV_GUI.py",
        "Temperature Control (Lakeshore)": "Lakeshore_350_340/Temp_Control_GUI.py"
    }

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher v{self.PROGRAM_VERSION}")
        self.root.geometry("1200x750")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1000, 700)

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

    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1, minsize=400)  # Info Panel
        self.root.grid_columnconfigure(1, weight=2)  # Launcher Panel

        info_panel = self.create_info_panel(self.root)
        info_panel.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)

        launcher_panel = self.create_launcher_panel(self.root)
        launcher_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)

    def create_info_panel(self, parent):
        info_frame = ttk.Frame(parent, padding=20)

        # --- Logo ---
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
        desc_text = "A suite of Python scripts for controlling and automating laboratory instruments for materials science and physics research."
        Label(info_frame, text=desc_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_INFO, wraplength=350, justify='center').pack(pady=(0, 20))
        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=20)
        author_text = ("Developed by Prathamesh Deshmukh\n"
                       "UGC-DAE Consortium for Scientific Research, Mumbai Centre\n"
                       "Sudip Mukherjee Materials Physics Lab")
        Label(info_frame, text=author_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=('Segoe UI', 10), justify='center').pack(side='bottom', pady=(20,0))
        return info_frame

    def create_launcher_panel(self, parent):
        launcher_frame = ttk.Frame(parent, padding=10)

        # --- Low Resistance ---
        low_res_frame = LabelFrame(launcher_frame, text='Low Resistance Measurement', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n')
        low_res_frame.pack(fill='x', expand=True, padx=10, pady=5)
        Button(low_res_frame, text="Delta Mode Resistivity (K6221/2182A)", command=lambda: self.launch_script(self.SCRIPT_PATHS["Delta Mode Resistivity (K6221/2182A)"])).pack(fill='x', padx=15, pady=15)

        # --- Mid Resistance ---
        mid_res_frame = LabelFrame(launcher_frame, text='Mid Resistance Measurement', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n')
        mid_res_frame.pack(fill='x', expand=True, padx=10, pady=5)
        Button(mid_res_frame, text="I-V Measurement (Keithley 2400)", command=lambda: self.launch_script(self.SCRIPT_PATHS["I-V Measurement (Keithley 2400)"])).pack(fill='x', padx=15, pady=(15,7))
        Button(mid_res_frame, text="4-Probe Measurement (K2400/2182)", command=lambda: self.launch_script(self.SCRIPT_PATHS["4-Probe Measurement (K2400/2182)"])).pack(fill='x', padx=15, pady=(7,15))

        # --- High Resistance ---
        high_res_frame = LabelFrame(launcher_frame, text='High Resistance Measurement', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n')
        high_res_frame.pack(fill='x', expand=True, padx=10, pady=5)
        Button(high_res_frame, text="High Resistance Measurement (K6517B)", command=lambda: self.launch_script(self.SCRIPT_PATHS["High Resistance Measurement (K6517B)"])).pack(fill='x', padx=15, pady=15)

        # --- Utilities & Diagnostics ---
        util_frame = LabelFrame(launcher_frame, text='Utilities & Diagnostics', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n')
        util_frame.pack(fill='x', expand=True, padx=10, pady=15)
        Button(util_frame, text="Test Connected GPIB/VISA Instruments", command=self.run_gpib_test).pack(fill='x', padx=15, pady=(15,7))
        Button(util_frame, text="Open User Manual", command=self.open_manual).pack(fill='x', padx=15, pady=(7,15))

        return launcher_frame

    def launch_script(self, script_path):
        """Launches a python script in a new process."""
        print(f"Attempting to launch: {script_path}")
        if not os.path.exists(script_path):
            messagebox.showerror("File Not Found", f"The script could not be found at the specified path:\n\n{script_path}\n\nPlease edit the SCRIPT_PATHS dictionary in the launcher script.")
            return
        try:
            script_directory = os.path.dirname(script_path) or '.'
            subprocess.Popen([sys.executable, script_path], cwd=script_directory)
            print(f"Successfully launched '{os.path.basename(script_path)}'")
        except Exception as e:
            messagebox.showerror("Launch Error", f"An error occurred while trying to launch the script:\n\n{e}")
            print(f"Error launching script: {e}")

    def open_manual(self):
        """Opens the user manual PDF file."""
        if not os.path.exists(self.MANUAL_FILE):
            messagebox.showwarning("Manual Not Found", f"The manual file '{self.MANUAL_FILE}' was not found in the same directory as the launcher.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(self.MANUAL_FILE)
            elif platform.system() == "Darwin": # macOS
                subprocess.run(['open', self.MANUAL_FILE])
            else: # linux
                subprocess.run(['xdg-open', self.MANUAL_FILE])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the manual file.\n\nError: {e}")

    def run_gpib_test(self):
        """Opens a window to list all available VISA resources."""
        if not PYVISA_AVAILABLE:
            messagebox.showerror("Dependency Missing", "The 'pyvisa' library is required for this feature.\n\nPlease install it by running:\npip install pyvisa")
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
            if resources:
                resource_list = "\n".join(resources)
                text_area.insert('1.0', resource_list)
            else:
                text_area.insert('1.0', "No VISA instruments found.\n\n- Check NI-VISA or other backend installation.\n- Ensure instruments are powered on and connected.")
        except Exception as e:
            text_area.insert('1.0', f"An error occurred while scanning for instruments:\n\n{e}\n\nMake sure a VISA backend (like NI-VISA) is installed correctly.")

        text_area.config(state='disabled') # Make text read-only
        Button(test_win, text="Close", command=test_win.destroy).pack(pady=10)

def main():
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
