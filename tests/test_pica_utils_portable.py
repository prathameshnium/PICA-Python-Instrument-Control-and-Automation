"""
Purpose: Guard the portable PICA Utils launcher (pica_utils_portable.py).

The launcher is frozen into a standalone .exe by the "Build Portable PICA
Utils" workflow, where a renamed class or a moved module shows up only as a
dead button in a shipped binary. These tests catch that here instead:

  * every key in the button registry resolves to a real GUI class,
  * the hardware-facing utilities stay OUT of the portable build (bundling
    pyvisa/pymeasure is what the portable build exists to avoid),
  * the spec file still names the PPMS namespace modules as hidden imports
    and still carries the logo assets.

No Tk root is created; only imports are exercised.
"""

import os
import re
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pica_utils_portable as pup  # noqa: E402

SPEC_FILE = os.path.join(project_root, 'PICA_Utils_Portable.spec')

# The eight utilities the portable build is meant to carry.
EXPECTED_KEYS = {
    'plotter', 'ppms-plotter', 'pe-plotter',
    'seq-visualizer', 'time-estimator',
    'quick-calc', 'time-utility', 'unit-converter',
}


def test_registry_matches_the_intended_tool_set():
    assert set(pup.TOOL_KEYS) == EXPECTED_KEYS
    assert len(pup.TOOL_KEYS) == len(set(pup.TOOL_KEYS)), "duplicate tool key"


def test_groups_and_labels_are_well_formed():
    keys_from_groups = [k for _, tools in pup.TOOL_GROUPS for k, _ in tools]
    assert keys_from_groups == pup.TOOL_KEYS
    for title, tools in pup.TOOL_GROUPS:
        assert title and tools, f"empty group: {title}"
        for key, label in tools:
            assert label.strip(), f"tool {key} has no button label"


@pytest.mark.parametrize('key', sorted(EXPECTED_KEYS))
def test_every_tool_key_loads_its_gui_class(key):
    cls = pup.load_app_class(key)
    assert isinstance(cls, type)
    # Every bundled tool is built as App(root) against a fresh Tk root.
    assert cls.__init__.__code__.co_argcount >= 2


def test_unknown_key_is_rejected():
    with pytest.raises(SystemExit):
        pup.load_app_class('no-such-tool')


def test_selftest_reports_all_tools(tmp_path):
    report = tmp_path / 'selftest.log'
    # This is exactly what the build workflow runs against the frozen exe.
    assert pup.selftest(str(report)) == 0
    text = report.read_text(encoding='utf-8')
    assert f"{len(EXPECTED_KEYS)}/{len(EXPECTED_KEYS)} tools importable" in text
    assert 'FAIL' not in text


def test_instrument_utilities_stay_out_of_the_portable_build():
    source = open(os.path.join(project_root, 'pica_utils_portable.py'),
                  encoding='utf-8').read()
    for module in ('SCPI_Console_GUI', 'GPIB_Instrument_Scanner_GUI',
                   'MD_Ratio_Calculator_GUI'):
        assert module not in source, f"{module} must not be bundled"


@pytest.mark.skipif(not os.path.exists(SPEC_FILE), reason="spec file absent")
def test_spec_pins_the_ppms_namespace_modules_and_assets():
    spec = open(SPEC_FILE, encoding='utf-8').read()
    # pica/PPMS has no __init__.py, so PyInstaller needs these spelled out.
    for module in ('pica.PPMS.PPMS_Plotter_GUI',
                   'pica.PPMS.PPMS_SeqVisualizer_GUI',
                   'pica.PPMS.PPMS_TimeEstimator_GUI'):
        assert module in spec, f"{module} missing from hiddenimports"
    # The tool scripts look for their logo at <module dir>/../assets/LOGO.
    assert re.search(r"pica/assets/LOGO'\s*,\s*'pica/assets/LOGO", spec)
    assert 'pyvisa' in spec, "the VISA stack should be excluded explicitly"


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
