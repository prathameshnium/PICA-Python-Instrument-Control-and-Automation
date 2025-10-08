# -------------------------------------------------------------------------------
# Name:         PICA Plotter Utility
# Purpose:      A general-purpose CSV/DAT file plotter for the PICA suite.
# Author:       Prathamesh K Deshmukh
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

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

def _dummy_process_target():
    """A picklable top-level function to satisfy multiprocessing on Windows."""
    pass

def launch_new_instance():
    """
    Launches a new instance of the plotter application in a separate process.
    This is necessary for creating independent windows.
    """
    try:
        # We execute the script file itself in a new process
        proc = Process(target=run_script_process, args=(__file__,))
        proc.start()
    except Exception as e:
        messagebox.showerror("Launch Error", f"Could not open a new plotter instance.\n\nError: {e}")

def run_script_process(script_path):
    """Wrapper function to execute a script in its own directory."""
    try:
        # This is a generic helper that can be used to run any script.
        # For our purpose, it runs a copy of this same plotter script.
        os.chdir(os.path.dirname(script_path))
        # Using exec avoids issues with runpy in frozen applications
        with open(script_path, 'r') as f:
            code = compile(f.read(), script_path, 'exec')
            exec(code, {'__name__': '__main__'})
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")

class PlotterApp:
    PROGRAM_VERSION = "2.1"
    CLR_BG = '#2B3D4F'
    CLR_HEADER = '#3A506B'
    CLR_FG = '#EDF2F4'
    CLR_FRAME_BG = '#3A506B'
    CLR_INPUT_BG = '#4C566A'
    CLR_ACCENT_GREEN = '#A7C957'
    CLR_ACCENT_RED = '#E74C3C'
    CLR_ACCENT_BLUE = '#8D99AE'
    CLR_ACCENT_GOLD = '#FFC107'
    CLR_CONSOLE_BG = '#1E2B38'
    FONT_BASE = ('Segoe UI', 11)
    FONT_TITLE = ('Segoe UI', 13, 'bold')
    FONT_TITLE_ITALIC = ('Segoe UI', 13, 'bold italic')
    
    try:
        # Robust path finding for assets
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "_assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../_assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Plotter Utility v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.active_filepath = None
        # New data structure to hold data for multiple files
        # Format: { "filepath": {"headers": [...], "data": {...}, "mod_time": ..., "size": ...} }
        self.file_data_cache = {}
        self.logo_image = None
        self.file_watcher_job = None

        self.setup_styles()
        self.create_widgets()
        self.log("Welcome to the PICA Plotter Utility. Please select a file to begin.")

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG, foreground=self.CLR_FG, font=self.FONT_BASE)
        style.configure('TFrame', background=self.CLR_BG)
        style.configure('TPanedWindow', background=self.CLR_BG)
        style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        style.configure('Header.TLabel', background=self.CLR_HEADER)
        style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_FG, insertcolor=self.CLR_FG)
        style.configure('TButton', font=self.FONT_BASE, padding=(10, 9), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], foreground=[('active', self.CLR_BG), ('hover', self.CLR_BG)])
        style.configure('Plot.TButton', background=self.CLR_ACCENT_GREEN, foreground=self.CLR_BG)
        style.map('Plot.TButton', background=[('active', '#8AB845'), ('hover', '#8AB845')])

        # Combobox styling
        style.map('TCombobox',
                  fieldbackground=[('readonly', self.CLR_INPUT_BG)],
                  selectbackground=[('readonly', self.CLR_ACCENT_BLUE)],
                  selectforeground=[('readonly', self.CLR_FG)],
                  foreground=[('readonly', self.CLR_FG)])
        style.configure('TCombobox', arrowcolor=self.CLR_FG)

        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG, font=self.FONT_TITLE)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        style.map('TCheckbutton',
                  background=[('active', self.CLR_FRAME_BG)],
                  indicatorcolor=[('selected', self.CLR_ACCENT_GREEN), ('!selected', self.CLR_FG)])
        mpl.rcParams.update({
            'font.family': 'Segoe UI', 'font.size': 11,
            'axes.titlesize': 15, 'axes.labelsize': 13,
            'figure.facecolor': self.CLR_BG, 'axes.facecolor': '#FFFFFF',
            'axes.edgecolor': self.CLR_FG, 'axes.labelcolor': self.CLR_FG,
            'xtick.color': self.CLR_FG, 'ytick.color': self.CLR_FG,
            'text.color': self.CLR_FG,
        })

    def create_widgets(self):
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)

        # --- New Instance Button ---
        new_instance_button = ttk.Button(header, text="+", command=launch_new_instance, width=3)
        new_instance_button.pack(side='right', padx=(0, 10), pady=10)

        # --- Header with Logo and Institute Name ---
        logo_canvas = Canvas(header, width=60, height=60, bg=self.CLR_HEADER, highlightthickness=0)
        logo_canvas.pack(side='left', padx=(20, 15), pady=10)
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((60, 60), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(30, 30, image=self.logo_image)
            except Exception as e:
                self.log(f"Warning: Could not load logo. {e}")

        institute_frame = tk.Frame(header, bg=self.CLR_HEADER); institute_frame.pack(side='left')
        ttk.Label(institute_frame, text="UGC-DAE Consortium for Scientific Research", style='Header.TLabel', font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(institute_frame, text="Mumbai Centre", style='Header.TLabel', font=('Segoe UI', 12)).pack(anchor='w')

        right_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        right_header_frame.pack(side='right', padx=20, pady=10)
        font_title_main = ('Segoe UI', self.FONT_BASE[1] + 4, 'bold')
        ttk.Label(right_header_frame, text=f"PICA General Purpose Plotter", style='Header.TLabel', font=font_title_main, foreground=self.CLR_ACCENT_GOLD).pack()

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
        file_buttons_frame.grid_columnconfigure((0,1), weight=1)
        ttk.Button(file_buttons_frame, text="Add File(s)...", command=self.browse_files).grid(row=0, column=0, sticky='ew', padx=(0,5))
        ttk.Button(file_buttons_frame, text="Remove Selected", command=self.remove_selected_file).grid(row=0, column=1, sticky='ew', padx=(5,0))

        self.file_listbox = tk.Listbox(file_frame, bg=self.CLR_INPUT_BG, fg=self.CLR_FG, selectbackground=self.CLR_ACCENT_BLUE, height=5, borderwidth=0, highlightthickness=0)
        self.file_listbox.grid(row=1, column=0, sticky='ew', padx=10, pady=(0,10))
        self.file_listbox.bind('<<ListboxSelect>>', self.on_file_select)


        # --- Plotting Parameters ---
        params_frame = ttk.LabelFrame(panel, text="Plot Parameters")
        params_frame.grid(row=1, column=0, sticky='new', pady=5)
        params_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(params_frame, text="X-Axis Column:").grid(row=0, column=0, sticky='w', padx=10, pady=5)
        self.x_col_cb = ttk.Combobox(params_frame, state='readonly', style='TCombobox')
        self.x_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.x_col_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Y-Axis Column:").grid(row=1, column=0, sticky='w', padx=10, pady=5)
        self.y_col_cb = ttk.Combobox(params_frame, state='readonly', style='TCombobox')
        self.y_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.y_col_cb.grid(row=1, column=1, sticky='ew', padx=10, pady=5)

        # --- Plotting Options ---
        options_frame = ttk.Frame(params_frame, style='TFrame') # Explicitly use TFrame style
        options_frame.grid(row=2, column=0, columnspan=2, sticky='ew', pady=10)
        options_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.live_update_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Live Update", variable=self.live_update_var, command=self.toggle_live_update).grid(row=0, column=2, sticky='e', padx=10)
        
        self.x_log_var = tk.BooleanVar()
        ttk.Checkbutton(options_frame, text="X Log Scale", variable=self.x_log_var, command=self.plot_data).grid(row=0, column=0, sticky='w', padx=10)

        self.y_log_var = tk.BooleanVar()
        ttk.Checkbutton(options_frame, text="Y Log Scale", variable=self.y_log_var, command=self.plot_data).grid(row=0, column=1, sticky='w', padx=10)

        # Set the background of the options frame to match its parent
        options_frame.configure(style='TFrame')

        ttk.Button(params_frame, text="Reload & Plot", style="Plot.TButton", command=lambda: self.load_file_data(self.active_filepath)).grid(row=3, column=0, columnspan=2, sticky='ew', padx=10, pady=10)

        # --- Information Box ---
        info_frame = ttk.LabelFrame(panel, text="Information")
        info_frame.grid(row=2, column=0, sticky='new', pady=5)
        info_frame.grid_columnconfigure(0, weight=1)
        info_text = "This utility plots data from CSV or DAT files. It supports live updates for monitoring ongoing experiments."
        ttk.Label(info_frame, text=info_text, justify='left', style='TLabel', wraplength=350).grid(row=0, column=0, sticky='ew', padx=10, pady=5)


        # --- Console ---
        console_frame = ttk.LabelFrame(panel, text="Console")
        console_frame.grid(row=3, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(console_frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG, font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        container = ttk.LabelFrame(panel, text='Plot')
        container.pack(fill='both', expand=True)

        self.figure = Figure(dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.line_main, = self.ax_main.plot([], [], color=self.CLR_ACCENT_RED, marker='o', markersize=4, linestyle='-')
        self.ax_main.set_title("Select a file and columns to plot", fontweight='bold')
        self.ax_main.set_xlabel("X-Axis")
        self.ax_main.set_ylabel("Y-Axis")
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        # Add the Matplotlib navigation toolbar
        toolbar_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        toolbar_frame.pack(fill='x', side='bottom', pady=(0,5))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.configure(background=self.CLR_FRAME_BG)
        toolbar._message_label.config(background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
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

    def browse_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Select a data file",
            filetypes=(("Data Files", "*.csv *.dat"), ("All files", "*.*"))
        )
        if not filepaths:
            return

        for fp in filepaths:
            if fp not in self.file_data_cache:
                self.file_listbox.insert('end', os.path.basename(fp))
                self.file_data_cache[fp] = {"path": fp} # Add placeholder
                self.log(f"Added file: {os.path.basename(fp)}")

        # If this is the first file, select and load it
        if self.file_listbox.size() > 0 and self.active_filepath is None:
            self.file_listbox.selection_set(0)
            self.on_file_select()

    def remove_selected_file(self):
        selected_indices = self.file_listbox.curselection()
        if not selected_indices:
            return

        selected_index = selected_indices[0]
        filename_to_remove = self.file_listbox.get(selected_index)
        
        # Find the full path from the cache
        path_to_remove = None
        for path, data in self.file_data_cache.items():
            if os.path.basename(path) == filename_to_remove:
                path_to_remove = path
                break

        if path_to_remove:
            del self.file_data_cache[path_to_remove]
            self.file_listbox.delete(selected_index)
            self.log(f"Removed file: {filename_to_remove}")

            # If the removed file was the active one, clear the plot
            if self.active_filepath == path_to_remove:
                self.active_filepath = None
                self.x_col_cb.set('')
                self.y_col_cb.set('')
                self.x_col_cb['values'] = []
                self.y_col_cb['values'] = []
                self.plot_data()

    def on_file_select(self, event=None):
        selected_indices = self.file_listbox.curselection()
        if not selected_indices:
            return

        filename = self.file_listbox.get(selected_indices[0])
        
        # Find the full path from the cache
        path_to_load = None
        for path, data in self.file_data_cache.items():
            if os.path.basename(path) == filename:
                path_to_load = path
                break
        
        if path_to_load:
            self.active_filepath = path_to_load
            self.load_file_data(path_to_load)

    def load_file_data(self, filepath):
        if not filepath:
            self.log("Cannot load data: No file selected.")
            return

        self.stop_file_watcher()

        try:
            # --- Two-pass loading for robustness ---
            # 1. Find the header row and the line number where data starts.
            header_line_index = -1
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f):
                    # A valid header in PICA files is the first line that is NOT a comment
                    # and contains multiple columns (separated by comma or tab).
                    line = line.strip()
                    if line and not line.startswith('#') and (',' in line or '\t' in line):
                        header_line_index = i
                        break
            
            if header_line_index == -1:
                raise ValueError("No valid data header row found. Ensure the file has a non-commented header with comma or tab-separated columns.")

            # 2. Use genfromtxt, telling it exactly where to start reading data.
            #    Explicitly set delimiter to comma for PICA files.
            data_array = np.genfromtxt(filepath, delimiter=',', names=True, comments='#', autostrip=True,
                                       invalid_raise=False, skip_header=header_line_index)
            
            # Check for catastrophic failure where genfromtxt returns a scalar or has no shape.
            if not hasattr(data_array, 'dtype') or data_array.dtype.names is None:
                raise ValueError("Could not automatically determine headers. Ensure the file has a header row not starting with '#'.")

            # Check if any data was actually loaded
            if data_array.size == 0:
                self.log(f"Warning: File '{os.path.basename(filepath)}' was loaded, but contains no valid data rows.")
                # Set up empty structure to prevent future errors
                file_info = self.file_data_cache[filepath]
                file_info['headers'] = [name.strip() for name in data_array.dtype.names] if data_array.dtype.names else []
                file_info['data'] = {h: np.array([]) for h in file_info['headers']}
                # Fall through to the rest of the logic, which will handle the empty state

            # Sanitize headers to remove extra spaces or quotes that genfromtxt might add
            headers = [name.strip() for name in data_array.dtype.names]
            
            # Store data in our cache
            file_info = self.file_data_cache[filepath]
            file_info['headers'] = headers
            file_info['data'] = {name: data_array[name] for name in headers}
            file_info['mod_time'] = os.path.getmtime(filepath)
            file_info['size'] = os.path.getsize(filepath)

            self.x_col_cb['values'] = headers
            self.y_col_cb['values'] = headers
            
            # Set default columns if they exist
            if len(headers) > 1:
                self.x_col_cb.set(headers[0])
                self.y_col_cb.set(headers[1])
            elif headers:
                self.x_col_cb.set(headers[0])

            num_points = len(data_array)
            self.log(f"Loaded {num_points} data points from '{os.path.basename(filepath)}'.")

        except Exception as e:
            # If loading fails, reset everything for this file to prevent chained errors.
            if filepath in self.file_data_cache:
                # Create an empty but valid cache entry.
                self.file_data_cache[filepath] = {"path": filepath, "headers": [], "data": {}}
            
            # Clear UI elements
            self.x_col_cb.set('')
            self.y_col_cb.set('')
            self.x_col_cb['values'] = []
            self.y_col_cb['values'] = []

            self.log(f"Error loading file: {traceback.format_exc()}")
            # Show a single, clear error message.
            messagebox.showerror("File Load Error", f"Could not read the data file. It may be empty, malformed, or in use.\n\nDetails: {e}")
            self.plot_data() # Call plot_data to clear the plot area
        finally:
            # Only start the watcher if the file is still the active one (load might have failed)
            if self.active_filepath == filepath:
                self.start_file_watcher()
            # Trigger a plot update with the newly loaded data
            self.plot_data()

    def append_file_data(self):
        """Efficiently reads and appends only new data from the file."""
        if not self.active_filepath or not os.path.exists(self.active_filepath):
            return

        self.stop_file_watcher()
        file_info = self.file_data_cache.get(self.active_filepath)

        if not file_info or 'size' not in file_info:
            self.log("Cannot append data: file information is incomplete. Performing full reload.")
            self.load_file_data(self.active_filepath)
            return

        try:
            with open(self.active_filepath, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(file_info['size'])
                new_lines = f.readlines()

                if not new_lines:
                    return # No new data to append

                # Read new lines into temporary lists
                reader = csv.reader(new_lines)
                new_data = {h: [] for h in file_info['headers']}
                appended_count = 0
                for row in reader:
                    if len(row) != len(file_info['headers']): continue
                    for i, header in enumerate(file_info['headers']):
                        try:
                            new_data[header].append(float(row[i]))
                        except (ValueError, TypeError):
                            new_data[header].append(np.nan)
                    appended_count += 1
                
                # Append new numpy arrays to existing data
                for header in file_info['headers']:
                    file_info['data'][header] = np.concatenate((file_info['data'][header], np.array(new_data[header], dtype=float)))
                
            file_info['mod_time'] = os.path.getmtime(self.active_filepath)
            file_info['size'] = os.path.getsize(self.active_filepath)
            self.log(f"Appended {appended_count} new data points.")
            self.plot_data()
        except Exception as e:
            self.log(f"Error appending data: {traceback.format_exc()}")
            # Fallback to a full reload in case of parsing error
            self.load_file_data(self.active_filepath)
        finally:
            # Always restart the watcher after an append operation
            self.start_file_watcher()

    def plot_data(self, event=None):
        x_col = self.x_col_cb.get()
        y_col = self.y_col_cb.get()

        if not self.active_filepath or not all([x_col, y_col]):
            self.ax_main.clear()
            self.ax_main.grid(True, linestyle='--', alpha=0.6)
            self.ax_main.set_title("Select a file and columns to plot")
            self.line_main.set_data([], [])
            self.ax_main.set_title("Select a file and columns to plot")
            self.ax_main.set_xlabel("X-Axis")
            self.ax_main.set_ylabel("Y-Axis")
            self.canvas.draw()
            return

        try:
            file_info = self.file_data_cache.get(self.active_filepath)
            if not file_info or 'headers' not in file_info or not file_info['headers']:
                self.log("Cannot plot: File data is missing or invalid.")
                return # Abort plotting if data structure is incomplete

            if x_col not in file_info['headers'] or y_col not in file_info['headers']:
                return # Columns not valid for this file

            raw_x = file_info['data'][x_col]
            raw_y = file_info['data'][y_col]
            
            # Create a mask for finite values to avoid plotting errors
            finite_mask = np.isfinite(raw_x) & np.isfinite(raw_y)
            
            # Apply the mask to get clean data for plotting
            plot_x = raw_x[finite_mask]
            plot_y = raw_y[finite_mask]

            self.line_main.set_data(plot_x, plot_y)
            # Update plot scales
            self.ax_main.set_xscale('log' if self.x_log_var.get() else 'linear')
            self.ax_main.set_yscale('log' if self.y_log_var.get() else 'linear')

            # Update labels and title
            self.ax_main.set_xlabel(x_col)
            self.ax_main.set_ylabel(y_col)
            self.ax_main.set_title(f"{y_col} vs. {x_col}")

            self.ax_main.relim()
            self.ax_main.autoscale_view()
            self.figure.tight_layout()
            self.canvas.draw()
            self.log(f"Plot updated: '{y_col}' vs. '{x_col}'")

        except Exception as e:
            self.log(f"Error plotting data: {traceback.format_exc()}")
            messagebox.showerror("Plotting Error", f"An error occurred while plotting.\n\n{e}")

    def toggle_live_update(self):
        if self.live_update_var.get():
            self.start_file_watcher()
        else:
            self.stop_file_watcher()

    def start_file_watcher(self):
        self.stop_file_watcher() # Ensure no multiple watchers are running
        if self.live_update_var.get() and self.active_filepath:
            self.log("Live update enabled. Watching for file changes...")
            self.file_watcher_job = self.root.after(1000, self.check_for_updates)

    def stop_file_watcher(self):
        if self.file_watcher_job:
            self.root.after_cancel(self.file_watcher_job)
            self.file_watcher_job = None
            self.log("Live update disabled.")

    def check_for_updates(self):
        if not self.active_filepath or not self.live_update_var.get() or not os.path.exists(self.active_filepath):
            self.file_watcher_job = None # Stop watching if file is gone or disabled
            return

        try:
            file_info = self.file_data_cache[self.active_filepath]
            mod_time = os.path.getmtime(self.active_filepath)
            current_size = os.path.getsize(self.active_filepath)

            if mod_time != file_info.get('mod_time'):
                if current_size > file_info.get('size', 0):
                    # File has grown, append new data
                    self.append_file_data()
                else:
                    # File was overwritten or shrunk, do a full reload
                    self.log("File has been overwritten. Performing full reload...")
                    self.load_file_data(self.active_filepath)
            else:
                # If no changes, schedule the next check
                self.file_watcher_job = self.root.after(1000, self.check_for_updates)

        except OSError:
            # File might have been deleted or is temporarily inaccessible
            self.log("File watcher stopped: file is inaccessible or has been deleted.")
            self.stop_file_watcher()

if __name__ == '__main__':
    # This is ESSENTIAL for multiprocessing to work in a bundled executable
    # and to prevent pickling errors with 'spawn' start method on Windows.
    multiprocessing.freeze_support()
    
    root = tk.Tk()
    app = PlotterApp(root)
    root.mainloop()