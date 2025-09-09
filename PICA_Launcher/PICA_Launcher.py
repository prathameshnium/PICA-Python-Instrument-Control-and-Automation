# -------------------------------------------------------------------------------
# Name:         PICA Launcher - Python Instrument Control & Automation
# Purpose:      A central meta front end to launch various measurement GUIs.
# Author:       Prathamesh Deshmukh
# Created:      10/09/2025
# Version:      1.1
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, Label, LabelFrame, Button, messagebox
import os
import sys
import subprocess

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True

except ImportError:
    PIL_AVAILABLE = False


class PICALauncherApp:
    """The main GUI application for the PICA Launcher."""
    PROGRAM_VERSION = "1.1"
    CLR_BG_DARK, CLR_HEADER, CLR_FG_LIGHT = '#2B3D4F', '#3A506B', '#EDF2F4'
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 4, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_INFO = ('Segoe UI', FONT_SIZE_BASE - 1)
    LOGO_FILE = "UGC_DAE_CSR.jpeg"
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
        # Electrical Characterization
        "I-V Measurement (Keithley 2400)": "C:/Users/ketan/Downloads/ready_to_use/PICA-Python-Instrument-Control-and-Automation/Keithley_2400/_untested_new/Frontend_IV_2400_V2.py",
        "Delta Mode Resistivity (K6221/2182A)": "Keithley_6221_2182A/Temp_vs_Res_GUI.py",
        "C-V Measurement (Keysight E4980A)": "Keysight_E4980A/CV_GUI.py",
        # Specialized & Thermal Systems
        "Pyroelectric Measurement (K6517B)": "Pyroelectric/Pyro_GUI.py",
        "Temperature Control (Lakeshore)": "Lakeshore_350/Temp_Control_GUI.py"
    }

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Launcher")
        self.root.geometry("1200x700")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1000, 650)

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
        # Configure the main grid
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

        # --- Title and Description ---
        Label(info_frame, text="PICA: Python Instrument\nControl & Automation", bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE, justify='center').pack(pady=(0, 15))

        desc_text = "A suite of Python scripts for controlling and automating laboratory instruments for materials science and physics research."
        Label(info_frame, text=desc_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_INFO, wraplength=350, justify='center').pack(pady=(0, 20))

        ttk.Separator(info_frame, orient='horizontal').pack(fill='x', pady=20)

        # --- Author Info at the bottom ---
        author_text = ("Developed by Prathamesh Deshmukh\n"
                       "UGC-DAE Consortium for Scientific Research, Mumbai Centre\n"
                       "Sudip Mukherjee Materials Physics Lab")
        Label(info_frame, text=author_text, bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=('Segoe UI', 10), justify='center').pack(side='bottom', pady=(20,0))

        return info_frame

    def create_launcher_panel(self, parent):
        launcher_frame = ttk.Frame(parent, padding=10)

        # --- Electrical Characterization Group ---
        elec_frame = LabelFrame(launcher_frame, text='Electrical Characterization', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n')
        elec_frame.pack(fill='x', expand=True, padx=10, pady=(10, 5))

        Button(elec_frame, text="I-V Measurement (Keithley 2400)", command=lambda: self.launch_script(self.SCRIPT_PATHS["I-V Measurement (Keithley 2400)"])).pack(fill='x', padx=15, pady=(15,7))
        Button(elec_frame, text="Delta Mode Resistivity (K6221/2182A)", command=lambda: self.launch_script(self.SCRIPT_PATHS["Delta Mode Resistivity (K6221/2182A)"])).pack(fill='x', padx=15, pady=7)
        Button(elec_frame, text="C-V Measurement (Keysight E4980A)", command=lambda: self.launch_script(self.SCRIPT_PATHS["C-V Measurement (Keysight E4980A)"])).pack(fill='x', padx=15, pady=(7,15))

        # --- Specialized & Thermal Systems Group ---
        spec_frame = LabelFrame(launcher_frame, text='Specialized & Thermal Systems', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n')
        spec_frame.pack(fill='x', expand=True, padx=10, pady=(15, 5))

        Button(spec_frame, text="Pyroelectric Measurement (K6517B)", command=lambda: self.launch_script(self.SCRIPT_PATHS["Pyroelectric Measurement (K6517B)"])).pack(fill='x', padx=15, pady=(15,7))
        Button(spec_frame, text="Temperature Control (Lakeshore)", command=lambda: self.launch_script(self.SCRIPT_PATHS["Temperature Control (Lakeshore)"])).pack(fill='x', padx=15, pady=(7,15))

        return launcher_frame

    def launch_script(self, script_path):
        """Launches a python script in a new process."""
        print(f"Attempting to launch: {script_path}")
        if not os.path.exists(script_path):
            messagebox.showerror("File Not Found", f"The script could not be found at the specified path:\n\n{script_path}\n\nPlease edit the SCRIPT_PATHS dictionary in the launcher script.")
            return

        try:
            # Use sys.executable to ensure the script runs with the same Python interpreter
            # Set the current working directory to the script's directory to resolve relative paths (like logos)
            script_directory = os.path.dirname(script_path) or '.'
            subprocess.Popen([sys.executable, script_path], cwd=script_directory)
            print(f"Successfully launched '{os.path.basename(script_path)}'")
        except Exception as e:
            messagebox.showerror("Launch Error", f"An error occurred while trying to launch the script:\n\n{e}")
            print(f"Error launching script: {e}")

def main():
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
