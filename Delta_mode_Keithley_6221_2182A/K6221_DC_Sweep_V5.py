#-------------------------------------------------------------------------------
# Name:         GPIB Passthrough I-V Logger
# Purpose:      Perform a software-timed I-V sweep by controlling a K2182A
#               through a K6221 acting as a GPIB-to-Serial bridge.
#
# Author:       Prathamesh Deshmukh
# Created:      03/10/2025
#
# Version:      1.0
#
# Description:  This program connects only to the K6221 via GPIB. It uses the
#               6221's 'SYST:COMM:SER' commands to send commands to and receive
#               data from a serially connected K2182A. This avoids direct
#               PC-to-RS232 communication issues.
#-------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
import os
import sys
import time
import traceback
from datetime import datetime
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl
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
class Backend_Passthrough:
    """ Manages K6221 and K2182A via GPIB passthrough communication. """
    def __init__(self):
        self.k6221 = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")

    def connect(self, k6221_visa):
        if not self.rm: raise ConnectionError("VISA is not available.")
        print("\n--- [Backend] Connecting to K6221 (GPIB Bridge) ---")
        self.k6221 = self.rm.open_resource(k6221_visa); self.k6221.timeout=25000
        print(f"  K6221 Connected: {self.k6221.query('*IDN?').strip()}")

    def configure_instruments(self, compliance):
        print("\n--- [Backend] Configuring Instruments via Passthrough ---")
        # 1. Configure K6221 (Master)
        self.k6221.write("*RST")
        self.k6221.write("SOUR:FUNC CURR")
        self.k6221.write("SOUR:CURR:RANG:AUTO ON")
        self.k6221.write(f"SOUR:CURR:COMP {compliance}")
        print("  K6221 configured for DC current source.")

        # 2. Configure K2182A (Slave) via passthrough commands
        # These commands are sent over GPIB to the 6221, which then forwards
        # them out its RS-232 port to the 2182A.
        print("  Sending commands to K2182A via K6221 RS-232 Port...")
        self.k6221.write("SYST:COMM:SER:SEND '*RST'")
        time.sleep(1) # Wait for 2182A to reset
        self.k6221.write("SYST:COMM:SER:SEND 'SENS:FUNC \"VOLT:DC\"'")
        self.k6221.write("SYST:COMM:SER:SEND 'SENS:VOLT:DC:RANG:AUTO ON'")
        print("  K2182A configured for DC Voltage measurement.")

    def measure_point(self, current):
        """ Sources a current and reads voltage via passthrough. """
        # 1. Set current on K6221
        self.k6221.write(f"SOUR:CURR {current}")
        self.k6221.write("OUTP:STAT ON")

        # 2. Send 'READ?' command to K2182A via the K6221
        self.k6221.write("SYST:COMM:SER:SEND 'READ?'")

        # 3. Ask K6221 to retrieve the serial response from the K2182A
        voltage_str = self.k6221.query("SYST:COMM:SER:ENT?")

        return float(voltage_str)

    def turn_off_output(self):
        if self.k6221:
            try: self.k6221.write("OUTP:STAT OFF")
            except: pass
            print("  K6221 source is OFF.")

    def close(self):
        print("\n--- [Backend] Closing connection. ---")
        if self.k6221:
            self.turn_off_output()
            self.k6221.close()
            print("  K6221 connection closed.")

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Passthrough_IV_GUI:
    PROGRAM_VERSION = "1.0"
    # ... (Styling and constants are standard) ...
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'; CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_GREEN = '#A7C957'; CLR_ACCENT_RED = '#E74C3C'; CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("GPIB Passthrough I-V Logger")
        self.root.geometry("1600x950"); self.root.minsize(1300, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.sweep_thread = None
        self.backend = Backend_Passthrough()
        self.data_storage = {'current': [], 'voltage': [], 'resistance': []}

        self.setup_styles(); self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam'); style.configure('TFrame', background=self.CLR_BG_DARK); style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE); style.configure('TRadiobutton', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.map('TRadiobutton', background=[('active', self.CLR_BG_DARK)]); style.configure('TButton', font=self.FONT_BASE, padding=(10, 9))
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN, font=('Segoe UI', 11, 'bold')); style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT, font=('Segoe UI', 11, 'bold')); style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11, 'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER); header.pack(side='top', fill='x')
        Label(header, text=f"GPIB Passthrough I-V Logger v{self.PROGRAM_VERSION}", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        main_pane = ttk.PanedWindow(self.root, orient='horizontal'); main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500); main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG); main_pane.add(right_panel, weight=3)
        top_controls = ttk.Frame(left_panel); left_panel.add(top_controls, weight=0)
        self.create_input_frame(top_controls)
        console_pane = self.create_console_frame(left_panel); left_panel.add(console_pane, weight=1)
        self.create_graph_frame(right_panel)

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Sweep Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}; pady_val, padx_val = (5, 5), 10

        # --- SIMPLIFIED: Only one connection needed ---
        conn_frame = LabelFrame(frame, text="Connection", relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); conn_frame.grid(row=0, column=0, columnspan=2, sticky='ew', padx=padx_val, pady=pady_val)
        conn_frame.columnconfigure(0, weight=1)
        Label(conn_frame, text="Keithley 6221 (GPIB Address):").grid(row=0, column=0, padx=padx_val, pady=pady_val, sticky='w')
        self.k6221_cb = ttk.Combobox(conn_frame, font=self.FONT_BASE, state='readonly'); self.k6221_cb.grid(row=1, column=0, padx=padx_val, pady=(0, 5), sticky='ew')
        ttk.Button(conn_frame, text="Scan for Instruments", command=self._scan_for_visa).grid(row=2, column=0, padx=padx_val, pady=4, sticky='ew')

        # --- Standard Sweep Parameters ---
        Label(frame, text="Sample Name:").grid(row=1, column=0, columnspan=2, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE); self.entries["Sample Name"].grid(row=2, column=0, columnspan=2, padx=padx_val, pady=(0, 10), sticky='ew')
        Label(frame, text="Start Current (A):").grid(row=3, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Start Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Start Current"].grid(row=4, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew'); self.entries["Start Current"].insert(0, "-1E-5")
        Label(frame, text="Stop Current (A):").grid(row=3, column=1, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Stop Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Stop Current"].grid(row=4, column=1, padx=(5, padx_val), pady=(0, 5), sticky='ew'); self.entries["Stop Current"].insert(0, "1E-5")
        Label(frame, text="Number of Points:").grid(row=5, column=0, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Num Points"] = Entry(frame, font=self.FONT_BASE); self.entries["Num Points"].grid(row=6, column=0, padx=(padx_val, 5), pady=(0, 5), sticky='ew'); self.entries["Num Points"].insert(0, "51")
        Label(frame, text="Step Delay (s):").grid(row=5, column=1, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Delay"] = Entry(frame, font=self.FONT_BASE); self.entries["Delay"].grid(row=6, column=1, padx=(5, padx_val), pady=(0, 5), sticky='ew'); self.entries["Delay"].insert(0, "0.5")
        Label(frame, text="Compliance (V):").grid(row=7, column=0, columnspan=2, padx=padx_val, pady=pady_val, sticky='w'); self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].grid(row=8, column=0, columnspan=2, padx=padx_val, pady=(0, 10), sticky='ew'); self.entries["Compliance"].insert(0, "10")
        self.sweep_scale_var = tk.StringVar(value="Linear"); Label(frame, text="Sweep Scale:").grid(row=9, column=0, padx=padx_val, pady=pady_val, sticky='w'); ttk.Radiobutton(frame, text="Linear", variable=self.sweep_scale_var, value="Linear").grid(row=10, column=0, padx=padx_val, sticky='w'); ttk.Radiobutton(frame, text="Logarithmic", variable=self.sweep_scale_var, value="Logarithmic").grid(row=10, column=1, padx=padx_val, sticky='w')

        ttk.Button(frame, text="Browse Save Location...", command=self._browse_save).grid(row=11, column=0, columnspan=2, padx=padx_val, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start Sweep", command=self.start_sweep, style='Start.TButton'); self.start_button.grid(row=12, column=0, padx=(padx_val, 5), pady=(15, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop Sweep", command=self.stop_sweep, style='Stop.TButton', state='disabled'); self.stop_button.grid(row=12, column=1, padx=(5, padx_val), pady=(15, 10), sticky='ew')

    def create_console_frame(self, parent):
        # ... (Identical to previous versions) ...
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE); self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0); self.console.pack(pady=5, padx=5, fill='both', expand=True); self.log("Console initialized."); return frame

    def create_graph_frame(self, parent):
        # ... (Identical to previous versions) ...
        container = LabelFrame(parent, text='I-V Curve', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_TEXT_DARK, font=self.FONT_TITLE); container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG); self.canvas = FigureCanvasTkAgg(self.figure, container)
        gs = gridspec.GridSpec(2, 1, figure=self.figure); self.ax_main = self.figure.add_subplot(gs[0]); self.ax_sub = self.figure.add_subplot(gs[1])
        self.line_main, = self.ax_main.plot([], [], 'o-', c=self.CLR_ACCENT_RED, markersize=4); self.ax_main.set_title("I-V Curve", fontweight='bold'); self.ax_main.set_xlabel("Current (A)"); self.ax_main.set_ylabel("Voltage (V)")
        self.line_sub, = self.ax_sub.plot([], [], 's:', c=self.CLR_ACCENT_GREEN, markersize=4); self.ax_sub.set_xlabel("Current (A)"); self.ax_sub.set_ylabel("Resistance (Ω)")
        for ax in [self.ax_main, self.ax_sub]: ax.grid(True, ls='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0); self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S"); self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}"); self.console.see('end'); self.console.config(state='disabled')

    def start_sweep(self):
        try:
            self.params = { 'name': self.entries["Sample Name"].get(), 'start_i': float(self.entries["Start Current"].get()), 'stop_i': float(self.entries["Stop Current"].get()), 'points': int(self.entries["Num Points"].get()), 'delay': float(self.entries["Delay"].get()), 'compliance': float(self.entries["Compliance"].get()), 'scale': self.entries["Sample Name"].get(), 'k6221_visa': self.k6221_cb.get()}
            if not all(p for k, p in self.params.items() if k != 'name') or not hasattr(self, 'save_path'): raise ValueError("All fields and a save location are required.")

            self.start_button.config(state='disabled'); self.stop_button.config(state='normal'); self.is_running = True
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub]: line.set_data([], [])
            self.ax_main.set_title(f"I-V Curve: {self.params['name']}"); self.canvas.draw()

            self.sweep_thread = threading.Thread(target=self._sweep_worker, args=(self.params,), daemon=True)
            self.sweep_thread.start()
        except Exception as e:
            self.log(f"ERROR on startup: {traceback.format_exc()}"); messagebox.showerror("Input Error", f"{e}")

    def stop_sweep(self):
        if self.is_running:
            self.is_running = False
            self.log("Stop command received. Finishing current step...")
            self.stop_button.config(state='disabled')

    def _sweep_worker(self, params):
        try:
            self.backend.connect(params['k6221_visa'])
            self.backend.configure_instruments(params['compliance'])

            if self.sweep_scale_var.get() == 'Linear':
                current_points = np.linspace(params['start_i'], params['stop_i'], params['points'])
            else: # Logarithmic
                if params['start_i'] * params['stop_i'] <= 0: raise ValueError("Log sweep cannot cross or include zero.")
                start_log, stop_log = np.log10(abs(params['start_i'])), np.log10(abs(params['stop_i']))
                log_sweep = np.logspace(start_log, stop_log, params['points']); current_points = log_sweep * np.sign(params['start_i'])

            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); filename = f"{params['name']}_{ts}_IV.dat"
            self.data_filepath = os.path.join(self.save_path, filename)
            with open(self.data_filepath, 'w', newline='') as f:
                csv.writer(f).writerow([f"# Sample: {params['name']}"])
                csv.writer(f).writerow(["Set Current (A)", "Measured Voltage (V)", "Resistance (Ohm)"])

            self.log("Sweep started...")
            for i, current in enumerate(current_points):
                if not self.is_running: self.log("Sweep aborted by user."); break

                self.log(f"Step {i+1}/{len(current_points)}: Sourcing {current:.4e} A...")
                time.sleep(params['delay']) # Settle delay
                voltage = self.backend.measure_point(current)
                self.root.after(0, self._update_ui_with_point, current, voltage)
            else:
                self.log("Sweep completed successfully.")
        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}")
        finally:
            self.is_running = False
            self.backend.close()
            self.root.after(0, self._sweep_cleanup_ui)

    def _update_ui_with_point(self, current, voltage):
        """ This runs on the main GUI thread to safely update UI and save data. """
        resistance = voltage/current if current != 0 else float('inf')
        self.log(f"  Read: {voltage:.6e} V, R: {resistance:.6e} Ω")

        self.data_storage['current'].append(current)
        self.data_storage['voltage'].append(voltage)
        self.data_storage['resistance'].append(resistance)

        with open(self.data_filepath, 'a', newline='') as f:
            csv.writer(f).writerow([f"{current:.6e}", f"{voltage:.6e}", f"{resistance:.6e}"])

        self.line_main.set_data(self.data_storage['current'], self.data_storage['voltage'])
        self.line_sub.set_data(self.data_storage['current'], self.data_storage['resistance'])
        for ax in [self.ax_main, self.ax_sub]: ax.relim(); ax.autoscale_view()
        self.figure.tight_layout(pad=3.0); self.canvas.draw()

    def _sweep_cleanup_ui(self):
        self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
        self.log("Ready for next sweep.")

    def _scan_for_visa(self):
        if self.backend.rm: self.log("Scanning..."); resources = self.backend.rm.list_resources()
        else: self.log("VISA manager not found."); return
        if resources:
            self.log(f"Found: {resources}"); gpib_res = [r for r in resources if 'GPIB' in r]
            self.k6221_cb['values'] = gpib_res
            if gpib_res: self.k6221_cb.set(gpib_res[0])
        else: self.log("No instruments found.")

    def _browse_save(self):
        path = filedialog.askdirectory();
        if path: self.save_path = path; self.log(f"Save location set to: {path}")

    def _on_closing(self):
        if self.is_running: self.is_running = False; time.sleep(0.2)
        self.backend.close(); self.root.destroy()

def main():
    root = tk.Tk()
    app = Passthrough_IV_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()