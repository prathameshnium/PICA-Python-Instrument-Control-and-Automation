"""
Module: PEPlotterUtility_GUI.py
Purpose: GUI module for PE data plotter utility.
"""

# -------------------------------------------------------------------------------
# Name:         PE Plotter Utility
# Purpose:      A general-purpose CSV/DAT/TXT file plotter for PE data.
# Created:      06/07/2026
# Version:      1.0
# -------------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import csv
import traceback
from datetime import datetime
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)
import matplotlib as mpl
import numpy as np

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
    try:
        subprocess.run(script_path, check=True)
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(str(script_path))} ---")
        print(e)
        print("-------------------------")


class PlotterAppGUI:
    PROGRAM_VERSION = "1.0"

    CLR_BG = "#B8A392"
    CLR_HEADER = "#E5DCD3"
    CLR_FG = "#2C2825"
    CLR_FRAME_BG = "#E5DCD3"
    CLR_INPUT_BG = "#F4EFEA"
    CLR_ACCENT_GREEN = "#B68B6E"
    CLR_ACCENT_BLUE = "#BA6B5E"
    CLR_ACCENT_GOLD = "#BA6B5E"
    CLR_CONSOLE_BG = "#E5DCD3"

    FONT_BASE = ("Segoe UI", 11)
    FONT_TITLE = ("Segoe UI", 13, "bold")

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(
            SCRIPT_DIR,
            "..",
            "assets",
            "LOGO",
            "UGC_DAE_CSR_NBG.jpeg",
        )
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title(f"PE Plotter Utility v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.active_filepath = None
        self.file_data_cache = {}
        self.file_ui_elements = {}
        self.logo_image = None
        self.file_watcher_job = None

        self.setup_styles()
        self.create_widgets()
        self.log("Welcome to the PE Plotter Utility. Please select a file.")

    def setup_styles(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")

        self.style.configure(
            ".",
            background=self.CLR_BG,
            foreground=self.CLR_FG,
            font=self.FONT_BASE,
        )
        self.style.configure("TFrame", background=self.CLR_BG)
        self.style.configure("TPanedWindow", background=self.CLR_BG)
        self.style.configure(
            "TLabel",
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG,
        )
        self.style.configure("Header.TLabel", background=self.CLR_HEADER)
        self.style.configure(
            "TEntry",
            fieldbackground=self.CLR_INPUT_BG,
            foreground=self.CLR_FG,
            insertcolor=self.CLR_FG,
        )
        self.style.configure(
            "TButton",
            font=self.FONT_BASE,
            padding=(10, 9),
            foreground=self.CLR_ACCENT_GOLD,
            background=self.CLR_HEADER,
        )
        self.style.map(
            "TButton",
            background=[
                ("active", self.CLR_ACCENT_GOLD),
                ("hover", self.CLR_ACCENT_GOLD),
            ],
            foreground=[
                ("active", self.CLR_BG),
                ("hover", self.CLR_BG),
            ],
        )
        self.style.configure(
            "Plot.TButton",
            background=self.CLR_ACCENT_GREEN,
            foreground=self.CLR_BG,
        )
        self.style.map(
            "Plot.TButton",
            background=[("active", "#8AB845"), ("hover", "#8AB845")],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.CLR_INPUT_BG)],
            selectbackground=[("readonly", self.CLR_ACCENT_BLUE)],
            selectforeground=[("readonly", self.CLR_FG)],
            foreground=[("readonly", self.CLR_FG)],
        )
        self.style.configure("TCombobox", arrowcolor=self.CLR_FG)
        self.style.configure(
            "TLabelframe",
            background=self.CLR_FRAME_BG,
            bordercolor=self.CLR_ACCENT_BLUE,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG,
            font=self.FONT_TITLE,
        )
        self.style.configure(
            "TCheckbutton",
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG,
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", self.CLR_FRAME_BG)],
            indicatorcolor=[
                ("selected", self.CLR_ACCENT_GREEN),
                ("!selected", self.CLR_FG),
            ],
        )

        mpl.rcParams.update(
            {
                "font.family": "Segoe UI",
                "font.size": 11,
                "axes.titlesize": 15,
                "axes.labelsize": 13,
                "figure.facecolor": self.CLR_BG,
                "axes.facecolor": "#F4EFEA",
                "axes.edgecolor": self.CLR_FG,
                "axes.labelcolor": self.CLR_FG,
                "xtick.color": self.CLR_FG,
                "ytick.color": self.CLR_FG,
                "text.color": self.CLR_FG,
            }
        )

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side="top", fill="x", padx=1, pady=1)
        header.grid_columnconfigure(1, weight=1)

        left_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        left_header_frame.grid(row=0, column=0, sticky="w")
        ttk.Label(
            left_header_frame,
            text="PE Plotter Utility",
            style="Header.TLabel",
            font=("Segoe UI", 17, "bold"),
            foreground=self.CLR_ACCENT_GOLD,
        ).pack(side="left", padx=20, pady=10)

        center_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        center_header_frame.grid(row=0, column=1, sticky="ew")

        logo_canvas = Canvas(
            center_header_frame,
            width=60,
            height=60,
            bg=self.CLR_HEADER,
            highlightthickness=0,
        )
        logo_canvas.pack(side="left", pady=10)

        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize(
                    (60, 60), Image.Resampling.LANCZOS
                )
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(30, 30, image=self.logo_image)
            except Exception as e:
                self.log(f"Warning: Could not load logo. {e}")

        institute_frame = tk.Frame(center_header_frame, bg=self.CLR_HEADER)
        institute_frame.pack(side="left", padx=15)
        ttk.Label(
            institute_frame,
            text="PE Data Plotting Tool",
            style="Header.TLabel",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            institute_frame,
            text="Standalone Plotter",
            style="Header.TLabel",
            font=("Segoe UI", 14),
        ).pack(anchor="w")

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(fill="both", expand=True, padx=10, pady=10)

        left_panel = self._create_left_panel(main_pane)
        main_pane.add(left_panel, weight=1)

        right_panel = self._create_right_panel(main_pane)
        main_pane.add(right_panel, weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=400)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(4, weight=1)

        file_frame = ttk.LabelFrame(panel, text="Data Source")
        file_frame.grid(row=0, column=0, sticky="new", pady=5)
        file_frame.grid_columnconfigure(0, weight=1)

        file_buttons_frame = ttk.Frame(file_frame)
        file_buttons_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        file_buttons_frame.grid_columnconfigure((0, 1), weight=1)

        ttk.Button(
            file_buttons_frame,
            text="Add File(s)...",
            command=self.browse_files,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))

        ttk.Button(
            file_buttons_frame,
            text="Remove Selected",
            command=self.remove_selected_file,
        ).grid(row=0, column=1, sticky="ew", padx=(5, 0))

        ttk.Button(
            file_frame,
            text="Open New Plotter Window",
            command=self.launch_new_instance_handler,
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(10, 5))

        list_container = ttk.Frame(file_frame, style="TFrame")
        list_container.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=(0, 10),
        )
        list_container.rowconfigure(0, weight=1)
        list_container.columnconfigure(0, weight=1)

        file_canvas = tk.Canvas(
            list_container,
            bg=self.CLR_INPUT_BG,
            highlightthickness=0,
            height=100,
        )
        scrollbar = ttk.Scrollbar(
            list_container,
            orient="vertical",
            command=file_canvas.yview,
        )
        self.file_list_frame = ttk.Frame(file_canvas, style="TFrame")
        self.file_list_frame.configure(style="Input.TFrame")

        file_canvas.create_window((0, 0), window=self.file_list_frame, anchor="nw")
        file_canvas.configure(yscrollcommand=scrollbar.set)
        file_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_list_frame.bind(
            "<Configure>",
            lambda e: file_canvas.configure(scrollregion=file_canvas.bbox("all")),
        )

        self.column_source_var = tk.StringVar(
            value="Columns from: (no file selected)"
        )
        ttk.Label(
            file_frame,
            textvariable=self.column_source_var,
            style="TLabel",
            font=("Segoe UI", 9, "italic"),
            wraplength=350,
        ).grid(row=2, column=0, sticky="w", padx=10, pady=(0, 10))

        params_frame = ttk.LabelFrame(panel, text="Plot Parameters")
        params_frame.grid(row=1, column=0, sticky="new", pady=5)
        params_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(params_frame, text="X-Axis Column:").grid(
            row=0, column=0, sticky="w", padx=10, pady=5
        )
        self.x_col_cb = ttk.Combobox(
            params_frame, state="readonly", style="TCombobox"
        )
        self.x_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.x_col_cb.grid(row=0, column=1, sticky="ew", padx=10, pady=5)

        ttk.Label(params_frame, text="Y-Axis Column:").grid(
            row=1, column=0, sticky="w", padx=10, pady=5
        )
        self.y_col_cb = ttk.Combobox(
            params_frame, state="readonly", style="TCombobox"
        )
        self.y_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.y_col_cb.grid(row=1, column=1, sticky="ew", padx=10, pady=5)

        ttk.Label(params_frame, text="Comment rows:").grid(
            row=2, column=0, sticky="w", padx=10, pady=5
        )
        self.skip_rows_var = tk.StringVar(value="")
        skip_entry = ttk.Entry(
            params_frame, textvariable=self.skip_rows_var, style="TEntry"
        )
        skip_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=5)
        skip_entry.bind(
            "<Return>",
            lambda e: self.load_file_data(self.active_filepath),
        )

        ttk.Label(
            params_frame,
            text=(
                "Leave blank to auto-detect the header, or enter the number "
                "of leading comment rows to skip."
            ),
            style="TLabel",
            font=("Segoe UI", 8, "italic"),
            wraplength=350,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=10)

        options_frame = ttk.Frame(params_frame, style="TFrame")
        options_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=10)
        options_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.live_update_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="Live Update",
            variable=self.live_update_var,
            command=self.toggle_live_update,
        ).grid(row=0, column=2, sticky="e", padx=10)

        self.x_log_var = tk.BooleanVar()
        ttk.Checkbutton(
            options_frame,
            text="X Log Scale",
            variable=self.x_log_var,
            command=self.plot_data,
        ).grid(row=0, column=0, sticky="w", padx=10)

        self.y_log_var = tk.BooleanVar()
        ttk.Checkbutton(
            options_frame,
            text="Y Log Scale",
            variable=self.y_log_var,
            command=self.plot_data,
        ).grid(row=0, column=1, sticky="w", padx=10)

        self.style.configure("Input.TFrame", background=self.CLR_INPUT_BG)

        ttk.Button(
            params_frame,
            text="Reload & Plot",
            style="Plot.TButton",
            command=lambda: self.load_file_data(self.active_filepath),
        ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

        info_frame = ttk.LabelFrame(panel, text="Information")
        info_frame.grid(row=2, column=0, sticky="new", pady=5)
        info_frame.grid_columnconfigure(0, weight=1)
        ttk.Label(
            info_frame,
            text=(
                "This utility plots PE data from CSV, DAT, or TXT files. "
                "It supports live updates and multi-file plotting."
            ),
            justify="left",
            style="TLabel",
            wraplength=350,
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=5)

        console_frame = ttk.LabelFrame(panel, text="Console")
        console_frame.grid(row=3, column=0, sticky="nsew", pady=5)
        self.console = scrolledtext.ScrolledText(
            console_frame,
            state="disabled",
            bg=self.CLR_CONSOLE_BG,
            fg=self.CLR_FG,
            font=("Consolas", 9),
            wrap="word",
            borderwidth=0,
        )
        self.console.pack(fill="both", expand=True, padx=5, pady=5)

        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        container = ttk.LabelFrame(panel, text="Plot")
        container.pack(fill="both", expand=True)

        self.figure = Figure(dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.set_title("Select a file and columns to plot", fontweight="bold")
        self.ax_main.set_xlabel("X-Axis")
        self.ax_main.set_ylabel("Y-Axis")
        self.ax_main.grid(True, linestyle="--", alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

        toolbar_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        toolbar_frame.pack(fill="x", side="bottom", pady=(0, 5))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.configure(background=self.CLR_FRAME_BG)
        toolbar._message_label.config(
            background=self.CLR_FRAME_BG,
            foreground=self.CLR_FG,
        )
        for button in toolbar.winfo_children():
            button.config(background=self.CLR_FRAME_BG)
        toolbar.update()

        return panel

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] {message}\n"
        self.console.config(state="normal")
        self.console.insert("end", log_msg)
        self.console.see("end")
        self.console.config(state="disabled")

    def launch_new_instance_handler(self):
        try:
            if getattr(sys, "frozen", False):
                args = [sys.executable]
            else:
                args = [sys.executable, __file__]
            Process(target=run_script_process, args=(args,)).start()
        except Exception as e:
            messagebox.showerror(
                "Launch Error",
                f"Could not open a new plotter instance.\n\nError: {e}",
            )

    def browse_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Select a data file",
            filetypes=(
                ("Data Files", "*.csv *.dat *.txt"),
                ("All files", "*.*"),
            ),
        )
        if not filepaths:
            return

        new_files_added = False
        for fp in filepaths:
            if fp not in self.file_data_cache:
                new_files_added = True
                self.file_data_cache[fp] = {"path": fp}
                self._add_file_to_ui(fp)
                self.log(f"Added file: {os.path.basename(fp)}")
                self._load_file_data_into_cache(fp)

        if filepaths and self.active_filepath is None:
            self._set_active_file(filepaths[0])
        elif new_files_added:
            self.plot_data()

    def _add_file_to_ui(self, filepath):
        var = tk.BooleanVar(value=True)

        entry_frame = ttk.Frame(self.file_list_frame, style="Input.TFrame")
        entry_frame.pack(fill="x", expand=True)

        chk = ttk.Checkbutton(entry_frame, variable=var, command=self.plot_data)
        chk.pack(side="left", padx=(5, 0))

        filename = os.path.basename(filepath)
        lbl = ttk.Label(
            entry_frame,
            text=filename,
            style="TLabel",
            anchor="w",
            background=self.CLR_INPUT_BG,
        )
        lbl.pack(side="left", fill="x", expand=True, padx=5)
        lbl.bind("<Button-1>", lambda e, fp=filepath: self._set_active_file(fp))

        self.file_ui_elements[filepath] = {
            "var": var,
            "chk": chk,
            "lbl": lbl,
            "frame": entry_frame,
        }

    def remove_selected_file(self):
        paths_to_remove = [
            fp for fp, ui in self.file_ui_elements.items() if ui["var"].get()
        ]
        if not paths_to_remove:
            messagebox.showinfo(
                "Remove Files",
                "No files are selected (checked) to be removed.",
            )
            return

        for path in paths_to_remove:
            if path in self.file_ui_elements:
                self.file_ui_elements[path]["frame"].destroy()
                del self.file_ui_elements[path]
            if path in self.file_data_cache:
                del self.file_data_cache[path]
            self.log(f"Removed file: {os.path.basename(path)}")

            if self.active_filepath == path:
                self.active_filepath = None
                remaining_files = list(self.file_ui_elements.keys())
                if remaining_files:
                    self._set_active_file(remaining_files[0])
                else:
                    self._set_active_file(None)

        self.plot_data()

    def _set_active_file(self, filepath):
        for ui in self.file_ui_elements.values():
            ui["lbl"].configure(background=self.CLR_INPUT_BG)

        if filepath is None:
            self.active_filepath = None
            self.column_source_var.set("Columns from: (no file selected)")
            self.x_col_cb.set("")
            self.y_col_cb.set("")
            self.x_col_cb["values"] = []
            self.y_col_cb["values"] = []
            return

        if filepath in self.file_ui_elements:
            self.file_ui_elements[filepath]["lbl"].configure(
                background=self.CLR_ACCENT_BLUE
            )

        self.column_source_var.set(f"Columns from: {os.path.basename(filepath)}")
        if self.active_filepath != filepath:
            self.active_filepath = filepath
            self.load_file_data(filepath)

    def _get_manual_skip_rows(self):
        val = self.skip_rows_var.get().strip()
        if not val:
            return None
        try:
            return max(0, int(val))
        except ValueError:
            self.log(f"Invalid 'Comment rows' value '{val}'. Auto-detecting.")
            return None

    def _detect_delimiter(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if "," in s:
                        return ","
                    if "\t" in s:
                        return "\t"
                    return None
        except Exception:
            pass
        return ","

    def _find_header_row(self, filepath, delimiter):
        manual = self._get_manual_skip_rows()
        if manual is not None:
            self.log(f"Skipping {manual} comment row(s) as specified.")
            return manual

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                total_lines = sum(1 for _ in f)
        except Exception:
            total_lines = 100

        max_attempts = min(100, max(total_lines, 1))

        for i in range(max_attempts):
            try:
                test = np.genfromtxt(
                    filepath,
                    delimiter=delimiter,
                    names=True,
                    comments="#",
                    autostrip=True,
                    invalid_raise=False,
                    skip_header=i,
                    max_rows=10,
                )
                if (
                    isinstance(test, np.ndarray)
                    and test.dtype is not None
                    and test.dtype.names is not None
                    and len(test.dtype.names) > 0
                    and test.size > 0
                ):
                    row0 = test if test.ndim == 0 else test[0]
                    all_numeric = True
                    for name in test.dtype.names:
                        try:
                            if not np.issubdtype(test[name].dtype, np.number):
                                all_numeric = False
                                break
                        except Exception:
                            all_numeric = False
                            break
                    if all_numeric:
                        try:
                            finite_ok = all(np.isfinite(row0[name]) for name in test.dtype.names)
                            if finite_ok:
                                if i > 0:
                                    self.log(
                                        f"Auto-detected header after {i} comment row(s)."
                                    )
                                return i
                        except Exception:
                            continue
            except Exception:
                continue

        return -1

    def _read_data_from_file(self, filepath, header_line_index):
        delimiter = self._detect_delimiter(filepath)

        if header_line_index == -1:
            raise ValueError(
                "No valid data header row found. If the file has many comment "
                "rows, enter their count in the 'Comment rows' box."
            )

        data_array = np.genfromtxt(
            filepath,
            delimiter=delimiter,
            names=True,
            comments="#",
            autostrip=True,
            invalid_raise=False,
            skip_header=header_line_index,
        )

        if (
            not isinstance(data_array, np.ndarray)
            or data_array.dtype is None
            or data_array.dtype.names is None
        ):
            raise ValueError("Could not parse data from file.")

        return data_array

    def _update_cache_and_ui(self, filepath, data_array):
        if data_array.size == 0:
            self.log(
                f"Warning: File '{os.path.basename(filepath)}' contains no valid data rows."
            )
            if filepath in self.file_data_cache:
                file_info = self.file_data_cache[filepath]
                file_info["headers"] = (
                    [name.strip() for name in data_array.dtype.names]
                    if data_array.dtype.names
                    else []
                )
                file_info["data"] = {h: np.array([]) for h in file_info["headers"]}
            data_array = np.array([])

        headers = [name.strip() for name in data_array.dtype.names]
        file_info = self.file_data_cache[filepath]
        file_info["headers"] = headers
        file_info["data"] = {name: data_array[name] for name in headers}
        file_info["mod_time"] = os.path.getmtime(filepath)
        file_info["size"] = os.path.getsize(filepath)

        self.x_col_cb["values"] = headers
        self.y_col_cb["values"] = headers

        if len(headers) > 1:
            self.x_col_cb.set(headers[0])
            self.y_col_cb.set(headers[1])
        elif headers:
            self.x_col_cb.set(headers[0])

        self.log(
            f"Loaded {len(data_array)} data points from '{os.path.basename(filepath)}'."
        )

    def _load_file_data_into_cache(self, filepath):
        if not filepath or not os.path.exists(filepath):
            return False

        try:
            delimiter = self._detect_delimiter(filepath)
            header_line_index = self._find_header_row(filepath, delimiter)
            data_array = self._read_data_from_file(filepath, header_line_index)

            file_info = self.file_data_cache[filepath]
            headers = [name.strip() for name in data_array.dtype.names]
            file_info["headers"] = headers
            file_info["data"] = {name: data_array[name] for name in headers}
            file_info["mod_time"] = os.path.getmtime(filepath)
            file_info["size"] = os.path.getsize(filepath)

            if data_array.size == 0:
                self.log(
                    f"Warning: File '{os.path.basename(filepath)}' contains no valid data rows."
                )

            self.log(
                f"Cached {len(data_array)} data points from '{os.path.basename(filepath)}'."
            )
            return True

        except Exception as e:
            if filepath in self.file_data_cache:
                self.file_data_cache[filepath].update({"headers": [], "data": {}})
            self.log(f"Error caching file '{os.path.basename(filepath)}': {e}")
            return False

    def load_file_data(self, filepath):
        if not filepath:
            self.log("Cannot load data: No file selected.")
            return

        self.stop_file_watcher()

        try:
            delimiter = self._detect_delimiter(filepath)
            header_line_index = self._find_header_row(filepath, delimiter)
            data_array = self._read_data_from_file(filepath, header_line_index)
            self._update_cache_and_ui(filepath, data_array)

        except Exception as e:
            self._handle_load_error(filepath, e)
        finally:
            if self.active_filepath == filepath:
                self.start_file_watcher()
            self.plot_data()

    def append_file_data(self):
        if not self.active_filepath or not os.path.exists(self.active_filepath):
            return

        self.stop_file_watcher()
        file_info = self.file_data_cache.get(self.active_filepath)

        if not file_info or "size" not in file_info:
            self.log(
                "Cannot append data: file information is incomplete. Performing full reload."
            )
            self.load_file_data(self.active_filepath)
            return

        try:
            new_lines = self._read_new_lines(self.active_filepath, file_info)
            appended_count = self._parse_and_append_new_data(new_lines, file_info)

            if appended_count > 0:
                file_info["mod_time"] = os.path.getmtime(self.active_filepath)
                file_info["size"] = os.path.getsize(self.active_filepath)
                self.log(f"Appended {appended_count} new data points.")
                self.plot_data()

        except Exception:
            self.log(f"Error appending data: {traceback.format_exc()}")
            self.load_file_data(self.active_filepath)
        finally:
            self.start_file_watcher()

    def _read_new_lines(self, filepath, file_info):
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(file_info.get("size", 0))
                new_text = f.read()

            if not new_text:
                return []

            return [
                line
                for line in new_text.strip().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except Exception as e:
            self.log(f"Error reading new lines from file: {e}")
            return []

    def _parse_and_append_new_data(self, new_lines, file_info):
        if not new_lines:
            return 0

        reader = csv.reader(new_lines)
        new_data = {h: [] for h in file_info["headers"]}
        appended_count = 0

        for row in reader:
            if len(row) != len(file_info["headers"]):
                continue
            for i, header in enumerate(file_info["headers"]):
                try:
                    new_data[header].append(float(row[i]))
                except (ValueError, TypeError):
                    new_data[header].append(np.nan)
            appended_count += 1

        for header in file_info["headers"]:
            file_info["data"][header] = np.concatenate(
                (file_info["data"][header], np.array(new_data[header], dtype=float))
            )

        return appended_count

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
                self.log(f"Plot updated for {len(selected_filepaths)} selected file(s).")
            else:
                self.log("No valid data to plot for the selected files and columns.")

        except Exception as e:
            self.log(f"Error plotting data: {traceback.format_exc()}")
            messagebox.showerror(
                "Plotting Error",
                f"An error occurred while plotting.\n\n{e}",
            )
        finally:
            self.canvas.draw_idle()

    def _get_plot_parameters(self):
        x_col = self.x_col_cb.get()
        y_col = self.y_col_cb.get()
        selected_filepaths = [
            fp for fp, ui in self.file_ui_elements.items() if ui["var"].get()
        ]
        return x_col, y_col, selected_filepaths

    def _clear_and_setup_plot(self):
        self.ax_main.clear()
        self.ax_main.grid(True, linestyle="--", alpha=0.6)

    def _plot_file_data(self, filepath, x_col, y_col):
        file_info = self.file_data_cache.get(filepath)
        filename = os.path.basename(filepath)

        if not file_info or "headers" not in file_info or not file_info["headers"]:
            self.log(f"Skipping '{filename}': Data is missing or invalid.")
            return False

        if (
            x_col not in file_info.get("headers", [])
            or y_col not in file_info.get("headers", [])
        ):
            self.log(
                f"Skipping '{filename}': Does not contain '{x_col}' or '{y_col}'."
            )
            return False

        raw_x = file_info["data"][x_col]
        raw_y = file_info["data"][y_col]

        finite_mask = np.isfinite(raw_x) & np.isfinite(raw_y)
        plot_x = raw_x[finite_mask]
        plot_y = raw_y[finite_mask]

        if plot_x.size > 0:
            label_text = (
                f"{y_col} vs {x_col} ({filename})"
                if len(self.file_ui_elements) > 1
                else f"{y_col} vs {x_col}"
            )
            self.ax_main.plot(
                plot_x,
                plot_y,
                marker="o",
                markersize=4,
                linestyle="-",
                label=label_text,
            )
            return True
        return False

    def _finalize_plot(self, x_col, y_col, selected_filepaths):
        if len(selected_filepaths) == 1:
            legend_title = f"File: {os.path.basename(selected_filepaths[0])}"
        else:
            legend_title = "Multiple Files"

        leg = self.ax_main.legend(title=legend_title, labelcolor=self.CLR_FG)
        if leg:
            leg.get_title().set_color(self.CLR_FG)

        self.ax_main.set_xscale("log" if self.x_log_var.get() else "linear")
        self.ax_main.set_yscale("log" if self.y_log_var.get() else "linear")
        self.ax_main.set_xlabel(x_col)
        self.ax_main.set_ylabel(y_col)

        if len(selected_filepaths) == 1:
            self.ax_main.set_title(
                os.path.basename(selected_filepaths[0]),
                fontweight="bold",
            )
        else:
            self.ax_main.set_title(f"{y_col} vs. {x_col}", fontweight="bold")

        self.figure.tight_layout()

    def _handle_load_error(self, filepath, e):
        if filepath in self.file_data_cache:
            self.file_data_cache[filepath] = {
                "path": filepath,
                "headers": [],
                "data": {},
            }
        self.column_source_var.set("Columns from: (no file selected)")
        self.active_filepath = None
        self.plot_data()
        self.x_col_cb.set("")
        self.y_col_cb.set("")
        self.x_col_cb["values"] = []
        self.y_col_cb["values"] = []
        self.live_update_var.set(False)
        self.toggle_live_update()
        self.log(f"Error loading file: {traceback.format_exc()}")
        messagebox.showerror(
            "File Load Error",
            f"Could not read the data file.\n\nDetails: {e}",
        )

    def toggle_live_update(self):
        if self.live_update_var.get():
            self.start_file_watcher()
        else:
            self.stop_file_watcher()

    def start_file_watcher(self):
        self.stop_file_watcher()
        if self.live_update_var.get() and self.active_filepath:
            self.log("Live update enabled. Watching for file changes...")
            self.file_watcher_job = self.root.after(1000, self.check_for_updates)

    def stop_file_watcher(self):
        if self.file_watcher_job:
            self.root.after_cancel(self.file_watcher_job)
            self.file_watcher_job = None
            self.log("Live update disabled.")

    def check_for_updates(self):
        if (
            not self.active_filepath
            or not self.live_update_var.get()
            or not os.path.exists(self.active_filepath)
        ):
            self.file_watcher_job = None
            return

        try:
            file_info = self.file_data_cache[self.active_filepath]
            mod_time = os.path.getmtime(self.active_filepath)
            current_size = os.path.getsize(self.active_filepath)

            if mod_time != file_info.get("mod_time"):
                if current_size > file_info.get("size", 0):
                    self.append_file_data()
                else:
                    self.log("File has been overwritten. Performing full reload...")
                    self.load_file_data(self.active_filepath)
            else:
                self.file_watcher_job = self.root.after(1000, self.check_for_updates)

        except OSError:
            self.log(
                "File watcher stopped: file is inaccessible or has been deleted."
            )
            self.stop_file_watcher()


if __name__ == "__main__":
    multiprocessing.freeze_support()

    root = tk.Tk()
    app = PlotterAppGUI(root)
    root.mainloop()