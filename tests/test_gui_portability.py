"""Repo-wide checks on GUI calls that only work on one operating system.

The lab runs Windows, so a Windows-only Tk call is invisible here and fails
only in CI, on the xvfb display, in a module nobody touched. This pins the
one that has actually broken a build: root.state('zoomed') is a Windows
window state and X11 answers

    _tkinter.TclError: bad argument "zoomed": must be normal, iconic, or
    withdrawn

which aborts the constructor before a single widget is made. Not being
maximised is not a reason for a panel to refuse to open, so every call site
must be wrapped in try/except TclError.

Runnable as plain Python as well as under pytest.
"""

import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_ROOT = os.path.join(REPO_ROOT, "pica")

# build/ is a copy of an older tree left by the packaging step, not source.
SKIP_DIRS = {"build", "dist", "__pycache__", ".git", "Untracked_Stuff"}


def _source_files():
    for dirpath, dirnames, filenames in os.walk(PACKAGE_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _is_zoomed_call(node):
    """True for `<anything>.state('zoomed')`."""
    if not isinstance(node, ast.Call):
        return False
    if getattr(node.func, "attr", "") != "state":
        return False
    return any(isinstance(arg, ast.Constant) and arg.value == "zoomed"
               for arg in node.args)


def _catches_tcl_error(handler):
    """True if an `except` clause would catch a TclError.

    Accepts `except tk.TclError`, `except TclError`, a tuple containing
    either, and a bare `except:` / `except Exception:`.
    """
    if handler.type is None:
        return True
    candidates = (handler.type.elts
                  if isinstance(handler.type, ast.Tuple) else [handler.type])
    for candidate in candidates:
        name = getattr(candidate, "attr", None) or getattr(candidate, "id", "")
        if name in ("TclError", "Exception", "BaseException"):
            return True
    return False


def _guarded_zoomed_calls(tree):
    """Every zoomed call sitting in a try body with a TclError handler."""
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(_catches_tcl_error(h) for h in node.handlers):
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                if _is_zoomed_call(inner):
                    guarded.add((inner.lineno, inner.col_offset))
    return guarded


def test_every_zoomed_window_call_is_guarded_against_tclerror():
    offenders = []
    for path in _source_files():
        tree = ast.parse(open(path, encoding="utf-8").read())
        guarded = _guarded_zoomed_calls(tree)
        for node in ast.walk(tree):
            if _is_zoomed_call(node) and \
                    (node.lineno, node.col_offset) not in guarded:
                offenders.append(
                    "%s:%d" % (os.path.relpath(path, REPO_ROOT), node.lineno))
    assert not offenders, (
        "state('zoomed') is Windows only and raises TclError on X11. Wrap "
        "these in try/except tk.TclError:\n  " + "\n  ".join(offenders))


def test_the_check_recognises_a_guarded_and_an_unguarded_call():
    """The guard detector itself, so a silent pass cannot go unnoticed."""
    unguarded = ast.parse("self.root.state('zoomed')\n")
    assert _guarded_zoomed_calls(unguarded) == set()
    assert any(_is_zoomed_call(n) for n in ast.walk(unguarded))

    guarded = ast.parse(
        "try:\n"
        "    self.root.state('zoomed')\n"
        "except tk.TclError:\n"
        "    pass\n")
    assert len(_guarded_zoomed_calls(guarded)) == 1

    # An except clause for something else does not count as a guard.
    wrong_handler = ast.parse(
        "try:\n"
        "    self.root.state('zoomed')\n"
        "except ValueError:\n"
        "    pass\n")
    assert _guarded_zoomed_calls(wrong_handler) == set()

    # A call in the except body is not protected by its own try.
    in_handler = ast.parse(
        "try:\n"
        "    pass\n"
        "except tk.TclError:\n"
        "    self.root.state('zoomed')\n")
    assert _guarded_zoomed_calls(in_handler) == set()


def test_the_walk_actually_reaches_the_gui_modules():
    """A path typo would make the check above pass by finding nothing."""
    files = list(_source_files())
    assert len(files) > 20, len(files)
    names = {os.path.basename(path) for path in files}
    assert "Comms_SR830_GUI.py" in names
    assert "T_Sensing_CC34_GUI.py" in names
    # And there is at least one real zoomed call in the tree to find.
    found = 0
    for path in files:
        tree = ast.parse(open(path, encoding="utf-8").read())
        found += sum(1 for node in ast.walk(tree) if _is_zoomed_call(node))
    assert found > 0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
