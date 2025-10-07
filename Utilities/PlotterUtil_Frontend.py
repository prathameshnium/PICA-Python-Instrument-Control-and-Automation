# -------------------------------------------------------------------------------
# Name:         PICA Plotter Utility
# Purpose:      A general-purpose CSV/DAT file plotter for the PICA suite.
# Author:       Prathamesh K Deshmukh (with Gemini Code Assist)
# Created:      06/10/2025
# Version:      1.0
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

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

class PlotterApp:
    PROGRAM_VERSION = "1.0"
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

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA Plotter Utility v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.filepath = None
        self.headers = []
        self.data = {}
        self.logo_image = None

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
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG, font=self.FONT_TITLE)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        style.map('TCheckbutton', background=[('active', self.CLR_FRAME_BG)])
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
        header.pack(side='top', fill='x')
        ttk.Label(header, text=f"PICA General Purpose Plotter", style='Header.TLabel', font=self.FONT_TITLE).pack(side='left', padx=20, pady=10)

        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = self._create_left_panel(main_pane)
        main_pane.add(left_panel, weight=1)

        right_panel = self._create_right_panel(main_pane)
        main_pane.add(right_panel, weight=3)

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=400)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        # --- File & Column Selection ---
        file_frame = ttk.LabelFrame(panel, text="Data Source")
        file_frame.grid(row=0, column=0, sticky='new', pady=5)
        file_frame.grid_columnconfigure(0, weight=1)

        ttk.Button(file_frame, text="Browse for File...", command=self.browse_file).grid(row=0, column=0, sticky='ew', padx=10, pady=10)
        self.filepath_label = ttk.Label(file_frame, text="No file selected.", wraplength=350, justify='left', style='TLabel')
        self.filepath_label.grid(row=1, column=0, sticky='ew', padx=10, pady=(0, 10))

        # --- Plotting Parameters ---
        params_frame = ttk.LabelFrame(panel, text="Plot Parameters")
        params_frame.grid(row=1, column=0, sticky='new', pady=5)
        params_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(params_frame, text="Skip Rows:").grid(row=0, column=0, sticky='w', padx=10, pady=5)
        self.skip_rows_entry = ttk.Entry(params_frame, width=10)
        self.skip_rows_entry.grid(row=0, column=1, sticky='w', padx=10, pady=5)
        self.skip_rows_entry.insert(0, "0")

        ttk.Label(params_frame, text="X-Axis Column:").grid(row=1, column=0, sticky='w', padx=10, pady=5)
        self.x_col_cb = ttk.Combobox(params_frame, state='readonly')
        self.x_col_cb.grid(row=1, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Y-Axis Column:").grid(row=2, column=0, sticky='w', padx=10, pady=5)
        self.y_col_cb = ttk.Combobox(params_frame, state='readonly')
        self.y_col_cb.grid(row=2, column=1, sticky='ew', padx=10, pady=5)

        # --- Plotting Options ---
        options_frame = ttk.Frame(params_frame)
        options_frame.grid(row=3, column=0, columnspan=2, sticky='ew', pady=10)
        options_frame.grid_columnconfigure((0,1), weight=1)

        self.x_log_var = tk.BooleanVar()
        ttk.Checkbutton(options_frame, text="X Log Scale", variable=self.x_log_var).grid(row=0, column=0, sticky='w', padx=10)

        self.y_log_var = tk.BooleanVar()
        ttk.Checkbutton(options_frame, text="Y Log Scale", variable=self.y_log_var).grid(row=0, column=1, sticky='w', padx=10)

        ttk.Button(params_frame, text="Update Plot", style="Plot.TButton", command=self.plot_data).grid(row=4, column=0, columnspan=2, sticky='ew', padx=10, pady=10)

        # --- Console ---
        console_frame = ttk.LabelFrame(panel, text="Console")
        console_frame.grid(row=2, column=0, sticky='nsew', pady=5)
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

    def browse_file(self):
        filepath = filedialog.askopenfilename(
            title="Select a data file",
            filetypes=(("Data Files", "*.csv *.dat"), ("All files", "*.*"))
        )
        if not filepath:
            return

        self.filepath = filepath
        self.filepath_label.config(text=os.path.basename(filepath))
        self.log(f"Selected file: {filepath}")
        self.load_file_data()

    def load_file_data(self):
        if not self.filepath:
            return

        try:
            skip_rows = int(self.skip_rows_entry.get())
        except ValueError:
            self.log("Error: 'Skip Rows' must be an integer.")
            messagebox.showerror("Input Error", "'Skip Rows' must be a valid integer.")
            return

        try:
            with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
                # Skip initial lines if they are comments
                lines = f.readlines()
                line_offset = 0
                for i, line in enumerate(lines):
                    if not line.strip().startswith('#'):
                        line_offset = i
                        break
                
                # Read header from the first non-comment line
                reader = csv.reader([lines[line_offset]])
                self.headers = next(reader)
                self.headers = [h.strip() for h in self.headers]

                # Reset data and load new data
                self.data = {header: [] for header in self.headers}
                
                # Use DictReader for robust data loading
                dict_reader = csv.DictReader(lines[line_offset + 1:], fieldnames=self.headers)
                for row in dict_reader:
                    for header in self.headers:
                        try:
                            self.data[header].append(float(row[header]))
                        except (ValueError, TypeError):
                            # If conversion fails, append NaN or a placeholder
                            self.data[header].append(float('nan'))

            self.x_col_cb['values'] = self.headers
            self.y_col_cb['values'] = self.headers
            if len(self.headers) > 1:
                self.x_col_cb.set(self.headers[0])
                self.y_col_cb.set(self.headers[1])
            elif self.headers:
                self.x_col_cb.set(self.headers[0])

            self.log(f"Loaded {len(self.data[self.headers[0]])} data points with headers: {self.headers}")
            self.plot_data()

        except Exception as e:
            self.log(f"Error loading file: {traceback.format_exc()}")
            messagebox.showerror("File Load Error", f"Could not read the data file.\n\n{e}")

    def plot_data(self):
        x_col = self.x_col_cb.get()
        y_col = self.y_col_cb.get()

        if not all([x_col, y_col, self.data]):
            self.log("Cannot plot. Select X and Y columns.")
            return

        try:
            x_data = self.data[x_col]
            y_data = self.data[y_col]

            self.line_main.set_data(x_data, y_data)

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

if __name__ == '__main__':
    root = tk.Tk()
    app = PlotterApp(root)
    root.mainloop()