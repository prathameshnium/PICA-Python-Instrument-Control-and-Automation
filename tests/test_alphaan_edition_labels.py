"""Every Novocontrol Alpha-AN GUI says on screen which edition it is.

The BDS PC only has a 32-bit gpib-32.dll, so a VISA (64-bit) module there
cannot reach the Alpha. The window title and the header banner of each
GUI name the edition, as the two frequency-scan editions always did.

Source-level checks (the modules are parsed, not imported), so no Tk
window is ever created. Runnable as plain Python as well as under pytest.
"""

import ast
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOVO_DIR = os.path.join(REPO_ROOT, "pica", "novocontrol")

TITLE_64 = "- 64-bit (VISA)"
HEADER_64 = "[64-bit edition, VISA]"
TITLE_32 = "- 32-bit (NI-488.2 GPIB)"
HEADER_32 = "[32-bit edition, NI-488.2 GPIB]"

# Command-line tools have no window to label.
NOT_A_GUI = {"Alpha_GPIB_Diagnostic_32bit.py", "__init__.py"}


def _gui_files():
    return sorted(n for n in os.listdir(NOVO_DIR)
                  if n.endswith(".py") and n not in NOT_A_GUI)


def _labels(path):
    """(window-title sources, text= sources) of one module."""
    src = open(path, encoding="utf-8").read()
    titles, texts = [], []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "title"
                and ast.get_source_segment(src, func.value) == "self.root"):
            titles += [ast.get_source_segment(src, a) for a in node.args]
        texts += [ast.get_source_segment(src, k.value)
                  for k in node.keywords if k.arg == "text"]
    return titles, texts


def test_every_alpha_gui_names_its_edition_in_title_and_header():
    files = _gui_files()
    assert len(files) == 11, files
    for name in files:
        title, header = ((TITLE_32, HEADER_32) if "32bit" in name
                         else (TITLE_64, HEADER_64))
        titles, texts = _labels(os.path.join(NOVO_DIR, name))
        assert len(titles) == 1, (name, titles)
        assert title in titles[0], (name, titles[0])
        assert any(header in t for t in texts), name


def test_no_gui_claims_the_other_edition():
    for name in _gui_files():
        wrong = HEADER_64 if "32bit" in name else HEADER_32
        titles, texts = _labels(os.path.join(NOVO_DIR, name))
        assert not any(wrong in t for t in titles + texts), name


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
