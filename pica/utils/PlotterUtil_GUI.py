"""
Module: PlotterUtil_GUI.py
Purpose: GUI module for the PICA Plotter Utility.
Legacy tag: v3 (old file-series number; current version is PROGRAM_VERSION)
"""

# -------------------------------------------------------------------------------
# Name:         PICA Plotter Utility
# Purpose:      A general-purpose CSV/DAT file plotter for the PICA suite.
# Author:       Prathamesh Deshmukh
# Created:      06/10/2025
# Version:      2.1 (Multi-file & Multi-instance support)
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import csv
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib as mpl
import numpy as np

# --- Multi-instance support ---
import sys
from multiprocessing import Process
import multiprocessing
import subprocess

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

def run_script_process(script_path):
    """
    Wrapper function to execute a script in a new process.
    This becomes the target for the new, isolated process.
    `script_path` can be a list (e.g., ['python', 'script.py']) or a string (path to .exe).
    """
    try:
        # This command works for both .py files (e.g., ['python', 'script.py'])
        # and .exe files (e.g., ['path/to/program.exe']).
        subprocess.run(script_path, check=True)
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")


class PlotterAppGUI:
    PROGRAM_VERSION = "2.1"
    CLR_BG = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_RED = '#BA6B5E'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_TITLE_ITALIC = ('Segoe UI', 13, 'bold italic')

    try:
        # Robust path finding for assets. When running from source, `__file__` is
        # inside pica/utils. We navigate one level up ('..') to the pica
        # package folder to correctly locate its 'assets' folder. This
        # approach also works when the script is bundled.
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",  # type: ignore
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        # Fallback for environments where `__file__` might not be defined.
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Plotter Utility v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.active_filepath = None
        # New data structure to hold data for multiple files
        # Format: { "filepath": {"headers": [...], "data": {...}, "mod_time": ..., "size": ...} }
        # --- Checkbox UI Change ---
        self.file_data_cache = {}
        # Stores {filepath: {'var': tk.BooleanVar, 'chk': ttk.Checkbutton,
        # 'lbl': ttk.Label}}
        self.file_ui_elements = {}
        self.logo_image = None

        self.file_watcher_job = None
        # Live update redraws in place: {filepath: Line2D} of the current plot
        # and the settings it was drawn with (None = must replot in full)
        self._plot_lines = {}
        self._plot_sig = None

        self.setup_styles()
        self.create_widgets()
        self.log(
            "Welcome to the PICA Plotter Utility. Please select a file to begin.")

    def setup_styles(self):
        # ... (style configuration remains the same)
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure(
            '.',
            background=self.CLR_BG,
            foreground=self.CLR_FG,
            font=self.FONT_BASE)
        self.style.configure('TFrame', background=self.CLR_BG)
        self.style.configure('TPanedWindow', background=self.CLR_BG)
        self.style.configure(
            'TLabel',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG)
        self.style.configure('Header.TLabel', background=self.CLR_HEADER)
        self.style.configure(
            'TEntry',
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG,
            insertcolor=self.CLR_FG)
        self.style.configure(
            'TButton',
            font=self.FONT_BASE,
            padding=(
                10,
                9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER)
        self.style.map(
            'TButton', background=[
                ('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[
                ('active', self.CLR_BG), ('hover', self.CLR_BG)])
        self.style.configure(
            'Plot.TButton',
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_BG)
        self.style.map(
            'Plot.TButton', background=[
                ('active', '#8AB845'), ('hover', '#8AB845')])

        # Combobox styling
        self.style.map('TCombobox',
                       fieldbackground=[('readonly', self.CLR_INPUT_BG)],
                       selectbackground=[('readonly', self.CLR_ACCENT_BLUE)],
                       selectforeground=[('readonly', self.CLR_FG)],
                       foreground=[('readonly', self.CLR_FG)])
        self.style.configure('TCombobox', arrowcolor=self.CLR_FG)

        self.style.configure(
            'TLabelframe',
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure(
            'TLabelframe.Label',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG,
            font=self.FONT_TITLE)
        self.style.configure(
            'TCheckbutton',
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG)
        self.style.map(
            'TCheckbutton', background=[
                ('active', self.CLR_FRAME_BG)], indicatorcolor=[
                ('selected', self.CLR_ACCENT_GREEN), ('!selected', self.CLR_FG)])
        mpl.rcParams.update({
            'font.family': 'Segoe UI', 'font.size': 11,
            'axes.titlesize': 15, 'axes.labelsize': 13,
            'figure.facecolor': self.CLR_BG, 'axes.facecolor': '#F4EFEA',
            'axes.edgecolor': self.CLR_FG, 'axes.labelcolor': self.CLR_FG,
            'xtick.color': self.CLR_FG, 'ytick.color': self.CLR_FG,
            'text.color': self.CLR_FG,
        })

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)
        # Allow center column to expand
        header.grid_columnconfigure(1, weight=1)

        # --- Left Section: Program Name ---
        left_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        left_header_frame.grid(row=0, column=0, sticky='w')
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(
            left_header_frame,
            text="PICA General Purpose Plotter",
            style='Header.TLabel',
            font=font_title_main,
            foreground=self.CLR_ACCENT_GOLD).pack(
            side='left',
            padx=20,
            pady=10)

        # --- Center Section: Logo and Institute Name ---
        center_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        center_header_frame.grid(row=0, column=1, sticky='ew')
        logo_canvas = Canvas(
            center_header_frame,
            width=60,
            height=60,
            bg=self.CLR_HEADER,
            highlightthickness=0)
        logo_canvas.pack(side='left', pady=10)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(
                    self.LOGO_FILE_PATH).resize(
                    (60, 60), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(30, 30, image=self.logo_image)
            except Exception as e:
                self.log(f"Warning: Could not load logo. {e}")
        institute_frame = tk.Frame(center_header_frame, bg=self.CLR_HEADER)
        institute_frame.pack(side='left', padx=15)
        ttk.Label(
            institute_frame,
            text="UGC-DAE Consortium for Scientific Research",
            style='Header.TLabel',
            font=(
                'Segoe UI',
                16,
                'bold')).pack(
            anchor='w')
        ttk.Label(
            institute_frame,
            text="Mumbai Centre",
            style='Header.TLabel',
            font=(
                'Segoe UI',
                14)).pack(
            anchor='w')

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = self._create_left_panel(main_pane)
        main_pane.add(left_panel, weight=1)

        right_panel = self._create_right_panel(main_pane)
        main_pane.add(right_panel, weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=400)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        # --- File Management ---
        file_frame = ttk.LabelFrame(panel, text="Data Source")
        file_frame.grid(row=0, column=0, sticky='new', pady=5)
        file_frame.grid_columnconfigure(0, weight=1)

        file_buttons_frame = ttk.Frame(file_frame)
        file_buttons_frame.grid(row=0, column=0, sticky='ew', padx=10, pady=5)
        file_buttons_frame.grid_columnconfigure((0, 1), weight=1)
        ttk.Button(
            file_buttons_frame,
            text="Add File(s)...",
            command=self.browse_files).grid(
            row=0,
            column=0,
            sticky='ew',
            padx=(
                0,
                5))
        ttk.Button(
            file_buttons_frame,
            text="Remove Selected",
            command=self.remove_selected_file).grid(
            row=0,
            column=1,
            sticky='ew',
            padx=(
                5,
                0))

        # --- New Instance Button ---
        # This button is placed here for easy access to open another plotter.
        new_instance_button = ttk.Button(
            file_frame,
            text="Open New Plotter Window",
            command=self.launch_new_instance_handler)
        new_instance_button.grid(
            row=3,
            column=0,
            sticky='ew',
            padx=10,
            pady=(
                10,
                5))

        # --- Checkbox UI: Create a scrollable frame for file checkboxes ---
        list_container = ttk.Frame(file_frame, style='TFrame')
        list_container.grid(
            row=1,
            column=0,
            sticky='nsew',
            padx=10,
            pady=(
                0,
                10))
        list_container.rowconfigure(0, weight=1)
        list_container.columnconfigure(0, weight=1)

        file_canvas = tk.Canvas(
            list_container,
            bg=self.CLR_INPUT_BG,
            highlightthickness=0,
            height=100)
        scrollbar = ttk.Scrollbar(
            list_container,
            orient="vertical",
            command=file_canvas.yview)
        # This frame will hold the checkboxes
        self.file_list_frame = ttk.Frame(file_canvas, style='TFrame')
        # Style to match input background
        self.file_list_frame.configure(style='Input.TFrame')

        file_canvas.create_window(
            (0, 0), window=self.file_list_frame, anchor="nw")
        file_canvas.configure(yscrollcommand=scrollbar.set)
        file_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_list_frame.bind(
            "<Configure>", lambda e: file_canvas.configure(
                scrollregion=file_canvas.bbox("all")))
        # Bind mousewheel for easier navigation
        file_canvas.bind_all("<MouseWheel>", lambda e: file_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self.column_source_var = tk.StringVar(
            value="Columns from: (no file selected)")
        column_source_label = ttk.Label(
            file_frame,
            textvariable=self.column_source_var,
            style='TLabel',
            font=(
                'Segoe UI',
                9,
                'italic'),
            wraplength=350)
        column_source_label.grid(
            row=2,
            column=0,
            sticky='w',
            padx=10,
            pady=(
                0,
                10))

        # --- Plotting Parameters ---
        params_frame = ttk.LabelFrame(panel, text="Plot Parameters")
        params_frame.grid(row=1, column=0, sticky='new', pady=5)
        params_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(params_frame, text="X-Axis Column:").grid(row=0,
                                                            column=0, sticky='w', padx=10, pady=5)
        self.x_col_cb = ttk.Combobox(
            params_frame, state='readonly', style='TCombobox')
        self.x_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.x_col_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Y-Axis Column:").grid(row=1,
                                                            column=0, sticky='w', padx=10, pady=5)
        self.y_col_cb = ttk.Combobox(
            params_frame, state='readonly', style='TCombobox')
        self.y_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.y_col_cb.grid(row=1, column=1, sticky='ew', padx=10, pady=5)

        # --- Plotting Options ---
        # Explicitly use TFrame style
        options_frame = ttk.Frame(params_frame, style='TFrame')
        options_frame.grid(row=2, column=0, columnspan=2, sticky='ew', pady=10)
        options_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.live_update_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="Live Update",
            variable=self.live_update_var,
            command=self.toggle_live_update).grid(
            row=0,
            column=2,
            sticky='e',
            padx=10)

        self.x_log_var = tk.BooleanVar()
        ttk.Checkbutton(
            options_frame,
            text="X Log Scale",
            variable=self.x_log_var,
            command=self.plot_data).grid(
            row=0,
            column=0,
            sticky='w',
            padx=10)

        self.y_log_var = tk.BooleanVar()
        ttk.Checkbutton(
            options_frame,
            text="Y Log Scale",
            variable=self.y_log_var,
            command=self.plot_data).grid(
            row=0,
            column=1,
            sticky='w',
            padx=10)

        # Set the background of the options frame to match its parent
        options_frame.configure(style='TFrame')

        self.style.configure('Input.TFrame', background=self.CLR_INPUT_BG)

        # --- Comment lines: detected on load, editable per file ---
        comment_frame = ttk.Frame(params_frame, style='TFrame')
        comment_frame.grid(row=3, column=0, columnspan=2, sticky='ew', padx=10, pady=(0, 5))
        ttk.Label(comment_frame, text="Comment lines at top:").pack(side='left')
        self.comment_lines_var = tk.StringVar(value="")
        digits_only = (self.root.register(lambda p: p == "" or p.isdigit()), '%P')
        self.comment_lines_sb = ttk.Spinbox(
            comment_frame, from_=0, to=500, width=5,
            textvariable=self.comment_lines_var,
            validate='key', validatecommand=digits_only,
            command=self._apply_comment_lines)
        self.comment_lines_sb.pack(side='left', padx=5)
        self.comment_lines_sb.bind("<Return>", self._apply_comment_lines)
        self.comment_lines_sb.bind("<FocusOut>", self._apply_comment_lines)
        ttk.Button(comment_frame, text="Auto", width=6,
                   command=self._reset_comment_lines).pack(side='left', padx=5)
        self.comment_source_var = tk.StringVar(value="")
        ttk.Label(comment_frame, textvariable=self.comment_source_var,
                  font=('Segoe UI', 9, 'italic')).pack(side='left', padx=5)

        ttk.Button(
            params_frame,
            text="Reload & Plot",
            style="Plot.TButton",
            command=lambda: self.load_file_data(
                self.active_filepath)).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky='ew',
            padx=10,
            pady=10)

        # --- Information Box ---
        info_frame = ttk.LabelFrame(panel, text="Information")
        info_frame.grid(row=2, column=0, sticky='new', pady=5)
        info_frame.grid_columnconfigure(0, weight=1)
        info_text = "This utility plots data from CSV or DAT files. It supports live updates for monitoring ongoing experiments."
        ttk.Label(
            info_frame,
            text=info_text,
            justify='left',
            style='TLabel',
            wraplength=350).grid(
            row=0,
            column=0,
            sticky='ew',
            padx=10,
            pady=5)

        # --- Console ---
        console_frame = ttk.LabelFrame(panel, text="Console")
        console_frame.grid(row=3, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(
            console_frame,
            state='disabled',
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG,
            font=(
                'Consolas',
                9),
            wrap='word',
            borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        container = ttk.LabelFrame(panel, text='Plot')
        container.pack(fill='both', expand=True)

        self.figure = Figure(dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.set_title(
            "Select a file and columns to plot",
            fontweight='bold')
        self.ax_main.set_xlabel("X-Axis")
        self.ax_main.set_ylabel("Y-Axis")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        # Add the Matplotlib navigation toolbar
        toolbar_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        toolbar_frame.pack(fill='x', side='bottom', pady=(0, 5))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.configure(background=self.CLR_FRAME_BG)
        toolbar._message_label.config(
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG)
        for button in toolbar.winfo_children():
            button.config(background=self.CLR_FRAME_BG)
        toolbar.update()

        return panel

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state='normal')
        self.console.insert('end', log_msg)
        self.console.see('end')
        self.console.config(state='disabled')

    @staticmethod
    def _detect_delimiter(line):
        """Infer delimiter from a header/data line. None means whitespace."""
        if '\t' in line:
            return '\t'
        if ',' in line:
            return ','
        if ';' in line:
            return ';'
        return None

    @staticmethod
    def _sniff_encoding(filepath):
        """Pick the text encoding from the byte-order mark, if any.

        Files re-saved from Excel carry a BOM: 'CSV UTF-8' prepends a UTF-8
        BOM (which would hide the leading '#' of the first comment line) and
        'Unicode Text' writes UTF-16. Without a BOM the file is UTF-8 unless
        it fails to decode, as Windows-native exports (e.g. WinDETA's
        'Temp. [°C]' header) are cp1252.
        """
        with open(filepath, 'rb') as f:
            head = f.read(65536)
        return PlotterAppGUI._sniff_encoding_bytes(head)

    @staticmethod
    def _sniff_encoding_bytes(head):
        """_sniff_encoding on bytes already read (the first 64 KB are enough)."""
        head = head[:65536]
        if head.startswith(b'\xef\xbb\xbf'):
            return 'utf-8-sig'
        if head.startswith((b'\xff\xfe', b'\xfe\xff')):
            return 'utf-16'
        try:
            head.decode('utf-8')
        except UnicodeDecodeError as e:
            if e.start < len(head) - 3:  # not just a character cut at the edge
                return 'cp1252'
        return 'utf-8'

    @staticmethod
    def _dedupe_headers(headers):
        """Cp, Cp'' -> stays distinct; true duplicates get _2, _3 suffixes."""
        seen, out = {}, []
        for h in headers:
            if h in seen:
                seen[h] += 1
                out.append(f"{h}_{seen[h]}")
            else:
                seen[h] = 0
                out.append(h)
        return out

    def launch_new_instance_handler(self):
        """
        Handles launching a new instance of the plotter. This logic correctly
        differentiates between running from a .py script and a bundled .exe.
        """
        try:
            if getattr(sys, 'frozen', False):
                # We are running in a bundle (as an .exe).
                # sys.executable is the path to the .exe itself.
                args = [sys.executable]
            else:
                # We are running in a normal Python environment.
                args = [sys.executable, __file__]
            Process(target=run_script_process, args=(args,)).start()
        except Exception as e:
            messagebox.showerror(
                "Launch Error",
                f"Could not open a new plotter instance.\n\nError: {e}")

    def browse_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Select a data file",
            filetypes=(("Data Files", "*.csv *.dat *.txt"), ("All files", "*.*"))
        )
        self.add_files(filepaths)

    def add_files(self, filepaths):
        """Add data files to the session.

        Used by Browse and by files passed on the command line, so that
        'File > Open Data as Graph' in the launcher opens them plotted.
        """
        if not filepaths:
            return

        new_files_added = False
        for fp in filepaths:
            if fp not in self.file_data_cache:
                new_files_added = True
                self.file_data_cache[fp] = {"path": fp}  # Add placeholder
                self._add_file_to_ui(fp)
                self.log(f"Added file: {os.path.basename(fp)}")
                # Load data for the new file without making it active
                self._load_file_data_into_cache(fp)

        # If this is the first file added, make it active
        if filepaths and self.active_filepath is None:
            self._set_active_file(filepaths[0])
        elif new_files_added:
            # If new files were added to an existing list, just replot
            self.plot_data()

    def _add_file_to_ui(self, filepath):
        """Creates and adds a checkbox and label for a new file to the UI."""
        var = tk.BooleanVar(value=True)

        # Create a frame for each file entry
        entry_frame = ttk.Frame(self.file_list_frame, style='Input.TFrame')
        entry_frame.pack(fill='x', expand=True)

        chk = ttk.Checkbutton(
            entry_frame,
            variable=var,
            command=self.plot_data)
        chk.pack(side='left', padx=(5, 0))

        filename = os.path.basename(filepath)
        lbl = ttk.Label(
            entry_frame,
            text=filename,
            style='TLabel',
            anchor='w',
            background=self.CLR_INPUT_BG)
        lbl.pack(side='left', fill='x', expand=True, padx=5)
        lbl.bind(
            "<Button-1>",
            lambda e,
            fp=filepath: self._set_active_file(fp))

        self.file_ui_elements[filepath] = {
            'var': var, 'chk': chk, 'lbl': lbl, 'frame': entry_frame}

    def remove_selected_file(self):
        # Remove files that are currently checked
        paths_to_remove = [
            fp for fp,
            ui in self.file_ui_elements.items() if ui['var'].get()]
        if not paths_to_remove:
            messagebox.showinfo(
                "Remove Files",
                "No files are selected (checked) to be removed.")
            return

        for path in paths_to_remove:
            if path in self.file_ui_elements:
                self.file_ui_elements[path]['frame'].destroy()
                del self.file_ui_elements[path]
            if path in self.file_data_cache:
                del self.file_data_cache[path]
            self.log(f"Removed file: {os.path.basename(path)}")

            # If the active file was removed, find a new one or reset
            if self.active_filepath == path:
                self.active_filepath = None
                # Try to set a new active file
                remaining_files = list(self.file_ui_elements.keys())
                if remaining_files:
                    self._set_active_file(remaining_files[0])
                else:
                    self._set_active_file(None)  # No files left, so reset

        self.plot_data()  # Re-plot with remaining files

    def _set_active_file(self, filepath):
        """Sets a file as 'active' for populating column dropdowns."""
        # Reset background color for all labels
        for ui in self.file_ui_elements.values():
            ui['lbl'].configure(background=self.CLR_INPUT_BG)

        if filepath is None:
            self.active_filepath = None
            self.column_source_var.set("Columns from: (no file selected)")
            self.x_col_cb.set('')
            self.y_col_cb.set('')
            self.x_col_cb['values'] = []
            self.y_col_cb['values'] = []
            self._show_comment_lines(None)
            return

        # Highlight the new active file's label
        if filepath in self.file_ui_elements:
            self.file_ui_elements[filepath]['lbl'].configure(
                background=self.CLR_ACCENT_BLUE)

        self.column_source_var.set(
            f"Columns from: {os.path.basename(filepath)}")
        if self.active_filepath != filepath:
            self.active_filepath = filepath
            self.load_file_data(filepath)
        else:
            self._show_comment_lines(filepath)

    def _show_comment_lines(self, filepath):
        """Puts the file's comment-line count in the box."""
        info = self.file_data_cache.get(filepath) or {}
        n = info.get('comment_lines')
        self.comment_lines_var.set("" if n is None else str(n))
        if n is None:
            self.comment_source_var.set("")
        elif info.get('comment_override') is None:
            self.comment_source_var.set("(detected)")
        else:
            self.comment_source_var.set("(set by you)")

    def _apply_comment_lines(self, event=None):
        """Re-reads the active file treating the first N lines as comments."""
        fp = self.active_filepath
        if not fp or fp not in self.file_data_cache:
            self.comment_lines_var.set("")
            return
        info = self.file_data_cache[fp]
        text = self.comment_lines_var.get().strip()
        if not text or int(text) == info.get('comment_lines'):
            self._show_comment_lines(fp)
            return
        n = int(text)
        try:
            table = self._read_data_snapshot(fp, n)
        except Exception as e:
            self.log(f"Cannot skip {n} comment lines in '{os.path.basename(fp)}': {e}")
            self._show_comment_lines(fp)
            return
        # Store what was just read so the reload below uses it, not a second read
        self._store_table(fp, table, n)
        headers = table[0]
        info['comment_override'] = n
        self.log(f"'{os.path.basename(fp)}': first {n} lines treated as comments.")
        self._reload_with_headers(fp, headers)

    def _reload_with_headers(self, fp, headers):
        """Reloads after the column names changed, dropping stale X/Y picks."""
        for cb in (self.x_col_cb, self.y_col_cb):
            if cb.get() not in headers:
                cb.set('')
        self.load_file_data(fp)

    def _reset_comment_lines(self):
        """Goes back to the automatically detected comment-line count."""
        fp = self.active_filepath
        if not fp or fp not in self.file_data_cache:
            return
        if self.file_data_cache[fp].pop('comment_override', None) is not None:
            self.log(f"'{os.path.basename(fp)}': comment lines back to auto-detect.")
            try:
                table = self._read_data_snapshot(fp)
                self._store_table(fp, table, None)
                headers = table[0]
            except Exception:
                headers = []  # load_file_data reports the error
            self._reload_with_headers(fp, headers)

    def _load_file_data_into_cache(self, filepath):
        """Loads file data into the cache using the positional parser."""
        if not filepath or not os.path.exists(filepath):
            return False

        try:
            override = self.file_data_cache[filepath].get('comment_override')
            table = self._read_data_snapshot(filepath, override)
            self._store_table(filepath, table, override)

            self.log(f"Cached {table[2].shape[0]} data points from '{os.path.basename(filepath)}'.")
            return True
        except Exception as e:
            if filepath in self.file_data_cache:
                self.file_data_cache[filepath].update(
                    {"headers": [], "data": {}})
            self.log(f"Error caching file '{os.path.basename(filepath)}': {e}")
            return False

    def _store_table(self, filepath, table, override):
        """Puts a _read_data_snapshot result into the file's cache entry."""
        headers, delim, data_array, n_comments, snap = table
        file_info = self.file_data_cache.setdefault(filepath, {"path": filepath})
        file_info['comment_lines'] = n_comments
        file_info['headers'] = headers
        file_info['delimiter'] = delim
        file_info['data'] = {h: data_array[:, j] for j, h in enumerate(headers)}
        file_info['loaded_override'] = override
        file_info.update(snap)

    @staticmethod
    def _cache_is_fresh(filepath, file_info):
        """True when the cached table still matches the file on disk.

        Same mtime and size as at the last read, read with the comment-line
        setting now in force. Saves re-reading a file on every click.
        """
        if not file_info.get('headers') or 'file_size' not in file_info:
            return False
        if file_info.get('loaded_override') != file_info.get('comment_override'):
            return False
        try:
            return (os.path.getmtime(filepath) == file_info.get('mod_time')
                    and os.path.getsize(filepath) == file_info['file_size'])
        except OSError:
            return False

    def _find_header_row(self, filepath, comment_lines=None, encoding=None):
        """Returns (header_index, delimiter, headers, comment_lines). Raises on failure.

        comment_lines is the number of lines above the column-name row (or
        above the first data row in a headerless file). Passing it overrides
        the detection: line comment_lines is taken as the column names, or as
        the first data row when it is all numbers.

        Locates the first fully-numeric data row, then walks back to the
        nearest preceding line with a matching column count to use as the
        header. Metadata preamble lines (e.g. the title and 'Fixed value(s)'
        lines in Novocontrol WinDETA exports) are skipped this way, since the
        delimiter is taken from the data row rather than the first line.
        """
        def is_num(tok):
            if tok == "":
                return True  # empty cell = missing value (loads as NaN)
            try:
                float(tok)
                return True
            except ValueError:
                return False

        def tokenize(s, delim):
            # Delimited files keep empty cells so the column count still
            # matches the header when a row has missing values; whitespace
            # files cannot carry empties.
            if delim:
                return [t.strip() for t in s.split(delim)]
            return [t for t in s.split() if t]

        lines = []
        encoding = encoding or self._sniff_encoding(filepath)
        with open(filepath, 'r', encoding=encoding, errors='ignore') as f:
            for i, line in enumerate(f):
                lines.append(line.strip())
                if i >= 500:  # header must appear near the top
                    break

        if comment_lines is not None:
            if comment_lines >= len(lines) or not lines[comment_lines]:
                raise ValueError(f"line {comment_lines + 1} is empty or past the end of the file")
            header = lines[comment_lines]
            if header.lstrip('"').startswith('#'):
                header = header.lstrip('"').lstrip('#').rstrip('"').strip()
            delim = self._detect_delimiter(header)
            tokens = tokenize(header, delim)
            if len(tokens) >= 2 and all(is_num(t) for t in tokens):
                return comment_lines - 1, delim, [f"Col{k + 1}" for k in range(len(tokens))], comment_lines
            # Column names: the delimiter and column count come from the
            # first data row below, as in the detection.
            body = [s for s in lines[comment_lines + 1:] if s and not s.lstrip('"').startswith('#')]
            row = body[0] if body else header  # text-column files: no numeric row
            for s in body:
                s_tokens = tokenize(s, self._detect_delimiter(s))
                if len(s_tokens) >= 2 and all(is_num(t) for t in s_tokens):
                    row = s
                    break
            delim = self._detect_delimiter(row)
            tokens = tokenize(header, delim)
            row_tokens = tokenize(row, delim)
            n_row = len(row_tokens)
            while n_row > 0 and row_tokens[n_row - 1] == "":
                n_row -= 1
            if len(tokens) < 2 or (row is not header and len(tokens) not in (len(row_tokens), n_row)):
                raise ValueError(f"line {comment_lines + 1} has {len(tokens)} column name(s) "
                                 f"but the data has {n_row} columns")
            return comment_lines, delim, self._dedupe_headers(tokens), comment_lines

        for i, s in enumerate(lines):
            if not s or s.lstrip('"').startswith('#'):
                continue
            delim = self._detect_delimiter(s)
            tokens = tokenize(s, delim)
            if len(tokens) < 2 or not all(is_num(t) for t in tokens):
                continue
            # `s` is the first data row; the nearest preceding non-empty
            # line with the same column count is the header.
            for j in range(i - 1, -1, -1):
                prev = lines[j]
                if not prev or prev.lstrip('"').startswith('#'):
                    continue
                prev_tokens = tokenize(prev, delim)
                n_row = len(tokens)
                while n_row > 0 and tokens[n_row - 1] == "":
                    n_row -= 1  # trailing delimiter / missing last cells
                if len(prev_tokens) in (len(tokens), n_row) and not all(is_num(t) for t in prev_tokens):
                    return j, delim, self._dedupe_headers(prev_tokens), j
                break
            # No matching header line -> headerless file
            return i - 1, delim, [f"Col{k + 1}" for k in range(len(tokens))], i

        # No fully-numeric row found (e.g. a text column in every row):
        # fall back to treating the first multi-token non-numeric line as
        # the header, as before.
        for i, s in enumerate(lines):
            if not s or s.lstrip('"').startswith('#'):
                continue
            delim = self._detect_delimiter(s)
            tokens = tokenize(s, delim)
            if len(tokens) >= 2:
                return i, delim, self._dedupe_headers(tokens), i
        raise ValueError("No valid data header row found.")

    def _read_data_from_file(self, filepath):
        """Reads data by column index preserving special characters in headers."""
        headers, delim, data_array, _ = self._read_data_table(filepath)
        return headers, delim, data_array

    def _read_data_table(self, filepath, comment_lines=None):
        """Returns (headers, delimiter, data, comment_lines); see _find_header_row."""
        return self._read_data_snapshot(filepath, comment_lines)[:4]

    @staticmethod
    def _parse_table_lines(lines, delim, skip):
        """Parses the data block. np.loadtxt is the fast path (~3.5x quicker);
        any file it rejects (empty cells, ragged rows, text cells) goes through
        np.genfromtxt exactly as before, which turns those into NaN or drops
        the row."""
        try:
            return np.loadtxt(lines, delimiter=delim, comments='#',
                              skiprows=skip, dtype=float)
        except ValueError:
            return np.genfromtxt(lines, delimiter=delim, comments='#',
                                 autostrip=True, invalid_raise=False,
                                 skip_header=skip)

    def _read_data_snapshot(self, filepath, comment_lines=None):
        """_read_data_table plus a snapshot dict for the cache and live update.

        The file is read once as bytes, so the snapshot knows exactly what was
        parsed: 'size' is the byte offset after the last complete line (where
        live update resumes), 'file_size' the bytes read, 'encoding' the
        sniffed encoding, and 'tail_row' whether a last line without its
        newline (possibly still being written) became the final data row.
        """
        mod_time = os.path.getmtime(filepath)  # before reading: a later write changes it
        with open(filepath, 'rb') as f:
            raw = f.read()
        encoding = self._sniff_encoding_bytes(raw)
        header_idx, delim, headers, n_comments = self._find_header_row(
            filepath, comment_lines, encoding)
        # Universal newlines, as when numpy opened the file itself
        text = raw.decode(encoding, errors='ignore')
        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        data_array = self._parse_table_lines(lines, delim, header_idx + 1)

        data_array = np.atleast_2d(np.asarray(data_array, dtype=float))
        if data_array.size == 0:
            data_array = np.empty((0, len(headers)))
        # Tolerate trailing-delimiter ghost columns
        if data_array.shape[1] > len(headers):
            data_array = data_array[:, :len(headers)]
        elif data_array.shape[1] < len(headers):
            raise ValueError(f"Found {data_array.shape[1]} data columns but {len(headers)} headers.")

        # A line ends at \n or at a lone \r (CR-only writers); a CRLF split
        # across two reads only leaves a blank line, which is skipped
        cut = max(raw.rfind(b'\n'), raw.rfind(b'\r')) + 1
        if encoding == 'utf-16' or cut == 0:
            cut = len(raw)  # UTF-16 always reloads; no newline at all = nothing to resume
        tail_row = False
        if cut < len(raw) and data_array.shape[0]:
            # Was the unterminated last line parsed as the last row? Then live
            # update drops that row and re-reads the line once it is complete.
            tail = raw[cut:].decode(encoding, errors='ignore')
            rows = self._parse_appended_lines([tail], headers, delim)
            tail_row = bool(rows) and np.array_equal(
                np.asarray(rows[0]), data_array[-1], equal_nan=True)
        snap = {'encoding': encoding, 'mod_time': mod_time, 'size': cut,
                'file_size': len(raw), 'tail_row': tail_row}
        return headers, delim, data_array, n_comments, snap

    def _update_cache_and_ui(self, filepath, headers, n_rows):
        """Fills the column pickers from the (already cached) file data."""
        self.x_col_cb['values'] = headers
        self.y_col_cb['values'] = headers
        if len(headers) > 1:
            if not self.x_col_cb.get(): self.x_col_cb.set(headers[0])
            if not self.y_col_cb.get(): self.y_col_cb.set(headers[1])
        elif headers:
            if not self.x_col_cb.get(): self.x_col_cb.set(headers[0])
        self.log(f"Loaded {n_rows} data points from '{os.path.basename(filepath)}'.")

    def load_file_data(self, filepath):
        if not filepath:
            self.log("Cannot load data: No file selected.")
            return

        self.stop_file_watcher(log_message=False)

        try:
            file_info = self.file_data_cache.setdefault(filepath, {"path": filepath})
            if not self._cache_is_fresh(filepath, file_info):
                override = file_info.get('comment_override')
                self._store_table(filepath, self._read_data_snapshot(filepath, override), override)
            headers = file_info['headers']
            n_rows = len(file_info['data'][headers[0]]) if headers else 0
            self._update_cache_and_ui(filepath, headers, n_rows)
            if self.active_filepath == filepath:
                self._show_comment_lines(filepath)

        except Exception as e:
            self._handle_load_error(filepath, e)
        finally:
            if self.active_filepath == filepath:
                self.start_file_watcher(log_message=False)
            self.plot_data()

    def append_file_data(self):
        """Efficiently reads and appends only new data from the file."""
        if not self.active_filepath or not os.path.exists(
                self.active_filepath):
            return

        self.stop_file_watcher(log_message=False)
        file_info = self.file_data_cache.get(self.active_filepath)

        if not file_info or 'size' not in file_info:
            self.log(
                "Cannot append data: file information is incomplete. Performing full reload.")
            self.load_file_data(self.active_filepath)
            return
        encoding = file_info.get('encoding')
        if encoding is None:
            encoding = file_info['encoding'] = self._sniff_encoding(self.active_filepath)
        if encoding == 'utf-16':
            # Byte offsets can't be resumed mid-file in UTF-16; reload instead
            self.load_file_data(self.active_filepath)
            return

        try:
            mod_time = os.path.getmtime(self.active_filepath)  # before reading
            offset = file_info['size']
            new_lines, new_offset, end = self._read_appended_lines(
                self.active_filepath, offset, encoding)
            file_info['mod_time'] = mod_time
            # bytes seen, not the resume point: the watcher compares the
            # file length with this to tell growth from an overwrite
            file_info['file_size'] = end
            if new_offset == offset:
                return  # only part of a line so far; wait for its newline

            headers = file_info['headers']
            dropped = file_info.pop('tail_row', False)
            if dropped:
                # The unterminated last line parsed at load time is re-read
                # whole now, so its (possibly cut-short) row goes
                for header in headers:
                    file_info['data'][header] = file_info['data'][header][:-1]
            rows = self._parse_appended_lines(new_lines, headers, file_info.get('delimiter'))
            if rows:
                block = np.array(rows, dtype=float)
                for j, header in enumerate(headers):
                    file_info['data'][header] = np.concatenate(
                        (file_info['data'][header], block[:, j]))
            file_info['size'] = new_offset

            if rows or dropped:
                if rows:
                    self.log(f"Appended {len(rows)} new data points.")
                self._refresh_plot()

        except Exception:
            self.log(f"Error appending data: {traceback.format_exc()}")
            # Fallback to a full reload in case of parsing error
            self.load_file_data(self.active_filepath)
        finally:
            # Always restart the watcher after an append operation.
            self.start_file_watcher(log_message=False)

    @staticmethod
    def _read_appended_lines(filepath, offset, encoding):
        """Returns (lines, new_offset, end) for the complete lines after byte
        offset; end is the byte length seen, including any unfinished line.

        Reads in binary and stops at the last newline, so a line the writer
        is still in the middle of is neither dropped nor parsed cut short
        (1.2345 -> 1.23): it stays unread and new_offset points at its start.
        """
        with open(filepath, 'rb') as f:
            f.seek(offset)
            chunk = f.read()
        end = offset + len(chunk)
        cut = max(chunk.rfind(b'\n'), chunk.rfind(b'\r')) + 1
        if cut == 0:
            return [], offset, end
        return (chunk[:cut].decode(encoding, errors='ignore').splitlines(),
                offset + cut, end)

    @staticmethod
    def _parse_appended_lines(lines, headers, delim):
        """Parses appended text lines into rows of floats, as the full load does.

        Comments and blank lines are skipped, empty cells become NaN, a
        trailing delimiter is tolerated, and rows with the wrong number of
        columns are dropped.
        """
        rows = []
        n = len(headers)
        for line in lines:
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if delim:
                tokens = [t.strip() for t in line.split(delim)]
                while len(tokens) > n and tokens[-1] == "":
                    tokens.pop()  # trailing-delimiter ghost column
            else:
                tokens = line.split()
            if len(tokens) != n:
                continue
            row = []
            for tok in tokens:
                try:
                    row.append(float(tok))
                except ValueError:
                    row.append(np.nan)
            rows.append(row)
        return rows

    def _plot_signature(self):
        """Everything that shapes the plot apart from the data itself."""
        x_col, y_col, selected_filepaths = self._get_plot_parameters()
        return (x_col, y_col, tuple(selected_filepaths), bool(self.x_log_var.get()),
                bool(self.y_log_var.get()), len(self.file_ui_elements))

    def _refresh_plot(self):
        """Redraws the active file's line in place after a live-update append.

        Same files, columns and scales as the last full plot: only the line
        data changes, so the legend, labels and title are kept and the axes
        rescale as a full replot would. Anything else falls back to plot_data.
        """
        fp = self.active_filepath
        if not self._plot_lines or self._plot_signature() != self._plot_sig:
            self.plot_data()
            return
        x_col, y_col, selected_filepaths = self._get_plot_parameters()
        if fp not in selected_filepaths:
            return  # the active file is unticked: nothing on screen changed
        line = self._plot_lines.get(fp)
        data = self.file_data_cache.get(fp, {}).get('data', {})
        if line is None or x_col not in data or y_col not in data:
            self.plot_data()
            return
        raw_x, raw_y = data[x_col], data[y_col]
        finite_mask = np.isfinite(raw_x) & np.isfinite(raw_y)
        if not finite_mask.any():
            self.plot_data()
            return
        line.set_data(raw_x[finite_mask], raw_y[finite_mask])
        line.set_markevery(self._markevery(int(finite_mask.sum())))
        self.ax_main.relim()
        self.ax_main.set_autoscale_on(True)  # a full replot reset the view too
        self.ax_main.autoscale_view()
        self.canvas.draw_idle()

    @staticmethod
    def _markevery(n_points):
        """Marker spacing: every point up to 400, then about 200 markers per line."""
        return max(1, n_points // 200)

    def plot_data(self, event=None):
        x_col, y_col, selected_filepaths = self._get_plot_parameters()
        self._clear_and_setup_plot()

        if not selected_filepaths or not all([x_col, y_col]):
            self.ax_main.set_title("Click 'Add File(s)...' to begin")
            self.ax_main.set_xlabel("X-Axis")
            self.ax_main.set_ylabel("Y-Axis")
            self.canvas.draw_idle()
            return

        plotted_something = False
        try:
            for filepath in selected_filepaths:
                if self._plot_file_data(filepath, x_col, y_col):
                    plotted_something = True

            if plotted_something:
                self._finalize_plot(x_col, y_col, selected_filepaths)
                self._plot_sig = self._plot_signature()
                self.log(f"Plot updated for {len(selected_filepaths)} selected file(s).")
            else:
                self.log("No valid data to plot for the selected files and columns.")

        except Exception as e:
            self.log(f"Error plotting data: {traceback.format_exc()}")
            messagebox.showerror("Plotting Error",
                                 f"An error occurred while plotting.\n\n{e}")
        finally:
            self.canvas.draw_idle()

    def _get_plot_parameters(self):
        """Gets selected columns and filepaths for plotting."""
        x_col = self.x_col_cb.get()
        y_col = self.y_col_cb.get()
        selected_filepaths = [
            fp for fp, ui in self.file_ui_elements.items() if ui['var'].get()]
        return x_col, y_col, selected_filepaths

    def _clear_and_setup_plot(self):
        """Clears the plot and sets up the grid."""
        self._plot_lines = {}
        self._plot_sig = None
        self.ax_main.clear()
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

    @staticmethod
    def _shorten_label(filename, max_len=32):
        """Truncates long filenames with a middle ellipsis, keeping the start and extension."""
        if len(filename) <= max_len:
            return filename
        stem, ext = os.path.splitext(filename)
        keep = max(max_len - len(ext) - 1, 8)
        head = (keep * 2) // 3
        tail = keep - head
        return f"{stem[:head]}…{stem[-tail:]}{ext}"

    @staticmethod
    def _match_column(name, headers):
        """The column in `headers` holding the same quantity as `name`.

        An exact name always wins. Otherwise a unit suffix is ignored when
        only one side has it ("Zp' [Ohms]" = "Zp'") and "Freq. [Hz]" =
        "Frequency", so a PICA .dat overlays a WinDETA export of the same
        scan. Two different units never match, nor does an ambiguous name.
        """
        if name in headers:
            return name

        def split(col):
            col = col.strip()
            base, unit = col, None
            if col.endswith(']') and '[' in col:
                base, unit = col[:-1].rsplit('[', 1)
                base, unit = base.strip(), unit.strip()
            if base.lower() in ('freq', 'freq.', 'frequency') and unit in (None, 'Hz'):
                base, unit = 'Frequency', None
            return base, unit

        base, unit = split(name)
        hits = []
        for h in headers:
            h_base, h_unit = split(h)
            if h_base == base and (unit is None or h_unit is None or unit == h_unit):
                hits.append(h)
        return hits[0] if len(hits) == 1 else None

    def _plot_file_data(self, filepath, x_col, y_col):
        """Plots data for a single file."""
        file_info = self.file_data_cache.get(filepath)
        filename = os.path.basename(filepath)

        if not file_info or 'headers' not in file_info or not file_info['headers']:
            self.log(f"Skipping '{filename}': Data is missing or invalid.")
            return False

        headers = file_info.get('headers', [])
        file_x = self._match_column(x_col, headers)
        file_y = self._match_column(y_col, headers)
        if file_x is None or file_y is None:
            self.log(f"Skipping '{filename}': Does not contain '{x_col}' or '{y_col}'.")
            return False
        if (file_x, file_y) != (x_col, y_col):
            self.log(f"'{filename}': plotting '{file_y}' vs '{file_x}' "
                     f"(same quantity, named differently).")

        raw_x = file_info['data'][file_x]
        raw_y = file_info['data'][file_y]

        # Log-scale with non-positive data warnings
        if self.x_log_var.get() and np.any(raw_x <= 0):
            self.log(f"Warning: X log-scale active for '{os.path.basename(filepath)}', but non-positive data exists.")
        if self.y_log_var.get() and np.any(raw_y <= 0):
            self.log(f"Warning: Y log-scale active for '{os.path.basename(filepath)}', but non-positive data exists.")

        finite_mask = np.isfinite(raw_x) & np.isfinite(raw_y)
        plot_x = raw_x[finite_mask]
        plot_y = raw_y[finite_mask]

        if plot_x.size > 0:
            label_text = (self._shorten_label(filename)
                          if len(self.file_ui_elements) > 1
                          else f"{y_col} vs {x_col}")
            line, = self.ax_main.plot(
                plot_x,
                plot_y,
                marker='o',
                markersize=4,
                markevery=self._markevery(plot_x.size),
                linestyle='-',
                label=label_text)
            self._plot_lines[filepath] = line
            return True
        return False

    def _finalize_plot(self, x_col, y_col, selected_filepaths):
        """Sets labels, titles, and legend for the plot."""
        if len(selected_filepaths) == 1:
            legend_title = f"File: {self._shorten_label(os.path.basename(selected_filepaths[0]))}"
        else:
            legend_title = "Multiple Files"
        # Compact legend: small font, translucent frame, two columns when crowded, draggable
        handles = self.ax_main.get_legend_handles_labels()[0]
        leg = None
        if handles:
            leg = self.ax_main.legend(title=legend_title,
                                      labelcolor=self.CLR_FG,
                                      fontsize=8, title_fontsize=9,
                                      framealpha=0.7,
                                      ncol=2 if len(handles) > 8 else 1)
        if leg:
            leg.get_title().set_color(self.CLR_FG)
            leg.set_draggable(True)

        self.ax_main.set_xscale('log' if self.x_log_var.get() else 'linear')  # type: ignore
        self.ax_main.set_yscale('log' if self.y_log_var.get() else 'linear')
        self.ax_main.set_xlabel(x_col)
        self.ax_main.set_ylabel(y_col)

        if len(selected_filepaths) == 1:
            self.ax_main.set_title(
                os.path.basename(
                    selected_filepaths[0]),
                fontweight='bold')
        else:
            self.ax_main.set_title(
                f"{y_col} vs. {x_col}",
                fontweight='bold')

    def _handle_load_error(self, filepath, e):
        """Handles errors during file loading."""
        if filepath in self.file_data_cache:
            self.file_data_cache[filepath] = {
                "path": filepath, "headers": [], "data": {}}
        self.column_source_var.set("Columns from: (no file selected)")
        self.active_filepath = None
        self.x_col_cb.set('')
        self.y_col_cb.set('')
        self.x_col_cb['values'] = []
        self.y_col_cb['values'] = []
        self.live_update_var.set(False)
        self.toggle_live_update()
        self.log(f"Error loading file: {traceback.format_exc()}")
        messagebox.showerror(
            "File Load Error",
            f"Could not read the data file. It may be empty, malformed, or in use.\n\nDetails: {e}")

    def toggle_live_update(self):
        if self.live_update_var.get():
            self.start_file_watcher()
        else:
            self.stop_file_watcher()

    def start_file_watcher(self, log_message=True):
        self.stop_file_watcher(log_message=False)
        if self.live_update_var.get() and self.active_filepath:
            if log_message:
                self.log("Live update enabled. Watching for file changes...")
            self.file_watcher_job = self.root.after(
                1000, self.check_for_updates)

    def stop_file_watcher(self, log_message=True):
        if self.file_watcher_job:
            self.root.after_cancel(self.file_watcher_job)
            self.file_watcher_job = None
            if log_message:
                self.log("Live update disabled.")

    def check_for_updates(self):
        if not self.active_filepath or not self.live_update_var.get(
        ) or not os.path.exists(self.active_filepath):
            self.file_watcher_job = None  # Stop watching if file is gone or disabled
            return

        try:
            file_info = self.file_data_cache[self.active_filepath]
            mod_time = os.path.getmtime(self.active_filepath)
            current_size = os.path.getsize(self.active_filepath)

            if mod_time != file_info.get('mod_time'):
                # Compare with the bytes last seen, not the resume point: a
                # file overwritten by a shorter one can still be longer than
                # the end of its last complete line
                if current_size > file_info.get('file_size',
                                                file_info.get('size', 0)):
                    # File has grown, append new data
                    self.append_file_data()
                else:
                    # File was overwritten or shrunk, do a full reload
                    self.log(
                        "File has been overwritten. Performing full reload...")
                    self.load_file_data(self.active_filepath)
            else:
                # If no changes, schedule the next check
                self.file_watcher_job = self.root.after(
                    1000, self.check_for_updates)

        except OSError:
            # File might have been deleted or is temporarily inaccessible
            self.log(
                "File watcher stopped: file is inaccessible or has been deleted.")
            self.stop_file_watcher()
if __name__ == '__main__':
    # This is ESSENTIAL for multiprocessing to work in a bundled executable
    # and to prevent pickling errors with 'spawn' start method on Windows.
    multiprocessing.freeze_support()

    root = tk.Tk()
    app = PlotterAppGUI(root)

    # Data files given on the command line are loaded and plotted once the
    # window exists (the PICA launcher uses this for "Open Data as Graph").
    preload = [p for p in sys.argv[1:] if os.path.exists(p)]
    if preload:
        root.after(250, lambda: app.add_files(preload))

    root.mainloop()