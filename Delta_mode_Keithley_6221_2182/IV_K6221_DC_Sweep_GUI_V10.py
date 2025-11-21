# -------------------------------------------------------------------------------
# Name:         GPIB Passthrough I-V
# Purpose:      Perform a software-timed I-V sweep by controlling a K2182
#               through a K6221 acting as a GPIB-to-Serial bridge.
# Author:       Prathamesh Deshmukh
# Version:      1.6 (Switched to Free-Running Fetch Mode)
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, filedialog, messagebox, scrolledtext, Canvas
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
import queue

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pyvisa
except ImportError:
    pyvisa = None

import runpy
from multiprocessing import Process

# --- Utility Functions ---


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


def run_script_process(script_path):
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"Sub-process Error: {e}")


def launch_plotter_utility():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        plotter_path = os.path.join(
            script_dir,
            "..",
            "Utilities",
            "PlotterUtil_GUI_v3.py")
        if not os.path.exists(plotter_path):
            return
        Process(target=run_script_process, args=(plotter_path,)).start()
    except Exception:
        pass


def launch_gpib_scanner():
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scanner_path = os.path.join(
            script_dir,
            "..",
            "Utilities",
            "GPIB_Instrument_Scanner_GUI_v4.py")
        if not os.path.exists(scanner_path):
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception:
        pass

# -------------------------------------------------------------------------------
# --- BACKEND ---
# -------------------------------------------------------------------------------


class Backend_Passthrough:
    def __init__(self):
        self.visa_queue = queue.Queue()
        self.k6221 = None
        self.rm = None
        if pyvisa:
            try:
                self.rm = pyvisa.ResourceManager()
            except Exception as e:
                print(f"VISA Init Error: {e}")

    def connect(self, k6221_visa):
        if not self.rm:
            raise ConnectionError("VISA is not available.")
        self.k6221 = self.rm.open_resource(k6221_visa)
        self.k6221.timeout = 25000
        print(f"K6221 Connected: {self.k6221.query('*IDN?').strip()}")

    def configure_instruments(self, compliance):
        print("\n--- [Backend] Configuring Instruments via Passthrough ---")
        self.k6221.write("*RST")
        self.k6221.write("SOUR:FUNC CURR")
        self.k6221.write("SOUR:CURR:RANG:AUTO ON")
        self.k6221.write(f"SOUR:CURR:COMP {compliance}")

        self.k6221.write("SYST:COMM:SER:SEND '*RST'")
        time.sleep(0.5)
        self.k6221.write("SYST:COMM:SER:SEND 'FUNC \"VOLT\"'")
        time.sleep(0.5)
        self.k6221.write("SYST:COMM:SER:SEND 'SENS:VOLT:DC:RANG:AUTO ON'")
        time.sleep(0.5)
        self.k6221.write("SYST:COMM:SER:SEND 'INIT:CONT ON'")
        time.sleep(0.5)

    def set_current(self, current):
        self.k6221.write(f"SOUR:CURR {current}")
        time.sleep(0.1)
        self.k6221.write("OUTP:STAT ON")
        time.sleep(0.1)

    def read_voltage(self):
        self.k6221.write("SYST:COMM:SER:SEND 'FETC?'")
        timeout = 2.0
        start_poll_time = time.time()
        voltage_str = ""
        while time.time() - start_poll_time < timeout:
            response = self.k6221.query("SYST:COMM:SER:ENT?").strip()
            if response:
                voltage_str = response
                break
            time.sleep(0.05)

        if not voltage_str:
            raise TimeoutError("No response from K2182.")
        return float(voltage_str.strip().split('\n')[-1])

    def close(self):
        if self.k6221:
            try:
                self.k6221.write("SYST:COMM:SER:SEND 'INIT:CONT OFF'")
                self.k6221.write("OUTP:STAT OFF")
                self.k6221.close()
            except Exception:
                pass

# -------------------------------------------------------------------------------
# --- GUI ---
# -------------------------------------------------------------------------------


class Passthrough_IV_GUI:
    PROGRAM_VERSION = "1.6"
    LOGO_SIZE = 110
    LOGO_FILE_PATH = resource_path("../assets/LOGO/UGC_DAE_CSR_NBG.jpeg")
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_RED = '#E74C3C'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_GRAPH_BG = '#FFFFFF'
    FONT_BASE = ('Segoe UI', 11)

    def __init__(self, root):
        self.root = root
        self.root.title("K6221/2182 I-V Sweep")
        self.root.geometry("1200x800")
        self.backend = Backend_Passthrough()
        self.data_storage = {'current': [], 'voltage': [], 'resistance': []}
        self.setup_styles()
        self.create_widgets()

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Start.TButton', background=self.CLR_ACCENT_GREEN)
        style.configure('Stop.TButton', background=self.CLR_ACCENT_RED)

    def create_widgets(self):
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True)

        left_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, weight=1)

        self.create_input_frame(left_frame)
        self.create_console_frame(left_frame)

        right_frame = ttk.Frame(main_pane)
        main_pane.add(right_frame, weight=3)
        self.create_graph_frame(right_frame)

    def create_input_frame(self, parent):
        frame = LabelFrame(parent, text="Parameters")
        frame.pack(fill='x', padx=5, pady=5)

        self.entries = {}
        fields = [
            "Sample Name",
            "Start Current",
            "Stop Current",
            "Num Points",
            "Delay",
            "Initial Delay",
            "Compliance"]
        defaults = ["Test", "-1e-6", "1e-6", "11", "0.1", "1.0", "10"]

        for i, (field, default) in enumerate(zip(fields, defaults)):
            Label(frame, text=field).grid(row=i, column=0, sticky='w')
            ent = Entry(frame)
            ent.insert(0, default)
            ent.grid(row=i, column=1, sticky='ew')
            self.entries[field] = ent

        self.k6221_cb = ttk.Combobox(frame)
        self.k6221_cb.grid(row=len(fields), column=0, columnspan=2)

        self.sweep_scale_var = tk.StringVar(value="Linear")

        self.start_button = ttk.Button(
            frame,
            text="Start",
            command=self.start_sweep,
            style='Start.TButton')
        self.start_button.grid(row=len(fields) + 2, column=0)

        self.stop_button = ttk.Button(
            frame,
            text="Stop",
            command=self.stop_sweep,
            style='Stop.TButton')
        self.stop_button.grid(row=len(fields) + 2, column=1)

    def create_console_frame(self, parent):
        self.console = scrolledtext.ScrolledText(parent, height=10)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

    def create_graph_frame(self, parent):
        self.figure = Figure(figsize=(5, 5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, parent)
        self.ax = self.figure.add_subplot(111)
        self.line, = self.ax.plot([], [], 'o-')
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

    def log(self, msg):
        self.console.insert('end', msg + "\n")
        self.console.see('end')

    def start_sweep(self):
        try:
            self.params = {
                'name': self.entries["Sample Name"].get(),
                'start_i': float(self.entries["Start Current"].get()),
                'stop_i': float(self.entries["Stop Current"].get()),
                'points': int(self.entries["Num Points"].get()),
                'delay': float(self.entries["Delay"].get()),
                'initial_delay': float(self.entries["Initial Delay"].get()),
                'compliance': float(self.entries["Compliance"].get()),
                'k6221_visa': self.k6221_cb.get()
            }
            self.save_path = "."  # Dummy for now
            self.is_running = True

            # Start worker thread
            threading.Thread(
                target=self._sweep_worker, args=(
                    self.params,), daemon=True).start()

        except Exception as e:
            self.log(f"Error: {e}")

    def stop_sweep(self):
        self.is_running = False

    def _sweep_worker(self, params):
        try:
            self.backend.connect(params['k6221_visa'])
            self.backend.configure_instruments(params['compliance'])

            points = np.linspace(
                params['start_i'],
                params['stop_i'],
                params['points'])
            self.data_filepath = "temp_data.csv"

            for i, curr in enumerate(points):
                if not self.is_running:
                    break
                self.backend.set_current(curr)
                time.sleep(params['delay'])
                volt = self.backend.read_voltage()
                self.log(f"I: {curr}, V: {volt}")

        except Exception as e:
            self.log(f"Worker Error: {e}")
        finally:
            self.backend.close()

    def start_visa_scan(self):
        pass  # Simplified for this snippet

    def _browse_save(self):
        pass

    def _on_closing(self):
        self.root.destroy()


def main():
    root = tk.Tk()
    Passthrough_IV_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
