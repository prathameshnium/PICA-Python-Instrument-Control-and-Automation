"""Alpha-AN modules: the impedance -> C, G, eps' ... conversion formulas.

The analyzer reports the series impedance Z = Z' + jZ'' (R + jX). Every
quantity in the data files is derived from it. These tests build Z from
known circuits (parallel RC, series RC, an inductor) with plain complex
arithmetic and check that each module recovers the circuit values, so the
formulas are checked against physics rather than against themselves:

    Y = 1/Z = G + jB         G = 1/Rp, B = w*Cp
    eps* = Y / (j*w*C0)      eps' = Cp/C0, eps'' = G/(w*C0)
    M* = 1/eps*              sigma' = G*d/A,  sigma'' = -w*eps0*(eps' - 1)
    Zp' = 1/G, Zp'' = 1/B    (WinDETA's parallel-equivalent impedance)

Every module keeps its own copy of the formulas (PICA modules are
self-contained), so every copy is checked and the copies must agree.
No hardware, VISA backend or Tk window is needed.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from alphaan_port_test_support import load_port  # noqa: E402

EPS0 = 8.8541878128e-12           # F/m
FREQS = (20.0, 1e3, 1e5, 1e7)     # spans the Alpha-AN range used here

# Round plate, 10 mm diameter, 1 mm thick
D_MM, T_MM = 10.0, 1.0
AREA_M2 = math.pi * (D_MM * 1e-3 / 2) ** 2
C0 = EPS0 * AREA_M2 / (T_MM * 1e-3)

DIELECTRIC_FILES = [  # modules with impedance_to_dielectric (WinDETA columns)
    ("Frequency_Scan_AlphaAN_GUI.py", ""),
    ("Frequency_Scan_AlphaAN_32bit_GUI.py", ""),
    ("Step_Frequency_Scan_AlphaAN_GUI.py", ""),
    ("Step_Frequency_Scan_L340_AlphaAN_GUI.py", ""),
    ("AlphaAN_FreqScan_Instrument_Control.py", "Instrument_Control"),
]
DERIVED_FILES = [  # modules with the 18 LCR-style columns (Q, D, G, B, Cp ...)
    "Temprature_Scan_AlphaAN_GUI.py",
    "Temprature_Scan_L340_AlphaAN_GUI.py",
    "Temprature_Scan_Passive_AlphaAN_GUI.py",
    "Temprature_Scan_Passive_L340_AlphaAN_GUI.py",
    "Temprature_Scan_Passive_CC34_AlphaAN_GUI.py",
    "Step_Frequency_Scan_AlphaAN_GUI.py",
    "Step_Frequency_Scan_L340_AlphaAN_GUI.py",
    "CV_AlphaAN_GUI.py",
]
DERIVED_ORDER = ("Q", "D", "G", "B", "Cp", "Lp", "Cs", "Ls", "|Z|", "theta",
                 "chi", "Rs", "theta_deg", "Rp", "1/|Z|", "omega", "Cp''", "Cs''")

_cache = {}


def _module(file_name, subdir=""):
    if (file_name, subdir) not in _cache:
        _cache[(file_name, subdir)] = load_port(file_name, subdir)[0]
    return _cache[(file_name, subdir)]


def _derive(file_name):
    """The module's 18-value conversion, wherever that module keeps it."""
    mod = _module(file_name)
    if hasattr(mod, "derived_columns"):
        return mod.derived_columns
    for obj in vars(mod).values():
        if isinstance(obj, type) and hasattr(obj, "calculate_impedance_parameters"):
            return lambda f, R, X, cls=obj: cls.calculate_impedance_parameters(None, f, R, X)
    raise AssertionError(f"{file_name}: no impedance conversion found")


def _close(a, b, rel=1e-9, abs_=0.0):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def _parallel_rc(f, rp, cp):
    """Series R, X of a resistor rp in parallel with a capacitor cp."""
    z = 1.0 / complex(1.0 / rp, 2 * math.pi * f * cp)
    return z.real, z.imag


# --- geometry ---------------------------------------------------------------

def test_c0_is_eps0_times_area_over_thickness_for_both_geometry_inputs():
    for name, sub in DIELECTRIC_FILES[:2]:
        mod = _module(name, sub)
        assert _close(mod.compute_c0(D_MM, T_MM), C0), name
        # area is entered in mm^2, as in WinDETA
        assert _close(mod.compute_c0_from_area(AREA_M2 * 1e6, T_MM), C0), name
        # 1 cm^2 at 1 mm is the textbook 0.885 pF
        assert _close(mod.compute_c0_from_area(100.0, 1.0), 8.8541878128e-13), name


# --- impedance -> LCR-style columns -------------------------------------------

def test_a_parallel_rc_is_recovered_as_cp_rp_g_b_and_d():
    rp, cp = 1e6, 100e-12
    for name in DERIVED_FILES:
        derive = _derive(name)
        for f in FREQS:
            w = 2 * math.pi * f
            R, X = _parallel_rc(f, rp, cp)
            v = dict(zip(DERIVED_ORDER, derive(f, R, X)))
            assert _close(v["Cp"], cp), (name, f)
            assert _close(v["Rp"], rp), (name, f)
            assert _close(v["G"], 1 / rp), (name, f)
            assert _close(v["B"], w * cp), (name, f)
            assert _close(v["D"], 1 / (w * rp * cp)), (name, f)
            assert _close(v["Q"], w * rp * cp), (name, f)
            assert _close(v["Cp''"], 1 / (w * rp)), (name, f)  # G / w
            assert _close(v["omega"], w), (name, f)


def test_impedance_magnitude_phase_and_series_values_follow_from_z():
    for name in DERIVED_FILES:
        derive = _derive(name)
        f, R, X = 1e3, 300.0, -400.0
        v = dict(zip(DERIVED_ORDER, derive(f, R, X)))
        assert _close(v["|Z|"], 500.0) and _close(v["1/|Z|"], 1 / 500.0), name
        assert _close(v["theta"], math.atan2(-400.0, 300.0)), name
        assert _close(v["theta_deg"], math.degrees(math.atan2(-400.0, 300.0))), name
        assert v["Rs"] == R and v["chi"] == X, name


def test_a_series_rc_is_recovered_as_cs_and_rs():
    rs, cs = 50.0, 10e-9
    for name in DERIVED_FILES:
        derive = _derive(name)
        for f in FREQS:
            w = 2 * math.pi * f
            X = -1 / (w * cs)
            v = dict(zip(DERIVED_ORDER, derive(f, rs, X)))
            assert _close(v["Cs"], cs), (name, f)
            assert v["Rs"] == rs, (name, f)
            # Cs'' = D * Cs, with D = G/B = Rs/|X| for a series RC
            assert _close(v["Cs''"], (rs / abs(X)) * cs), (name, f)


def test_an_inductor_is_recovered_as_ls_and_lp():
    L = 1e-3
    for name in DERIVED_FILES:
        derive = _derive(name)
        f = 1e4
        w = 2 * math.pi * f
        v = dict(zip(DERIVED_ORDER, derive(f, 1e-3, w * L)))  # tiny R, X = wL
        assert _close(v["Ls"], L), name
        assert _close(v["Lp"], L, rel=1e-6), name
        assert v["Cp"] < 0, name  # inductive -> negative capacitance


# --- impedance -> WinDETA dielectric columns --------------------------------

def test_a_parallel_rc_gives_the_textbook_permittivity_modulus_and_conductivity():
    rp, cp = 1e6, 100e-12
    for name, sub in DIELECTRIC_FILES:
        to_eps = _module(name, sub).impedance_to_dielectric
        for f in FREQS:
            w = 2 * math.pi * f
            R, X = _parallel_rc(f, rp, cp)
            fo, e1, e2, m1, m2, zp1, zp2, s1, s2 = to_eps(R, X, f, C0)
            assert fo == f, (name, f)
            assert _close(e1, cp / C0), (name, f)
            assert _close(e2, 1 / (w * rp * C0)), (name, f)
            eps = complex(e1, -e2)                       # eps* = eps' - j eps''
            assert _close(m1, (1 / eps).real), (name, f)  # M* = 1/eps* = M' + jM''
            assert _close(m2, (1 / eps).imag), (name, f)
            # sigma' = G * d / A, in S/cm
            assert _close(s1, (1 / rp) * (T_MM * 1e-3) / AREA_M2 / 100), (name, f)
            # sigma'' = -w eps0 (eps' - 1), eps0 in F/cm (vacuum subtracted)
            assert _close(s2, -w * EPS0 / 100 * (e1 - 1)), (name, f)
            assert _close(zp1, rp), (name, f)            # Zp' = 1/G
            assert _close(zp2, 1 / (w * cp)), (name, f)  # Zp'' = 1/B, positive


def test_a_lossless_capacitor_has_no_loss_and_eps_equal_to_c_over_c0():
    c = 50e-12
    for name, sub in DIELECTRIC_FILES:
        to_eps = _module(name, sub).impedance_to_dielectric
        f = 1e3
        _, e1, e2, *_rest = to_eps(0.0, -1 / (2 * math.pi * f * c), f, C0)
        assert _close(e1, c / C0) and e2 == 0.0, name


def test_permittivity_and_capacitance_columns_describe_the_same_sample():
    # Cp from the LCR-style columns equals eps' * C0 from the WinDETA columns
    to_eps = _module(*DIELECTRIC_FILES[0]).impedance_to_dielectric
    derive = _derive(DERIVED_FILES[0])
    for f in FREQS:
        R, X = _parallel_rc(f, 2e5, 33e-12)
        v = dict(zip(DERIVED_ORDER, derive(f, R, X)))
        _, e1, e2, *_rest = to_eps(R, X, f, C0)
        assert _close(v["Cp"], e1 * C0), f
        assert _close(v["Cp''"], e2 * C0), f
        assert _close(v["D"], e2 / e1), f  # tan(delta) = eps''/eps'


def test_every_module_copy_of_a_formula_gives_identical_results():
    points = [(f, *_parallel_rc(f, 4.7e5, 220e-12)) for f in FREQS] + [(1e3, 300.0, -400.0)]
    ref = [tuple(_derive(DERIVED_FILES[0])(f, R, X)) for f, R, X in points]
    for name in DERIVED_FILES[1:]:
        got = [tuple(_derive(name)(f, R, X)) for f, R, X in points]
        assert got == ref, name
    to_eps0 = _module(*DIELECTRIC_FILES[0]).impedance_to_dielectric
    ref = [to_eps0(R, X, f, C0) for f, R, X in points]
    for name, sub in DIELECTRIC_FILES[1:]:
        to_eps = _module(name, sub).impedance_to_dielectric
        assert [to_eps(R, X, f, C0) for f, R, X in points] == ref, name


def test_every_column_of_the_pica_and_windeta_files_holds_the_right_quantity():
    # The real frequency-scan writer, driven headless; each column is read
    # back BY ITS HEADER NAME and compared with the circuit's textbook value.
    import tempfile
    import test_fscan_against_references as fscan_test
    rp, cp, f = 1e6, 100e-12, 1e3
    w = 2 * math.pi * f
    e1, e2 = cp / C0, 1 / (w * rp * C0)
    m = 1 / complex(e1, -e2)
    expected = {
        "Frequency": f, "Eps'": e1, "Eps''": e2, "Modulus'": m.real, "Modulus''": m.imag,
        "Zp'": rp, "Zp''": 1 / (w * cp),
        "Sig'": (1 / rp) * (T_MM * 1e-3) / AREA_M2 / 100,
        "Sig''": -w * EPS0 / 100 * (e1 - 1),
    }
    windeta_names = {"Freq. [Hz]": "Frequency", "Zp' [Ohms]": "Zp'", "Zp'' [Ohms]": "Zp''",
                     "Sig' [S/cm]": "Sig'", "Sig'' [S/cm]": "Sig''"}
    with tempfile.TemporaryDirectory() as d:
        gui = fscan_test.make_headless_gui(d)
        params = fscan_test.base_params(geometry_mode="diameter")
        params["diameter_mm"] = D_MM
        gui.c0_farads = C0
        gui._open_output_files(params)
        R, X = _parallel_rc(f, rp, cp)
        gui._write_row(fscan_test.fscan.impedance_to_dielectric(R, X, f, C0))
        for path in (gui.dat_filepath, gui.txt_filepath):
            with open(path, encoding="utf-8") as fh:
                lines = [ln.rstrip("\r\n") for ln in fh if not ln.startswith("#")]
            header = [h.strip() for h in lines[-2].split("\t")]
            values = [float(v) for v in lines[-1].split("\t")]
            assert len(header) == len(values) == 9, path
            for name, value in zip(header, values):
                want = expected[windeta_names.get(name, name)]
                # files carry 6 significant digits (%.5e)
                assert _close(value, want, rel=1e-5), (os.path.basename(path), name, value, want)


if __name__ == "__main__":
    failed = 0
    for test_name, fn in sorted(globals().items()):
        if test_name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {test_name}")
            except Exception as exc:  # report every failure, then exit non-zero
                failed += 1
                print(f"FAIL  {test_name}: {exc!r}")
    sys.exit(1 if failed else 0)
