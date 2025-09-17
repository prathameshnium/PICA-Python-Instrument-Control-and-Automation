# -------------------------------------------------------------------------------
# Name:         PICA Launcher - Python Instrument Control & Automation
# Purpose:      A central meta front end to launch various measurement GUIs.
# Author:       Prathamesh Deshmukh
# Created:      10/09/2025
# Version:      1.3
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
    PROGRAM_VERSION = "1.3"
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
    #      YOU MUST EDIT THE PATHS IN THESE DICTIONARIES TO MATCH YOUR FILES
    #
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
    # ---------------------------------------------------------------------------

    # --- 1. DEFINE PATHS TO YOUR MEASUREMENT SCRIPTS ---
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
    }

    # --- 2. DEFINE PATHS TO YOUR DATA FOLDERS ---
    DATA_PATHS = {
        "Delta Mode": "C:/PICA_Data/Delta_Mode_Data",
        "K2400": "C:/PICA_Data/K2400_Data",
        "K2400_2182": "C:/PICA_Data/K2400_K2182_Data",
        "K6517B": "C:/PICA_Data/K6517B_Data",
        "Pyroelectric": "C:/PICA_Data/Pyroelectric_Data",
    }


    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Launcher v{self.PROGRAM_VERSION}")
        self.root.geometry("1200x850")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(1000, 800)

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
        style.configure('Browse.TButton', padding=(8, 4), font=('Segoe UI', 10))


    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1, minsize=400)
        self.root.grid_columnconfigure(1, weight=2)

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
        low_res_frame = LabelFrame(launcher_frame, text='Low Resistance (Delta Mode)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        low_res_frame.pack(fill='x', padx=10, pady=5)
        low_res_frame.grid_columnconfigure(0, weight=1)
        Button(low_res_frame, text="R vs. T Measurement", command=lambda: self.launch_script(self.SCRIPT_PATHS["Delta Mode R-T"])).grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        Button(low_res_frame, text="I-V Measurement", command=lambda: self.launch_script(self.SCRIPT_PATHS["Delta Mode I-V"])).grid(row=1, column=0, sticky='ew', padx=5, pady=5)
        ttk.Button(low_res_frame, text="Browse Data", style='Browse.TButton', command=lambda: self.open_data_folder(self.DATA_PATHS["Delta Mode"])).grid(row=0, column=1, rowspan=2, sticky='ns', padx=5, pady=5)

        # --- Mid Resistance K2400 ---
        mid_res_frame1 = LabelFrame(launcher_frame, text='Mid Resistance (Keithley 2400)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        mid_res_frame1.pack(fill='x', padx=10, pady=5)
        mid_res_frame1.grid_columnconfigure(0, weight=1)
        Button(mid_res_frame1, text="I-V Measurement", command=lambda: self.launch_script(self.SCRIPT_PATHS["K2400 I-V"])).grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        Button(mid_res_frame1, text="Time vs. Current", command=lambda: self.launch_script(self.SCRIPT_PATHS["K2400 Time-Current"])).grid(row=1, column=0, sticky='ew', padx=5, pady=5)
        ttk.Button(mid_res_frame1, text="Browse Data", style='Browse.TButton', command=lambda: self.open_data_folder(self.DATA_PATHS["K2400"])).grid(row=0, column=1, rowspan=2, sticky='ns', padx=5, pady=5)

        # --- Mid Resistance K2400 / K2182 ---
        mid_res_frame2 = LabelFrame(launcher_frame, text='Mid Resistance (K2400 / K2182)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        mid_res_frame2.pack(fill='x', padx=10, pady=5)
        mid_res_frame2.grid_columnconfigure(0, weight=1)
        Button(mid_res_frame2, text="R vs. T Measurement", command=lambda: self.launch_script(self.SCRIPT_PATHS["K2400_2182 R-T"])).grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        Button(mid_res_frame2, text="I-V Measurement", command=lambda: self.launch_script(self.SCRIPT_PATHS["K2400_2182 I-V"])).grid(row=1, column=0, sticky='ew', padx=5, pady=5)
        ttk.Button(mid_res_frame2, text="Browse Data", style='Browse.TButton', command=lambda: self.open_data_folder(self.DATA_PATHS["K2400_2182"])).grid(row=0, column=1, rowspan=2, sticky='ns', padx=5, pady=5)

        # --- High Resistance ---
        high_res_frame = LabelFrame(launcher_frame, text='High Resistance (Keithley 6517B)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        high_res_frame.pack(fill='x', padx=10, pady=5)
        high_res_frame.grid_columnconfigure(0, weight=1)
        Button(high_res_frame, text="Resistivity Measurement", command=lambda: self.launch_script(self.SCRIPT_PATHS["K6517B Resistivity"])).grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        ttk.Button(high_res_frame, text="Browse Data", style='Browse.TButton', command=lambda: self.open_data_folder(self.DATA_PATHS["K6517B"])).grid(row=0, column=1, sticky='ns', padx=5, pady=5)

        # --- Pyroelectric Measurement ---
        pyro_frame = LabelFrame(launcher_frame, text='Pyroelectric Measurement (K6517B)', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        pyro_frame.pack(fill='x', padx=10, pady=5)
        pyro_frame.grid_columnconfigure(0, weight=1)
        Button(pyro_frame, text="Pyro Current vs. Temp", command=lambda: self.launch_script(self.SCRIPT_PATHS["Pyroelectric Current"])).grid(row=0, column=0, sticky='ew', padx=5, pady=5)
        ttk.Button(pyro_frame, text="Browse Data", style='Browse.TButton', command=lambda: self.open_data_folder(self.DATA_PATHS["Pyroelectric"])).grid(row=0, column=1, sticky='ns', padx=5, pady=5)

        # --- Utilities & Diagnostics ---
        util_frame = LabelFrame(launcher_frame, text='Utilities & Diagnostics', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_SUBTITLE, labelanchor='n', padx=10, pady=10)
        util_frame.pack(fill='x', padx=10, pady=15)
        Button(util_frame, text="Test Connected GPIB/VISA Instruments", command=self.run_gpib_test).pack(fill='x', padx=5, pady=(5, 2.5))
        Button(util_frame, text="Open User Manual", command=self.open_manual).pack(fill='x', padx=5, pady=(2.5, 5))

        return launcher_frame

    def launch_script(self, script_path):
        """Launches a python script in a new process."""
        print(f"Attempting to launch: {script_path}")
        if not os.path.exists(script_path):
            messagebox.showerror("File Not Found", f"The script could not be found at the specified path:\n\n{script_path}\n\nPlease edit the SCRIPT_PATHS dictionary.")
            return
        try:
            script_directory = os.path.dirname(script_path) or '.'
            subprocess.Popen([sys.executable, script_path], cwd=script_directory)
            print(f"Successfully launched '{os.path.basename(script_path)}'")
        except Exception as e:
            messagebox.showerror("Launch Error", f"An error occurred while trying to launch the script:\n\n{e}")
            print(f"Error launching script: {e}")

    def open_data_folder(self, folder_path):
        """Creates folder if it doesn't exist, then opens it."""
        try:
            # Create the directory if it doesn't exist.
            os.makedirs(folder_path, exist_ok=True)

            if platform.system() == "Windows":
                os.startfile(folder_path)
            elif platform.system() == "Darwin": # macOS
                subprocess.run(['open', folder_path])
            else: # linux
                subprocess.run(['xdg-open', folder_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the folder.\nPath: {folder_path}\n\nError: {e}")

    def open_manual(self):
        """Opens the user manual PDF file."""
        if not os.path.exists(self.MANUAL_FILE):
            messagebox.showwarning("Manual Not Found", f"The manual file '{self.MANUAL_FILE}' was not found in the same directory as the launcher.")
            return
        self.open_data_folder(os.path.abspath(self.MANUAL_FILE))

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
                text_area.insert('1.0', "\n".join(resources))
            else:
                text_area.insert('1.0', "No VISA instruments found.\n\n- Check NI-VISA or other backend installation.\n- Ensure instruments are powered on and connected.")
        except Exception as e:
            text_area.insert('1.0', f"An error occurred while scanning for instruments:\n\n{e}\n\nMake sure a VISA backend (like NI-VISA) is installed correctly.")

        text_area.config(state='disabled')
        Button(test_win, text="Close", command=test_win.destroy).pack(pady=10)

def main():
    root = tk.Tk()
    app = PICALauncherApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()
