'''
===============================================================================
 PROGRAM:      PICA SCPI Console
 PURPOSE:      A small standalone terminal for talking to VISA instruments:
               select an instrument, send SCPI commands, read back responses.
               Ships with a categorized library of universal IEEE-488.2 / SCPI
               commands. Separate from the GPIB/VISA Instrument Scanner, which
               only discovers instruments.
 AUTHOR:       Prathamesh Deshmukh
 VERSION:      V: 1.0
===============================================================================
'''

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import queue
import time
from collections import deque
from datetime import datetime

# --- Packages for Back end ---
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    pyvisa = None
    PYVISA_AVAILABLE = False


# -------------------------------------------------------------------------------
# --- PRELOADED UNIVERSAL SCPI COMMAND LIBRARY ---
# IEEE-488.2 mandated commands plus SCPI-required subsystem queries.
# These are safe on any conforming instrument (see the *RST caveat below).
# -------------------------------------------------------------------------------
SCPI_LIBRARY = {
    "Identity": [
        ("*IDN?", "Identification: maker, model, serial, firmware"),
        ("*OPT?", "Installed options / cards"),
    ],
    "Reset & Clear": [
        ("*RST", "Reset instrument to its default state"),
        ("*CLS", "Clear status byte and all event registers"),
    ],
    "Status & Sync": [
        ("*OPC", "Set the operation-complete bit when done"),
        ("*OPC?", "Block until all pending operations complete (returns 1)"),
        ("*WAI", "Wait: no further commands executed until done"),
        ("*TST?", "Run internal self-test (0 = pass)"),
        ("*STB?", "Read the status byte register"),
        ("*ESR?", "Read and clear the standard event status register"),
        ("*ESE?", "Read the standard event status enable mask"),
        ("*SRE?", "Read the service request enable mask"),
    ],
    "Errors": [
        ("SYST:ERR?", "Pop the oldest entry off the error queue"),
        ("SYST:ERR:COUN?", "Number of entries waiting in the error queue"),
        ("SYST:VERS?", "SCPI version the instrument conforms to"),
    ],
    "Measurement": [
        ("READ?", "Trigger a measurement and return the reading"),
        ("INIT", "Arm / initiate the trigger model"),
        ("FETC?", "Return the last reading without re-triggering"),
        ("MEAS?", "Configure, trigger and read in one step"),
        ("ABOR", "Abort the measurement in progress"),
    ],
    "Trigger": [
        ("*TRG", "Send a bus trigger"),
        ("TRIG:SOUR?", "Current trigger source"),
        ("TRIG:COUN?", "Number of triggers per initiation"),
    ],
    "Memory": [
        ("*SAV 0", "Save the current setup into memory slot 0"),
        ("*RCL 0", "Recall the setup stored in memory slot 0"),
    ],
}

# Commands that perturb instrument state -- highlighted in the library panel.
SCPI_WARN_COMMANDS = {"*RST", "*SAV 0", "*RCL 0", "ABOR"}

# Label separator used to render "address  ->  IDN" in the instrument combobox.
ADDR_SEP = "  ->  "

# Human label -> actual termination character(s) handed to PyVISA.
TERMINATIONS = {
    "\\n (LF)": "\n",
    "\\r\\n (CRLF)": "\r\n",
    "\\r (CR)": "\r",
    "None": None,
    "EOI": "EOI",  # Special case for End-Of-Instruction termination
}


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------

class PICASCPIConsoleApp:
    """Interactive SCPI send/receive terminal for a single VISA instrument."""

    PROGRAM_VERSION = "1.0"

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_INPUT_BG = '#F4EFEA'

    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA SCPI Console v{self.PROGRAM_VERSION}")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.geometry("1000x620")
        self.root.minsize(880, 540)

        # --- Backend state ---
        self.rm = None
        self.instrument = None
        self.busy = False               # a VISA transaction is in flight
        self.msg_queue = queue.Queue()  # (kind, text) tuples from worker threads

        # --- Command history ---
        self.history = deque(maxlen=100)
        self.history_index = 0

        self.setup_styles()
        self.create_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.after(100, self.process_queue)

        self.log("info", "PICA SCPI Console ready.")
        if not PYVISA_AVAILABLE:
            self.log(
                "error",
                "CRITICAL: PyVISA library not found. Run 'pip install pyvisa'.")
            self._set_controls_enabled(connected=False, visa_ok=False)
        else:
            self.log("info", "Scanning for instruments...")
            self.root.after(500, self.refresh_instruments)

    # ---------------------------------------------------------------- styling
    def setup_styles(self):
        """Configures ttk styles to match the PICA launcher."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_BASE)
        style.configure(
            'App.TButton',
            font=self.FONT_BASE,
            padding=(10, 6),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'App.TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)],
            foreground=[('active', self.CLR_TEXT_DARK),
                        ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Scan.TButton',
            font=self.FONT_BASE,
            padding=(10, 6),
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_ACCENT_GREEN,
            borderwidth=0,
            focusthickness=0,
            focuscolor='none')
        style.map(
            'Scan.TButton',
            background=[('active', self.CLR_ACCENT_GOLD),
                        ('hover', self.CLR_ACCENT_GOLD)])
        style.configure(
            'Lib.Treeview',
            background=self.CLR_CONSOLE_BG,
            fieldbackground=self.CLR_CONSOLE_BG,
            foreground=self.CLR_TEXT_DARK,
            font=self.FONT_CONSOLE,
            rowheight=20,
            borderwidth=0)
        style.configure(
            'Lib.Treeview.Heading',
            background=self.CLR_HEADER,
            foreground=self.CLR_ACCENT_GOLD,
            font=self.FONT_BASE)
        style.map('Lib.Treeview', background=[('selected', self.CLR_ACCENT_GOLD)])

    # ---------------------------------------------------------------- widgets
    def create_widgets(self):
        self.create_header()

        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill='both', expand=True)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)

        self.create_connection_bar(main_frame)

        # --- Library / console split ---
        paned = ttk.PanedWindow(main_frame, orient='horizontal')
        paned.grid(row=1, column=0, sticky='nsew', pady=(10, 10))
        paned.add(self.create_library_frame(paned), weight=0)
        paned.add(self.create_console_frame(paned), weight=1)

        self.create_entry_bar(main_frame)
        self.create_footer(main_frame)

        self._set_controls_enabled(connected=False, visa_ok=PYVISA_AVAILABLE)

    def create_header(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x')
        tk.Label(
            header,
            text="PICA SCPI Console",
            bg=self.CLR_HEADER,
            fg=self.CLR_ACCENT_GOLD,
            font=('Segoe UI', self.FONT_SIZE_BASE + 4, 'bold')
        ).pack(side='left', padx=12, pady=8)
        tk.Label(
            header,
            text=f"Version: {self.PROGRAM_VERSION}",
            bg=self.CLR_HEADER,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_BASE
        ).pack(side='right', padx=12, pady=8)

    def create_connection_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky='ew')
        bar.columnconfigure(1, weight=1)

        ttk.Label(bar, text="Instrument:").grid(row=0, column=0, padx=(0, 6))
        self.instrument_combobox = ttk.Combobox(
            bar, state='readonly', font=self.FONT_BASE)
        self.instrument_combobox.grid(row=0, column=1, sticky='ew', padx=(0, 6))

        self.refresh_button = ttk.Button(
            bar, text="Refresh", style='App.TButton',
            command=self.refresh_instruments)
        self.refresh_button.grid(row=0, column=2, padx=(0, 6))

        ttk.Label(bar, text="Timeout (ms):").grid(row=0, column=3, padx=(6, 4))
        self.timeout_entry = ttk.Entry(bar, width=7, font=self.FONT_BASE)
        self.timeout_entry.insert(0, "2000")
        self.timeout_entry.grid(row=0, column=4, padx=(0, 6))

        ttk.Label(bar, text="Term:").grid(row=0, column=5, padx=(6, 4))
        self.term_combobox = ttk.Combobox(
            bar, state='readonly', width=12, font=self.FONT_BASE,
            values=list(TERMINATIONS.keys()))
        self.term_combobox.set("\\n (LF)")
        self.term_combobox.grid(row=0, column=6, padx=(0, 10))

        self.connect_button = ttk.Button(
            bar, text="Connect", style='Scan.TButton', command=self.connect)
        self.connect_button.grid(row=0, column=7, padx=(0, 6))

        self.disconnect_button = ttk.Button(
            bar, text="Disconnect", style='App.TButton', command=self.disconnect)
        self.disconnect_button.grid(row=0, column=8)

    def create_library_frame(self, parent):
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        tree = ttk.Treeview(
            frame, columns=('desc',), show='tree headings',
            style='Lib.Treeview', selectmode='browse')
        tree.heading('#0', text='SCPI Library', anchor='w')
        tree.heading('desc', text='Description', anchor='w')
        tree.column('#0', width=150, minwidth=110, stretch=False)
        tree.column('desc', width=210, minwidth=120, stretch=True)
        tree.tag_configure('warn', foreground='#BA2D2D')

        for category, commands in SCPI_LIBRARY.items():
            parent_id = tree.insert('', 'end', text=category, open=True)
            for command, description in commands:
                tags = ('cmd', 'warn') if command in SCPI_WARN_COMMANDS else ('cmd',)
                tree.insert(parent_id, 'end', text=command,
                            values=(description,), tags=tags)

        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        tree.bind('<<TreeviewSelect>>', self._on_library_select)
        tree.bind('<Double-1>', self._on_library_double_click)
        self.library_tree = tree
        return frame

    def create_console_frame(self, parent):
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.console_widget = scrolledtext.ScrolledText(
            frame, state='disabled', bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT, font=self.FONT_CONSOLE,
            wrap='word', bd=0, padx=8, pady=8)
        self.console_widget.grid(row=0, column=0, sticky='nsew', padx=(8, 0))

        self.console_widget.tag_configure(
            'tx', foreground='#2E5266', font=('Consolas', 10, 'bold'))
        self.console_widget.tag_configure('rx', foreground=self.CLR_TEXT_DARK)
        self.console_widget.tag_configure('error', foreground='#BA2D2D')
        self.console_widget.tag_configure(
            'info', foreground='#666666', font=('Consolas', 9, 'italic'))
        return frame

    def create_entry_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.grid(row=2, column=0, sticky='ew')
        bar.columnconfigure(1, weight=1)

        tk.Label(
            bar, text="SCPI >", font=self.FONT_CONSOLE,
            bg=self.CLR_BG_DARK, fg=self.CLR_TEXT_DARK
        ).grid(row=0, column=0, padx=(0, 6))

        self.command_entry = ttk.Entry(bar, font=self.FONT_CONSOLE)
        self.command_entry.grid(row=0, column=1, sticky='ew', padx=(0, 8))
        self.command_entry.bind('<Return>', lambda e: self.send())
        self.command_entry.bind('<Up>', self._history_prev)
        self.command_entry.bind('<Down>', self._history_next)

        self.send_button = ttk.Button(
            bar, text="Send", style='Scan.TButton', command=self.send)
        self.send_button.grid(row=0, column=2, padx=(0, 4))
        self.write_button = ttk.Button(
            bar, text="Write", style='App.TButton', command=self.write_only)
        self.write_button.grid(row=0, column=3, padx=4)
        self.read_button = ttk.Button(
            bar, text="Read", style='App.TButton', command=self.read_only)
        self.read_button.grid(row=0, column=4, padx=4)
        self.query_button = ttk.Button(
            bar, text="Query", style='App.TButton', command=self.query_only)
        self.query_button.grid(row=0, column=5, padx=(4, 0))

    def create_footer(self, parent):
        footer = ttk.Frame(parent)
        footer.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        footer.columnconfigure(2, weight=1)

        ttk.Button(footer, text="Clear Log", style='App.TButton',
                   command=self.clear_log).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(footer, text="Save Log", style='App.TButton',
                   command=self.save_log).grid(row=0, column=1)

        self.status_label = tk.Label(
            footer, text="Disconnected", anchor='e',
            bg=self.CLR_BG_DARK, fg=self.CLR_FG_LIGHT, font=self.FONT_BASE)
        self.status_label.grid(row=0, column=2, sticky='e', padx=(10, 0))

    # ---------------------------------------------------------------- logging
    def log(self, kind, message):
        """Appends a tagged, timestamped line to the console widget."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_widget.config(state='normal')
        self.console_widget.insert('end', f"[{timestamp}] {message}\n", kind)
        self.console_widget.see('end')
        self.console_widget.config(state='disabled')

    def clear_log(self):
        self.console_widget.config(state='normal')
        self.console_widget.delete('1.0', 'end')
        self.console_widget.config(state='disabled')
        self.log("info", "Log cleared.")

    def save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"scpi_session_{datetime.now():%Y%m%d_%H%M%S}.txt")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(self.console_widget.get('1.0', 'end-1c'))
            self.log("info", f"Log saved to {path}")
        except Exception as exc:
            self.log("error", f"ERROR: Could not save log. {exc}")
            messagebox.showerror("Save Failed", str(exc))

    # ------------------------------------------------------- queue / threading
    def process_queue(self):
        """Drains all pending worker-thread messages onto the GUI."""
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == 'done':
                    self._on_transaction_done(payload)
                elif kind == 'resources':
                    self._populate_instruments(payload)
                elif kind == 'connected':
                    self._on_connected(payload)
                else:
                    self.log(kind, payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_queue)

    def _run_worker(self, target, *args):
        """Starts a daemon worker; guards against overlapping transactions."""
        if self.busy:
            self.log("info", "Busy: a transaction is already in progress.")
            return
        self.busy = True
        self._set_busy(True)
        threading.Thread(target=target, args=args, daemon=True).start()

    def _on_transaction_done(self, _payload):
        self.busy = False
        self._set_busy(False)

    # ------------------------------------------------------------ enable/disable
    def _set_controls_enabled(self, connected, visa_ok=True):
        """Master switch for widget states based on connection status."""
        conn_state = 'normal' if visa_ok and not connected else 'disabled'
        cmd_state = 'normal' if visa_ok and connected else 'disabled'

        self.refresh_button.config(state=conn_state)
        self.connect_button.config(state=conn_state)
        self.timeout_entry.config(state=conn_state)
        self.instrument_combobox.config(
            state='readonly' if conn_state == 'normal' else 'disabled')
        self.term_combobox.config(
            state='readonly' if conn_state == 'normal' else 'disabled')
        self.disconnect_button.config(
            state='normal' if visa_ok and connected else 'disabled')

        for widget in (self.command_entry, self.send_button, self.write_button,
                       self.read_button, self.query_button):
            widget.config(state=cmd_state)

    def _set_busy(self, busy):
        """Temporarily locks the command widgets while VISA I/O is running."""
        state = 'disabled' if busy else ('normal' if self.instrument else 'disabled')
        for widget in (self.command_entry, self.send_button, self.write_button,
                       self.read_button, self.query_button):
            widget.config(state=state)
        if not self.instrument:
            self.refresh_button.config(state='disabled' if busy else 'normal')
            self.connect_button.config(state='disabled' if busy else 'normal')

    # ----------------------------------------------------------- library panel
    def _selected_command(self):
        selection = self.library_tree.selection()
        if not selection:
            return None
        item = selection[0]
        if 'cmd' not in self.library_tree.item(item, 'tags'):
            return None   # a category row
        return self.library_tree.item(item, 'text')

    def _on_library_select(self, _event=None):
        command = self._selected_command()
        if command and str(self.command_entry.cget('state')) == 'normal':
            self.command_entry.delete(0, tk.END)
            self.command_entry.insert(0, command)

    def _on_library_double_click(self, _event=None):
        command = self._selected_command()
        if not command:
            return
        if not self.instrument:
            self.log("info", "Not connected. Connect to an instrument first.")
            return
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, command)
        self.send()

    # --------------------------------------------------------------- history
    def _remember(self, command):
        if not self.history or self.history[-1] != command:
            self.history.append(command)
        self.history_index = len(self.history)

    def _history_prev(self, _event=None):
        if not self.history or self.history_index == 0:
            return 'break'
        self.history_index -= 1
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, self.history[self.history_index])
        return 'break'

    def _history_next(self, _event=None):
        if not self.history:
            return 'break'
        if self.history_index >= len(self.history) - 1:
            self.history_index = len(self.history)
            self.command_entry.delete(0, tk.END)
            return 'break'
        self.history_index += 1
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, self.history[self.history_index])
        return 'break'

    # ------------------------------------------------------------ VISA: scan
    def refresh_instruments(self):
        if not PYVISA_AVAILABLE:
            self.log("error", "ERROR: PyVISA is not available.")
            return
        self._run_worker(self._scan_thread)

    def _scan_thread(self):
        """Enumerates VISA resources and probes each with *IDN?."""
        found = []
        try:
            if self.rm is None:
                self.rm = pyvisa.ResourceManager()
            addresses = self.rm.list_resources()
            if not addresses:
                self.msg_queue.put(('info', "No VISA instruments found."))
            for address in addresses:
                idn = "Unknown / no response"
                try:
                    with self.rm.open_resource(address) as device:
                        device.timeout = 2000
                        device.read_termination = "\n"
                        device.write_termination = "\n"
                        idn = device.query('*IDN?').strip()
                except Exception:
                    pass  # busy / non-SCPI / timeout -- keep it in the list anyway
                label = f"{address}{ADDR_SEP}{idn}"
                found.append(label)
                self.msg_queue.put(('info', f"  {label}"))
        except Exception as exc:
            self.msg_queue.put((
                'error',
                f"A critical VISA error occurred: {exc}. "
                "Ensure a VISA backend (e.g. NI-VISA) is installed."))
        self.msg_queue.put(('resources', found))
        self.msg_queue.put(('done', None))

    def _populate_instruments(self, labels):
        self.instrument_combobox['values'] = labels
        if labels and not self.instrument_combobox.get():
            self.instrument_combobox.set(labels[0])

    # --------------------------------------------------------- VISA: connect
    def connect(self):
        label = self.instrument_combobox.get().strip()
        if not label:
            self.log("error", "ERROR: No instrument selected.")
            return
        try:
            timeout_ms = int(self.timeout_entry.get())
            if timeout_ms <= 0:
                raise ValueError
        except ValueError:
            self.log("error", "ERROR: Timeout must be a positive integer (ms).")
            return

        address = label.split(ADDR_SEP)[0].strip()
        termination = TERMINATIONS[self.term_combobox.get()]
        self._run_worker(self._connect_thread, address, timeout_ms, termination)

    def _connect_thread(self, address, timeout_ms, termination):
        try:
            if self.rm is None:
                self.rm = pyvisa.ResourceManager()
            device = self.rm.open_resource(address)
            device.timeout = timeout_ms
            # Handle the special EOI termination case
            if termination == "EOI":
                device.read_termination = ""
                device.write_termination = ""
                device.send_end = True
            else:
                device.read_termination = termination
                device.write_termination = termination
                device.send_end = False  # Default behavior
            idn = device.query('*IDN?').strip()
            self.instrument = device
            self.msg_queue.put(('info', f"Connected to {address}"))
            self.msg_queue.put(('rx', f"<< {idn}"))
            self.msg_queue.put(('connected', idn))
        except Exception as exc:
            self.instrument = None
            self.msg_queue.put(('error', f"ERROR: Could not connect. {exc}"))
        self.msg_queue.put(('done', None))

    def _on_connected(self, idn):
        self._set_controls_enabled(connected=True)
        short_idn = idn if len(idn) <= 60 else idn[:57] + "..."
        self.status_label.config(
            text=f"Connected: {short_idn}", fg=self.CLR_ACCENT_GREEN)
        self.command_entry.focus_set()

    def disconnect(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
                self.log("info", "Disconnected.")
            except Exception as exc:
                self.log("error", f"ERROR while closing: {exc}")
            finally:
                self.instrument = None
        self._set_controls_enabled(connected=False, visa_ok=PYVISA_AVAILABLE)
        self.status_label.config(text="Disconnected", fg=self.CLR_FG_LIGHT)

    # ------------------------------------------------------ VISA: transactions
    def _require_command(self):
        command = self.command_entry.get().strip()
        if not command:
            self.log("info", "Enter a command first.")
            return None
        return command

    def send(self):
        """Auto-detect: commands ending in '?' are queried, others written."""
        command = self._require_command()
        if command is None or self.instrument is None:
            return
        mode = 'query' if command.endswith('?') else 'write'
        self._dispatch(mode, command)

    def write_only(self):
        command = self._require_command()
        if command is None or self.instrument is None:
            return
        self._dispatch('write', command)

    def query_only(self):
        command = self._require_command()
        if command is None or self.instrument is None:
            return
        self._dispatch('query', command)

    def read_only(self):
        """Drains a pending response; needs no command."""
        if self.instrument is None:
            return
        self._dispatch('read', None)

    def _dispatch(self, mode, command):
        if command is not None:
            self._remember(command)
            self.command_entry.delete(0, tk.END)
        self._run_worker(self._transaction_thread, mode, command)

    def _transaction_thread(self, mode, command):
        """Runs one VISA transaction. Never drops the connection on failure."""
        device = self.instrument
        if device is None:
            self.msg_queue.put(('error', "ERROR: Not connected."))
            self.msg_queue.put(('done', None))
            return

        if command is not None:
            self.msg_queue.put(('tx', f">> {command}"))
        else:
            self.msg_queue.put(('tx', ">> (read)"))

        start = time.perf_counter()
        try:
            if mode == 'write':
                device.write(command)
                elapsed = (time.perf_counter() - start) * 1000
                self.msg_queue.put(('rx', f"<< (write ok)  [{elapsed:.1f} ms]"))
            else:
                response = (device.query(command) if mode == 'query'
                            else device.read())
                elapsed = (time.perf_counter() - start) * 1000
                self.msg_queue.put(
                    ('rx', f"<< {response.strip()}  [{elapsed:.1f} ms]"))
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            if pyvisa is not None and isinstance(exc, pyvisa.VisaIOError) \
                    and exc.error_code == pyvisa.constants.StatusCode.error_timeout:
                self.msg_queue.put((
                    'error',
                    f"TIMEOUT after {elapsed:.0f} ms -- no response."))
            else:
                self.msg_queue.put(('error', f"ERROR: {exc}"))
        self.msg_queue.put(('done', None))

    # --------------------------------------------------------------- shutdown
    def _on_closing(self):
        if self.instrument is not None:
            try:
                self.instrument.close()
            except Exception:
                pass
            self.instrument = None
        self.root.destroy()


def main():
    """Initializes the application."""
    root = tk.Tk()
    PICASCPIConsoleApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
