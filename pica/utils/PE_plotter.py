"""
Module: PE_Plotter_GUI.py
Purpose: PE Hysteresis Plotter Utility (Part of PICA suite).
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, Canvas
import os
import io
import pandas as pd
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


class PEPlotterAppGUI:
    PROGRAM_VERSION = "2.2"
    
    # --- PICA Styling ---
    CLR_BG = '#B8A392'
    CLR_HEADER = '#E5DCD3'
    CLR_FG = '#2C2825'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F4EFEA'
    CLR_ACCENT_GREEN = '#B68B6E'
    CLR_ACCENT_BLUE = '#BA6B5E'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_CONSOLE_BG = '#E5DCD3'
    FONT_BASE = ('Segoe UI', 10)
    FONT_TITLE = ('Segoe UI', 12, 'bold')

    try:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        LOGO_FILE_PATH = os.path.join(SCRIPT_DIR, "..", "assets", "LOGO", "UGC_DAE_CSR_NBG.jpeg")
    except NameError:
        LOGO_FILE_PATH = "../assets/LOGO/UGC_DAE_CSR_NBG.jpeg"

    def __init__(self, root):
        self.root = root
        self.root.title(f"PICA PE Plotter Utility v{self.PROGRAM_VERSION}")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)
        self.root.configure(bg=self.CLR_BG)

        self.active_filepath = None
        self.file_data_cache = {}  # {filepath: {'df': dataframe, 'metadata': dict}}
        self.file_ui_elements = {} # {filepath: {'var': boolVar, 'chk': checkbutton, 'lbl': label, 'frame': frame}}
        self.logo_image = None

        self.setup_styles()
        self.create_widgets()
        self.log("Welcome to the PICA PE Hysteresis Plotter Utility.")
        self.log("Click 'Add File(s)...' to load your .txt or .csv measurement files.")

    def setup_styles(self):
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('.', background=self.CLR_BG, foreground=self.CLR_FG, font=self.FONT_BASE)
        self.style.configure('TFrame', background=self.CLR_BG)
        self.style.configure('TPanedWindow', background=self.CLR_BG)
        self.style.configure('TLabel', background=self.CLR_FRAME_BG, foreground=self.CLR_FG)
        self.style.configure('Header.TLabel', background=self.CLR_HEADER)
        self.style.configure('TButton', font=self.FONT_BASE, padding=(8, 5), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_HEADER)
        self.style.map('TButton', background=[('active', self.CLR_ACCENT_GOLD), ('hover', self.CLR_ACCENT_GOLD)], 
                                  foreground=[('active', self.CLR_BG), ('hover', self.CLR_BG)])
        self.style.map('TCombobox', fieldbackground=[('readonly', self.CLR_INPUT_BG)])
        self.style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_ACCENT_BLUE)
        self.style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_FG, font=self.FONT_TITLE)
        self.style.configure('Input.TFrame', background=self.CLR_INPUT_BG)

        mpl.rcParams.update({
            'font.family': 'Segoe UI', 'font.size': 11,
            'axes.titlesize': 14, 'axes.labelsize': 12,
            'figure.facecolor': self.CLR_BG, 'axes.facecolor': '#F4EFEA',
            'axes.edgecolor': self.CLR_FG, 'axes.labelcolor': self.CLR_FG,
            'text.color': self.CLR_FG, 'xtick.color': self.CLR_FG, 'ytick.color': self.CLR_FG
        })

    def create_widgets(self):
        # --- Header (PICA Style with Logo and Institute Name) ---
        header = tk.Frame(self.root, bg=self.CLR_HEADER)
        header.pack(side='top', fill='x', padx=1, pady=1)
        header.grid_columnconfigure(1, weight=1)

        left_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        left_header_frame.grid(row=0, column=0, sticky='w')
        font_title_main = ('Segoe UI', 14, 'bold')
        
        ttk.Label(left_header_frame, text="PE Hysteresis Plotter Utility", style='Header.TLabel', 
                  font=font_title_main, foreground=self.CLR_ACCENT_GOLD).pack(side='top', anchor='w', padx=20, pady=(10, 0))
        ttk.Label(left_header_frame, text="(Part of the PICA Suite)", style='Header.TLabel', 
                  font=('Segoe UI', 10, 'italic'), foreground=self.CLR_FG).pack(side='top', anchor='w', padx=20, pady=(0, 10))

        center_header_frame = tk.Frame(header, bg=self.CLR_HEADER)
        center_header_frame.grid(row=0, column=1, sticky='ew')
        
        logo_canvas = Canvas(center_header_frame, width=60, height=60, bg=self.CLR_HEADER, highlightthickness=0)
        logo_canvas.pack(side='left', pady=10)
        
        if PIL_AVAILABLE and os.path.exists(self.LOGO_FILE_PATH):
            try:
                img = Image.open(self.LOGO_FILE_PATH).resize((60, 60), Image.Resampling.LANCZOS)
                self.logo_image = ImageTk.PhotoImage(img)
                logo_canvas.create_image(30, 30, image=self.logo_image)
            except Exception as e:
                self.log(f"Warning: Could not load logo. {e}")
                
        institute_frame = tk.Frame(center_header_frame, bg=self.CLR_HEADER)
        institute_frame.pack(side='left', padx=15)
        ttk.Label(institute_frame, text="UGC-DAE Consortium for Scientific Research", 
                  style='Header.TLabel', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(institute_frame, text="Mumbai Centre", 
                  style='Header.TLabel', font=('Segoe UI', 14)).pack(anchor='w')

        # --- Main Layout ---
        main_pane = ttk.PanedWindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True, padx=10, pady=10)

        left_panel = self._create_left_panel(main_pane)
        main_pane.add(left_panel, weight=1)

        right_panel = self._create_right_panel(main_pane)
        main_pane.add(right_panel, weight=4) # Higher weight to ensure plot takes most space

    def _create_left_panel(self, parent):
        panel = ttk.Frame(parent, width=350)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        # --- File Management ---
        file_frame = ttk.LabelFrame(panel, text="Data Sources")
        file_frame.grid(row=0, column=0, sticky='new', pady=5)
        file_frame.grid_columnconfigure(0, weight=1)

        btn_frame = ttk.Frame(file_frame)
        btn_frame.grid(row=0, column=0, sticky='ew', padx=10, pady=5)
        ttk.Button(btn_frame, text="Add File(s)...", command=self.browse_files).pack(side='left', fill='x', expand=True, padx=(0, 2))
        ttk.Button(btn_frame, text="Remove Selected", command=self.remove_selected_file).pack(side='left', fill='x', expand=True, padx=(2, 0))

        # Checkbox UI for files
        list_container = ttk.Frame(file_frame, style='TFrame')
        list_container.grid(row=1, column=0, sticky='nsew', padx=10, pady=(0, 10))
        
        file_canvas = tk.Canvas(list_container, bg=self.CLR_INPUT_BG, highlightthickness=0, height=150)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=file_canvas.yview)
        self.file_list_frame = ttk.Frame(file_canvas, style='Input.TFrame')
        
        file_canvas.create_window((0, 0), window=self.file_list_frame, anchor="nw")
        file_canvas.configure(yscrollcommand=scrollbar.set)
        file_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.file_list_frame.bind("<Configure>", lambda e: file_canvas.configure(scrollregion=file_canvas.bbox("all")))

        # --- Plot Parameters ---
        params_frame = ttk.LabelFrame(panel, text="Axes Selection")
        params_frame.grid(row=1, column=0, sticky='new', pady=5)
        params_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(params_frame, text="X-Axis:").grid(row=0, column=0, sticky='w', padx=10, pady=5)
        self.x_col_cb = ttk.Combobox(params_frame, state='readonly')
        self.x_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.x_col_cb.grid(row=0, column=1, sticky='ew', padx=10, pady=5)

        ttk.Label(params_frame, text="Y-Axis:").grid(row=1, column=0, sticky='w', padx=10, pady=5)
        self.y_col_cb = ttk.Combobox(params_frame, state='readonly')
        self.y_col_cb.bind("<<ComboboxSelected>>", self.plot_data)
        self.y_col_cb.grid(row=1, column=1, sticky='ew', padx=10, pady=5)

        # --- Console / Metadata Log ---
        console_frame = ttk.LabelFrame(panel, text="Extracted Metadata & Logs")
        console_frame.grid(row=2, column=0, sticky='nsew', pady=5)
        self.console = scrolledtext.ScrolledText(console_frame, state='disabled', bg=self.CLR_CONSOLE_BG, fg=self.CLR_FG, font=('Consolas', 9), wrap='word', borderwidth=0)
        self.console.pack(fill='both', expand=True, padx=5, pady=5)

        return panel

    def _create_right_panel(self, parent):
        panel = ttk.Frame(parent)
        # Using pack to make sure it fills the complete right side seamlessly
        container = ttk.LabelFrame(panel, text='Visualization')
        container.pack(fill='both', expand=True)

        self.figure = Figure(dpi=100)
        self.ax_main = self.figure.add_subplot(111)
        self.ax_main.set_title("No Data Loaded", fontweight='bold')
        self.ax_main.grid(True, linestyle='--', alpha=0.6)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, container)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=5, pady=5)

        toolbar_frame = tk.Frame(container, bg=self.CLR_FRAME_BG)
        toolbar_frame.pack(fill='x', side='bottom', pady=(0, 5))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.configure(background=self.CLR_FRAME_BG)
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

    def parse_file_custom(self, filepath):
        """Custom parser to handle comments and extract metadata"""
        metadata = {
            'Measurement Date': 'Unknown',
            'Sample Area': 'Unknown',
            'Sample Thickness': 'Unknown',
            'Applied Voltage': 'Unknown',
            'Frequency (Hz)': 'Unknown'
        }
        
        data_lines = []
        is_data = False
        headers = []

        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line_clean = line.strip()
                if not line_clean: continue
                
                if 'Sample Area' in line_clean:
                    metadata['Sample Area'] = line_clean.split(':')[-1].strip()
                elif 'Sample Thickness' in line_clean:
                    metadata['Sample Thickness'] = line_clean.split(':')[-1].strip()
                elif 'Volts:' in line_clean:
                    metadata['Applied Voltage'] = line_clean.split(':')[-1].strip()
                elif 'Hysteresis Period' in line_clean:
                    try:
                        period_ms = float(line_clean.split(':')[-1].strip())
                        if period_ms != 0:
                            metadata['Frequency (Hz)'] = f"{1000.0 / period_ms:.1f}"
                    except ValueError: pass
                elif 'Executed:' in line_clean:
                    metadata['Measurement Date'] = line_clean.split('Executed:')[-1].strip()
                elif 'Stored:' in line_clean and metadata['Measurement Date'] == 'Unknown':
                    metadata['Measurement Date'] = line_clean.split('Stored:')[-1].strip()
                
                if 'Point' in line_clean and ('Time' in line_clean or 'Drive' in line_clean):
                    sep = ',' if ',' in line_clean else '\t'
                    headers = [h.strip() for h in line_clean.split(sep)]
                    is_data = True
                    continue
                
                if is_data:
                    sep = ',' if ',' in line_clean else '\t'
                    parts = line_clean.split(sep)
                    if len(parts) >= 3:
                        data_lines.append(line_clean)

        if data_lines:
            sep = ',' if filepath.lower().endswith('.csv') else '\t'
            df = pd.read_csv(io.StringIO('\n'.join(data_lines)), sep=sep, header=None)
            if len(df.columns) == len(headers):
                df.columns = headers
        else:
            df = pd.DataFrame()

        return metadata, df

    def browse_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Select Data File(s)",
            filetypes=(("Data Files", "*.txt *.csv"), ("All files", "*.*"))
        )
        if not filepaths: return

        for fp in filepaths:
            if fp not in self.file_data_cache:
                try:
                    metadata, df = self.parse_file_custom(fp)
                    if df.empty:
                        self.log(f"Warning: No tabular data found in {os.path.basename(fp)}")
                        continue
                    
                    self.file_data_cache[fp] = {'df': df, 'metadata': metadata}
                    self._add_file_to_ui(fp)
                    
                    self.log(f"Loaded: {os.path.basename(fp)}")
                    self.log(f"  Date: {metadata['Measurement Date']}")
                    self.log(f"  Area: {metadata['Sample Area']} cm2 | Thick: {metadata['Sample Thickness']} um")
                    self.log(f"  Voltage: {metadata['Applied Voltage']} V | Freq: {metadata['Frequency (Hz)']} Hz\n")

                    self._update_dropdowns(df.columns.tolist())
                    
                except Exception as e:
                    self.log(f"Error parsing {os.path.basename(fp)}: {e}")

        self.plot_data()

    def _add_file_to_ui(self, filepath):
        var = tk.BooleanVar(value=True)
        entry_frame = ttk.Frame(self.file_list_frame, style='Input.TFrame')
        entry_frame.pack(fill='x', expand=True, pady=1)

        chk = ttk.Checkbutton(entry_frame, variable=var, command=self.plot_data)
        chk.pack(side='left', padx=(5, 0))

        filename = os.path.basename(filepath)
        lbl = ttk.Label(entry_frame, text=filename, background=self.CLR_INPUT_BG, cursor="hand2")
        lbl.pack(side='left', fill='x', expand=True, padx=5)

        self.file_ui_elements[filepath] = {'var': var, 'chk': chk, 'lbl': lbl, 'frame': entry_frame}

    def remove_selected_file(self):
        paths_to_remove = [fp for fp, ui in self.file_ui_elements.items() if ui['var'].get()]
        for path in paths_to_remove:
            self.file_ui_elements[path]['frame'].destroy()
            del self.file_ui_elements[path]
            del self.file_data_cache[path]
            self.log(f"Removed: {os.path.basename(path)}")
        self.plot_data()

    def _update_dropdowns(self, columns):
        current_x = self.x_col_cb.get()
        current_y = self.y_col_cb.get()

        self.x_col_cb['values'] = columns
        self.y_col_cb['values'] = columns

        if not current_x:
            default_x = next((c for c in columns if 'Voltage' in c or 'Field' in c), columns[0])
            self.x_col_cb.set(default_x)
        if not current_y:
            default_y = next((c for c in columns if 'Polarization' in c), columns[1] if len(columns)>1 else columns[0])
            self.y_col_cb.set(default_y)

    def plot_data(self, event=None):
        self.ax_main.clear()
        self.ax_main.grid(True, linestyle='--', alpha=0.6)

        x_col = self.x_col_cb.get()
        y_col = self.y_col_cb.get()
        selected_filepaths = [fp for fp, ui in self.file_ui_elements.items() if ui['var'].get()]

        if not selected_filepaths or not x_col or not y_col:
            self.ax_main.set_title("Waiting for data selection...")
            self.canvas.draw_idle()
            return

        for filepath in selected_filepaths:
            df = self.file_data_cache[filepath]['df']
            filename = os.path.basename(filepath)

            if x_col in df.columns and y_col in df.columns:
                self.ax_main.plot(df[x_col], df[y_col], linewidth=1.5, label=filename)

        self.ax_main.set_xlabel(x_col, fontweight='bold')
        self.ax_main.set_ylabel(y_col, fontweight='bold')
        self.ax_main.set_title("PE Hysteresis Overlay", fontweight='bold')
        
        # Placing legend *inside* the plot area (loc='best' finds the clearest corner automatically)
        self.ax_main.legend(title="Sample Files", loc='best')
        
        self.figure.tight_layout()
        self.canvas.draw_idle()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = PEPlotterAppGUI(root)
    root.mainloop()