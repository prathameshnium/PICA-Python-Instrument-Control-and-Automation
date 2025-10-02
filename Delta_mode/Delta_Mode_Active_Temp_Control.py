# -------------------------------------------------------------------------------
# Name:         Delta Mode Active Temperature Control
# Purpose:      Perform a temperature-dependent Delta mode measurement with a
#               Keithley 6221/2182A by actively controlling the temperature
#               with a Lakeshore 350.
#
# Author:       Prathamesh Deshmukh
# Created:      03/10/2025
#
# Version:      1.0 - Active Control
#
# Description:  This version implements an active temperature control workflow:
#               Ramp -> Stabilize -> Measure. It uses heater range 5 for
#               heating and includes a critical safety feature to turn the
#               heater off on stop, error, or exit.
# -------------------------------------------------------------------------------

# --- Packages ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, Button, filedialog, messagebox, scrolledtext, Canvas
import numpy as np
import os
import time
import traceback
from datetime import datetime
import csv
from collections import deque
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib as mpl

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

# -------------------------------------------------------------------------------
# --- BACKEND INSTRUMENT CONTROL ---
# -------------------------------------------------------------------------------

class Active_Delta_Backend:
    """ Manages both Keithley 6221 and Lakeshore 350 for active measurements. """
    def __init__(self):
        self.keithley = None
        self.lakeshore = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"Could not initialize VISA resource manager. Error: {e}")
                self.rm = None
        else:
            self.rm = None

    def initialize_instruments(self, keithley_visa, lakeshore_visa):
        """ Connects to both instruments. """
        print("\n--- [Backend] Initializing Instruments ---")
        if not self.rm:
            raise ConnectionError("VISA Resource Manager is not available.")
        # Connect to Keithley
        print(f"  Connecting to Keithley 6221 at {keithley_visa}...")
        self.keithley = self.rm.open_resource(keithley_visa)
        self.keithley.timeout = 25000
        print(f"    Connected to: {self.keithley.query('*IDN?').strip()}")
        # Connect to Lakeshore
        print(f"  Connecting to Lakeshore 350 at {lakeshore_visa}...")
        self.lakeshore = self.rm.open_resource(lakeshore_visa)
        print(f"    Connected to: {self.lakeshore.query('*IDN?').strip()}")
        print("--- [Backend] Instrument Initialization Complete ---")

    def setup_keithley_delta(self, current, compliance):
        """ Configures the Keithley for a Delta Mode measurement. """
        if not self.keithley: return
        print("  Configuring Keithley for Delta Mode...")
        self.keithley.write("*rst; status:preset; *cls")
        self.keithley.write(f"SOUR:DELT:HIGH {current}")
        self.keithley.write(f"SOUR:DELT:PROT {compliance}")
        self.keithley.write("SOUR:DELT:ARM")
        time.sleep(1)
        self.keithley.write("INIT:IMM")
        print("  Keithley Armed for Delta Measurement.")

    def setup_lakeshore_ramp(self, setpoint, rate):
        """ Configures the Lakeshore to heat to a setpoint. """
        if not self.lakeshore: return
        print(f"  Configuring Lakeshore to ramp to {setpoint} K at {rate} K/min...")
        self.lakeshore.write('*RST'); time.sleep(0.5); self.lakeshore.write('*CLS')
        # Set heater to Output 1, Control Sensor A, PID mode, display as Power
        self.lakeshore.write("OUTMODE 1,1,1,1")
        # Set the Setpoint for Output 1
        self.lakeshore.write(f"SETP 1,{setpoint}")
        # Set Ramp for Output 1: ON, Rate
        self.lakeshore.write(f"RAMP 1,1,{rate}")
        # Set Heater Range for Output 1 to HIGH (5)
        self.lakeshore.write("RANGE 1,5")
        print("  Lakeshore ramp configured. Heater is ON (Range 5).")

    def get_temperature(self):
        if not self.lakeshore: return 0.0
        return float(self.lakeshore.query('KRDG? A').strip())

    def get_delta_measurement(self):
        if not self.keithley: return 0.0, 0.0
        raw_data = self.keithley.query('SENSe:DATA:FRESh?')
        voltage = float(raw_data.strip().split(',')[0])
        return voltage

    def close_instruments(self):
        """ CRITICAL: Turns off heater and closes all connections. """
        print("--- [Backend] Closing instrument connections. ---")
        try:
            if self.lakeshore:
                print("  SAFETY: Setting Lakeshore heater to OFF (Range 0).")
                self.lakeshore.write("RANGE 1,0")
                self.lakeshore.write("SETP 1,0")
            if self.keithley:
                print("  Clearing Keithley source.")
                self.keithley.write("SOUR:CLE"); self.keithley.write("*RST")
        except Exception as e:
            print(f"  WARNING: A non-critical error occurred during shutdown: {e}")
        finally:
            if self.keithley: self.keithley.close(); self.keithley = None; print("  Keithley connection closed.")
            if self.lakeshore: self.lakeshore.close(); self.lakeshore = None; print("  Lakeshore connection closed.")


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class Active_Delta_GUI:
    PROGRAM_VERSION = "1.0 Active"
    LOGO_FILE_PATH = "UGC_DAE_CSR.jpeg"
    # --- State Machine Parameters ---
    RAMP_TOLERANCE = 1.0       # K, how close to get to setpoint before stabilizing
    STABILITY_TOLERANCE = 0.05 # K, max deviation for temperature to be "stable"
    STABILITY_CHECKS = 5       # Number of consecutive checks for stability

    # --- Theming ---
    CLR_BG_DARK = '#2B3D4F'; CLR_HEADER = '#3A506B'; CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'; CLR_ACCENT_GREEN = '#A7C957'; CLR_ACCENT_RED = '#E74C3C'
    CLR_CONSOLE_BG = '#1E2B38'; CLR_GRAPH_BG = '#FFFFFF'
    FONT_BASE = ('Segoe UI', 11); FONT_TITLE = ('Segoe UI', 13, 'bold'); FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("Delta Mode with Active Temperature Control")
        self.root.geometry("1600x950"); self.root.minsize(1300, 850)
        self.root.configure(bg=self.CLR_BG_DARK)

        self.is_running = False
        self.measurement_state = 'IDLE' # IDLE, RAMPING, STABILIZING, MEASURING
        self.backend = Active_Delta_Backend()
        self.stability_buffer = deque(maxlen=self.STABILITY_CHECKS)
        self.data_storage = {'time': [], 'voltage': [], 'resistance': [], 'temperature': []}
        self.logo_image = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        style = ttk.Style(self.root); style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TPanedWindow', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9)); style.configure('Start.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_GREEN, foreground=self.CLR_TEXT_DARK)
        style.map('Start.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure('Stop.TButton', font=self.FONT_BASE, padding=(10, 9), background=self.CLR_ACCENT_RED, foreground=self.CLR_FG_LIGHT)
        style.map('Stop.TButton', background=[('active', '#D63C2A'), ('hover', '#D63C2A')])
        mpl.rcParams.update({'font.family': 'Segoe UI', 'font.size': 11, 'axes.titlesize': 15, 'axes.labelsize': 13})

    def create_widgets(self):
        # Header
        header = tk.Frame(self.root, bg=self.CLR_HEADER); header.pack(side='top', fill='x')
        Label(header, text="Delta Mode (Active Temperature Control)", bg=self.CLR_HEADER, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        # Main Layout
        main_pane = ttk.PanedWindow(self.root, orient='horizontal'); main_pane.pack(fill='both', expand=True, padx=10, pady=10)
        left_panel = ttk.PanedWindow(main_pane, orient='vertical', width=500); main_pane.add(left_panel, weight=1)
        right_panel = tk.Frame(main_pane, bg=self.CLR_GRAPH_BG); main_pane.add(right_panel, weight=3)
        # Left Panel Content
        top_controls = ttk.Frame(left_panel); left_panel.add(top_controls, weight=0)
        self.create_input_frame(top_controls) # Info is now inside input frame
        console_pane = self.create_console_frame(left_panel); left_panel.add(console_pane, weight=1)
        # Right Panel Content
        self.create_graph_frame(right_panel)

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text='Experiment Parameters', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        frame.pack(pady=5, padx=10, fill='x')
        for i in range(2): frame.grid_columnconfigure(i, weight=1)
        self.entries = {}
        pady=(4,4); padx=10

        Label(frame, text="Sample Name:").grid(row=0, column=0, columnspan=2, padx=padx, pady=pady, sticky='w')
        self.entries["Sample Name"] = Entry(frame, font=self.FONT_BASE); self.entries["Sample Name"].grid(row=1, column=0, columnspan=2, padx=padx, pady=(0, 8), sticky='ew')

        Label(frame, text="Apply Current (A):").grid(row=2, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Apply Current"] = Entry(frame, font=self.FONT_BASE); self.entries["Apply Current"].insert(0, "1E-6")
        self.entries["Apply Current"].grid(row=3, column=0, padx=(padx, 5), pady=(0, 8), sticky='ew')
        Label(frame, text="Compliance (V):").grid(row=2, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Compliance"] = Entry(frame, font=self.FONT_BASE); self.entries["Compliance"].insert(0, "10")
        self.entries["Compliance"].grid(row=3, column=1, padx=(5, padx), pady=(0, 8), sticky='ew')

        Label(frame, text="Set Point (K):").grid(row=4, column=0, padx=padx, pady=pady, sticky='w')
        self.entries["Setpoint"] = Entry(frame, font=self.FONT_BASE); self.entries["Setpoint"].insert(0, "300")
        self.entries["Setpoint"].grid(row=5, column=0, padx=(padx, 5), pady=(0, 8), sticky='ew')
        Label(frame, text="Ramp Rate (K/min):").grid(row=4, column=1, padx=padx, pady=pady, sticky='w')
        self.entries["Ramp Rate"] = Entry(frame, font=self.FONT_BASE); self.entries["Ramp Rate"].insert(0, "5")
        self.entries["Ramp Rate"].grid(row=5, column=1, padx=(5, padx), pady=(0, 8), sticky='ew')

        Label(frame, text="Keithley 6221 VISA:").grid(row=6, column=0, padx=padx, pady=pady, sticky='w')
        self.keithley_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.keithley_cb.grid(row=7, column=0, padx=(padx, 5), pady=(0, 8), sticky='ew')
        Label(frame, text="Lakeshore 350 VISA:").grid(row=6, column=1, padx=padx, pady=pady, sticky='w')
        self.lakeshore_cb = ttk.Combobox(frame, font=self.FONT_BASE, state='readonly'); self.lakeshore_cb.grid(row=7, column=1, padx=(5, padx), pady=(0, 8), sticky='ew')

        # Buttons
        ttk.Button(frame, text="Scan for Instruments", command=self._scan_for_visa).grid(row=8, column=0, columnspan=2, padx=padx, pady=4, sticky='ew')
        ttk.Button(frame, text="Browse Save Location...", command=self._browse_save).grid(row=9, column=0, columnspan=2, padx=padx, pady=4, sticky='ew')
        self.start_button = ttk.Button(frame, text="Start", command=self.start_measurement, style='Start.TButton')
        self.start_button.grid(row=10, column=0, padx=(padx, 5), pady=(10, 10), sticky='ew')
        self.stop_button = ttk.Button(frame, text="Stop", command=self.stop_measurement, style='Stop.TButton', state='disabled')
        self.stop_button.grid(row=10, column=1, padx=(5, padx), pady=(10, 10), sticky='ew')

    def create_console_frame(self, parent):
        frame = LabelFrame(parent, text='Console Output', relief='groove', bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        self.console = scrolledtext.ScrolledText(frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console.pack(pady=5, padx=5, fill='both', expand=True); self.log("Console initialized.")
        return frame

    def create_graph_frame(self, parent):
        container = LabelFrame(parent, text='Live Graphs', relief='groove', bg=self.CLR_GRAPH_BG, fg=self.CLR_TEXT_DARK, font=self.FONT_TITLE)
        container.pack(fill='both', expand=True, padx=5, pady=5)
        self.figure = Figure(figsize=(8, 8), dpi=100, facecolor=self.CLR_GRAPH_BG)
        self.canvas = FigureCanvasTkAgg(self.figure, container)
        gs = gridspec.GridSpec(2, 2, figure=self.figure); self.ax_main = self.figure.add_subplot(gs[0, :]); self.ax_sub1 = self.figure.add_subplot(gs[1, 0]); self.ax_sub2 = self.figure.add_subplot(gs[1, 1])
        self.line_main, = self.ax_main.plot([], [], 'o-', c=self.CLR_ACCENT_RED, markersize=3)
        self.ax_main.set_title("Resistance vs. Temperature"); self.ax_main.set_xlabel("Temperature (K)"); self.ax_main.set_ylabel("Resistance (Ω)")
        self.line_sub1, = self.ax_sub1.plot([], [], '.-', c='blue', markersize=4)
        self.ax_sub1.set_xlabel("Temperature (K)"); self.ax_sub1.set_ylabel("Voltage (V)")
        self.line_sub2, = self.ax_sub2.plot([], [], '.-', c=self.CLR_ACCENT_GREEN, markersize=4)
        self.ax_sub2.set_xlabel("Time (s)"); self.ax_sub2.set_ylabel("Temperature (K)")
        for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]: ax.grid(True, ls='--', alpha=0.6)
        self.figure.tight_layout(pad=3.0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S"); self.console.config(state='normal')
        self.console.insert('end', f"[{ts}] {message}\n"); self.console.see('end'); self.console.config(state='disabled')

    def start_measurement(self):
        try:
            self.params = { 'name': self.entries["Sample Name"].get(), 'current': float(self.entries["Apply Current"].get()), 'compliance': float(self.entries["Compliance"].get()), 'setpoint': float(self.entries["Setpoint"].get()), 'rate': float(self.entries["Ramp Rate"].get()), 'k_visa': self.keithley_cb.get(), 'l_visa': self.lakeshore_cb.get() }
            if not all(self.params.values()) or not hasattr(self, 'save_path'): raise ValueError("All fields and a save location are required.")

            self.backend.initialize_instruments(self.params['k_visa'], self.params['l_visa'])
            self.backend.setup_keithley_delta(self.params['current'], self.params['compliance'])
            self.backend.setup_lakeshore_ramp(self.params['setpoint'], self.params['rate'])

            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); filename = f"{self.params['name']}_{ts}_Delta_Active.dat"
            self.data_filepath = os.path.join(self.save_path, filename)
            with open(self.data_filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Sample: {self.params['name']}", f"I: {self.params['current']:.2e}A", f"Setpoint: {self.params['setpoint']}K"])
                writer.writerow(["Timestamp", "Elapsed Time (s)", "Temperature (K)", "Voltage (V)", "Resistance (Ohm)"])
            self.log(f"Output file created: {filename}")

            self.is_running = True; self.start_time = time.time(); self.measurement_state = 'RAMPING'
            self.start_button.config(state='disabled'); self.stop_button.config(state='normal')
            for key in self.data_storage: self.data_storage[key].clear()
            for line in [self.line_main, self.line_sub1, self.line_sub2]: line.set_data([], [])
            self.canvas.draw()
            self.log(f"State changed to RAMPING. Target: {self.params['setpoint']} K.")
            self.root.after(2000, self._state_monitor_loop)
        except Exception as e:
            self.log(f"ERROR on startup: {traceback.format_exc()}"); messagebox.showerror("Initialization Error", f"{e}")
            self.backend.close_instruments()

    def stop_measurement(self):
        if self.is_running:
            self.is_running = False # This will stop the loop
            self.log("Stop command received. Shutting down safely...")
            self.backend.close_instruments()
            self.measurement_state = 'IDLE'
            self.start_button.config(state='normal'); self.stop_button.config(state='disabled')
            messagebox.showinfo("Info", "Measurement stopped and instruments shut down safely.")

    def _state_monitor_loop(self):
        if not self.is_running: return
        try:
            current_temp = self.backend.get_temperature()
            elapsed_time = time.time() - self.start_time
            self.data_storage['time'].append(elapsed_time); self.data_storage['temperature'].append(current_temp)
            self.line_sub2.set_data(self.data_storage['time'], self.data_storage['temperature']) # Always update T-t plot

            # --- RAMPING STATE ---
            if self.measurement_state == 'RAMPING':
                self.log(f"Ramping... Current Temp: {current_temp:.2f} K")
                if abs(current_temp - self.params['setpoint']) < self.RAMP_TOLERANCE:
                    self.measurement_state = 'STABILIZING'
                    self.stability_buffer.clear()
                    self.log(f"State changed to STABILIZING. Temp near setpoint.")
            # --- STABILIZING STATE ---
            elif self.measurement_state == 'STABILIZING':
                self.stability_buffer.append(current_temp)
                log_msg = f"Stabilizing... Temp: {current_temp:.3f} K | Buffer: {len(self.stability_buffer)}/{self.STABILITY_CHECKS}"
                self.log(log_msg)
                if len(self.stability_buffer) == self.STABILITY_CHECKS:
                    temp_range = max(self.stability_buffer) - min(self.stability_buffer)
                    if temp_range < self.STABILITY_TOLERANCE:
                        self.measurement_state = 'MEASURING'
                        self.log(f"State changed to MEASURING. Temperature is stable (deviation < {self.STABILITY_TOLERANCE} K).")
                    else: # Not stable yet, buffer will slide
                        pass
            # --- MEASURING STATE ---
            elif self.measurement_state == 'MEASURING':
                voltage = self.backend.get_delta_measurement()
                resistance = voltage / self.params['current'] if self.params['current'] != 0 else float('inf')
                self.data_storage['voltage'].append(voltage); self.data_storage['resistance'].append(resistance)
                # Save data
                with open(self.data_filepath, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), f"{elapsed_time:.2f}", f"{current_temp:.4f}", f"{voltage:.6e}", f"{resistance:.6e}"])
                # Update main plots
                self.line_main.set_data(self.data_storage['temperature'], self.data_storage['resistance'])
                self.line_sub1.set_data(self.data_storage['temperature'], self.data_storage['voltage'])
                self.log(f"Measuring... T:{current_temp:.3f}K | R:{resistance:.3e}Ω")

            # Redraw all axes and schedule next loop
            for ax in [self.ax_main, self.ax_sub1, self.ax_sub2]: ax.relim(); ax.autoscale_view()
            self.figure.tight_layout(pad=3.0); self.canvas.draw()
            self.root.after(2000, self._state_monitor_loop)

        except Exception as e:
            self.log(f"RUNTIME ERROR: {traceback.format_exc()}"); self.stop_measurement()
            messagebox.showerror("Runtime Error", "A critical error occurred. Check console.")

    def _scan_for_visa(self):
        if self.backend.rm: self.log("Scanning..."); resources = self.backend.rm.list_resources()
        else: self.log("VISA manager not found."); return
        if resources: self.log(f"Found: {resources}"); self.keithley_cb['values'] = resources; self.lakeshore_cb['values'] = resources
        for r in resources:
            if "13" in r: self.keithley_cb.set(r)
            if "15" in r or "12" in r: self.lakeshore_cb.set(r)

    def _browse_save(self):
        path = filedialog.askdirectory()
        if path: self.save_path = path; self.log(f"Save location: {path}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno("Exit", "Measurement running. Stop and exit safely?"): self.stop_measurement(); self.root.destroy()
        else: self.root.destroy()

def main():
    root = tk.Tk()
    app = Active_Delta_GUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
