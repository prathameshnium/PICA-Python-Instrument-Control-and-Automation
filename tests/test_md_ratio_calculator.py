"""
Purpose: Verify the pure logic of pica/utils/MD_Ratio_Calculator_GUI.py
(no Tk, no hardware):

  - load_tscan_file: legacy 19-column parsing, header skipped, source
    file NEVER modified (original data is immutable).
  - find_warming_ramps: a continuous protocol trace (cooldown -> 0 Oe
    warming ramp -> cooldown -> field warming ramp) yields exactly the
    two warming ramps, in file order; sits/holds/cooldowns excluded.
  - interp_onto + compute_ratio_pct: reproduce an analytic MD curve on
    the overlap region.
  - list_freq_files: per-frequency file discovery.
  - estimate_fscan_sweep_seconds (PPMS_TimeEstimator_GUI): positive and
    aperture-ordered.

Runnable as plain python too:
    python tests/test_md_ratio_calculator.py
"""

import os
import shutil
import sys
import tempfile

import numpy as np

# Setup path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from pica.utils import MD_Ratio_Calculator_GUI as m
from pica.PPMS import PPMS_TimeEstimator_GUI as te


# ------------------------------------------------------------------
# Synthetic continuous protocol trace
# ------------------------------------------------------------------
def protocol_temperatures():
    """Temperature per row: cool 300->20, sit, warm 20->310 (0 Oe run),
    cool 310->20, sit, warm 20->310 (field run), short cool tail."""
    rng = np.random.default_rng(42)
    segs = [
        np.linspace(300, 20, 240),      # cooldown 1
        np.full(60, 20.0),              # sit at base
        np.linspace(20, 310, 400),      # warming ramp #1 (0 Oe)
        np.linspace(310, 20, 240),      # cooldown 2
        np.full(40, 20.0),              # sit at base
        np.linspace(20, 310, 400),      # warming ramp #2 (field)
        np.linspace(310, 250, 60),      # next cooldown starts
    ]
    T = np.concatenate(segs)
    return T + rng.normal(0.0, 0.05, len(T))   # realistic sensor noise


def cp_of(T, field_on):
    """Analytic Cp(T): field suppresses Cp by exactly 2% everywhere."""
    base = 1e-10 * (1.0 + 0.004 * T)
    return base * (0.98 if field_on else 1.0)


def write_synthetic_file(path):
    """A legacy 19-column file for the full protocol trace. Ramp #2
    carries the field-suppressed Cp. Returns the row temperatures."""
    T = protocol_temperatures()
    n1_end = 240 + 60 + 400            # end of ramp #1 region
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Temperature\tQ\tD\tG(1/Rp)\tB\tCp\tLp\tCs\tLs\tlZl\t"
                 "theta\tchi\tR(Rs)\ttheta(deg.)\tRp\t1/lZl\tOmega\t"
                 "Cp''\tCs''\n")
        for i, t in enumerate(T):
            field_on = i >= n1_end
            cp = cp_of(t, field_on)
            g = 2e-7 * (1.0 + 0.002 * t) * (1.05 if field_on else 1.0)
            d = 0.01 * (1.10 if field_on else 1.0)
            row = [t, 100.0, d, g, 1e-6, cp] + [0.0] * 13
            fh.write("\t".join(f"{v:.6E}" for v in row) + "\n")
    return T


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------
def test_load_and_ramp_detection_and_immutability():
    work = tempfile.mkdtemp(prefix="pica_md_")
    try:
        path = os.path.join(work, "Sample-1000Hz.txt")
        write_synthetic_file(path)
        before = open(path, "rb").read()

        data = m.load_tscan_file(path)
        assert set(data) == {"T", "Cp", "G", "D"}
        assert len(data["T"]) == len(data["Cp"]) > 1000

        ramps = m.find_warming_ramps(data["T"])
        assert len(ramps) == 2, f"expected 2 warming ramps, got {ramps}"
        (a0, b0), (a1, b1) = ramps
        assert a0 < b0 <= a1 < b1          # in file order, no overlap
        T = data["T"]
        # Both ramps span most of 20 -> 310 K; neither includes the sits
        for (a, b) in ramps:
            assert T[a] < 40.0 and T[b - 1] > 280.0
        # Cooldowns are never inside a detected ramp: T rises overall
        for (a, b) in ramps:
            assert T[b - 1] - T[a] > 240.0

        # IMMUTABILITY: loading changed nothing on disk
        assert open(path, "rb").read() == before
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_interpolation_and_md_ratio_analytic():
    work = tempfile.mkdtemp(prefix="pica_md_")
    try:
        path = os.path.join(work, "Sample-1000Hz.txt")
        write_synthetic_file(path)
        data = m.load_tscan_file(path)
        (a0, b0), (a1, b1) = m.find_warming_ramps(data["T"])

        T0, Cp0 = data["T"][a0:b0], data["Cp"][a0:b0]
        TH, CpH = data["T"][a1:b1], data["Cp"][a1:b1]
        mask, CpH_i = m.interp_onto(T0, TH, CpH)
        md = m.compute_ratio_pct(Cp0[mask], CpH_i)

        # Analytic answer: exactly −2 % everywhere (field factor 0.98);
        # sensor noise + interpolation allow a small tolerance.
        assert np.isfinite(md).all()
        assert abs(np.median(md) - (-2.0)) < 0.05, np.median(md)
        assert np.percentile(np.abs(md + 2.0), 95) < 0.5
        # Overlap restricted to the common range
        Tc = T0[mask]
        assert Tc.min() >= max(T0.min(), TH.min()) - 1e-9
        assert Tc.max() <= min(T0.max(), TH.max()) + 1e-9
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_ratio_guards_and_no_overlap_error():
    md = m.compute_ratio_pct([0.0, 1.0, np.nan], [1.0, 1.1, 1.0])
    assert np.isnan(md[0])                     # y0 == 0 -> NaN, no crash
    assert abs(md[1] - 10.0) < 1e-9
    assert np.isnan(md[2])
    try:
        m.interp_onto(np.array([1.0, 2.0]), np.array([10.0, 11.0]),
                      np.array([1.0, 1.0]))
    except ValueError as e:
        assert "overlap" in str(e)
    else:
        raise AssertionError("disjoint ranges must raise")


def test_list_freq_files():
    work = tempfile.mkdtemp(prefix="pica_md_")
    try:
        for name in ("S-1000Hz.txt", "S-250000Hz.txt", "S_T-log.txt",
                     "notes.txt"):
            open(os.path.join(work, name), "w").close()
        files = m.list_freq_files(work)
        assert sorted(files) == [1000.0, 250000.0]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_ramp_detection_ignores_short_blips():
    # A sit with a small 5 K excursion must not register as a ramp.
    T = np.concatenate([
        np.linspace(300, 20, 200),
        np.full(50, 20.0),
        np.linspace(20, 25, 20),      # brief 5 K blip — below min span
        np.full(50, 25.0),
    ])
    assert m.find_warming_ramps(T, min_span_k=30.0) == []


def test_estimator_fscan_sweep_seconds():
    for aper in ("SHOR", "MED", "LONG"):
        assert te.estimate_fscan_sweep_seconds(377, aper, 0.2) > 0
    s = te.estimate_fscan_sweep_seconds
    assert s(377, "SHOR", 0.2) < s(377, "MED", 0.2) < s(377, "LONG", 0.2)
    # More points, more time; delay adds ~n*delay
    assert s(754, "MED", 0.2) > s(377, "MED", 0.2)
    assert abs(s(100, "MED", 1.0) - s(100, "MED", 0.0) - 100.0) < 1e-6


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
    print("\nALL TESTS PASSED")
