'''
===============================================================================
 PROGRAM:      PICA Quick Calc
 PURPOSE:      A calculator for the lab bench that shows the maths the way it
               is written on paper.

               Each line is one expression. As it is typed, the line is
               typeset underneath -- a/b as a stacked fraction, x^2 as a
               superscript, sqrt(x) under a radical -- and its value is shown
               on the right, as an exact fraction where the value is one
               (3/4, not 0.75000000001).

               Lines can name values and functions and use them further down,
               as in a worksheet:
                   R = 4.7e3
                   I = 1.2e-6
                   V = I R               -> 0.00564
                   f(x) = x^2 + 1
                   f(3)                  -> 10
               "ans" is the value of the line above.

 SYNTAX:       ^ is a power (x^2); a number or a closing bracket followed by
               a name or "(" multiplies (2pi, 3(x+1), (a+b)(a-b)); the usual
               functions are there (sin, cos, tan, asin, ..., ln, log, log10,
               exp, sqrt, abs, factorial), with the constants pi, e, tau and
               inf. Trigonometry is in radians unless DEG is selected.

 SAFETY:       Lines are parsed, not executed: only arithmetic, names,
               calls of the listed functions (and user functions), and
               assignments are accepted. Nothing can import, open a file or
               reach outside the calculator.
===============================================================================
'''
import ast
import base64
import io
import math
import re
import tkinter as tk
from fractions import Fraction
from tkinter import ttk

# --- PICA palette (the launcher's warm sand / cream / terracotta) ------------
CLR_APP = '#B8A392'
CLR_PANEL = '#E5DCD3'
CLR_PANEL2 = '#F1EBE4'
CLR_ACCENT = '#BA6B5E'
CLR_ACCENT_SOFT = '#EAD9D2'
CLR_HOVER = '#8B3A2F'
CLR_TEXT = '#2C2825'
CLR_TEXT_DIM = '#6B5F54'
CLR_TEXT_LIGHT = '#FFFFFF'
CLR_BORDER = '#C4B2A0'


# =============================================================================
#  Parsing
# =============================================================================
_NUMBER = r'(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'


def preprocess(text):
    """Calculator syntax -> Python syntax: ^ is a power, juxtaposition multiplies.

    "2pi" -> "2*pi", "3(x+1)" -> "3*(x+1)", "(a)(b)" -> "(a)*(b)",
    "(a)x" -> "(a)*x", "x^2" -> "x**2". A number inside a name ("log10",
    "x2") is left alone, and so is an exponent ("1e-3").
    """
    text = text.replace('^', '**').replace('×', '*').replace('÷', '/')
    text = text.replace('π', 'pi').replace('√', 'sqrt')
    # n! (a number or a name) -> factorial(n)
    text = re.sub(r'(?<![\w.])(\d+|[A-Za-z_]\w*)\s*!(?!=)', r'factorial(\1)', text)

    # A whole number followed by a name or "(": insert *. The number is
    # matched whole first (so "1e-3" stays one number) and the look-behind
    # keeps a digit that is part of a name ("log10", "x2") out of it.
    def _number(m):
        rest = text[m.end():].lstrip()
        if rest[:1].isalpha() or rest[:1] in ('_', '('):
            return m.group(0) + '*'
        return m.group(0)
    text = re.sub(r'(?<![\w.])' + _NUMBER, _number, text)
    # Two names (or a name and a number) side by side multiply: "I R",
    # "R 2". A name followed by "(" stays a call: "f (x)".
    text = re.sub(r'([A-Za-z_]\w*)\s+(?=[A-Za-z_\d.])', r'\1*', text)
    # ")" followed by a name, a number or "(": insert *.
    text = re.sub(r'\)\s*(?=[\w(.])', ')*', text)
    return text


FUNCTION_DEF = re.compile(r'^\s*([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)?\s*\)\s*=(?!=)(.*)$')
ASSIGNMENT = re.compile(r'^\s*([A-Za-z_]\w*)\s*=(?!=)(.*)$')


def parse_line(text):
    """One line -> ('empty'|'expr'|'assign'|'func', name, params, ast.Expression).

    Raises SyntaxError for anything that is not a calculator line.
    """
    if not text.strip():
        return ('empty', None, (), None)
    m = FUNCTION_DEF.match(text)
    if m:
        params = tuple(p.strip() for p in (m.group(2) or "").split(',') if p.strip())
        return ('func', m.group(1), params, _parse_expr(m.group(3)))
    m = ASSIGNMENT.match(text)
    if m:
        return ('assign', m.group(1), (), _parse_expr(m.group(2)))
    return ('expr', None, (), _parse_expr(text))


_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name,
            ast.Load, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div,
            ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Tuple)


def _parse_expr(text):
    if not text.strip():
        raise SyntaxError("nothing after '='")
    src = preprocess(text).strip()
    tree = ast.parse(src, mode='eval')
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            # Typeset a number as it was typed ("4.7e3", not "4700.0").
            node.typed = ast.get_source_segment(src, node)
        if not isinstance(node, _ALLOWED):
            raise SyntaxError(f"'{type(node).__name__}' is not allowed here")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise SyntaxError("only plain function calls, e.g. sqrt(2)")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, complex)):
            raise SyntaxError("only numbers are allowed")
    return tree


# =============================================================================
#  Evaluation
# =============================================================================
def _builtins(degrees=False):
    to_rad = math.radians if degrees else (lambda x: x)
    from_rad = math.degrees if degrees else (lambda x: x)
    fns = {
        'sin': lambda x: math.sin(to_rad(x)), 'cos': lambda x: math.cos(to_rad(x)),
        'tan': lambda x: math.tan(to_rad(x)),
        'asin': lambda x: from_rad(math.asin(x)), 'acos': lambda x: from_rad(math.acos(x)),
        'atan': lambda x: from_rad(math.atan(x)),
        'atan2': lambda y, x: from_rad(math.atan2(y, x)),
        'sinh': math.sinh, 'cosh': math.cosh, 'tanh': math.tanh,
        'sqrt': math.sqrt, 'cbrt': lambda x: math.copysign(abs(x) ** (1 / 3), x),
        'exp': math.exp, 'ln': math.log, 'log10': math.log10, 'log2': math.log2,
        'log': lambda x, base=None: math.log10(x) if base is None else math.log(x, base),
        'abs': abs, 'factorial': math.factorial, 'floor': math.floor,
        'ceil': math.ceil, 'round': round, 'min': min, 'max': max,
        'deg': math.degrees, 'rad': math.radians, 'hypot': math.hypot,
    }
    consts = {'pi': math.pi, 'e': math.e, 'tau': math.tau, 'inf': math.inf}
    return fns, consts


class _Evaluator:
    def __init__(self, names, functions):
        self.names = names
        self.functions = functions

    def eval(self, node):
        method = getattr(self, '_' + type(node).__name__, None)
        if method is None:
            raise SyntaxError(f"'{type(node).__name__}' is not allowed here")
        return method(node)

    def _Expression(self, node):
        return self.eval(node.body)

    def _Constant(self, node):
        return node.value

    def _Tuple(self, node):
        return tuple(self.eval(e) for e in node.elts)

    def _Name(self, node):
        if node.id in self.names:
            return self.names[node.id]
        raise NameError(f"'{node.id}' is not defined")

    def _UnaryOp(self, node):
        v = self.eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else +v

    def _BinOp(self, node):
        a, b = self.eval(node.left), self.eval(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            return a / b
        if isinstance(op, ast.FloorDiv):
            return a // b
        if isinstance(op, ast.Mod):
            return a % b
        if isinstance(op, ast.Pow):
            if abs(b) > 10000 and abs(a) > 1:
                raise OverflowError("result too large")
            return a ** b
        raise SyntaxError("operator not allowed")

    def _Call(self, node):
        fn = self.functions.get(node.func.id)
        if fn is None:
            raise NameError(f"'{node.func.id}' is not a function")
        args = [self.eval(a) for a in node.args]
        return fn(*args)


def evaluate_lines(lines, degrees=False):
    """Evaluate a worksheet. Returns one dict per line:
    {'kind', 'name', 'value', 'error', 'latex'}; value is None on an
    empty line, a definition of a function, or an error."""
    fns, consts = _builtins(degrees)
    names = dict(consts)
    functions = dict(fns)
    results = []
    ans = None
    for text in lines:
        out = {'kind': 'empty', 'name': None, 'value': None, 'error': None, 'latex': ''}
        try:
            kind, name, params, tree = parse_line(text)
            out['kind'], out['name'] = kind, name
            if kind != 'empty':
                out['latex'] = line_latex(kind, name, params, tree)
            if ans is not None:
                names['ans'] = ans
            if kind == 'func':
                if name in fns:
                    raise NameError(f"'{name}' is a built-in function")
                functions[name] = _user_function(name, params, tree, names, functions)
            elif kind in ('expr', 'assign'):
                value = _Evaluator(names, functions).eval(tree)
                if isinstance(value, complex) or isinstance(value, tuple):
                    raise ValueError("not a real number")
                if kind == 'assign':
                    if name in fns:
                        raise NameError(f"'{name}' is a built-in function")
                    names[name] = value
                out['value'] = value
                ans = value
        except ZeroDivisionError:
            out['error'] = "division by zero"
        except (SyntaxError, NameError, ValueError, TypeError,
                OverflowError, RecursionError) as e:
            msg = e.msg if isinstance(e, SyntaxError) and e.msg else str(e)
            out['error'] = msg or type(e).__name__
        results.append(out)
    return results


def _user_function(name, params, tree, names, functions):
    def fn(*args):
        if len(args) != len(params):
            raise TypeError(f"{name} takes {len(params)} value(s), got {len(args)}")
        scope = dict(names)
        scope.update(zip(params, args))
        return _Evaluator(scope, functions).eval(tree)
    return fn


# =============================================================================
#  Showing a value
# =============================================================================
def as_fraction(value, max_den=10000):
    """The exact fraction a value is, or None when it is not a simple one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int) or not math.isfinite(value):
        return None
    if value == int(value):
        return None
    frac = Fraction(value).limit_denominator(max_den)
    if abs(float(frac) - value) <= 1e-12 * max(1.0, abs(value)):
        return frac
    return None


def format_number(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        if abs(value) < 10 ** 6:
            return str(value)
        if abs(value) < 10 ** 15:
            return f"{value:,}".replace(',', ' ')
        value = float(value)
    if not math.isfinite(value):
        return "∞" if value > 0 else ("−∞" if value < 0 else "undefined")
    if value == 0:
        return "0"
    mag = abs(value)
    if 1e-4 <= mag < 1e10:
        text = f"{value:.10g}"
    else:
        text = f"{value:.8e}"
        mant, exp = text.split('e')
        mant = mant.rstrip('0').rstrip('.')
        return f"{mant} × 10^{int(exp)}"
    return text


def value_latex(value):
    """The result as typeset maths: '\\frac{3}{4}', or the number."""
    frac = as_fraction(value)
    if frac is not None:
        sign = '-' if frac < 0 else ''
        return f"{sign}\\frac{{{abs(frac.numerator)}}}{{{frac.denominator}}}"
    text = format_number(value)
    m = re.fullmatch(r'(.*) × 10\^(-?\d+)', text)
    if m:
        return f"{m.group(1)} \\times 10^{{{m.group(2)}}}"
    return text.replace('∞', '\\infty').replace('−', '-')


# =============================================================================
#  Typesetting: the parsed line -> mathtext (matplotlib's LaTeX subset)
# =============================================================================
_GREEK = {'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta',
          'iota', 'kappa', 'lambda', 'mu', 'nu', 'xi', 'pi', 'rho', 'sigma',
          'tau', 'upsilon', 'phi', 'chi', 'psi', 'omega', 'Gamma', 'Delta',
          'Theta', 'Lambda', 'Xi', 'Pi', 'Sigma', 'Phi', 'Psi', 'Omega'}
_FUNC_TEX = {'sin': r'\sin', 'cos': r'\cos', 'tan': r'\tan', 'asin': r'\arcsin',
             'acos': r'\arccos', 'atan': r'\arctan', 'sinh': r'\sinh',
             'cosh': r'\cosh', 'tanh': r'\tanh', 'ln': r'\ln', 'log': r'\log',
             'exp': r'\exp', 'min': r'\min', 'max': r'\max'}
# Binding strength, for deciding where brackets are needed.
_PREC = {ast.Add: 1, ast.Sub: 1, ast.Mult: 2, ast.Div: 2, ast.FloorDiv: 2,
         ast.Mod: 2, ast.Pow: 4}


def _name_tex(name):
    if name in _GREEK:
        return '\\' + name
    if name == 'inf':
        return r'\infty'
    base, _, sub = name.partition('_')
    base_tex = ('\\' + base) if base in _GREEK else (
        base if len(base) == 1 else r'\mathrm{' + base + '}')
    return f"{base_tex}_{{{sub}}}" if sub else base_tex


def _num_tex(value, typed=None):
    if isinstance(value, float) or typed:
        text = typed or repr(value)
        if 'e' in text.lower():
            mant, exp = re.split('[eE]', text)
            return f"{mant} \\times 10^{{{int(exp)}}}"
        return text
    return str(value)


def _prec(node):
    if isinstance(node, ast.BinOp):
        return _PREC[type(node.op)]
    if isinstance(node, ast.UnaryOp):
        return 3
    return 5


def to_latex(node):
    if isinstance(node, ast.Expression):
        return to_latex(node.body)
    if isinstance(node, ast.Constant):
        return _num_tex(node.value, getattr(node, 'typed', None))
    if isinstance(node, ast.Name):
        return _name_tex(node.id)
    if isinstance(node, ast.Tuple):
        return ', '.join(to_latex(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp):
        inner = _wrap(node.operand, 3)
        return ('-' if isinstance(node.op, ast.USub) else '+') + inner
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op is ast.Div:
            return f"\\frac{{{to_latex(node.left)}}}{{{to_latex(node.right)}}}"
        if op is ast.Pow:
            base = node.left
            base_tex = to_latex(base)
            if _prec(base) <= 4 or (isinstance(base, ast.Constant) and base.value < 0):
                base_tex = rf"\left({base_tex}\right)"
            return f"{{{base_tex}}}^{{{to_latex(node.right)}}}"
        p = _PREC[op]
        left = _wrap(node.left, p)
        # a - (b + c): the right side of - binds tighter than the left.
        right = _wrap(node.right, p + (1 if op in (ast.Sub, ast.Mod, ast.FloorDiv) else 0))
        if op is ast.Add:
            return f"{left} + {right}"
        if op is ast.Sub:
            return f"{left} - {right}"
        if op is ast.Mod:
            return rf"{left} \bmod {right}"
        if op is ast.FloorDiv:
            return rf"\left\lfloor \frac{{{to_latex(node.left)}}}{{{to_latex(node.right)}}} \right\rfloor"
        # Multiplication: "2\pi", "3x" written together, numbers kept apart.
        if isinstance(node.right, ast.Constant) or (
                isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)):
            return rf"{left} \cdot {right}"
        if isinstance(node.left, ast.Constant) and _starts_with_letter(node.right):
            return f"{left}{right}"
        # Two symbols: "IR", as on paper, with a thin space between.
        if isinstance(node.left, ast.Name) and _starts_with_letter(node.right):
            return rf"{left}\,{right}"
        return rf"{left} \cdot {right}"
    if isinstance(node, ast.Call):
        name = node.func.id
        args = [to_latex(a) for a in node.args]
        joined = ', '.join(args)
        if name == 'sqrt' and len(args) == 1:
            return rf"\sqrt{{{joined}}}"
        if name == 'cbrt' and len(args) == 1:
            return rf"\sqrt[3]{{{joined}}}"
        if name == 'abs' and len(args) == 1:
            return rf"\left|{joined}\right|"
        if name == 'exp' and len(args) == 1:
            return f"e^{{{joined}}}"
        if name == 'factorial' and len(args) == 1:
            return f"{_wrap(node.args[0], 5)}!"
        if name == 'log10' and len(args) == 1:
            return rf"\log_{{10}}\left({joined}\right)"
        if name == 'log2' and len(args) == 1:
            return rf"\log_{{2}}\left({joined}\right)"
        if name == 'log' and len(args) == 2:
            return rf"\log_{{{args[1]}}}\left({args[0]}\right)"
        if name == 'floor' and len(args) == 1:
            return rf"\left\lfloor {joined} \right\rfloor"
        if name == 'ceil' and len(args) == 1:
            return rf"\left\lceil {joined} \right\rceil"
        head = _FUNC_TEX.get(name, _name_tex(name))
        return rf"{head}\left({joined}\right)"
    raise SyntaxError("cannot typeset this")


def _starts_with_letter(node):
    """True when the typeset node begins with a name or function ("x",
    "x^2", "sin(x)"), so a number in front of it is written as 2x."""
    if isinstance(node, (ast.Name, ast.Call)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        return isinstance(node.left, (ast.Name, ast.Call))
    return False


def _wrap(node, min_prec):
    tex = to_latex(node)
    if _prec(node) < min_prec:
        return rf"\left({tex}\right)"
    return tex


def line_latex(kind, name, params, tree):
    body = to_latex(tree)
    if kind == 'assign':
        return f"{_name_tex(name)} = {body}"
    if kind == 'func':
        args = ', '.join(_name_tex(p) for p in params)
        return rf"{_name_tex(name)}\left({args}\right) = {body}"
    return body


# =============================================================================
#  Rendering mathtext to a Tk image
# =============================================================================
_IMAGE_CACHE = {}


def render_math(latex, size=15, color=CLR_TEXT, background=CLR_PANEL2):
    """mathtext -> PNG bytes (base64) on the row's own colour, or None when
    it cannot be drawn (the caller then shows plain text)."""
    key = (latex, size, color, background)
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]
    data = None
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(0.01, 0.01), facecolor=background)
        FigureCanvasAgg(fig)
        fig.text(0, 0, f"${latex}$", fontsize=size, color=color)
        buf = io.BytesIO()
        fig.savefig(buf, dpi=100, format='png', bbox_inches='tight',
                    pad_inches=0.02, facecolor=background)
        data = base64.b64encode(buf.getvalue())
    except Exception:
        data = None
    if len(_IMAGE_CACHE) > 400:
        _IMAGE_CACHE.clear()
    _IMAGE_CACHE[key] = data
    return data


# =============================================================================
#  The window
# =============================================================================
class _Row:
    """One worksheet line: number, input, typeset preview and result."""

    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=CLR_PANEL2, highlightthickness=1,
                              highlightbackground=CLR_BORDER)
        self.num = tk.Label(self.frame, text="", width=3, bg=CLR_PANEL,
                            fg=CLR_TEXT_DIM, font=('Segoe UI', 10, 'bold'))
        self.num.pack(side='left', fill='y')
        self.remove = tk.Label(self.frame, text="✕", bg=CLR_PANEL2, fg=CLR_TEXT_DIM,
                               font=('Segoe UI', 11), cursor='hand2', padx=8)
        self.remove.pack(side='right', fill='y')
        self.remove.bind("<Button-1>", lambda _e: app.delete_row(self))
        body = tk.Frame(self.frame, bg=CLR_PANEL2)
        body.pack(side='left', fill='both', expand=True, padx=(10, 4), pady=6)
        self.entry = tk.Entry(body, font=('Consolas', 13), bg=CLR_PANEL2, fg=CLR_TEXT,
                              relief='flat', bd=0, insertbackground=CLR_ACCENT,
                              highlightthickness=0)
        self.entry.pack(fill='x')
        out = tk.Frame(body, bg=CLR_PANEL2)
        out.pack(fill='x', pady=(4, 0))
        self.math = tk.Label(out, bg=CLR_PANEL2, fg=CLR_TEXT, anchor='w',
                             font=('Segoe UI', 12))
        self.math.pack(side='left')
        self.result = tk.Label(out, bg=CLR_PANEL2, fg=CLR_TEXT, anchor='e',
                               font=('Segoe UI', 12))
        self.result.pack(side='right')
        self.decimal = tk.Label(out, bg=CLR_PANEL2, fg=CLR_TEXT_DIM, anchor='e',
                                font=('Consolas', 10))
        self.decimal.pack(side='right', padx=(0, 10))
        self._images = []
        self.value = None

        e = self.entry
        e.bind("<KeyRelease>", lambda _e: app.schedule_update())
        e.bind("<Return>", lambda _e: app.new_row_after(self))
        e.bind("<KP_Enter>", lambda _e: app.new_row_after(self))
        e.bind("<Up>", lambda _e: app.move_focus(self, -1))
        e.bind("<Down>", lambda _e: app.move_focus(self, +1))
        e.bind("<BackSpace>", lambda _e: app.backspace(self))
        e.bind("<FocusIn>", lambda _e: app.set_active(self))
        for w in (self.frame, body, out, self.math, self.result):
            w.bind("<Button-1>", lambda _e: self.entry.focus_set())

    def set_active(self, active):
        colour = CLR_ACCENT if active else CLR_BORDER
        self.frame.configure(highlightbackground=colour, highlightcolor=colour)
        self.num.configure(bg=CLR_ACCENT if active else CLR_PANEL,
                           fg=CLR_TEXT_LIGHT if active else CLR_TEXT_DIM)

    def _image(self, latex, size, color):
        data = render_math(latex, size, color)
        if data is None:
            return None
        # Bound to this window's interpreter, not Tk's default root.
        img = tk.PhotoImage(master=self.frame, data=data)
        self._images.append(img)
        return img

    def show(self, res):
        self._images = []
        self.value = res['value']
        # Typeset preview of what was typed.
        img = self._image(res['latex'], 15, CLR_TEXT) if res['latex'] else None
        if img is not None:
            self.math.configure(image=img, text="")
        else:
            self.math.configure(image="", text="")
        if res['error']:
            self.result.configure(image="", text=res['error'], fg=CLR_HOVER,
                                  font=('Segoe UI', 10, 'italic'))
            self.decimal.configure(text="")
            return
        if res['value'] is None:
            self.result.configure(image="", text="")
            self.decimal.configure(text="")
            return
        tex = "= " + value_latex(res['value'])
        img = self._image(tex, 16, CLR_ACCENT)
        if img is not None:
            self.result.configure(image=img, text="")
        else:
            self.result.configure(image="", text="= " + format_number(res['value']),
                                  fg=CLR_ACCENT, font=('Segoe UI', 13, 'bold'))
        # A fraction also gets its decimal, for reading off a meter.
        if as_fraction(res['value']) is not None:
            self.decimal.configure(text="≈ " + format_number(res['value']))
        else:
            self.decimal.configure(text="")


class PICAQuickCalcApp:
    KEYPAD = [
        # (label, text to insert, cursor offset back from the end of it)
        ("a/b", "()/()", 4), ("x²", "^2", 0), ("xʸ", "^", 0), ("√", "sqrt()", 1),
        ("π", "pi", 0), ("e", "e", 0), ("(", "(", 0), (")", ")", 0),
        ("sin", "sin()", 1), ("cos", "cos()", 1), ("tan", "tan()", 1), ("|a|", "abs()", 1),
        ("ln", "ln()", 1), ("log", "log10()", 1), ("eˣ", "exp()", 1), ("ans", "ans", 0),
    ]

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Quick Calc")
        self.root.geometry("760x620")
        self.root.minsize(560, 420)
        self.root.configure(bg=CLR_APP)
        self.rows = []
        self.active = None
        self._pending = None
        self.degrees = tk.BooleanVar(value=False)
        self._build()
        self.add_row()
        self.rows[0].entry.focus_set()
        self.update_all()

    # ------------------------------------------------------------ layout
    def _build(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Calc.Vertical.TScrollbar', background=CLR_PANEL,
                        troughcolor=CLR_APP, bordercolor=CLR_APP, arrowcolor=CLR_TEXT_DIM)

        head = tk.Frame(self.root, bg=CLR_ACCENT)
        head.pack(fill='x')
        tk.Label(head, text="Quick Calc", bg=CLR_ACCENT, fg=CLR_TEXT_LIGHT,
                 font=('Segoe UI', 17, 'bold')).pack(side='left', padx=16, pady=10)
        modes = tk.Frame(head, bg=CLR_ACCENT)
        modes.pack(side='right', padx=16)
        for text, val in (("RAD", False), ("DEG", True)):
            tk.Radiobutton(modes, text=text, value=val, variable=self.degrees,
                           indicatoron=False, command=self.update_all,
                           font=('Segoe UI', 10, 'bold'), width=5, bd=0,
                           bg=CLR_ACCENT_SOFT, fg=CLR_TEXT, selectcolor=CLR_HOVER,
                           activebackground=CLR_PANEL, cursor='hand2',
                           relief='flat', offrelief='flat').pack(side='left', padx=1)
        self._mode_buttons = modes.winfo_children()
        self._paint_modes()

        # Keypad at the bottom, packed first so the sheet takes what is left.
        pad = tk.Frame(self.root, bg=CLR_APP)
        pad.pack(side='bottom', fill='x', padx=12, pady=(0, 12))
        for i, (label, text, back) in enumerate(self.KEYPAD):
            b = tk.Button(pad, text=label, font=('Segoe UI', 12), bg=CLR_PANEL2, fg=CLR_TEXT,
                          activebackground=CLR_HOVER, activeforeground=CLR_TEXT_LIGHT,
                          relief='flat', bd=0, highlightthickness=1,
                          highlightbackground=CLR_BORDER, cursor='hand2', pady=4,
                          command=lambda t=text, k=back: self.insert(t, k))
            b.grid(row=i // 8, column=i % 8, sticky='nsew', padx=2, pady=2)
            self._hover(b)
        for c in range(8):
            pad.columnconfigure(c, weight=1, uniform='k')

        bar = tk.Frame(self.root, bg=CLR_APP)
        bar.pack(side='bottom', fill='x', padx=12, pady=(4, 6))
        tk.Label(bar, text="Enter: new line   ·   ^ power   ·   2pi = 2·π   ·   "
                           "name = value   ·   f(x) = …",
                 bg=CLR_APP, fg=CLR_TEXT_DIM, font=('Segoe UI', 9)).pack(side='left')
        for text, cmd in (("Clear all", self.clear_all), ("+ Line", lambda: self.add_row(focus=True))):
            b = tk.Button(bar, text=text, command=cmd, font=('Segoe UI', 10, 'bold'),
                          bg=CLR_PANEL, fg=CLR_TEXT, relief='flat', bd=0,
                          highlightthickness=1, highlightbackground=CLR_BORDER,
                          activebackground=CLR_HOVER, activeforeground=CLR_TEXT_LIGHT,
                          padx=10, pady=2, cursor='hand2')
            b.pack(side='right', padx=(6, 0))
            self._hover(b)

        # The worksheet: a scrolling column of rows.
        wrap = tk.Frame(self.root, bg=CLR_APP)
        wrap.pack(fill='both', expand=True, padx=12, pady=(12, 0))
        self.canvas = tk.Canvas(wrap, bg=CLR_APP, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview,
                           style='Calc.Vertical.TScrollbar')
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.sheet = tk.Frame(self.canvas, bg=CLR_APP)
        self._sheet_id = self.canvas.create_window(0, 0, window=self.sheet, anchor='nw')
        self.sheet.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._sheet_id, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._wheel, add='+')
        for seq, step in (("<Button-4>", -1), ("<Button-5>", 1)):
            self.canvas.bind_all(seq, lambda e, k=step: self.canvas.yview_scroll(k, 'units')
                                 if str(e.widget).startswith(str(self.canvas)) else None,
                                 add='+')

    def _wheel(self, event):
        if str(event.widget).startswith(str(self.canvas)):
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')

    def _hover(self, b):
        b.bind("<Enter>", lambda _e: b.configure(bg=CLR_HOVER, fg=CLR_TEXT_LIGHT))
        b.bind("<Leave>", lambda _e, bg=b.cget('bg'): b.configure(bg=bg, fg=CLR_TEXT))

    def _paint_modes(self):
        for b in self._mode_buttons:
            on = (b.cget('text') == 'DEG') == self.degrees.get()
            b.configure(bg=CLR_HOVER if on else CLR_ACCENT_SOFT,
                        fg=CLR_TEXT_LIGHT if on else CLR_TEXT)

    # ------------------------------------------------------------ rows
    def _repack(self):
        for i, row in enumerate(self.rows, 1):
            row.frame.pack_forget()
            row.num.configure(text=str(i))
        for row in self.rows:
            row.frame.pack(fill='x', pady=(0, 6))

    def add_row(self, index=None, focus=False):
        row = _Row(self, self.sheet)
        if index is None:
            self.rows.append(row)
        else:
            self.rows.insert(index, row)
        self._repack()
        if focus:
            row.entry.focus_set()
            self.root.after_idle(lambda: self.canvas.yview_moveto(1.0)
                                 if row is self.rows[-1] else None)
        return row

    def new_row_after(self, row):
        self.add_row(self.rows.index(row) + 1, focus=True)
        self.update_all()
        return 'break'

    def delete_row(self, row):
        if len(self.rows) == 1:
            row.entry.delete(0, 'end')
        else:
            i = self.rows.index(row)
            self.rows.remove(row)
            row.frame.destroy()
            self._repack()
            self.rows[max(0, i - 1)].entry.focus_set()
        self.update_all()

    def backspace(self, row):
        if row.entry.get() == "" and len(self.rows) > 1:
            self.delete_row(row)
            target = self.active.entry if self.active else None
            if target is not None:
                target.icursor('end')
            return 'break'
        return None

    def move_focus(self, row, step):
        i = self.rows.index(row) + step
        if 0 <= i < len(self.rows):
            self.rows[i].entry.focus_set()
            self.rows[i].entry.icursor('end')
        return 'break'

    def set_active(self, row):
        self.active = row
        for r in self.rows:
            r.set_active(r is row)

    def clear_all(self):
        for row in self.rows[1:]:
            row.frame.destroy()
        self.rows = self.rows[:1]
        self.rows[0].entry.delete(0, 'end')
        self._repack()
        self.rows[0].entry.focus_set()
        self.update_all()

    def insert(self, text, back=0):
        row = self.active or self.rows[-1]
        e = row.entry
        e.insert('insert', text)
        if back:
            e.icursor(e.index('insert') - back)
        e.focus_set()
        self.update_all()

    # ------------------------------------------------------------ evaluate
    def schedule_update(self):
        if self._pending is not None:
            self.root.after_cancel(self._pending)
        self._pending = self.root.after(120, self.update_all)

    def update_all(self):
        self._pending = None
        self._paint_modes()
        results = evaluate_lines([r.entry.get() for r in self.rows],
                                 degrees=self.degrees.get())
        for row, res in zip(self.rows, results):
            row.show(res)


if __name__ == '__main__':
    root = tk.Tk()
    app = PICAQuickCalcApp(root)
    root.mainloop()
