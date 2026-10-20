"""
Purpose: Guard PICA Quick Calc (pica/utils/Quick_Calc_GUI.py): the worksheet
evaluation, the calculator syntax (^, implicit multiplication, n!), exact
fractions, the typesetting of each line, and that a line cannot run code.
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pica.utils import Quick_Calc_GUI as qc  # noqa: E402


def _values(lines, **kw):
    return [r['value'] for r in qc.evaluate_lines(lines, **kw)]


# ----------------------------------------------------------------- syntax
@pytest.mark.parametrize("typed, python", [
    ("x^2", "x**2"),
    ("2pi", "2*pi"),
    ("3(x+1)", "3*(x+1)"),
    ("(a+b)(a-b)", "(a+b)*(a-b)"),
    ("(a)x", "(a)*x"),
    ("I R", "I*R"),
    ("log10(x2)", "log10(x2)"),
    ("1e-3*4.7e3", "1e-3*4.7e3"),        # an exponent is not e * 3
    ("1e-3x", "1e-3*x"),
    ("10!", "factorial(10)"),
    ("√(2)", "sqrt(2)"),
])
def test_calculator_syntax_becomes_python(typed, python):
    assert qc.preprocess(typed) == python


def test_a_worksheet_carries_names_functions_and_ans():
    vals = _values(["R = 4.7e3", "I = 1.2e-6", "V = I R", "f(x) = x^2 + 1",
                    "f(3)", "ans * 2", "2x", "x = 5", "2x"])
    assert vals[0] == 4700.0
    assert vals[2] == pytest.approx(5.64e-3)
    assert vals[3] is None                 # defining f has no value
    assert vals[4] == 10
    assert vals[5] == 20
    assert vals[7] == 5 and vals[8] == 10


def test_errors_stay_on_their_line():
    res = qc.evaluate_lines(["1/0", "y + 1", "2 + 2"])
    assert res[0]['error'] == "division by zero"
    assert "'y' is not defined" in res[1]['error']
    assert res[2]['value'] == 4 and res[2]['error'] is None


def test_degrees_mode():
    assert _values(["sin(30)"], degrees=True)[0] == pytest.approx(0.5)
    assert _values(["sin(30)"])[0] == pytest.approx(-0.988031624)


@pytest.mark.parametrize("line", [
    "__import__('os')", "import os", "open('x')", "().__class__",
    "[1, 2]", "lambda: 1", "x if 1 else 2", "eval('1')", "sqrt.__code__",
])
def test_a_line_cannot_run_code(line):
    res = qc.evaluate_lines([line])[0]
    assert res['value'] is None and res['error']


def test_a_huge_power_is_refused_not_computed():
    assert qc.evaluate_lines(["9^99999999"])[0]['error']


@pytest.mark.parametrize("line", [
    "9^9999 * 9^9999", "(9^9999)^9999", "100000!", "factorial(10^9)",
])
def test_a_huge_whole_number_is_refused_not_computed(line):
    res = qc.evaluate_lines([line])[0]
    assert res['value'] is None and res['error'] == "result too large"


def test_ordinary_big_numbers_still_work():
    assert _values(["2^64", "20!", "2^1000 * 3"]) == [2 ** 64, 2432902008176640000,
                                                      2 ** 1000 * 3]


# -------------------------------------------------------------- fractions
@pytest.mark.parametrize("value, frac", [
    (0.75, (3, 4)), (1 / 3, (1, 3)), (-2 / 7, (-2, 7)), (0.1 + 0.2, (3, 10)),
])
def test_a_simple_value_shows_as_a_fraction(value, frac):
    f = qc.as_fraction(value)
    assert (f.numerator, f.denominator) == frac


@pytest.mark.parametrize("value", [2 ** 0.5, 3.0, 7, float('inf'), 3.14159])
def test_other_values_are_not_forced_into_fractions(value):
    assert qc.as_fraction(value) is None


def test_result_typesetting():
    assert qc.value_latex(0.75) == r"\frac{3}{4}"
    assert qc.value_latex(-0.5) == r"-\frac{1}{2}"
    assert qc.value_latex(1e-9) == r"1 \times 10^{-9}"
    assert qc.value_latex(2 ** 100).startswith(r"1.2676506 \times 10^{30}")


# ------------------------------------------------------------ typesetting
@pytest.mark.parametrize("line, latex", [
    ("3/4", r"\frac{3}{4}"),
    ("1/(1+1/x)", r"\frac{1}{1 + \frac{1}{x}}"),
    ("sqrt(2)/2", r"\frac{\sqrt{2}}{2}"),
    ("2x^2 + 1", r"2{x}^{2} + 1"),
    ("(1/2)^2", r"{\left(\frac{1}{2}\right)}^{2}"),
    ("a - (b + c)", r"a - \left(b + c\right)"),
    ("2pi", r"2\pi"),
    ("abs(x)", r"\left|x\right|"),
    ("log10(1000)", r"\log_{10}\left(1000\right)"),
    ("4.7e3", r"4.7 \times 10^{3}"),
    ("x_1 = 5", r"x_{1} = 5"),
    ("f(t) = t^2", r"f\left(t\right) = {t}^{2}"),
    ("V = I R", r"V = I\,R"),
    ("5!", r"5!"),
])
def test_each_line_is_typeset(line, latex):
    assert qc.evaluate_lines([line])[0]['latex'] == latex


def test_every_typeset_line_renders():
    lines = ["3/4 + 1/6", "1/(1+1/x)", "sqrt(2)/2", "(1/2)^2", "abs(-2/3)",
             "log10(1000)", "f(t) = t^2 + 1", "floor(7/2)", "cbrt(8)", "5!"]
    for res in qc.evaluate_lines(lines):
        assert qc.render_math(res['latex']) is not None, res['latex']


# --------------------------------------------------------------- window
# Run in a fresh interpreter: other tests in this suite replace
# sys.modules['tkinter'] with mocks while they run, so a real Tk window in
# this process would depend on test order.
_WINDOW_SCRIPT = r"""
import sys
import tkinter as tk
sys.path.insert(0, sys.argv[1])
from pica.utils import Quick_Calc_GUI as qc
try:
    root = tk.Tk()
except Exception as e:
    print("NO-TK", e)
    sys.exit(0)
root.withdraw()
app = qc.PICAQuickCalcApp(root)
app.rows[0].entry.insert(0, "1/3 + 1/6")
app.new_row_after(app.rows[0])
app.rows[1].entry.insert(0, "ans * 4")
app.update_all()
root.update()
assert abs(app.rows[0].value - 0.5) < 1e-12, app.rows[0].value
assert abs(app.rows[1].value - 2.0) < 1e-12, app.rows[1].value
# Both lines are typeset (an image, not the plain-text fallback).
assert str(app.rows[0].math.cget("image")), "line 1 not typeset"
assert str(app.rows[0].result.cget("image")), "result not typeset"
# The keypad's fraction key leaves the cursor in the numerator.
app.set_active(app.rows[1])
app.rows[1].entry.delete(0, "end")
app.insert("()/()", 4)
assert app.rows[1].entry.index("insert") == 1
app.clear_all()
assert len(app.rows) == 1 and app.rows[0].entry.get() == ""
root.destroy()
print("OK")
"""


@pytest.mark.real_windows
def test_the_window_builds_and_updates():
    import subprocess
    proc = subprocess.run([sys.executable, "-c", _WINDOW_SCRIPT, REPO_ROOT],
                          capture_output=True, text=True, timeout=120)
    if proc.stdout.startswith("NO-TK"):
        pytest.skip(f"no usable Tk: {proc.stdout.strip()}")
    assert proc.returncode == 0 and "OK" in proc.stdout, proc.stderr[-2000:]
