# -------------------------------------------------------------------------------
# Name:         GPIB/VISA Instrument Scanner GUI
# Purpose:      A graphical user interface to find all connected instruments
#               and display their identification strings.
# Author:       Prathamesh Deshmukh
# Created:      17/09/2025
# Version:      V: 1.0
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
    PROGRAM_VERSION = "1.0"
    # --- Styling constants borrowed from the provided example ---
    CLR_BG_DARK = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG_LIGHT = '#EDF2F4'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_CONSOLE_BG = '#1E2B38'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("GPIB/VISA Instrument Scanner")
        self.root.geometry("800x600")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.minsize(600, 400)

        # Queue for thread-safe communication from backend to GUI
        self.result_queue = queue.Queue()

        self.setup_styles()
        self.create_widgets()

        # Start a periodic check of the queue for messages from the worker thread
        self.root.after(100, self.process_queue)

    def setup_styles(self):
        """Configures ttk styles for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabel', background=self.CLR_BG_DARK, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 8))
        style.map('TButton', foreground=[('!active', self.CLR_BG_DARK), ('active', self.CLR_FG_LIGHT)],
                  background=[('!active', self.CLR_ACCENT_BLUE), ('active', self.CLR_BG_DARK)])
        style.configure('Scan.TButton', background=self.CLR_ACCENT_GREEN)

    def create_widgets(self):
        """Lays out the main frames and populates them with widgets."""
        self.create_header()

        # --- Main Content Frame ---
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill='both', expand=True)

        # --- Controls Frame ---
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(side='top', fill='x', pady=(0, 10))
        controls_frame.grid_columnconfigure(0, weight=1)
        controls_frame.grid_columnconfigure(1, weight=1)

        self.scan_button = ttk.Button(controls_frame, text="Scan for Instruments", command=self.start_scan, style='Scan.TButton')
        self.scan_button.grid(row=0, column=0, padx=5, pady=5, sticky='ew')

        self.clear_button = ttk.Button(controls_frame, text="Clear Log", command=self.clear_log)
        self.clear_button.grid(row=0, column=1, padx=5, pady=5, sticky='ew')

        # --- Console/Results Frame ---
        console_frame = ttk.LabelFrame(main_frame, text='Scan Results', style='TFrame')
        console_frame.pack(fill='both', expand=True)

        self.console_widget = scrolledtext.ScrolledText(console_frame, state='disabled', bg=self.CLR_CONSOLE_BG,
                                                       fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE, wrap='word', bd=0)
        self.console_widget.pack(pady=5, padx=5, fill='both', expand=True)

        self.log("Welcome to the GPIB/VISA Instrument Scanner.")
        if not PYVISA_AVAILABLE:
            self.log("CRITICAL: PyVISA library not found. Please run 'pip install pyvisa'.", level='error')
            self.scan_button.config(state='disabled')
        else:
            self.log("Click 'Scan for Instruments' to begin.")

    def create_header(self):
        """Creates the top header bar."""
        header_frame = tk.Frame(self.root, bg=self.CLR_HEADER)
        header_frame.pack(side='top', fill='x')
        ttk.Label(header_frame, text="GPIB/VISA Connection Check", background=self.CLR_HEADER, foreground=self.CLR_FG_LIGHT, font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)
        ttk.Label(header_frame, text=f"Version: {self.PROGRAM_VERSION}", background=self.CLR_HEADER, foreground=self.CLR_FG_LIGHT, font=self.FONT_BASE).pack(side='right', padx=20, pady=10)

    def log(self, message, level='info'):
        """Adds a message to the console widget with a timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}\n"

        self.console_widget.config(state='normal')
        self.console_widget.insert('end', formatted_message)
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def clear_log(self):
        """Clears all text from the console widget."""
        self.console_widget.config(state='normal')
        self.console_widget.delete(1.0, 'end')
        self.console_widget.config(state='disabled')
        self.log("Log cleared.")

    def start_scan(self):
        """Disables the scan button and starts the backend scan in a new thread."""
        self.scan_button.config(state='disabled')
        self.log("Starting scan... The GUI will remain responsive.")

        # Run the actual scanning logic in a separate thread to prevent GUI freezing
        scan_thread = threading.Thread(target=self.run_scan_thread, daemon=True)
        scan_thread.start()

    def process_queue(self):
        """Checks the queue for messages from the worker thread and updates the GUI."""
        try:
            # Process all available messages in the queue
            while True:
                message = self.result_queue.get_nowait()
                if message == "SCAN_COMPLETE":
                    self.scan_button.config(state='normal')
                    self.log("Scan complete.")
                else:
                    # Messages from the thread are already formatted
                    self.console_widget.config(state='normal')
                    self.console_widget.insert('end', message)
                    self.console_widget.see('end')
                    self.console_widget.config(state='disabled')

        except queue.Empty:
            # If the queue is empty, do nothing
            pass
        finally:
            # Schedule the next check
            self.root.after(100, self.process_queue)

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
                self.result_queue.put(f"-> Found {len(instrument_addresses)} instrument(s). Checking them now...\n\n")

                for address in instrument_addresses:
                    try:
                        with rm.open_resource(address) as instrument:
                            instrument.timeout = 2000  # 2-second timeout
                            idn = instrument.query('*IDN?')
                            result = (f"Address: {address}\n"
                                      f"   ID: {idn.strip()}\n\n")
                            self.result_queue.put(result)
                    except Exception as e:
                        result = (f"Address: {address}\n"
                                  f"   Error: Could not get ID. {e}\n\n")
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
