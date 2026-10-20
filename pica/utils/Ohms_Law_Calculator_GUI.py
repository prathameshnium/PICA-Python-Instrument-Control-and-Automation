'''
===============================================================================
 PROGRAM:      PICA Ohm's Law Calculator
 PURPOSE:      Type any two of voltage, current and resistance; the third is
               worked out as you type (V = I x R). Each quantity has its own
               unit picker (mV / V / kV, nA / uA / mA / A, ohm / kohm / Mohm
               ...), so values can be entered and read in whatever unit is
               handy. The power V x I is shown alongside.

 No instrument is touched and nothing is written. Only tkinter is used.
===============================================================================
'''
import tkinter as tk
from tkinter import ttk

PROGRAM_VERSION = "1.0"

# Unit choices per quantity, as (label, factor to the base unit).
UNITS = {
    'V': [("µV", 1e-6), ("mV", 1e-3), ("V", 1.0), ("kV", 1e3)],
    'I': [("pA", 1e-12), ("nA", 1e-9), ("µA", 1e-6), ("mA", 1e-3), ("A", 1.0)],
    'R': [("mΩ", 1e-3), ("Ω", 1.0), ("kΩ", 1e3), ("MΩ", 1e6), ("GΩ", 1e9),
          ("TΩ", 1e12)],
}
DEFAULT_UNIT = {'V': "V", 'I': "mA", 'R': "Ω"}
NAMES = {'V': "Voltage", 'I': "Current", 'R': "Resistance"}

# Prefixes for the auto-scaled power read-out.
_PREFIXES = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
             (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]


# -----------------------------------------------------------------------------
# Pure logic (no Tk) -- unit-tested in tests/test_ohms_law_calculator.py
# -----------------------------------------------------------------------------
def factor(quantity, unit):
    """Multiplier from `unit` to the base unit (V, A or ohm)."""
    return dict(UNITS[quantity])[unit]


def parse_value(text):
    """A typed number, or None for an empty box. Raises ValueError otherwise."""
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    value = float(text)
    if value != value or value in (float('inf'), float('-inf')):
        raise ValueError("not a finite number")
    return value


def solve(v=None, i=None, r=None):
    """Given exactly two of v (V), i (A) and r (ohm), return (key, value) for
    the missing one. Raises ValueError with a plain-language reason."""
    given = {k for k, x in (('V', v), ('I', i), ('R', r)) if x is not None}
    if len(given) < 2:
        raise ValueError("Enter any two values.")
    if len(given) == 3:
        raise ValueError("Leave one box empty: that is the one worked out.")
    if 'V' not in given:
        return 'V', i * r
    if 'I' not in given:
        if r == 0:
            raise ValueError("Resistance is zero, so the current is unbounded.")
        return 'I', v / r
    if i == 0:
        raise ValueError("Current is zero, so the resistance is unbounded.")
    return 'R', v / i


def format_number(x):
    """Up to 6 significant figures, scientific only when it has to be."""
    if x == 0:
        return "0"
    if 1e-4 <= abs(x) < 1e7:
        return f"{x:.6g}"
    return f"{x:.4e}"


def format_power(watts):
    """Power with an automatic SI prefix, e.g. 0.0047 -> '4.7 mW'."""
    if watts == 0:
        return "0 W"
    for scale, prefix in _PREFIXES:
        if abs(watts) >= scale:
            return f"{watts / scale:.4g} {prefix}W"
    return f"{watts:.3e} W"


# -----------------------------------------------------------------------------
# GUI
# -----------------------------------------------------------------------------
class PICAOhmsLawApp:

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_ERROR = '#8B2E22'

    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SMALL = ('Segoe UI', FONT_SIZE_BASE - 1)
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')
    FONT_HEADLINE = ('Segoe UI', FONT_SIZE_BASE + 8, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Ohm's Law Calculator")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.resizable(False, False)

        self.value_vars = {k: tk.StringVar() for k in 'VIR'}
        self.unit_vars = {k: tk.StringVar(value=DEFAULT_UNIT[k]) for k in 'VIR'}
        self.headline_var = tk.StringVar(value="Enter any two values.")
        self.detail_var = tk.StringVar(value="")
        self.headline_label = None

        for var in list(self.value_vars.values()) + list(self.unit_vars.values()):
            var.trace_add("write", lambda *_: self.recalculate())

        self.setup_styles()
        self.create_widgets()
        self.recalculate()

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG,
                        bordercolor=self.CLR_BG_DARK, borderwidth=2, padding=12)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG,
                        foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 4),
                        foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG,
                        borderwidth=0)
        style.map('App.TButton', background=[('active', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_DARK)])
        style.map('TCombobox', fieldbackground=[('readonly', self.CLR_FRAME_BG)],
                  selectbackground=[('readonly', self.CLR_ACCENT_GOLD)],
                  selectforeground=[('readonly', self.CLR_TEXT_DARK)])

    def create_widgets(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill='both', expand=True)

        box = ttk.LabelFrame(main, text="V = I × R   (fill any two, leave one empty)")
        box.pack(fill='x')
        for row, key in enumerate('VIR'):
            tk.Label(box, text=f"{NAMES[key]}  {key}", bg=self.CLR_FRAME_BG,
                     font=self.FONT_BASE, width=13, anchor='w').grid(
                         row=row, column=0, sticky='w', pady=4)
            ttk.Entry(box, textvariable=self.value_vars[key], font=self.FONT_BASE,
                      width=16).grid(row=row, column=1, padx=6, pady=4)
            ttk.Combobox(box, textvariable=self.unit_vars[key], state='readonly',
                         values=[u for u, _ in UNITS[key]], font=self.FONT_BASE,
                         width=5).grid(row=row, column=2, pady=4)

        self.headline_label = tk.Label(
            main, textvariable=self.headline_var, font=self.FONT_HEADLINE,
            bg=self.CLR_BG_DARK, fg=self.CLR_TEXT_DARK, anchor='w')
        self.headline_label.pack(fill='x', pady=(12, 0))
        tk.Label(main, textvariable=self.detail_var, font=self.FONT_SMALL,
                 bg=self.CLR_BG_DARK, fg=self.CLR_TEXT, anchor='w').pack(fill='x')

        ttk.Button(main, text="Clear", style='App.TButton',
                   command=self.clear).pack(anchor='e', pady=(10, 0))

    def clear(self):
        for var in self.value_vars.values():
            var.set("")

    def recalculate(self):
        """Read the boxes, solve for the empty one and show it in its unit."""
        try:
            base = {}
            for key in 'VIR':
                typed = parse_value(self.value_vars[key].get())
                base[key] = (None if typed is None
                             else typed * factor(key, self.unit_vars[key].get()))
        except (ValueError, TypeError, KeyError):
            self._show("Not a number in one of the boxes.", "", error=True)
            return
        try:
            key, value = solve(base['V'], base['I'], base['R'])
        except ValueError as e:
            self._show(str(e), "", error=len([x for x in base.values() if x is not None]) >= 2)
            return
        unit = self.unit_vars[key].get()
        shown = value / factor(key, unit)
        base[key] = value
        self._show(f"{NAMES[key]}  {key} = {format_number(shown)} {unit}",
                   f"Power  P = V × I = {format_power(base['V'] * base['I'])}")

    def _show(self, headline, detail, error=False):
        self.headline_var.set(headline)
        self.detail_var.set(detail)
        if self.headline_label is not None:
            self.headline_label.configure(
                fg=self.CLR_ERROR if error else self.CLR_TEXT_DARK)


if __name__ == '__main__':
    root = tk.Tk()
    app = PICAOhmsLawApp(root)
    root.mainloop()
