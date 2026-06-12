'''
===============================================================================
 PROGRAM:      PICA Unit Converter Utility
 PURPOSE:      A minimalistic unit converter and prefix reference for lab measurements.
===============================================================================
'''
import tkinter as tk
from tkinter import ttk


class PICAUnitConverterApp:

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 4, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')
    FONT_MONO = ('Consolas', 11)
    FONT_RESULT = ('Consolas', 18, 'bold')

    # --- Conversion Data ---
    UNIT_DATA = {
        "Metric Prefixes": {
            "Exa (E)": 1e18,
            "Peta (P)": 1e15,
            "Tera (T)": 1e12,
            "Giga (G)": 1e9,
            "Mega (M)": 1e6,
            "Kilo (k)": 1e3,
            "Base Unit (1)": 1.0,
            "milli (m)": 1e-3,
            "micro (µ)": 1e-6,
            "nano (n)": 1e-9,
            "pico (p)": 1e-12,
            "femto (f)": 1e-15
        },
        "Length": {
            "Meter (m)": 1.0,
            "Kilometer (km)": 1000.0,
            "Centimeter (cm)": 0.01,
            "Millimeter (mm)": 0.001,
            "Mile (mi)": 1609.34,
            "Foot (ft)": 0.3048,
            "Inch (in)": 0.0254,
            "Angstrom (Å)": 1e-10
        },
        "Mass": {
            "Kilogram (kg)": 1.0,
            "Gram (g)": 0.001,
            "Milligram (mg)": 1e-6,
            "Pound (lb)": 0.453592,
            "Ounce (oz)": 0.0283495
        },
        "Temperature": {
            "Celsius (°C)": "special",
            "Fahrenheit (°F)": "special",
            "Kelvin (K)": "special"
        }
    }

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Unit Converter")
        self.root.geometry("650x420")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.resizable(False, False)

        # --- Variables ---
        self.category_var = tk.StringVar(value="Metric Prefixes")
        self.from_unit_var = tk.StringVar()
        self.to_unit_var = tk.StringVar()
        self.input_value_var = tk.StringVar(value="1")
        self.result_var = tk.StringVar(value="---")

        # Triggers conversion when user types
        self.input_value_var.trace_add("write", lambda *args: self.calculate_conversion())

        self.setup_styles()
        self.create_widgets()
        self.update_unit_dropdowns() # Initialize dropdowns

    def setup_styles(self):
        """Initializes the PICA theme styles."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_BG_DARK, borderwidth=2, padding=10)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)
        
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 5), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG, borderwidth=0)
        style.map('App.TButton', 
                  background=[('active', self.CLR_ACCENT_GOLD)], 
                  foreground=[('active', self.CLR_TEXT_DARK)])

        # Style for Comboboxes
        style.map('TCombobox', 
                  fieldbackground=[('readonly', self.CLR_FRAME_BG)],
                  selectbackground=[('readonly', self.CLR_ACCENT_GOLD)],
                  selectforeground=[('readonly', self.CLR_TEXT_DARK)])

    def create_widgets(self):
        """Constructs the two-column interface."""
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill='both', expand=True)
        main_frame.columnconfigure(0, weight=1, uniform="col")
        main_frame.columnconfigure(1, weight=1, uniform="col")

        # ==========================================
        # LEFT COLUMN: CONVERTER
        # ==========================================
        conv_frame = ttk.LabelFrame(main_frame, text="Unit Converter")
        conv_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 10))

        # Category Dropdown
        tk.Label(conv_frame, text="Category:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(anchor='w', pady=(5, 0))
        cat_combo = ttk.Combobox(conv_frame, textvariable=self.category_var, values=list(self.UNIT_DATA.keys()), state="readonly", font=self.FONT_BASE)
        cat_combo.pack(fill='x', pady=(0, 10))
        cat_combo.bind("<<ComboboxSelected>>", self.update_unit_dropdowns)

        # Input Entry
        tk.Label(conv_frame, text="Value:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(anchor='w')
        input_entry = ttk.Entry(conv_frame, textvariable=self.input_value_var, font=self.FONT_BASE)
        input_entry.pack(fill='x', pady=(0, 10))

        # From / To Dropdowns
        tk.Label(conv_frame, text="From:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(anchor='w')
        self.combo_from = ttk.Combobox(conv_frame, textvariable=self.from_unit_var, state="readonly", font=self.FONT_BASE)
        self.combo_from.pack(fill='x', pady=(0, 5))
        self.combo_from.bind("<<ComboboxSelected>>", lambda e: self.calculate_conversion())

        tk.Label(conv_frame, text="To:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(anchor='w')
        self.combo_to = ttk.Combobox(conv_frame, textvariable=self.to_unit_var, state="readonly", font=self.FONT_BASE)
        self.combo_to.pack(fill='x', pady=(0, 15))
        self.combo_to.bind("<<ComboboxSelected>>", lambda e: self.calculate_conversion())

        # Result Display
        result_label = tk.Label(conv_frame, textvariable=self.result_var, font=self.FONT_RESULT, bg=self.CLR_FRAME_BG, fg=self.CLR_ACCENT_GOLD, anchor='e')
        result_label.pack(fill='x', pady=5)

        # ==========================================
        # RIGHT COLUMN: REFERENCE DEFINITIONS
        # ==========================================
        ref_frame = ttk.LabelFrame(main_frame, text="Standard Prefixes")
        ref_frame.grid(row=0, column=1, sticky='nsew')

        prefix_text = (
            "Exa   (E) : 10^18\n"
            "Peta  (P) : 10^15\n"
            "Tera  (T) : 10^12\n"
            "Giga  (G) : 10^9 \n"
            "Mega  (M) : 10^6 \n"
            "Kilo  (k) : 10^3 \n"
            "-----------------\n"
            "Base      : 10^0 \n"
            "-----------------\n"
            "milli (m) : 10^-3\n"
            "micro (µ) : 10^-6\n"
            "nano  (n) : 10^-9\n"
            "pico  (p) : 10^-12\n"
            "femto (f) : 10^-15\n"
        )

        lbl_ref = tk.Label(ref_frame, text=prefix_text, font=self.FONT_MONO, bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT, justify='left', anchor='nw')
        lbl_ref.pack(fill='both', expand=True, padx=10, pady=5)


    def update_unit_dropdowns(self, event=None):
        """Updates the From and To comboboxes based on the selected category."""
        category = self.category_var.get()
        units = list(self.UNIT_DATA[category].keys())
        
        self.combo_from['values'] = units
        self.combo_to['values'] = units
        
        if units:
            self.combo_from.current(0)
            self.combo_to.current(1 if len(units) > 1 else 0)
            
        self.calculate_conversion()

    def calculate_conversion(self):
        """Performs the math based on user input and selected units."""
        category = self.category_var.get()
        unit_from = self.from_unit_var.get()
        unit_to = self.to_unit_var.get()
        val_str = self.input_value_var.get()

        # Handle empty or invalid inputs gracefully
        try:
            val = float(val_str)
        except ValueError:
            self.result_var.set("---")
            return

        if not unit_from or not unit_to:
            return

        # Temperature uses special offset formulas
        if category == "Temperature":
            celsius = val
            # First convert to Celsius
            if unit_from == "Fahrenheit (°F)":
                celsius = (val - 32) * 5/9
            elif unit_from == "Kelvin (K)":
                celsius = val - 273.15
            
            # Then convert from Celsius to target
            if unit_to == "Fahrenheit (°F)":
                result = (celsius * 9/5) + 32
            elif unit_to == "Kelvin (K)":
                result = celsius + 273.15
            else:
                result = celsius

        # Everything else uses simple multiplication factors relative to a base unit
        else:
            factor_from = self.UNIT_DATA[category][unit_from]
            factor_to = self.UNIT_DATA[category][unit_to]
            
            # val * factor_from converts to base unit. / factor_to converts to target unit.
            result = val * (factor_from / factor_to)

        # Format result for readability (use scientific notation for very large/small numbers)
        if abs(result) < 0.0001 and result != 0 or abs(result) > 999999:
            self.result_var.set(f"{result:.4e}")
        else:
            # Round to 6 decimal places to prevent floating point artifacts (e.g., 0.300000000004)
            self.result_var.set(f"{round(result, 6):g}")


if __name__ == '__main__':
    root = tk.Tk()
    app = PICAUnitConverterApp(root)
    root.mainloop()