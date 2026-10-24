"""Terminal -> PICA console copy (TerminalTee / install_terminal_log).

Every Alpha-AN GUI carries its own copy of the block, so each copy is
checked. No Tk window: the GUI is a stand-in with a fake root whose after()
callbacks are run by hand.

Runs under pytest or as a plain script: python tests/test_alphaan_terminal_log.py
"""

import ast
import io
import os
import queue
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import NOVO_DIR, load_port  # noqa: E402

# The read-only diagnostics tool has no PICA console, so it carries no block.
GUI_FILES = sorted(f for f in os.listdir(NOVO_DIR)
                   if "AlphaAN" in f and f.endswith("GUI.py")
                   and not f.startswith("Diagnostics_"))
BLOCK_NAMES = ("TERMINAL_LOG_POLL_MS", "TERMINAL_LOG_MAX_LINES_PER_POLL",
               "TerminalTee", "install_terminal_log")


class FakeRoot:
    def __init__(self):
        self.pending = {}
        self.bindings = []
        self._next = 0

    def after(self, ms, func):
        self._next += 1
        self.pending[self._next] = func
        return self._next

    def after_cancel(self, after_id):
        self.pending.pop(after_id, None)

    def bind(self, event, func, add=None):
        self.bindings.append((event, func))

    def run_pending(self):
        jobs, self.pending = self.pending, {}
        for func in jobs.values():
            func()

    def destroy(self, widget=None):
        event = type("E", (), {"widget": widget or self})()
        for name, func in self.bindings:
            if name == "<Destroy>":
                func(event)


class FakeGui:
    def __init__(self, echo=False):
        self.root = FakeRoot()
        self.lines = []
        self.echo = echo

    def log(self, message):
        self.lines.append(message)
        if self.echo:
            print("log() itself printed this")  # must not loop back


@pytest.fixture(params=GUI_FILES)
def mod(request):
    return load_port(request.param)[0]


def test_tee_forwards_and_queues_complete_lines(mod):
    out, q = io.StringIO(), queue.Queue()
    tee = mod.TerminalTee(out, q)
    tee.write("*RST failed; escalating")
    assert q.empty()                       # no newline yet
    tee.write(" to RSTH.\r\nsecond\n\n")
    assert out.getvalue() == "*RST failed; escalating to RSTH.\r\nsecond\n\n"
    assert [q.get_nowait() for _ in range(q.qsize())] == [
        "*RST failed; escalating to RSTH.", "second"]


def test_tee_without_original_stream(mod):
    # pythonw: sys.stdout is None
    q = queue.Queue()
    tee = mod.TerminalTee(None, q)
    tee.write("hello\n")
    tee.flush()
    assert q.get_nowait() == "hello"


def test_install_copies_worker_prints_and_restores(mod):
    saved = (sys.stdout, sys.stderr)
    gui = FakeGui(echo=True)
    mod.install_terminal_log(gui)
    try:
        t = threading.Thread(target=lambda: print("  Warning: RSTH also "
                                                  "failed: timeout"))
        t.start()
        t.join()
        sys.stderr.write("Traceback line\n")
        gui.root.run_pending()
        gui.root.run_pending()             # a second drain: no echo of log()
        assert gui.lines == ["[terminal]   Warning: RSTH also failed: timeout",
                             "[terminal] Traceback line"]
        assert len(gui.root.pending) == 1  # poll re-armed
        gui.root.destroy(widget=object())  # a child widget: ignored
        assert sys.stdout is not saved[0]
    finally:
        gui.root.destroy()
    assert (sys.stdout, sys.stderr) == saved
    assert gui.root.pending == {}          # after chain cancelled


def test_drain_is_bounded_per_poll(mod):
    gui = FakeGui()
    mod.install_terminal_log(gui)
    try:
        for i in range(mod.TERMINAL_LOG_MAX_LINES_PER_POLL + 5):
            print(i)
        gui.root.run_pending()
        assert len(gui.lines) == mod.TERMINAL_LOG_MAX_LINES_PER_POLL
        gui.root.run_pending()
        assert len(gui.lines) == mod.TERMINAL_LOG_MAX_LINES_PER_POLL + 5
    finally:
        gui.root.destroy()


def _block_sources(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0],
                                                       ast.Name):
            name = node.targets[0].id
        if name in BLOCK_NAMES:
            out[name] = ast.get_source_segment(src, node)
    return out, tree


def test_block_identical_and_installed_in_every_gui():
    paths = [os.path.join(NOVO_DIR, f) for f in GUI_FILES]
    reference, _ = _block_sources(paths[0])
    assert set(reference) == set(BLOCK_NAMES)
    for path in paths:
        block, tree = _block_sources(path)
        assert block == reference, path
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "__init__"
                 and "install_terminal_log(self)" in ast.unparse(n)]
        assert len(calls) == 1, path


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
