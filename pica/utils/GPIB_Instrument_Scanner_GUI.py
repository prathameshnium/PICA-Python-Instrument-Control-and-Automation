"""
Module: GPIB_Instrument_Scanner_GUI.py
Purpose: GUI module for the GPIB/VISA Instrument Scanner.
Legacy tag: v4 (old file-series number; current version is PROGRAM_VERSION)
"""

# -------------------------------------------------------------------------------
# Name:         GPIB/VISA Instrument Scanner GUI
# Purpose:      A graphical user interface to find all connected instruments
#               and display their identification strings. (Updated to match PICA Launcher)
# Author:       Prathamesh Deshmukh
# Created:      17/09/2025
# Version:      V: 2.1
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, scrolledtext
from tkinter import font as tkfont
import threading
import queue
import importlib.util  # find pyvisa without importing it
from datetime import datetime

# --- Packages for Back end ---
# pyvisa costs ~0.7 s to import and the Address Book (also opened from the v2
# launcher) never needs it, so it is only located here and imported by the scan.
pyvisa = None
try:
    PYVISA_AVAILABLE = importlib.util.find_spec("pyvisa") is not None
except ValueError:  # already in sys.modules without a spec (e.g. a stand-in), so present
    PYVISA_AVAILABLE = True


def _load_pyvisa():
    """Import pyvisa on first use; returns the module or None."""
    global pyvisa, PYVISA_AVAILABLE
    if pyvisa is None and PYVISA_AVAILABLE:
        try:
            import pyvisa as _pyvisa
            pyvisa = _pyvisa
        except Exception:   # not just ImportError: a broken install raises
            PYVISA_AVAILABLE = False   # others, and the Scan button must recover
    return pyvisa

# -------------------------------------------------------------------------------
# --- ADDRESS BOOK ---
# -------------------------------------------------------------------------------
# One row per GPIB primary address 0-30, shown by the Address Book popup.
# Hints, not facts: any address can be changed from an instrument's front panel,
# so modules identify instruments by their *IDN? reply and only use the address
# as a pre-selection. Always trust the scan over this table.
#
# Provenance:
#   * 8, 11, 12, 13, 17, 19, 20, 24, 27: all answered on GPIB1 in the scan of
#     21 Oct 2026. 23 (Cryocon 34) answered on GPIB1 the same day, in a later
#     scan with the Cryocon switched on.
#   * The E4980A (17) used to sit on GPIB0; on 21 Oct 2026 it answered on GPIB1.
#     GPIB0::12 and GPIB0::17 still show up but fail with VI_ERROR_ALLOC: stale
#     entries for a board that is no longer there, not instruments.
#   * L340 -> 19 and Cryocon 34 -> 23 were set on the front panels on
#     3 Sep 2026. Both used to sit on 12, the factory default they share with
#     the L350.
#   * The AFG3022B (11) is switched on and off at the rack.
#   * K197A (20) has no *IDN?; the 1973A/1972A card hands back a pending
#     reading such as "OOHM+9.99999E+9" (overflow ohms). Probable, not proven.
#   * K2182 (7) has not been seen on recent scans; keep 7 for it.
#   * 4 was the K2400's old address. On 21 Oct 2026 every K2400 module was
#     moved to default to GPIB1::24, so 4 is free again.
#   * Novocontrol Alpha-AN (10) answers *IDN? but is not SCPI; never probe it.
#     It sits on the BDS PC's own GPIB0 bus, not the rack's GPIB1: WinALPHA's
#     auto-detect found it at 10 on 22 Oct 2026. 5 was only ever a guess in
#     the code, never its real address; keep 5 unused.
#   * Keysight E4991B RF impedance analyzer (SN MY54100159, fw A.01.03): the
#     same WinALPHA auto-detect found it at 17 on the BDS GPIB0 bus. It shares
#     17 with the E4980A only because they sit on different buses; move one
#     before the two ever share a bus. No PICA module talks to it yet.
#   * The L350 and L340 also answer on every secondary address (GPIB1::12::0
#     to ::12::30): their GPIB chips ignore the secondary byte. Same instrument,
#     not 31 more of them.
#
# Never: 0 is the controller (the PC's GPIB board), 21 is used by controllers
# and Keysight adapters, 30 by some adapters, 31 is invalid.
# Avoid: factory defaults of instruments a lab tends to acquire later.
# A VI_ERROR_TMO is a busy instrument (held open by a running module), not an
# empty address. Close the other program and rescan before concluding anything.
#
# When you assign a new address: set it on the front panel, change its row here,
# and update the *_ADDRESS_HINT of the module that talks to the instrument.
ADDRESS_BOOK_SEEN = "21 Oct 2026"

# (address, status, instrument, bus, last seen, note)
# status is one of: "In use", "Reserved", "Free", "Avoid", "Never"
ADDRESS_BOOK = [
    (0, "Never", "", "", "", "The PC's GPIB board (bus controller)"),
    (1, "Avoid", "", "", "", "Tektronix factory default"),
    (2, "Free", "", "", "", ""),
    (3, "Free", "", "", "", ""),
    (4, "Free", "", "", "", ""),
    (5, "Avoid", "", "", "",
     "Old Alpha-AN guess, never its real address; don't use"),
    (6, "Free", "", "", "", ""),
    (7, "Reserved", "Keithley 2182", "", "not recently",
     "Kept for the 2182"),
    (8, "In use", "SRS SR830 lock-in", "GPIB1", ADDRESS_BOOK_SEEN, ""),
    (9, "Avoid", "", "", "", "Agilent 34970A factory default"),
    (10, "In use", "Novocontrol Alpha-AN", "BDS GPIB0", "22 Oct 2026",
     "BDS PC bus, not the rack. Not SCPI: never probe"),
    (11, "In use", "Tektronix AFG3022B", "GPIB1", ADDRESS_BOOK_SEEN,
     "Switched on/off at the rack"),
    (12, "In use", "Lakeshore 350", "GPIB1", ADDRESS_BOOK_SEEN,
     "Also answers on every secondary address"),
    (13, "In use", "Keithley 6221", "GPIB1", ADDRESS_BOOK_SEEN, ""),
    (14, "Avoid", "", "", "", "Keithley 6485 factory default"),
    (15, "Avoid", "", "", "",
     "Old Lakeshore address, still in two Instrument_Control scripts"),
    (16, "Avoid", "", "", "", "Keithley 2000/2010/2700 factory default"),
    (17, "In use", "Keysight E4980A / E4991B", "GPIB1", ADDRESS_BOOK_SEEN,
     "E4980A on GPIB1 (was GPIB0). E4991B on BDS GPIB0, seen 22 Oct 2026"),
    (18, "Avoid", "", "", "", "Keithley 2450 factory default"),
    (19, "In use", "Lakeshore 340", "GPIB1", ADDRESS_BOOK_SEEN,
     "Was 12 before 3 Sep 2026; answers on every secondary"),
    (20, "In use", "Keithley 197A DMM", "GPIB1", ADDRESS_BOOK_SEEN,
     "No *IDN?: replies with a reading"),
    (21, "Never", "", "", "", "Controllers and Keysight adapters"),
    (22, "Avoid", "", "", "", "Agilent 3458A/34401A factory default"),
    (23, "In use", "Cryocon 34", "GPIB1", ADDRESS_BOOK_SEEN,
     "Was 12 before 3 Sep 2026"),
    (24, "In use", "Keithley 2400", "GPIB1", ADDRESS_BOOK_SEEN, ""),
    (25, "Free", "", "", "", ""),
    (26, "Free", "", "", "", ""),
    (27, "In use", "Keithley 6517B", "GPIB1", ADDRESS_BOOK_SEEN, ""),
    (28, "Free", "", "", "", ""),
    (29, "Free", "", "", "", ""),
    (30, "Never", "", "", "", "Used by some adapters (31 is invalid)"),
]

# Free addresses in the order to hand them out, safest first.
FREE_ADDRESSES_SAFEST_FIRST = (26, 29, 25, 28, 2, 3, 4, 6)

# Address Book colours by status: (tile background, tile text, table row).
ADDRESS_BOOK_COLOURS = {
    "In use": ('#5E8C4F', '#FFFFFF', '#D9E8CF'),
    "Reserved": ('#8F8173', '#FFFFFF', '#E5DCD3'),
    "Free": ('#FFFFFF', '#2C2825', '#FFFFFF'),
    "Avoid": ('#E3B24A', '#2C2825', '#F6E3B8'),
    "Never": ('#B5483B', '#FFFFFF', '#EDC2BB'),
}

# Long instrument names shortened to fit an address tile.
_TILE_NAME_CUTS = (("Keithley ", "K"), ("Lakeshore ", "LS"), ("Tektronix ", ""),
                   ("SRS ", ""), (" lock-in", ""), ("Keysight ", ""),
                   ("Novocontrol ", ""), (" DMM", ""))


def _tile_name(row):
    """Short instrument name for a tile, or the status for an empty address."""
    name = row[2]
    if not name:
        return row[1].lower()
    for old, new in _TILE_NAME_CUTS:
        name = name.replace(old, new)
    return name


def show_address_book(parent):
    """Open the Address Book popup over parent and return its Toplevel.

    A colour map of addresses 0-30 to read at a glance, then the full table.
    Plain tk widgets and its own Treeview style, so it looks the same from the
    scanner and from the v2 launcher's Instrument Status window.
    """
    bg, panel, text, dim, accent = '#B8A392', '#E5DCD3', '#1A1A1A', '#4A423C', '#BA6B5E'
    font_base = ('Segoe UI', 11)

    win = tk.Toplevel(parent)
    win.title("PICA GPIB Address Book")
    win.configure(bg=bg)
    win.geometry("1000x860")
    win.minsize(820, 600)

    frame = tk.Frame(win, bg=bg, padx=16, pady=12)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(4, weight=1)

    # --- headline --------------------------------------------------------
    head = tk.Frame(frame, bg=bg)
    head.grid(row=0, column=0, sticky='ew')
    tk.Label(head, text="GPIB Address Book", bg=bg, fg=accent,
             font=('Segoe UI', 18, 'bold')).pack(side='left')
    tk.Label(head, text=f"  bus as seen {ADDRESS_BOOK_SEEN} · hints, trust the scan",
             bg=bg, fg=dim, font=('Segoe UI', 10)).pack(side='left', pady=(8, 0))
    tk.Label(head, text=f"Next free address:  {FREE_ADDRESSES_SAFEST_FIRST[0]}",
             bg=ADDRESS_BOOK_COLOURS["In use"][0], fg='#FFFFFF',
             font=('Segoe UI', 13, 'bold'), padx=12, pady=4).pack(side='right')

    # --- legend with counts ----------------------------------------------
    legend = tk.Frame(frame, bg=bg)
    legend.grid(row=1, column=0, sticky='w', pady=(8, 6))
    for status, (tile_bg, tile_fg, _) in ADDRESS_BOOK_COLOURS.items():
        count = sum(1 for row in ADDRESS_BOOK if row[1] == status)
        tk.Label(legend, text=f" {status}  {count} ", bg=tile_bg, fg=tile_fg,
                 font=('Segoe UI', 10, 'bold'), padx=6, pady=2,
                 highlightthickness=1, highlightbackground='#8F8173').pack(
            side='left', padx=(0, 8))

    # --- address map: 0-30 in rows of eight -------------------------------
    grid = tk.Frame(frame, bg=bg)
    grid.grid(row=2, column=0, sticky='ew')
    cols = 8
    for c in range(cols):
        grid.columnconfigure(c, weight=1, uniform='tile')
    tiles = list(ADDRESS_BOOK) + [(31, "Never", "", "", "", "Invalid address")]
    tree_rows = {}

    def _select(addr):
        item = tree_rows.get(addr)
        if item:
            tree.selection_set(item)
            tree.see(item)

    for i, row in enumerate(tiles):
        tile_bg, tile_fg, _ = ADDRESS_BOOK_COLOURS[row[1]]
        tile = tk.Frame(grid, bg=tile_bg, highlightthickness=1,
                        highlightbackground='#8F8173', padx=6, pady=4, cursor='hand2')
        tile.grid(row=i // cols, column=i % cols, sticky='nsew', padx=3, pady=3)
        num = tk.Label(tile, text=str(row[0]), bg=tile_bg, fg=tile_fg,
                       font=('Segoe UI', 18, 'bold'))
        num.pack(anchor='w')
        name = tk.Label(tile, text=_tile_name(row) if row[0] < 31 else "invalid",
                        bg=tile_bg, fg=tile_fg,
                        font=('Segoe UI', 10, 'bold' if row[2] else 'normal'))
        name.pack(anchor='w')
        for widget in (tile, num, name):
            widget.bind('<Button-1>', lambda _e, a=row[0]: _select(a))

    # --- full table --------------------------------------------------------
    tk.Label(frame, text="DETAILS  (click a tile to jump to its row)", bg=bg, fg=dim,
             font=('Segoe UI', 10, 'bold')).grid(row=3, column=0, sticky='w',
                                                 pady=(12, 4))
    table = tk.Frame(frame, bg=bg)
    table.grid(row=4, column=0, sticky='nsew')
    table.rowconfigure(0, weight=1)
    table.columnconfigure(0, weight=1)

    # Row height from the font, or rows clip on a high-DPI screen.
    style = ttk.Style(win)
    line = tkfont.Font(root=win, font=font_base).metrics('linespace')
    style.configure('AddrBook.Treeview', font=font_base, rowheight=int(line * 1.4))
    style.configure('AddrBook.Treeview.Heading', font=font_base + ('bold',))

    columns = ("addr", "status", "instrument", "bus", "seen", "note")
    headings = ("Addr", "Status", "Instrument", "Bus", "Last seen", "Note")
    widths = (55, 90, 220, 65, 115, 340)
    tree = ttk.Treeview(table, columns=columns, show='headings',
                        style='AddrBook.Treeview')
    for col, heading, width in zip(columns, headings, widths):
        tree.heading(col, text=heading)
        tree.column(col, width=width, minwidth=width,
                    anchor='center' if col in ("addr", "status", "bus") else 'w',
                    stretch=(col == "note"))
    for status, (_, _, row_bg) in ADDRESS_BOOK_COLOURS.items():
        tree.tag_configure(status, background=row_bg)
    for row in ADDRESS_BOOK:
        tree_rows[row[0]] = tree.insert('', 'end', values=row, tags=(row[1],))
    scroll = ttk.Scrollbar(table, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky='nsew')
    scroll.grid(row=0, column=1, sticky='ns')

    notes = ("•  Lakeshores also answer on every secondary address "
             "(GPIB1::12::0 to ::12::30): same instrument, not more of them.\n"
             "•  GPIB0 entries failing with VI_ERROR_ALLOC are stale; a "
             "VI_ERROR_TMO is a busy instrument, not a free address.\n"
             "•  New address: set it on the front panel, edit ADDRESS_BOOK in "
             "GPIB_Instrument_Scanner_GUI.py, update the module's *_ADDRESS_HINT.")
    tk.Label(frame, text=notes, justify='left', anchor='w', wraplength=950,
             bg=bg, fg=text, font=('Segoe UI', 10)).grid(
        row=5, column=0, sticky='ew', pady=(10, 0))

    tk.Button(frame, text="Close", command=win.destroy, bg=panel, fg=accent,
              font=font_base, relief='flat', pady=6).grid(
        row=6, column=0, sticky='ew', pady=(10, 0))
    return win


# -------------------------------------------------------------------------------
# --- FRONT END (GUI) ---
# -------------------------------------------------------------------------------


class GPIB_Instrument_Scanner_GUI:
    """The main GUI application class for scanning VISA instruments."""
    PROGRAM_VERSION = "2.1"
    # --- Styling constants from PICA Launcher ---
    CLR_BG_DARK = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG_LIGHT = '#2C2825'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_CONSOLE_BG = '#E5DCD3'
    CLR_TEXT_DARK = '#1A1A1A'
    FONT_SIZE_BASE = 11
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 2, 'bold')
    FONT_CONSOLE = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title(
            f"GPIB/VISA Instrument Scanner v{self.PROGRAM_VERSION}")
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
        self.address_book_win = None

        self.setup_styles()
        self.create_widgets()

        self.log("GPIB/VISA scanner window opened. Auto-scan will begin shortly.")
        self.root.after(100, self.process_queue)  # Start the queue processor
        # Auto-start the scan once the window has had a moment to draw
        self.root.after(100, self.start_scan)

    def setup_styles(self):
        """Configures ttk styles for a modern look."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure(
            'TLabel',
            background=self.CLR_BG_DARK,
            foreground=self.CLR_FG_LIGHT,
            font=self.FONT_TITLE)
        style.configure(
            'App.TButton',
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
            'App.TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_TEXT_DARK), ('hover', self.CLR_TEXT_DARK)])
        style.configure(
            'Scan.TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_TEXT_DARK,
            background=self.CLR_ACCENT_GREEN)
        style.map(
            'Scan.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])

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

        self.scan_button = ttk.Button(
            controls_frame,
            text="Scan for Instruments",
            command=self.start_scan,
            style='Scan.TButton')
        self.scan_button.grid(row=0, column=0, padx=(0, 5), sticky='ew')

        guide_button = ttk.Button(
            controls_frame,
            text="Address Book",
            command=self.show_address_guide,
            style='App.TButton')
        guide_button.grid(row=0, column=1, padx=5, sticky='ew')

        clear_button = ttk.Button(
            controls_frame,
            text="Clear Log",
            command=self.clear_log,
            style='App.TButton')
        clear_button.grid(row=0, column=2, padx=(5, 0), sticky='ew')

        # --- Console/Results Frame ---
        self.console_widget = scrolledtext.ScrolledText(
            main_frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG_LIGHT,
            font=self.FONT_CONSOLE,
            wrap='word',
            bd=0)
        self.console_widget.grid(row=1, column=0, sticky='nsew')

        ttk.Button(
            main_frame,
            text="Close",
            style='App.TButton',
            command=self.root.destroy).grid(
            row=2,
            column=0,
            sticky='ew',
            pady=(
                15,
                0))

        self.log("Welcome to the GPIB/VISA Instrument Scanner.")
        if not PYVISA_AVAILABLE:
            self.log(
                "CRITICAL: PyVISA library not found. Please run 'pip install pyvisa'.",
                level='error')
            self.scan_button.config(state='disabled')
        else:
            self.log("Auto-scanning for instruments...")

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

        # Run the actual scanning logic in a separate thread to prevent GUI
        # freezing
        scan_thread = threading.Thread(
            target=self.run_scan_thread, daemon=True)
        scan_thread.start()

    def process_queue(self):
        """Drains every queued message from the worker thread and updates the GUI."""
        try:
            while True:
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
        """Opens the Address Book popup, or raises it if it is already open."""
        win = self.address_book_win
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            return
        self.address_book_win = show_address_book(self.root)

    def run_scan_thread(self):
        """
        This is the backend function that runs in a separate thread.
        It performs the VISA scan and puts results into the thread-safe queue.
        This is the logic of GPIB_VISA_InterfaceTest_Simple_Instrument_Control.py.
        """
        if not _load_pyvisa():
            self.result_queue.put("ERROR: PyVISA is not available.\n")
            self.result_queue.put("SCAN_COMPLETE")
            return

        try:
            rm = pyvisa.ResourceManager()
            instrument_addresses = rm.list_resources()

            if not instrument_addresses:
                self.result_queue.put(
                    "-> No instruments found. Check connections and VISA installation.\n")
            else:
                self.result_queue.put(
                    f"-> Found {len(instrument_addresses)} instrument(s). Querying...\n\n")

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
    GPIB_Instrument_Scanner_GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
