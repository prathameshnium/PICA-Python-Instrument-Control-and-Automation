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

import cmath
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
        "Tan(Delta)": e2 / e1,
        # phase of Z = -(phase of Y), Y = G + jB
        "Phi [Deg]": -math.degrees(math.atan2(w * cp, 1 / rp)),
        "Cp [F]": cp, "G [S]": 1 / rp,
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
        gui._write_row(fscan_test.fscan.impedance_to_dielectric(R, X, f, C0),
                       fscan_test.fscan.loss_phase_cp_g(R, X, f))
        for path, n_cols in ((gui.dat_filepath, 13), (gui.txt_filepath, 11)):
            with open(path, encoding="utf-8") as fh:
                lines = [ln.rstrip("\r\n") for ln in fh if not ln.startswith("#")]
            header = [h.strip() for h in lines[-2].split("\t")]
            values = [float(v) for v in lines[-1].split("\t")]
            assert len(header) == len(values) == n_cols, path
            for name, value in zip(header, values):
                want = expected[windeta_names.get(name, name)]
                # files carry 6 significant digits (%.5e)
                assert _close(value, want, rel=1e-5), (os.path.basename(path), name, value, want)


# --- frequency scan: tan(delta), phase, Cp, G ---------------------------------

def _fscan():
    return _module(*DIELECTRIC_FILES[0])


def test_cp_and_g_recover_a_parallel_rc_circuit():
    extra = _fscan().loss_phase_cp_g
    for rp, cp in ((1e6, 100e-12), (37.0, 4.4e-10), (1e12, 2e-15)):
        for f in FREQS:
            R, X = _parallel_rc(f, rp, cp)
            _, _, cp_out, g_out = extra(R, X, f)
            assert _close(cp_out, cp, rel=1e-9), (rp, cp, f)
            assert _close(g_out, 1 / rp, rel=1e-9), (rp, cp, f)


def test_tan_delta_and_phase_of_a_parallel_rc_circuit():
    extra = _fscan().loss_phase_cp_g
    rp, cp = 2e5, 33e-12
    for f in FREQS:
        w = 2 * math.pi * f
        R, X = _parallel_rc(f, rp, cp)
        tan_d, phi, _, _ = extra(R, X, f)
        assert _close(tan_d, 1 / (w * rp * cp)), f        # G / (w Cp)
        # Z = Rp / (1 + j w Rp Cp): phase = -atan(w Rp Cp), capacitive < 0
        assert _close(phi, -math.degrees(math.atan(w * rp * cp))), f
        assert -90 < phi < 0, f
        # loss angle and phase angle are complementary: delta = 90 + phi
        assert _close(tan_d, math.tan(math.radians(90 + phi)), rel=1e-6), f


def test_phase_of_pure_and_inductive_impedances():
    extra = _fscan().loss_phase_cp_g
    f = 1e3
    w = 2 * math.pi * f
    assert extra(50.0, 0.0, f)[1] == 0.0                       # pure R
    assert _close(extra(0.0, -1 / (w * 1e-9), f)[1], -90.0)    # pure C
    assert _close(extra(0.0, w * 1e-3, f)[1], 90.0)            # pure L
    # series R-L like WinDETA's 3 MHz message line: |Z| 27.97, +13.394 deg
    R, X = 27.97 * math.cos(math.radians(13.394)), 27.97 * math.sin(math.radians(13.394))
    tan_d, phi, cp, _ = extra(R, X, 3e6)
    assert _close(phi, 13.394, rel=1e-9)
    assert tan_d < 0 and cp < 0          # inductive: negative eps' and Cp
    # WinDETA reports C = -4.394e-10 F for that point
    assert _close(cp, -4.394e-10, rel=2e-3)


def test_extra_columns_agree_with_the_windeta_columns():
    fscan = _fscan()
    points = [(f, *_parallel_rc(f, 4.7e5, 220e-12)) for f in FREQS] + [
        (1e3, 300.0, -400.0), (3e6, 27.2, 6.5)]
    for f, R, X in points:
        _, e1, e2, _, _, zp1, _, _, _ = fscan.impedance_to_dielectric(R, X, f, C0)
        tan_d, _, cp, g = fscan.loss_phase_cp_g(R, X, f)
        assert _close(tan_d, e2 / e1), f          # tan(delta) = eps''/eps'
        assert _close(cp, e1 * C0), f             # Cp = eps' * C0
        assert _close(g, 1 / zp1), f              # G = 1/Zp'


def test_bottom_plot_draws_tan_delta_not_eps2():
    # The real _process_sweep_point/_update_sweep_plot on a headless GUI
    # with a real matplotlib figure: the plotted y data must be eps''/eps'.
    from matplotlib.figure import Figure
    fscan = _fscan()
    gui = object.__new__(fscan.AlphaAN_FreqScan_GUI)
    gui.data_storage = {"freq": [], "eps1": [], "tand": []}
    gui.log = lambda msg: None
    gui._write_row = lambda row, extra: None
    fig = Figure()
    gui.ax_eps1, gui.ax_tand = fig.add_subplot(2, 1, 1), fig.add_subplot(2, 1, 2)
    gui.line_eps1, = gui.ax_eps1.plot([], [])
    gui.line_tand, = gui.ax_tand.plot([], [])
    gui.canvas = type("C", (), {"draw_idle": lambda self: None})()
    gui.progress_bar = {}
    gui.log_y_var = type("V", (), {"get": lambda self: True})()
    gui._decade_ylims = {}
    gui.sweep_index = 0
    want_eps1, want_tand = [], []
    for f in FREQS:
        R, X = _parallel_rc(f, 2e5, 33e-12)
        row = fscan.impedance_to_dielectric(R, X, f, C0)
        gui._process_sweep_point(row, fscan.loss_phase_cp_g(R, X, f), 1)
        want_eps1.append(row[1])
        want_tand.append(row[2] / row[1])
    gui._update_sweep_plot()
    assert list(gui.line_tand.get_xdata()) == list(FREQS)
    for got, want in zip(gui.line_tand.get_ydata(), want_tand):
        assert _close(got, want)
    for got, want in zip(gui.line_eps1.get_ydata(), want_eps1):
        assert _close(got, want)
    # create_graph_frame needs a Tk parent, so check its label line instead.
    with open(os.path.join(HERE, "..", "pica", "novocontrol", DIELECTRIC_FILES[0][0]),
              encoding="utf-8") as fh:
        assert 'self.ax_tand.set_ylabel("Loss factor, tan δ")' in fh.read()


def _par(*zs):
    """Impedance of branches in parallel."""
    return 1 / sum(1 / z for z in zs)


# name -> Z(w), each built from its circuit with plain complex arithmetic
SIMULATED_CIRCUITS = {
    "parallel Rp||Cp": lambda w: _par(1e6, 1 / (1j * w * 100e-12)),
    "series Rs+Cs": lambda w: 50.0 + 1 / (1j * w * 10e-9),
    # Debye eps* = 3 + 40/(1 + jw tau) plus dc conduction, in the C0 cell
    "Debye + sigma_dc": lambda w: 1 / (1j * w * C0 * (
        3.0 + 40.0 / (1 + 1j * w * 1e-5) - 1j * 1e-7 / (w * EPS0))),
    # 2 ohm contacts + 1 uH leads in series with a 10 k || 1 nF sample:
    # capacitive at low f, inductive above ~5 MHz
    "lossy sample + Rs + L": lambda w: 2.0 + 1j * w * 1e-6 + _par(1e4, 1 / (1j * w * 1e-9)),
    "37 ohm || 1 uF": lambda w: _par(37.0, 1 / (1j * w * 1e-6)),
    "pure C": lambda w: 1 / (1j * w * 1e-9),
    "pure L": lambda w: 1j * w * 1e-3,
}
SIM_FREQS = (20.0, 100.0, 1e3, 1e4, 1e5, 1e6, 3e6, 1e7)


def test_tan_delta_phase_cp_and_g_of_simulated_circuits():
    # Expected values straight from Y = 1/Z of each simulated circuit:
    # tan d = Re(Y)/Im(Y), phi = phase(Z), Cp = Im(Y)/w, G = Re(Y); and the
    # same sample through the WinDETA columns: Cp = eps'*C0, G = 1/Zp',
    # tan d = eps''/eps'. Agreement is at round-off (worst seen ~3e-16).
    fscan = _fscan()
    for name, z_of_w in SIMULATED_CIRCUITS.items():
        for f in SIM_FREQS:
            w = 2 * math.pi * f
            z = z_of_w(w)
            y = 1 / z
            tan_d, phi, cp, g = fscan.loss_phase_cp_g(z.real, z.imag, f)
            where = (name, f)
            assert _close(tan_d, y.real / y.imag, abs_=1e-300), where
            assert _close(phi, math.degrees(cmath.phase(z)), abs_=1e-300), where
            assert _close(cp, y.imag / w), where
            assert _close(g, y.real, abs_=1e-300), where
            _, e1, e2, _, _, zp1, _, _, _ = fscan.impedance_to_dielectric(z.real, z.imag, f, C0)
            assert _close(cp, e1 * C0), where
            assert _close(tan_d, e2 / e1, abs_=1e-300), where
            if y.real:
                assert _close(g, 1 / zp1), where
    # the physics the circuits must show
    lossy = SIMULATED_CIRCUITS["lossy sample + Rs + L"]
    z_lo, z_hi = lossy(2 * math.pi * 20.0), lossy(2 * math.pi * 1e7)
    lo, hi = fscan.loss_phase_cp_g(z_lo.real, z_lo.imag, 20.0), fscan.loss_phase_cp_g(
        z_hi.real, z_hi.imag, 1e7)
    assert lo[1] < 0 and lo[0] > 0 and lo[2] > 0      # capacitive at 20 Hz
    assert hi[1] > 0 and hi[0] < 0 and hi[2] < 0      # inductive at 10 MHz
    z37 = SIMULATED_CIRCUITS["37 ohm || 1 uF"](2 * math.pi * 20.0)
    t37, _, c37, g37 = fscan.loss_phase_cp_g(z37.real, z37.imag, 20.0)
    assert _close(c37, 1e-6) and _close(g37, 1 / 37.0)
    assert _close(t37, 1 / (2 * math.pi * 20.0 * 37.0 * 1e-6))   # ~215
    assert fscan.impedance_to_dielectric(z37.real, z37.imag, 20.0, C0)[1] > 1e6
    # pure R: phase 0, no capacitance, G = 1/R (tan d = G/B is undefined)
    _, phi_r, cp_r, g_r = fscan.loss_phase_cp_g(50.0, 0.0, 1e3)
    assert phi_r == 0.0 and cp_r == 0.0 and _close(g_r, 1 / 50.0)


def _all_columns(fscan, z, f):
    """Every file quantity for one point, by PICA_HEADER name."""
    row = fscan.impedance_to_dielectric(z.real, z.imag, f, C0)
    extra = fscan.loss_phase_cp_g(z.real, z.imag, f)
    _, pica = fscan.file_rows(row, extra)
    names = [h.split(" [")[0] for h in fscan.PICA_HEADER.split("\t")]
    return dict(zip(names, pica))


def test_any_column_pair_gives_back_z_and_every_other_column():
    # Back and forth: rebuild the series impedance Z from each independent
    # pair of output columns with textbook inversions, then recompute all
    # 13 columns from that Z. Every route must land on the same sample.
    fscan = _fscan()
    eps0_cm = EPS0 / 100.0
    routes = {
        # eps* = eps' - j eps'' = Y / (j w C0)
        "Eps', Eps''": lambda c, w: 1 / (1j * w * C0 * complex(c["Eps'"], -c["Eps''"])),
        # M* = M' + j M'' = 1/eps*
        "Modulus', Modulus''": lambda c, w: 1 / (
            1j * w * C0 / complex(c["Modulus'"], c["Modulus''"])),
        # Zp' = 1/G, Zp'' = 1/B
        "Zp', Zp''": lambda c, w: 1 / complex(1 / c["Zp'"], 1 / c["Zp''"]),
        # sigma' = w eps0 eps'', sigma'' = -w eps0 (eps' - 1)
        "Sig', Sig''": lambda c, w: 1 / (1j * w * C0 * complex(
            1 - c["Sig''"] / (w * eps0_cm), -c["Sig'"] / (w * eps0_cm))),
        "Cp, G": lambda c, w: 1 / complex(c["G"], w * c["Cp"]),
        "Eps', Tan(Delta)": lambda c, w: 1 / (1j * w * C0 * complex(
            c["Eps'"], -c["Eps'"] * c["Tan(Delta)"])),
        "Cp, Tan(Delta)": lambda c, w: 1 / complex(
            c["Tan(Delta)"] * w * c["Cp"], w * c["Cp"]),
        # Y = exp(-j phi)/|Z|, so G = cos(phi)/|Z|
        "Phi, G": lambda c, w: cmath.rect(
            math.cos(math.radians(c["Phi"])) / c["G"], math.radians(c["Phi"])),
        # B = w Cp = -sin(phi)/|Z|
        "Phi, Cp": lambda c, w: cmath.rect(
            -math.sin(math.radians(c["Phi"])) / (w * c["Cp"]),
            math.radians(c["Phi"])),
    }
    lossy = ("parallel Rp||Cp", "series Rs+Cs", "Debye + sigma_dc",
             "lossy sample + Rs + L", "37 ohm || 1 uF")
    for name in lossy:
        for f in SIM_FREQS:
            w = 2 * math.pi * f
            z = SIMULATED_CIRCUITS[name](w)
            cols = _all_columns(fscan, z, f)
            # loss angle and phase angle: delta = 90 deg + phi
            assert _close(cols["Tan(Delta)"], -1 / math.tan(math.radians(cols["Phi"])),
                          rel=1e-9), (name, f)
            for route, rebuild in routes.items():
                z_back = rebuild(cols, w)
                where = (name, f, route)
                assert _close(z_back.real, z.real, rel=1e-8), where
                assert _close(z_back.imag, z.imag, rel=1e-8), where
                again = _all_columns(fscan, z_back, f)
                for key, value in cols.items():
                    assert _close(again[key], value, rel=1e-7), (*where, key)


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
