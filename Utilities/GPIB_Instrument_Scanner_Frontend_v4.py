# -------------------------------------------------------------------------------
# Name:         GPIB/VISA Instrument Scanner GUI
# Purpose:      A graphical user interface to find all connected instruments
#               and display their identification strings. (Updated to match PICA Launcher)
# Author:       Prathamesh Deshmukh
# Created:      17/09/2025
# Version:      V: 2.0
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
from datetime import datetime

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False

# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------
class GpibScannerGUI:
    """The main GUI application class for scanning VISA instruments."""
    PROGRAM_VERSION = "2.0"
    # --- Styling constants from PICA Launcher ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_CONSOLE_BG = '#1E2B38'
    CLR_TEXT_DARK = '#1A1A1A'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(f"GPIB/VISA Instrument Scanner v{self.PROGRAM_VERSION}")
        self.root.configure(bg=self.CLR_BG_DARK)

        # --- Position the window to the top-right of the screen ---
        win_width, win_height = 500, 400
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        x_pos = screen_width - win_width - 50
        y_pos = 50
        self.root.geometry(f"{win_width}x{win_height}+{x_pos}+{y_pos}")
        self.root.minsize(500, 350)

        # Queue for thread-safe communication from backend to GUI
        self.result_queue = queue.Queue()

        self.setup_styles()
        self.create_widgets()

        self.log("GPIB/VISA scanner window opened. Auto-scan will begin shortly.")
        self.root.after(100, self.process_queue)  # Start the queue processor
        self.root.after(1000, self.start_scan)     # Auto-start the scan after 1 second

    def setup_styles(self):
        """Configures ttk styles for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE)
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 8), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER, borderwidth=0, focusthickness=0, focuscolor='none')
        style.map('App.TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure('Scan.TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_TEXT_DARK, background=self.CLR_ACCENT_GREEN)
        style.map('Scan.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill='both', expand=True)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # --- Controls Frame ---
        controls_frame = ttk.Frame(main_frame)
        controls_frame.grid(row=0, column=0, sticky='ew', pady=(0, 15))
        controls_frame.columnconfigure((0, 1, 2), weight=1)

        self.scan_button = ttk.Button(controls_frame, text="Scan for Instruments", command=self.start_scan, style='Scan.TButton')
        self.scan_button.grid(row=0, column=0, padx=(0, 5), sticky='ew')

        guide_button = ttk.Button(controls_frame, text="Address Guide", command=self.show_address_guide, style='App.TButton')
        guide_button.grid(row=0, column=1, padx=5, sticky='ew')

        clear_button = ttk.Button(controls_frame, text="Clear Log", command=self.clear_log, style='App.TButton')
        clear_button.grid(row=0, column=2, padx=(5, 0), sticky='ew')

        # --- Console/Results Frame ---
        self.console_widget = scrolledtext.ScrolledText(main_frame, state='disabled', bg=self.CLR_CONSOLE_BG,
                                                       fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.grid(row=1, column=0, sticky='nsew')

        ttk.Button(main_frame, text="Close", style='App.TButton', command=self.root.destroy).grid(row=2, column=0, sticky='ew', pady=(15, 0))

        self.log("Welcome to the GPIB/VISA Instrument Scanner.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA library not found. Please run 'pip install pyvisa'.", level='error')
            self.scan_button.config(state='disabled')
        else:
            self.log("Auto-scanning for instruments in 1 second...")

    def log(self, message, add_timestamp=True):
        """Adds a message to the console widget with a timestamp."""
        self.console_widget.config(state='normal')
        if add_timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.console_widget.insert('end', f"[{timestamp}] {message}\n")
        else:
            self.console_widget.insert('end', message)
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def clear_log(self):
        """Clears all text from the console widget."""
        self.console_widget.config(state='normal')
        self.console_widget.delete('1.0', 'end')
        self.console_widget.config(state='disabled')
        self.log("Log cleared.")

    def start_scan(self):
        """Disables the scan button and starts the backend scan in a new thread."""
        self.scan_button.config(state='disabled')
        self.log("Starting scan...")

        # Run the actual scanning logic in a separate thread to prevent GUI freezing
        scan_thread = threading.Thread(target=self.run_scan_thread, daemon=True)
        scan_thread.start()

    def process_queue(self):
        """Checks the queue for messages from the worker thread and updates the GUI."""
        try:
            message = self.result_queue.get_nowait()
            if message == "SCAN_COMPLETE":
                self.scan_button.config(state='normal')
                self.log("Scan complete.")
            else:
                self.log(message, add_timestamp=False)
        except queue.Empty:
            pass
        finally:
            # Schedule the next check
            self.root.after(100, self.process_queue)

    def show_address_guide(self):
        """Displays a list of common instrument addresses in the console."""
        guide_text = """
--- PICA Instrument Address Guide ---
Note: These are typical addresses. Use the scan results for exact values.

Temperature Controllers
  • Lakeshore 340:      GPIB0::12::INSTR
  • Lakeshore 350:      GPIB1::15::INSTR

Source-Measure Units (SMU) & Electrometers
  • Keithley 2400:      GPIB1::4::INSTR
  • Keithley 6221:      GPIB0::13::INSTR
  • Keithley 6517B:     GPIB1::27::INSTR

Nanovoltmeters, LCR Meters & Amplifiers
  • Keithley 2182:      GPIB0::7::INSTR
  • Keysight E4980A:    GPIB0::17::INSTR
  • SRS SR830 Lock-in:  GPIB0::8::INSTR

\n---------------------------------------------
"""
        self.log(guide_text, add_timestamp=False)

    def run_scan_thread(self):
        """
        This is the backend function that runs in a separate thread.
        It performs the VISA scan and puts results into the thread-safe queue.
        This is the logic from your original 'gpib_interface_test.py' script.
        """
        if not pyvisa:
            self.result_queue.put("ERROR: PyVISA is not available.\n")
            self.result_queue.put("SCAN_COMPLETE")
            return

        try:
            rm = pyvisa.ResourceManager()
            instrument_addresses = rm.list_resources()

            if not instrument_addresses:
                self.result_queue.put("-> No instruments found. Check connections and VISA installation.\n")
            else:
                self.result_queue.put(f"-> Found {len(instrument_addresses)} instrument(s). Querying...\n\n")

                for address in instrument_addresses:
                    try:
                        with rm.open_resource(address) as instrument:
                            instrument.timeout = 2000  # 2-second timeout
                            idn = instrument.query('*IDN?')
                            result = (f"Address: {address}\n"
                                      f"    ID: {idn.strip()}\n\n")
                            self.result_queue.put(result)
                    except Exception as e:
                        result = (f"Address: {address}\n"
                                  f"    Error: Could not get ID. {e}\n\n")
                        self.result_queue.put(result)
        except Exception as e:
            # This catches errors in initializing ResourceManager itself
            error_msg = f"A critical VISA error occurred: {e}\n" \
                        "Please ensure a VISA backend (e.g., NI-VISA) is installed correctly.\n"
            self.result_queue.put(error_msg)

        # Signal that the scan is finished
        self.result_queue.put("SCAN_COMPLETE")


def main():
    """Initializes the application."""
    root = tk.Tk()
    app = GpibScannerGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
