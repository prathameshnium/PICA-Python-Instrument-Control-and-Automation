#-------------------------------------------------------------------------------
# Name:         Keithley 6221 DC Sweeper
# Purpose:      Automate a DC current sweep for sample screening.
#
# Author:       Prathamesh Deshmukh
#
# Created:      03/10/2025
#
# Version:      4.0 (Revision: To DC Current Sweeper)
#
# Description:  This program provides a GUI to automatically sweep a range of
#               DC currents using a Keithley 6221. It is designed for quickly
#               screening samples by observing the resulting voltage on an
#               external multimeter. The sweep runs in a background thread
#               to keep the GUI responsive.
#-------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, messagebox, scrolledtext, Canvas, Radiobutton
import numpy as np
import os
import sys
import time
import traceback
from datetime import datetime
import threading

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------
class Backend_DCS:
    """ Backend for controlling the 6221 as a simple DC Current Source. """
    # This backend is identical to the previous version (3.0)
    def __init__(self):
        self.keithley = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")

    def connect(self, visa_address):
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        print("\n--- [Backend] Connecting to Keithley 6221 ---")
        self.keithley = self.rm.open_resource(visa_address)
        self.keithley.timeout = 10000
        print(f"  Connected to: {self.keithley.query('*IDN?').strip()}")

    def configure_source(self, compliance_v):
        if not self.keithley: raise ConnectionError("Instrument not connected.")
        print("--- [Backend] Configuring for DC Source mode ---")
        self.keithley.write("*RST")
        self.keithley.write("SOUR:FUNC CURR")
        self.keithley.write(f"SOUR:CURR:COMP {compliance_v}")
        print(f"  Compliance set to {compliance_v} V.")

    def set_current(self, current_level):
        if not self.keithley: raise ConnectionError("Instrument not connected.")
        self.keithley.write(f"SOUR:CURR {current_level}")
        self.keithley.write("OUTP:STAT ON")

    def turn_off_output(self):
        if not self.keithley: raise ConnectionError("Instrument not connected.")
        print("--- [Backend] Turning Output OFF ---")
        self.keithley.write("OUTP:STAT OFF")

    def close(self):
        print("--- [Backend] Shutting down instrument ---")
        if self.keithley:
            try:
                self.turn_off_output()
                self.keithley.close()
                print("  Connection closed.")
            except pyvisa.errors.VisaIOError as e:
                print(f"  Warning during shutdown: {e}")
            finally:
                self.keithley = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class DC_Sweeper_GUI:
    PROGRAM_VERSION = "4.0"
    LOGO_SIZE = 110
    LOGO_FILE_PATH = resource_path("_assets/UGC_DAE_CSR.jpeg")

    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'; CLR_ACCENT_GOLD = '#FFC107'; CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'; CLR_CONSOLE_BG = '#1E2B38'
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Keithley 6221 DC Sweeper")
        self.root.geometry("550x900")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(500, 850)

        self.backend = Backend_DCS()
        self.logo_image = None
        self.is_sweeping = False
        self.sweep_thread = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TRadiobutton', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.map('TRadiobutton', background=[('active', self.CLR_BG_DARK)])
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_TEXT_DARK,
                        background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)])
        style.configure('Start.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])

    def create_widgets(self):
        self.create_header()
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        self.create_connection_frame(main_frame)
        self.create_control_frame(main_frame)
        self.create_console_frame(main_frame)

    def create_header(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        Label(header, text="K6221 DC Current Sweeper", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        Label(header, text=f"v{self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def create_connection_frame(self, parent):
        frame = LabelFrame(parent, text='Connection', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=3); frame.columnconfigure(1, weight=1)
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly')
        self.keithley_cb.grid(row=0, column=0, padx=(10,5), pady=10, sticky='ew')
        self.scan_button = ttk.Button(frame, text="Scan", command=self._scan_for_instruments)
        self.scan_button.grid(row=0, column=1, padx=(0,10), pady=10, sticky='ew')
        self.connect_button = ttk.Button(frame, text="Connect", command=self._connect_instrument)
        self.connect_button.grid(row=1, column=0, columnspan=2, padx=10, pady=(0,10), sticky='ew')

    def create_control_frame(self, parent):
        frame = LabelFrame(parent, text='Sweep Controls', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        frame.columnconfigure(0, weight=1); frame.columnconfigure(1, weight=1)
        self.entries = {}
        pady = (5,5); padx=10

        Label(frame, text="Start Current (A):").grid(row=0, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Start Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Start Current"].insert(0, "-1E-5")
        self.entries["Start Current"].grid(row=1, column=0, padx=(padx, 5), pady=(0, 5), sticky='ew')

        Label(frame, text="Stop Current (A):").grid(row=0, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Stop Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Stop Current"].insert(0, "1E-5")
        self.entries["Stop Current"].grid(row=1, column=1, padx=(5, padx), pady=(0, 5), sticky='ew')

        Label(frame, text="Number of Points:").grid(row=2, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Num Points"] = Entry(frame, font=self.FONT_BASE); self.entries["Num Points"].insert(0, "51")
        self.entries["Num Points"].grid(row=3, column=0, padx=(padx, 5), pady=(0, 5), sticky='ew')

        Label(frame, text="Step Delay (s):").grid(row=2, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Delay"] = Entry(frame, font=self.FONT_BASE); self.entries["Delay"].insert(0, "0.5")
        self.entries["Delay"].grid(row=3, column=1, padx=(5, padx), pady=(0, 5), sticky='ew')

        Label(frame, text="Compliance (V):").grid(row=4, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].insert(0, "10")
        self.entries["Compliance"].grid(row=5, column=0, columnspan=2, padx=padx, pady=(0, 10), sticky='ew')

        self.sweep_scale_var = tk.StringVar(value="Linear")
        Label(frame, text="Sweep Scale:").grid(row=6, column=0, columnspan=2, padx=padx, pady=pady, sticky='w')
        ttk.Radiobutton(frame, text="Linear", variable=self.sweep_scale_var, value="Linear").grid(row=7, column=0, padx=padx, sticky='w')
        ttk.Radiobutton(frame, text="Logarithmic", variable=self.sweep_scale_var, value="Logarithmic").grid(row=7, column=1, padx=padx, sticky='w')

        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_sweep, style='Start.TButton', state='disabled')
        self.start_button.grid(row=8, column=0, padx=(padx,5), pady=(15, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop Sweep", command=self.stop_sweep, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=8, column=1, padx=(5,padx), pady=(15, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='both', expand=True)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console.pack(pady=5, padx=5, fill='both', expand=True)
        self.log("Console initialized. Please connect to an instrument.")

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n")
        self.console.see('end')
        self.console.config(state='disabled')

    def _scan_for_instruments(self):
        # ... (code is identical to previous versions)
        if not pyvisa or self.backend.rm is None:
            self.log("ERROR: PyVISA not found or failed to initialize."); return
        self.log("Scanning for VISA instruments...")
        try:
            resources = self.backend.rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_cb['values'] = resources
                for res in resources:
                    if "GPIB" in res and "13" in res: self.keithley_cb.set(res)
            else: self.log("No VISA instruments found.")
        except Exception as e: self.log(f"Could not scan for instruments: {e}")

    def _connect_instrument(self):
        try:
            visa_address = self.keithley_cb.get()
            if not visa_address: raise ValueError("Please select a VISA address.")
            self.backend.connect(visa_address)
            self.log("Connection successful.")
            self.start_button.config(state='normal')
            self.connect_button.config(text="Connected", state='disabled')
        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Connection Error", f"Could not connect.\n{e}")

    def start_sweep(self):
        try:
            params = {
                'start_i': float(self.entries["Start Current"].get()),
                'stop_i': float(self.entries["Stop Current"].get()),
                'points': int(self.entries["Num Points"].get()),
                'delay': float(self.entries["Delay"].get()),
                'compliance': float(self.entries["Compliance"].get()),
                'scale': self.sweep_scale_var.get()
            }
            if params['points'] < 2: raise ValueError("Number of points must be at least 2.")
            if params['delay'] < 0: raise ValueError("Delay must not be negative.")

            if params['scale'] == 'Linear':
                current_points = np.linspace(params['start_i'], params['stop_i'], params['points'])
            else:
                if params['start_i'] * params['stop_i'] <= 0:
                    raise ValueError("Log sweep cannot cross or include zero.")
                start_log, stop_log = np.log10(abs(params['start_i'])), np.log10(abs(params['stop_i']))
                log_sweep = np.logspace(start_log, stop_log, params['points'])
                current_points = log_sweep * np.sign(params['start_i'])

            self.is_sweeping = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')

            self.backend.configure_source(params['compliance'])

            # Run the sweep in a background thread to keep the GUI responsive
            self.sweep_thread = threading.Thread(target=self._sweep_worker,
                                                 args=(current_points, params['delay']),
                                                 daemon=True)
            self.sweep_thread.start()

        except Exception as e:
            self.log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Input Error", f"Could not start sweep.\n{e}")

    def stop_sweep(self):
        if self.is_sweeping:
            self.is_sweeping = False
            self.log("Stop command received. Finishing current step...")
            self.stop_button.config(state='disabled') # Prevent multiple clicks

    def _sweep_worker(self, current_points, delay):
        """ This function runs in a separate thread. Do not touch GUI elements from here. """
        self.log("Sweep started...")
        try:
            for i, current in enumerate(current_points):
                if not self.is_sweeping:
                    self.log("Sweep aborted by user.")
                    break

                self.log(f"Step {i+1}/{len(current_points)}: Setting current to {current:.4e} A")
                self.backend.set_current(current)
                time.sleep(delay)
            else: # This 'else' belongs to the 'for' loop, runs if loop completes without 'break'
                self.log("Sweep completed successfully.")

        except Exception as e:
            self.log(f"RUNTIME ERROR: {e}")
            messagebox.showerror("Runtime Error", f"A critical error occurred during the sweep.\n{e}")
        finally:
            # Always turn off the output and clean up the UI
            self.is_sweeping = False
            self.backend.turn_off_output()
            # Schedule the UI update to run on the main GUI thread
            self.root.after(0, self._sweep_cleanup_ui)

    def _sweep_cleanup_ui(self):
        """ This function is called from the worker thread to safely update the GUI. """
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.log("Output is OFF. Ready for next sweep.")

    def _on_closing(self):
        if self.is_sweeping:
            self.is_sweeping = False # Signal the thread to stop
            time.sleep(0.2) # Give a moment for the thread to see the flag
        self.backend.close()
        self.root.destroy()

def main():
    root = tk.Tk()
    app = DC_Sweeper_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()