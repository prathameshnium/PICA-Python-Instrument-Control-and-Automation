"""
Module: V_Bias_K6517B_GUI.py
Purpose: GUI module for applying a constant DC voltage bias (Polling)
         using the Keithley 6517B. No measurement or plotting.
"""

# --- Packages for Front end ---
import tkinter as tk
from tkinter import ttk, Label, Entry, LabelFrame, messagebox, scrolledtext, Canvas
import os
import time
import traceback
from datetime import datetime
import runpy
from multiprocessing import Process

# --- Pillow for Logo Image ---
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True

except ImportError:
    PIL_AVAILABLE = False

# --- Packages for Back end ---
try:
    import pyvisa
    from pymeasure.instruments.keithley import Keithley6517B
    from pyvisa.errors import VisaIOError
    PYMEASURE_AVAILABLE = True

except ImportError:
    pyvisa = None
    Keithley6517B = None
    VisaIOError = None
    PYMEASURE_AVAILABLE = False


def run_script_process(script_path):
    """
    Wrapper function to execute a script using runpy in its own directory.
    This becomes the target for the new, isolated process.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


def launch_gpib_scanner():
    """Finds and launches the GPIB scanner utility in a new process."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up 3 levels: High_Resistance -> k6517b -> keithley -> pica
        scanner_path = os.path.join(
            script_dir,
            "..", "..", "..", "utils", "GPIB_Instrument_Scanner_GUI.py")

        if not os.path.exists(scanner_path):
            messagebox.showerror(
                "File Not Found",
                f"GPIB Scanner not found at expected path:\n{scanner_path}")
            return
        Process(target=run_script_process, args=(scanner_path,)).start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Failed to launch GPIB Scanner: {e}")
# -------------------------------------------------------------------------------
# --- REAL INSTRUMENT BACKEND ---
# -------------------------------------------------------------------------------


class Keithley6517B_Backend:
    """
    A dedicated class to handle backend communication with a real Keithley 6517B.
    Configured for voltage sourcing only (no measurement).
    """

    def __init__(self):
        self.keithley = None
        self.is_connected = False
        if not PYMEASURE_AVAILABLE:
            raise ImportError(
                "PyMeasure or PyVISA is not installed. Please run 'pip install pymeasure'.")

    def initialize_instruments(self, parameters):
        """Connects to the instrument and prepares the voltage source."""
        print(
            f"\n--- [Backend] Initializing Instrument at {parameters['keithley_visa']} ---")
        try:
            self.keithley = Keithley6517B(
                parameters['keithley_visa'], timeout=20000)
            print(f"  Successfully connected to: {self.keithley.id}")

            print("  Resetting instrument and configuring voltage source...")
            self.keithley.reset()
            time.sleep(1)

            self.is_connected = True
            print("--- [Backend] Instrument Initialized and Ready ---")

        except VisaIOError as e:
            print(f"  [VISA Connection Error] Could not connect. Details: {e}")
            raise ConnectionError(
                "Could not connect to Keithley 6517B.\nCheck address and connections.") from e
        except Exception as e:
            print(f"  [Unexpected Error] during initialization. Details: {e}")
            raise e

    def set_voltage(self, voltage):
        """Sets the voltage source level and enables the output."""
        if not self.is_connected:
            raise ConnectionError("Instrument not connected.")
        self.keithley.source_voltage = voltage
        self.keithley.enable_source()

    def disable_voltage(self):
        """Disables the voltage source output without disconnecting."""
        if self.keithley:
            self.keithley.source_voltage = 0
            self.keithley.disable_source()

    def close_instruments(self):
        """Safely shuts down the voltage source and disconnects."""
        print("--- [Backend] Closing instrument connection. ---")
        if self.keithley:
            try:
                print("  Shutting down voltage source...")
                self.keithley.shutdown()
                print("  Voltage source OFF. Instrument is safe.")
            except Exception as e:
                print(
                    f"  Warning: Could not gracefully shut down instrument. Error: {e}")
            finally:
                self.is_connected = False
                self.keithley = None

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class VoltagePolling_GUI:
    """The main GUI application class (Front End)."""
    PROGRAM_VERSION = "1.0.0"
    LOGO_SIZE = 110
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        # Path is three directories up from the script location
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "..",
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where __file__ is not defined
        LOGO_FILE_PATH = "../../../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#EF233C'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_INDICATOR_ON = '#4CAF50'
    CLR_INDICATOR_OFF = '#9E2A2B'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SUB_LABEL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)
    FONT_TIMER = ('Consolas', 28, 'bold')
    FONT_STATUS = ('Segoe UI', FONT_SIZE_BASE + 3, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("Keithley 6517B: Voltage Polling (Constant Bias)")
        self.root.geometry("700x900")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(600, 800)

        self.is_running = False
        self.start_time = None
        self.timer_job = None
        self.logo_image = None  # Attribute to hold the logo image reference
        try:
            self.backend = Keithley6517B_Backend()
        except Exception as e:
            messagebox.showerror(
                "Backend Error",
                f"Could not initialize the backend.\nError: {e}\n\n"
                "Please ensure PyMeasure and NI-VISA are installed correctly.")
            self.backend = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def setup_styles(self):
        """Configures ttk styles for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                8),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[('active', self.CLR_TEXT_DARK),
                        ('hover', self.CLR_TEXT_DARK)]
        )
        style.configure(
            'Start.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_TEXT_DARK)
        style.map(
            'Start.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])
        style.configure(
            'Stop.TButton',
            background=self.CLR_ACCENT_RED,
            foreground=self.CLR_FG_LIGHT)
        style.map(
            'Stop.TButton', background=[
                ('active', '#D63C2A'), ('hover', '#D63C2A')])

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        # Create the console frame first to initialize the logger,
        # but pack it last so the layout order is correct.
        console_frame = self.create_console_frame(main_frame)

        self.create_info_frame(main_frame)
        self.create_input_frame(main_frame)
        self.create_status_frame(main_frame)

        console_frame.pack(pady=10, padx=10, fill='both', expand=True)

    def create_header(self):
        font_title_main = ('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')

        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')

        Label(
            header_frame,
            text="Keithley 6517B: Voltage Polling",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=font_title_main).pack(
            side='left',
            padx=20,
            pady=10)

        # --- GPIB Scanner Launch Button ---
        gpib_button = ttk.Button(
            header_frame,
            text="📟",
            command=launch_gpib_scanner,
            width=3)
        gpib_button.pack(side='right', padx=10, pady=5)

        Label(
            header_frame,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_SUB_LABEL).pack(
            side='right',
            padx=20,
            pady=10)

    def create_info_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Information',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=(10, 10), padx=10, fill='x')
        frame.grid_columnconfigure(1, weight=1)

        logo_canvas = Canvas(
            frame,
            width=self.LOGO_SIZE,
            height=self.LOGO_SIZE,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        logo_canvas.grid(row=0, column=0, rowspan=3, padx=15, pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH)
                img.thumbnail((self.LOGO_SIZE, self.LOGO_SIZE),
                              Image.Resampling.LANCZOS)
                # IMPORTANT: Keep a reference to the image to prevent it from
                # being garbage collected
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    image=self.logo_image)
            except Exception as e:
                self.log(f"ERROR: Failed to load logo. {e}")
                logo_canvas.create_text(
                    self.LOGO_SIZE / 2,
                    self.LOGO_SIZE / 2,
                    text="LOGO\nERROR",
                    font=self.FONT_BASE,
                    fill=self.CLR_FG_LIGHT,
                    justify='center')
        else:
            self.log(f"Warning: Logo not found at '{self.LOGO_FILE_PATH}'")
            logo_canvas.create_text(
                self.LOGO_SIZE / 2,
                self.LOGO_SIZE / 2,
                text="LOGO\nMISSING",
                font=self.FONT_BASE,
                fill=self.CLR_FG_LIGHT,
                justify='center')

        institute_font = ('Segoe UI', self.FONT_SIZE_BASE + 1, 'bold')
        info_label = ttk.Label(
            frame,
            text="UGC-DAE Consortium for Scientific Research",
            font=institute_font,
            background=self.CLR_BG_DARK)
        info_label.grid(
            row=0,
            column=1,
            padx=10,
            pady=(
                10,
                0),
            sticky='sw')
        ttk.Label(
            frame,
            text="Mumbai Centre",
            font=institute_font,
            background=self.CLR_BG_DARK).grid(
            row=1,
            column=1,
            padx=10,
            sticky='nw')

        ttk.Separator(
            frame,
            orient='horizontal').grid(
            row=2,
            column=1,
            sticky='ew',
            padx=10,
            pady=8)

        details_text = ("Program Name: Voltage Polling (Constant Bias)\n"
                        "Instrument: Keithley 6517B Electrometer\n"
                        "Source Range: ±1000 V DC")
        ttk.Label(
            frame,
            text=details_text,
            justify='left').grid(
            row=3,
            column=0,
            columnspan=2,
            padx=15,
            pady=(
                0,
                10),
            sticky='w')

    def create_input_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Polling Parameters',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        for i in range(4):
            frame.grid_columnconfigure(i, weight=1)

        pady_val = (5, 5)

        Label(
            frame,
            text="Applied Voltage (V):").grid(
            row=0,
            column=0,
            columnspan=2,
            padx=10,
            pady=pady_val,
            sticky='w')
        self.voltage_entry = Entry(frame, font=self.FONT_BASE)
        self.voltage_entry.grid(
            row=0, column=2, columnspan=2, padx=10,
            pady=pady_val, sticky='ew')

        Label(
            frame,
            text="Keithley 6517B VISA:").grid(
            row=1,
            column=0,
            columnspan=4,
            padx=10,
            pady=(
                10,
                5),
            sticky='w')
        self.keithley_combobox = ttk.Combobox(
            frame, font=self.FONT_BASE, state='readonly')
        self.keithley_combobox.grid(
            row=2, column=0, columnspan=4, padx=10, pady=(
                0, 5), sticky='ew')

        self.scan_button = ttk.Button(
            frame,
            text="Scan for Instruments",
            command=self._scan_for_visa_instruments)
        self.scan_button.grid(
            row=3,
            column=0,
            columnspan=4,
            padx=10,
            pady=5,
            sticky='ew')

        self.start_button = ttk.Button(
            frame,
            text="Start (Voltage ON)",
            command=self.start_polling,
            style='Start.TButton')
        self.start_button.grid(
            row=4,
            column=0,
            columnspan=2,
            padx=10,
            pady=15,
            sticky='ew')
        self.stop_button = ttk.Button(
            frame,
            text="Stop (Voltage OFF)",
            command=self.stop_polling,
            style='Stop.TButton',
            state='disabled')
        self.stop_button.grid(
            row=4,
            column=2,
            columnspan=2,
            padx=10,
            pady=15,
            sticky='ew')

    def create_status_frame(self, parent):
        """Creates the frame with the ON/OFF indicator and elapsed time counter."""
        frame = LabelFrame(
            parent,
            text='Status',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        frame.pack(pady=10, padx=10, fill='x')
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=2)

        # --- Indicator (LED-style circle + text) ---
        indicator_container = tk.Frame(frame, bg=self.CLR_BG_DARK)
        indicator_container.grid(row=0, column=0, padx=20, pady=15)

        self.indicator_canvas = Canvas(
            indicator_container,
            width=50,
            height=50,
            bg=self.CLR_BG_DARK,
            highlightthickness=0)
        self.indicator_canvas.pack()
        self.indicator_circle = self.indicator_canvas.create_oval(
            5, 5, 45, 45,
            fill=self.CLR_INDICATOR_OFF,
            outline=self.CLR_FG_LIGHT,
            width=2)

        self.indicator_label = Label(
            indicator_container,
            text="VOLTAGE OFF",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_INDICATOR_OFF,
            font=self.FONT_STATUS)
        self.indicator_label.pack(pady=(5, 0))

        # --- Elapsed Time Counter ---
        timer_container = tk.Frame(frame, bg=self.CLR_BG_DARK)
        timer_container.grid(row=0, column=1, padx=20, pady=15)

        Label(
            timer_container,
            text="Elapsed Time (HH:MM:SS)",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE).pack()
        self.timer_label = Label(
            timer_container,
            text="00:00:00",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_TEXT_DARK,
            font=self.FONT_TIMER)
        self.timer_label.pack()

        # --- Applied voltage readout ---
        self.applied_v_label = Label(
            frame,
            text="Set Voltage: --- V",
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        self.applied_v_label.grid(
            row=1, column=0, columnspan=2, pady=(0, 15))

    def create_console_frame(self, parent):
        frame = LabelFrame(
            parent,
            text='Console Output',
            relief='groove',
            bg=self.CLR_BG_DARK,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        self.console_widget = scrolledtext.ScrolledText(
            frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)
        self.log(
            "Console initialized. Enter voltage and scan for instruments.")
        if not PYMEASURE_AVAILABLE:
            self.log(
                "CRITICAL: PyMeasure or PyVISA not found. Please run 'pip install pymeasure'.")
        return frame

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def _set_indicator(self, on):
        """Updates the LED indicator and status text."""
        if on:
            self.indicator_canvas.itemconfig(
                self.indicator_circle, fill=self.CLR_INDICATOR_ON)
            self.indicator_label.config(
                text="VOLTAGE ON", fg=self.CLR_INDICATOR_ON)
        else:
            self.indicator_canvas.itemconfig(
                self.indicator_circle, fill=self.CLR_INDICATOR_OFF)
            self.indicator_label.config(
                text="VOLTAGE OFF", fg=self.CLR_INDICATOR_OFF)

    def _update_timer(self):
        """Updates the elapsed-time counter every second while running."""
        if self.is_running and self.start_time is not None:
            elapsed = int(time.time() - self.start_time)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
            self.timer_job = self.root.after(1000, self._update_timer)

    def start_polling(self):
        if self.backend is None:
            messagebox.showerror(
                "Backend Error",
                "Backend is not available. Cannot start polling.")
            return
        try:
            params = {}
            voltage = float(self.voltage_entry.get())
            params['keithley_visa'] = self.keithley_combobox.get()

            if not params['keithley_visa']:
                raise ValueError(
                    "A voltage value and VISA address are required.")
            if abs(voltage) > 1000:
                raise ValueError(
                    "Voltage out of range. The 6517B source is limited to ±1000 V.")

            self.backend.initialize_instruments(params)
            self.log("Backend initialized.")

            self.backend.set_voltage(voltage)
            self.log(f"Voltage source ON: {voltage:.3f} V applied to sample.")

            self.is_running = True
            self.start_time = time.time()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.voltage_entry.config(state='disabled')
            self.scan_button.config(state='disabled')
            self.keithley_combobox.config(state='disabled')

            self._set_indicator(on=True)
            self.applied_v_label.config(text=f"Set Voltage: {voltage:.3f} V")
            self.timer_label.config(text="00:00:00")
            self._update_timer()

        except Exception as e:
            self.log(f"ERROR during startup: {traceback.format_exc()}")
            if self.backend and self.backend.is_connected:
                self.backend.close_instruments()
            messagebox.showerror(
                "Initialization Error",
                f"Could not start polling.\n{e}")

    def stop_polling(self, from_user=True):
        if self.is_running:
            self.is_running = False
            if self.timer_job:
                self.root.after_cancel(self.timer_job)
                self.timer_job = None

            elapsed = int(time.time() - self.start_time)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.log(
                f"Voltage applied for a total of {h:02d}:{m:02d}:{s:02d}.")

            if self.backend:
                self.backend.close_instruments()
            self.log("Voltage source OFF. Instrument connection closed.")

            self._set_indicator(on=False)
            self.applied_v_label.config(text="Set Voltage: --- V")
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.voltage_entry.config(state='normal')
            self.scan_button.config(state='normal')
            self.keithley_combobox.config(state='readonly')

            if from_user:
                messagebox.showinfo(
                    "Info",
                    "Voltage turned OFF and instrument disconnected.")

    def _scan_for_visa_instruments(self):
        if not pyvisa:
            self.log("ERROR: PyVISA is not installed. Cannot scan.")
            return
        try:
            rm = pyvisa.ResourceManager()
            self.log("Scanning for VISA instruments...")
            resources = rm.list_resources()
            if resources:
                self.log(f"Found: {resources}")
                self.keithley_combobox['values'] = resources
                # Attempt to find a likely candidate for the Keithley
                for res in resources:
                    if "GPIB" in res.upper() and ("27" in res or "26" in res or "25" in res):
                        self.keithley_combobox.set(res)
                        break
                else:
                    self.keithley_combobox.set(resources[0])
            else:
                self.log("No VISA instruments found.")
                self.keithley_combobox['values'] = []
                self.keithley_combobox.set("")
        except Exception as e:
            self.log(f"ERROR during VISA scan: {e}")

    def _on_closing(self):
        if self.is_running:
            if messagebox.askyesno(
                    "Exit", "Voltage is currently ON. Turn it OFF and exit?"):
                self.stop_polling(from_user=False)
                self.root.destroy()
        else:
            if self.backend and self.backend.is_connected:
                self.backend.close_instruments()
            self.root.destroy()


def main():
    root = tk.Tk()
    VoltagePolling_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()