"""
Lake Shore Model 340 siblings of the Model 350 measurement modules.

Every module that drove or read a Lakeshore 350 has an L340 twin (3 Sep 2026).
This suite checks, for each twin, that it exists, compiles, is registered in
the CLI / launcher / v2 catalogue, carries the 340 command forms and none of
the 350-only ones, and that the L350 original was left untouched.
Runnable as plain python too.
"""
import importlib
import os
import py_compile
import re
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# (L350 original, L340 twin, kind)  paths relative to pica/
PAIRS = [
    ("keithley/delta_mode/Delta_RT_K6221_K2182_L350_Sensing_GUI.py",
     "keithley/delta_mode/Delta_RT_K6221_K2182_L340_Sensing_GUI.py", "sensing"),
    ("keithley/delta_mode/Delta_RT_K6221_K2182_L350_T_Control_GUI.py",
     "keithley/delta_mode/Delta_RT_K6221_K2182_L340_T_Control_GUI.py", "control"),
    ("keithley/k2400/RT_K2400_L350_T_Sensing_GUI.py",
     "keithley/k2400/RT_K2400_L340_T_Sensing_GUI.py", "sensing"),
    ("keithley/k2400/RT_K2400_L350_T_Control_GUI.py",
     "keithley/k2400/RT_K2400_L340_T_Control_GUI.py", "control"),
    ("keithley/k2400_2182/RT_K2400_K2182_L350_T_Sensing_GUI.py",
     "keithley/k2400_2182/RT_K2400_K2182_L340_T_Sensing_GUI.py", "sensing"),
    ("keithley/k2400_2182/RT_K2400_K2182_T_Control_GUI.py",
     "keithley/k2400_2182/RT_K2400_K2182_L340_T_Control_GUI.py", "control"),
    ("keithley/k6517b/High_Resistance/RT_K6517B_L350_T_Sensing_GUI.py",
     "keithley/k6517b/High_Resistance/RT_K6517B_L340_T_Sensing_GUI.py", "sensing"),
    ("keithley/k6517b/High_Resistance/RT_K6517B_L350_T_Control_GUI.py",
     "keithley/k6517b/High_Resistance/RT_K6517B_L340_T_Control_GUI.py", "control"),
    ("keithley/k6517b/Pyroelectricity/Pyroelectric_K6517B_L350_GUI.py",
     "keithley/k6517b/Pyroelectricity/Pyroelectric_K6517B_L340_GUI.py", "control"),
    ("keithley/k6221_k197a/RT_AC_K6221_K197A_L350_T_Sensing_GUI.py",
     "keithley/k6221_k197a/RT_AC_K6221_K197A_L340_T_Sensing_GUI.py", "sensing"),
    ("keithley/k6221_k197a/RT_AC_K6221_K197A_L350_T_Control_GUI.py",
     "keithley/k6221_k197a/RT_AC_K6221_K197A_L340_T_Control_GUI.py", "control"),
    ("lockin/sr830/RT_AC_K6221_SR830_L350_T_Sensing_GUI.py",
     "lockin/sr830/RT_AC_K6221_SR830_L340_T_Sensing_GUI.py", "sensing"),
    ("lockin/sr830/RT_AC_K6221_SR830_L350_T_Control_GUI.py",
     "lockin/sr830/RT_AC_K6221_SR830_L340_T_Control_GUI.py", "control"),
    ("keysight/Temprature_Scan_Passive_E4980A_GUI.py",
     "keysight/Temprature_Scan_Passive_L340_E4980A_GUI.py", "sensing"),
    ("keysight/Temprature_Scan_E4980A_GUI.py",
     "keysight/Temprature_Scan_L340_E4980A_GUI.py", "control"),
    ("keysight/Step_Frequency_Scan_E4980A_GUI.py",
     "keysight/Step_Frequency_Scan_L340_E4980A_GUI.py", "control"),
    ("keysight/PPMS_Sync_Freq_Scan_E4980A_GUI.py",
     "keysight/PPMS_Sync_Freq_Scan_L340_E4980A_GUI.py", "sensing"),
    ("keysight/PPMS_Dielectric_Master_Tscan_Fscan_E4980A_GUI.py",
     "keysight/PPMS_Dielectric_Master_Tscan_Fscan_L340_E4980A_GUI.py", "sensing"),
    ("lakeshore/Sensor_Curve_Viewer_L350_GUI.py",
     "lakeshore/Sensor_Curve_Viewer_L340_GUI.py", "sensing"),
    ("lakeshore/T_Control_L350_Step_GUI.py",
     "lakeshore/T_Control_L340_Step_GUI.py", "control"),
    ("lakeshore/T_Control_L350_Step_GUI_advanced.py",
     "lakeshore/T_Control_L340_Step_GUI_advanced.py", "control"),
    ("lakeshore/T_Sensing_L350_GUI.py",
     "lakeshore/T_Sensing_L340_GUI.py", "sensing"),
    ("lakeshore/T_Control_L350_RangeControl_GUI.py",
     "lakeshore/T_Control_L340_RangeControl_GUI.py", "control"),
    ("lakeshore/T_Control_L350_DirectControl_GUI.py",
     "lakeshore/T_Control_L340_DirectControl_GUI.py", "control"),
]

# Launcher keys every twin must be reachable under (pica/main.py SCRIPT_PATHS).
KEYS = {
    "keithley/delta_mode/Delta_RT_K6221_K2182_L340_Sensing_GUI.py": "Delta Mode R-T (T_Sensing, L340)",
    "keithley/delta_mode/Delta_RT_K6221_K2182_L340_T_Control_GUI.py": "Delta Mode R-T (L340)",
    "keithley/k2400/RT_K2400_L340_T_Sensing_GUI.py": "K2400 R-T (T_Sensing, L340)",
    "keithley/k2400/RT_K2400_L340_T_Control_GUI.py": "K2400 R-T (L340)",
    "keithley/k2400_2182/RT_K2400_K2182_L340_T_Sensing_GUI.py": "K2400_2182 R-T (T_Sensing, L340)",
    "keithley/k2400_2182/RT_K2400_K2182_L340_T_Control_GUI.py": "K2400_2182 R-T (L340)",
    "keithley/k6517b/High_Resistance/RT_K6517B_L340_T_Sensing_GUI.py": "K6517B R-T (T_Sensing, L340)",
    "keithley/k6517b/High_Resistance/RT_K6517B_L340_T_Control_GUI.py": "K6517B R-T (L340)",
    "keithley/k6517b/Pyroelectricity/Pyroelectric_K6517B_L340_GUI.py": "Pyroelectric Current (L340)",
    "keithley/k6221_k197a/RT_AC_K6221_K197A_L340_T_Sensing_GUI.py": "K197A AC R-T (T_Sensing, L340)",
    "keithley/k6221_k197a/RT_AC_K6221_K197A_L340_T_Control_GUI.py": "K197A AC R-T (L340)",
    "lockin/sr830/RT_AC_K6221_SR830_L340_T_Sensing_GUI.py": "SR830 AC R-T (T_Sensing, L340)",
    "lockin/sr830/RT_AC_K6221_SR830_L340_T_Control_GUI.py": "SR830 AC R-T (L340)",
    "keysight/Temprature_Scan_Passive_L340_E4980A_GUI.py": "LCR Temp. Scan (T_Sensing, L340)",
    "keysight/Temprature_Scan_L340_E4980A_GUI.py": "LCR Temp. Scan (T_Control, L340)",
    "keysight/Step_Frequency_Scan_L340_E4980A_GUI.py": "LCR Temp. Step Freq. Scan (T_Control, L340)",
    "keysight/PPMS_Sync_Freq_Scan_L340_E4980A_GUI.py": "PPMS Sync Freq. Scan (L340)",
    "keysight/PPMS_Dielectric_Master_Tscan_Fscan_L340_E4980A_GUI.py": "PPMS Dielectric Master (L340)",
    "lakeshore/Sensor_Curve_Viewer_L340_GUI.py": "Lakeshore 340 Sensor Curve Viewer",
    "lakeshore/T_Control_L340_Step_GUI.py": "Lakeshore 340 Step Control",
    "lakeshore/T_Control_L340_Step_GUI_advanced.py": "Lakeshore 340 Step Control (Advanced)",
    "lakeshore/T_Sensing_L340_GUI.py": "Lakeshore 340 Temp Monitor",
    "lakeshore/T_Control_L340_RangeControl_GUI.py": "Lakeshore 340 Temp Control",
    "lakeshore/T_Control_L340_DirectControl_GUI.py": "Lakeshore 340 Direct Control",
}

# Model 350 command forms that must not appear in executable code of a 340 file.
FORBIDDEN = [
    re.compile(r"HTR\?\s*[12]\b"),          # HTR? 1
    re.compile(r"RANGE\s+[12]\s*,"),          # RANGE 1,n
    re.compile(r"RANGE\?\s*[12]\b"),         # RANGE? 1
    re.compile(r"HTRST\?\s*[12]\b"),
    re.compile(r"OUTMODE(\?|\s*[0-9{]|\s+[0-9{])"),   # the command, not prose about it
    re.compile(r"TLIMIT(\?|\s*[A-D{]|\s+[A-D{])"),
    re.compile(r"\bDFLD\b"),
    re.compile(r"\bINNAME\b"),
    re.compile(r"\bHTRSET\b"),
    re.compile(r"MODEL\s?350"),
    re.compile(r"""['"]1[25]['"]\s+in\s"""),   # '12' in r / '15' in r address guesses
]

# Triple-quoted strings: module and function docstrings legitimately quote
# the 350 forms they replaced, so they are blanked before the scan.
_TRIPLE_DQ = re.compile('"""[\\s\\S]*?"""')
_TRIPLE_SQ = re.compile("'''[\\s\\S]*?'''")


def _code_lines(source):
    """Executable lines only: docstrings and comment lines are dropped."""
    stripped = _TRIPLE_SQ.sub("''", _TRIPLE_DQ.sub('""', source))
    out = []
    for line in stripped.splitlines():
        if line.strip().startswith("#"):
            out.append("")
            continue
        out.append(line.split("  # ")[0])
    return out


_LAKESHORE_LINE = re.compile(r"(?i)lakeshore|lake_shore|_ls\b|\bls_|\bls\.|l340|\b340\b")


def _pica_path(rel):
    return os.path.join(REPO_ROOT, "pica", rel)


@pytest.mark.parametrize("l350,l340,kind", PAIRS, ids=[p[1] for p in PAIRS])
def test_twin_exists_and_compiles(l350, l340, kind):
    path = _pica_path(l340)
    assert os.path.exists(path), l340
    py_compile.compile(path, doraise=True)


@pytest.mark.parametrize("l350,l340,kind", PAIRS, ids=[p[1] for p in PAIRS])
def test_twin_uses_340_forms_only(l350, l340, kind):
    src = open(_pica_path(l340), encoding="utf-8").read()
    up = src.upper().replace(" ", "")
    assert "MODEL340" in up, "no MODEL340 identity check"
    assert "::19::" in src, "no lab-address hint ::19::"
    for i, line in enumerate(_code_lines(src), start=1):
        for rx in FORBIDDEN:
            assert not rx.search(line), f"{l340}:{i}: 350-only form {rx.pattern!r}: {line.strip()}"
        # *RST is fine for the Keithley / LCR meter, never for the 340 (it
        # resets loop, setpoint and ramp). The Direct Control window keeps a
        # double-confirmed reset button, so it is exempt.
        if "*RST" in line and "DirectControl" not in l340:
            assert not _LAKESHORE_LINE.search(line),                 f"{l340}:{i}: *RST sent to the Lake Shore: {line.strip()}"
    if kind == "control":
        assert re.search(r"CSET\s+(1|{[a-z_]+})\s*,", src), "control twin never enables Loop 1 (CSET)"
        assert re.search(r"CMODE\s+(1|{[a-z_]+})\s*,\s*(1|{[a-z_]+})", src), "control twin never pins Manual PID (CMODE 1,1)"
        assert "CLIMIT?" in src, "control twin never reads the loop limits"


@pytest.mark.parametrize("l350,l340,kind", PAIRS, ids=[p[0] for p in PAIRS])
def test_original_350_file_is_untouched(l350, l340, kind):
    out = subprocess.run(
        ["git", "diff", "--stat", "HEAD", "--", os.path.join("pica", l350)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"{l350} was modified:\n{out.stdout}"


def test_every_twin_is_registered_in_the_launchers():
    main = importlib.import_module("pica.main")
    paths = main.PICALauncherApp.SCRIPT_PATHS
    v2 = importlib.import_module("pica.main_v2")
    v2_keys = {key for cat in v2.CATALOG for _l, key, _f in cat["modules"]}
    for rel, key in KEYS.items():
        assert key in paths, key
        assert os.path.normpath(paths[key]).endswith(os.path.normpath(rel)), (key, paths[key])
        assert key in v2_keys, f"{key} missing from the v2 catalogue"


def test_cli_lists_the_twins_next_to_their_350_entries():
    cli = importlib.import_module("pica.cli")
    mods = set(cli.ALL_GUI_MODULES)
    for m in list(mods):
        if "L350" in m and "lakeshore.T_Control_L350_RangeControl" not in m \
                and "DirectControl" not in m and "Step_GUI_advanced" not in m:
            twin = m.replace("L350", "L340")
            assert twin in mods, f"CLI lists {m} but not {twin}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
