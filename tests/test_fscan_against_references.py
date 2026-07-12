'''
 PROGRAM:      Offline verification for Frequency_Scan_AlphaAN_GUI.py
 PURPOSE:      Checks the Alpha-AN frequency-scan program against the three
               WinDETA references in pica/novocontrol/data_file_for_ref/
               WITHOUT any instrument attached:

                 1. Numeric regression: rows of both WinDETA exports
                    (Fscan_data_Novo_Windeta.dat and Sample_Fscan_RT.TXT)
                    are inverted back to complex impedance and pushed
                    through impedance_to_dielectric; every column must
                    match the reference.
                 2. Frequency presets: the frozen 40-pt tuple must equal
                    the mes_def LIST ORDERING; the frozen 115-pt tuple
                    must equal the Sample_Fscan_RT.TXT frequency column.
                 3. Header/format: files produced by _open_output_files
                    must match the WinDETA reference structure (comment
                    line, date style, Fixed value(s) line, Zp-before-Sig
                    column order, CRLF endings).
                 4. Mock-instrument dry run: a fake VISA session answers
                    *IDN?/INTTYP?/ZRE? so the full backend sweep runs
                    end-to-end (both presets); output files and the GPIB
                    command log are then verified (no DCV/DCE ever,
                    generator parked on close).

 RUN:          python tests/test_fscan_against_references.py
               (plain python), or with the repo suite: pytest tests/
'''

import importlib.util
import math
import queue
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOVO_DIR = HERE.parent / "pica" / "novocontrol"
PROGRAM = NOVO_DIR / "Frequency_Scan_AlphaAN_GUI.py"
REF_DIR = NOVO_DIR / "data_file_for_ref"

MES_DEF = REF_DIR / "mes_def_Fscan.txt"
DAT_REF = REF_DIR / "Fscan_data_Novo_Windeta.dat"   # ASCII, pairs w/ mes_def
TXT_REF = REF_DIR / "Sample_Fscan_RT.TXT"

# The WinDETA reference exports are deliberately gitignored (lab data, never
# committed). Sections 1-3 compare against them, so they only run on a
# machine that has them; the mock-instrument dry run (section 4) needs no
# reference files and always runs (including CI).
REFERENCES_PRESENT = all(p.exists() for p in (MES_DEF, DAT_REF, TXT_REF))
REFS_MISSING_MSG = (
    "WinDETA reference exports not present (gitignored lab data) - "
    "skipping reference-comparison checks."
)

# mes_def_Fscan.txt SAMPLE section: round plate, D = 12.36 mm, d = 1 mm.
MES_DEF_DIAMETER_MM = 12.36
MES_DEF_THICKNESS_MM = 1.0

_spec = importlib.util.spec_from_file_location("fscan_gui", PROGRAM)
fscan = importlib.util.module_from_spec(_spec)
sys.modules["fscan_gui"] = fscan
_spec.loader.exec_module(fscan)

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(name)


def rel_diff(a, b):
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


# ---------------------------------------------------------------------------
# Reference-file parsing
# ---------------------------------------------------------------------------

def parse_export(path):
    """Parse a WinDETA export -> (comment_line, fixed_line, header, rows).

    Rows are lists of 9 floats in the file's own column order.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [
        [float(v) for v in ln.split("\t")]
        for ln in lines[3:] if ln.strip()
    ]
    return lines[0], lines[1], lines[2], rows


def parse_mes_def_frequencies(path):
    """Extract the LIST ORDERING frequencies from the mes_def."""
    text = path.read_text(encoding="utf-8")
    block = text.split("LIST ORDERING", 1)[1].split("AVERAGING", 1)[0]
    return [float(m) for m in re.findall(r"\d+:\s*([0-9.eE+-]+)", block)]


def invert_row_via_zp(zp1, zp2):
    """Parallel Zp', Zp'' (WinDETA columns) -> series (R, X).

    Y = G + jB with G = 1/Zp', B = 1/Zp'';  Z = 1/Y.
    """
    g = 1.0 / zp1
    b = 1.0 / zp2
    den = g * g + b * b
    return g / den, -b / den


# ---------------------------------------------------------------------------
# 1. Numeric regression against both exports
# ---------------------------------------------------------------------------

def regression_against_export(path, column_map, c0, label, tol=5e-4):
    """Invert each reference row Zp -> Z, push through
    impedance_to_dielectric, compare all 9 columns.

    column_map maps our tuple order (f, eps1, eps2, m1, m2, zp1, zp2,
    sig1, sig2) onto the file's column indices.
    """
    _, _, _, rows = parse_export(path)
    worst = 0.0
    worst_where = ""
    for row in rows:
        f = row[column_map["f"]]
        zp1, zp2 = row[column_map["zp1"]], row[column_map["zp2"]]
        zr, zi = invert_row_via_zp(zp1, zp2)
        ours = fscan.impedance_to_dielectric(zr, zi, f, c0)
        for our_idx, key in enumerate(
            ("f", "eps1", "eps2", "m1", "m2", "zp1", "zp2", "sig1", "sig2")
        ):
            d = rel_diff(ours[our_idx], row[column_map[key]])
            if d > worst:
                worst, worst_where = d, f"{key} @ {f:.5g} Hz"
    check(
        f"numeric regression vs {label} ({len(rows)} rows, all 9 columns)",
        worst < tol,
        f"worst rel diff {worst:.2e} at {worst_where}, tol {tol:g}",
    )


def run_numeric_regression():
    print("\n== 1. Numeric regression against the WinDETA exports ==")

    # Fscan_data_Novo_Windeta.dat pairs with the mes_def: C0 is known from
    # the round-plate geometry. Column order there: Zp BEFORE Sig.
    c0_dat = fscan.compute_c0(MES_DEF_DIAMETER_MM, MES_DEF_THICKNESS_MM)
    dat_map = {"f": 0, "eps1": 1, "eps2": 2, "m1": 3, "m2": 4,
               "zp1": 5, "zp2": 6, "sig1": 7, "sig2": 8}
    regression_against_export(DAT_REF, dat_map, c0_dat,
                              "Fscan_data_Novo_Windeta.dat (mes_def C0)")

    # Sample_Fscan_RT.TXT: geometry not recorded, so derive C0 from the
    # rows themselves and check it is constant (one geometry throughout).
    txt_map = {"f": 0, "eps1": 1, "eps2": 2, "m1": 3, "m2": 4,
               "sig1": 5, "sig2": 6, "zp1": 7, "zp2": 8}
    _, _, _, rows = parse_export(TXT_REF)
    c0s = []
    for row in rows:
        f, eps1 = row[0], row[1]
        zr, zi = invert_row_via_zp(row[7], row[8])
        zsq = zr * zr + zi * zi
        c0s.append(-zi / (2.0 * math.pi * f * eps1 * zsq))
    c0_txt = sorted(c0s)[len(c0s) // 2]
    spread = max(rel_diff(c, c0_txt) for c in c0s)
    check(
        "Sample_Fscan_RT.TXT implies one constant C0",
        spread < 5e-4, f"C0 = {c0_txt:.5e} F, spread {spread:.2e}",
    )
    regression_against_export(TXT_REF, txt_map, c0_txt,
                              "Sample_Fscan_RT.TXT (derived C0)")


# ---------------------------------------------------------------------------
# 2. Frequency presets
# ---------------------------------------------------------------------------

def run_frequency_presets():
    print("\n== 2. Frozen frequency presets ==")

    mes_freqs = parse_mes_def_frequencies(MES_DEF)
    check(
        "mes_def LIST ORDERING has 40 points",
        len(mes_freqs) == 40, f"found {len(mes_freqs)}",
    )
    check(
        "40-pt preset equals mes_def list exactly",
        list(fscan.FREQUENCIES_40PT_10MHZ) == mes_freqs,
    )

    _, _, _, rows = parse_export(TXT_REF)
    txt_freqs = [r[0] for r in rows]
    ours = fscan.FREQUENCIES_115PT_1MHZ
    same = len(ours) == len(txt_freqs) and all(
        rel_diff(a, b) < 5e-6 for a, b in zip(ours, txt_freqs)
    )
    check(
        "115-pt preset equals Sample_Fscan_RT.TXT frequency column",
        same, f"{len(ours)} vs {len(txt_freqs)} points",
    )

    check(
        "both presets registered in FREQ_PRESETS",
        set(fscan.FREQ_PRESETS.values())
        == {fscan.FREQUENCIES_115PT_1MHZ, fscan.FREQUENCIES_40PT_10MHZ},
    )
    check(
        "default preset is the 115-pt list",
        fscan.FREQ_PRESETS[fscan.DEFAULT_FREQ_PRESET]
        is fscan.FREQUENCIES_115PT_1MHZ,
    )


# ---------------------------------------------------------------------------
# 3. Header / format of the produced files
# ---------------------------------------------------------------------------

def make_headless_gui(out_dir, geometry_mode="area"):
    """An AlphaAN_FreqScan_GUI instance without Tk, just enough state for
    _open_output_files / _write_row."""
    gui = object.__new__(fscan.AlphaAN_FreqScan_GUI)
    gui.file_location_path = str(out_dir)
    gui.cal_age_str = "Last REF calibration: unknown (not recorded by PICA)"
    gui.log = lambda msg: None
    if geometry_mode == "area":
        gui.c0_farads = fscan.compute_c0_from_area(1.0, 1.0)
    else:
        gui.c0_farads = fscan.compute_c0(
            MES_DEF_DIAMETER_MM, MES_DEF_THICKNESS_MM
        )
    return gui


def base_params(geometry_mode="area"):
    p = {
        "sample_name": "TestSample",
        "comment": "Cs",
        "acv": 1.0,
        "mtm": 0.5,
        "geometry_mode": geometry_mode,
        "thickness_mm": 1.0,
        "delay": 0.0,
        "wire_mode": "2",
        "visa": "GPIB0::10::INSTR",
        "freq_preset": fscan.DEFAULT_FREQ_PRESET,
        "frequencies": fscan.FREQ_PRESETS[fscan.DEFAULT_FREQ_PRESET],
    }
    if geometry_mode == "area":
        p["area_cm2"] = 1.0
    else:
        p["diameter_mm"] = MES_DEF_DIAMETER_MM
    return p


def run_header_format():
    print("\n== 3. WinDETA .txt header / format ==")

    out_dir = Path(tempfile.mkdtemp(prefix="fscan_test_"))
    try:
        gui = make_headless_gui(out_dir)
        gui._open_output_files(base_params())
        raw = Path(gui.txt_filepath).read_bytes()
        lines = raw.decode("utf-8").splitlines()

        check(
            "line endings are CRLF like the WinDETA exports",
            b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""),
        )
        # " 9.07.2026, 20:00": space-padded day, 0-padded month & minute.
        check(
            "line 1 = comment + .dat-style date (day %2d, month %02d, "
            "minute %02d)",
            re.fullmatch(
                r"Cs, [ \d]\d\.\d\d\.\d{4}, \d{1,2}:\d\d", lines[0]
            ) is not None,
            repr(lines[0]),
        )
        check(
            "line 1 carries no cal-age note",
            "calibration" not in lines[0].lower(),
        )
        if REFERENCES_PRESENT:
            # Byte-level comparison against the real WinDETA export; only
            # possible on a machine with the (gitignored) reference files.
            ref_line1, ref_fixed, ref_header, _ = parse_export(DAT_REF)
            ref_pattern = re.sub(
                r"[ \d]\d\.\d\d\.\d{4}, \d{1,2}:\d\d", "<DATE>", ref_line1
            )
            our_pattern = re.sub(
                r"[ \d]\d\.\d\d\.\d{4}, \d{1,2}:\d\d", "<DATE>", lines[0]
            )
            check(
                "line 1 structure matches Fscan_data_Novo_Windeta.dat",
                ref_pattern == our_pattern,
                f"{ref_pattern!r} vs {our_pattern!r}",
            )
            check(
                "Fixed value(s) line matches the reference (1.0 Vrms)",
                lines[1] == ref_fixed.rstrip(),
                f"{lines[1]!r} vs {ref_fixed.rstrip()!r}",
            )
            check(
                "column header matches the .dat reference (Zp before Sig)",
                lines[2] == ref_header.rstrip(),
                f"{lines[2]!r}",
            )
        else:
            print(f"  [SKIP] .dat byte-comparison checks: {REFS_MISSING_MSG}")
        check(
            "WINDETA_HEADER has Zp before Sig",
            fscan.WINDETA_HEADER.split("\t")[5].strip().startswith("Zp'"),
        )
        check(
            "PICA_HEADER order mirrors WINDETA_HEADER (Zp before Sig)",
            fscan.PICA_HEADER.split("\t")[5:7] == ["Zp'", "Zp''"],
        )

        # A written row must be 9 x %.5e in tuple order.
        row = fscan.impedance_to_dielectric(1e5, -1e6, 20.0, gui.c0_farads)
        gui._write_row(row)
        data_line = Path(gui.txt_filepath).read_text(
            encoding="utf-8"
        ).splitlines()[3]
        check(
            "data row is 9 tab-separated %.5e values",
            data_line == "\t".join(f"{v:.5e}" for v in row),
        )

        # Area-mode geometry line lands in the PICA .dat header.
        dat_head = Path(gui.dat_filepath).read_text(encoding="utf-8")
        check(
            "PICA .dat records area-mode geometry and C0",
            "A = 1.0 cm^2" in dat_head
            and f"C0 = {gui.c0_farads:.5e} F" in dat_head,
        )
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. Mock-instrument end-to-end dry run
# ---------------------------------------------------------------------------

class FakeAlphaInstrument:
    """Answers the Novocontrol high-level command subset the program uses.

    Simulates a lossy capacitor with constant eps* = EPS1 - j*EPS2 in the
    cell whose C0 is passed in, so every produced number is predictable.

    It also enforces the acknowledgment protocol from the Alpha manual:
    every executable (set) command leaves an 'OK' in the result buffer that
    MUST be read before the next response. A bare write() of a set command
    therefore poisons the buffer, and the next query returns the stale
    'OK' instead of data - exactly like the real instrument.
    """

    EPS1, EPS2 = 100.0, 10.0

    # Commands that leave NO buffered ack (per manual/JUMP): task starters
    # signalled by SRQ, and IEEE-488.2 common commands.
    NO_ACK = ("MST", "*RST", "RSTH")

    def __init__(self, c0):
        self.c0 = c0
        self.writes = []
        self.freq = None
        self.pending = []   # unread response buffer (FIFO)
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.send_end = None

    def _execute(self, cmd):
        """Run a command; queue its response(s) into the buffer."""
        if cmd.startswith("GFR="):
            self.freq = float(cmd.split("=", 1)[1])
        if cmd == "*IDN?":
            self.pending.append("NOVOCONTROL,Alpha-AN,00000,FAKE")
        elif cmd == "INTTYP?":
            self.pending.append("INTTYP=5 12345")
        elif cmd == "ZRE?":
            omega = 2.0 * math.pi * self.freq
            g = omega * self.c0 * self.EPS2
            b = omega * self.c0 * self.EPS1
            den = g * g + b * b
            zr, zi = g / den, -b / den
            self.pending.append(
                f"ZRE={zr:.10e} {zi:.10e} {self.freq:.10e} 2 1"
            )
        elif cmd.endswith("?"):
            raise AssertionError(f"unexpected query: {cmd}")
        elif cmd in self.NO_ACK:
            pass
        else:
            self.pending.append("OK")  # executable-command acknowledgment

    def write(self, cmd):
        self.writes.append(cmd)
        self._execute(cmd)

    def read(self):
        assert self.pending, "read() with empty result buffer"
        return self.pending.pop(0)

    def query(self, cmd):
        self.write(cmd)
        return self.read()

    def read_stb(self):
        return 0x41  # SRQ + bit 0, as after a completed task

    def wait_for_srq(self, timeout_ms):
        return None  # every point completes instantly

    def close(self):
        self.writes.append("<CLOSE>")


class FakeResourceManager:
    def __init__(self, instrument):
        self.instrument = instrument

    def open_resource(self, addr):
        return self.instrument


def dry_run_preset(preset_name):
    params = base_params()
    params["freq_preset"] = preset_name
    params["frequencies"] = fscan.FREQ_PRESETS[preset_name]

    out_dir = Path(tempfile.mkdtemp(prefix="fscan_dry_"))
    try:
        gui = make_headless_gui(out_dir)
        fake = FakeAlphaInstrument(gui.c0_farads)

        backend = fscan.AlphaAN_Backend()
        backend.rm = FakeResourceManager(fake)
        backend.connect(params["visa"])
        backend.initialize_instrument(params)

        gui.backend = backend
        gui.data_queue = queue.Queue()
        gui.stop_event = threading.Event()
        gui.sweep_delay = 0.0
        gui.sweep_frequencies = params["frequencies"]
        gui._open_output_files(params)

        gui._sweep_loop()  # run synchronously - the fake never blocks

        rows = []
        done = False
        while not gui.data_queue.empty():
            kind, payload = gui.data_queue.get_nowait()
            if kind == "POINT":
                _idx, row, _ref = payload
                rows.append(row)
                gui._write_row(row)
            elif kind == "DONE":
                done = True
            elif kind == "ERROR":
                raise payload[0]

        backend.close_instrument()

        n = len(params["frequencies"])
        check(f"[{preset_name}] sweep completed with DONE", done)
        check(
            f"[{preset_name}] all {n} points acquired",
            len(rows) == n, f"got {len(rows)}",
        )

        eps_ok = all(
            rel_diff(r[1], fake.EPS1) < 1e-9
            and rel_diff(r[2], fake.EPS2) < 1e-9 for r in rows
        )
        check(f"[{preset_name}] eps'/eps'' recovered exactly", eps_ok)

        zp_ok = all(
            rel_diff(
                r[5],
                1.0 / (2 * math.pi * r[0] * gui.c0_farads * fake.EPS2),
            ) < 1e-9 for r in rows
        )
        check(f"[{preset_name}] column 6 is Zp' (Zp before Sig)", zp_ok)

        lines = Path(gui.txt_filepath).read_text(
            encoding="utf-8"
        ).splitlines()
        check(
            f"[{preset_name}] .txt has 3 header lines + {n} rows",
            len(lines) == 3 + n, f"got {len(lines)}",
        )
        # Rows are written with %.5e, so compare at that precision.
        freqs_in_file = [float(ln.split("\t")[0]) for ln in lines[3:]]
        check(
            f"[{preset_name}] file frequency column equals the preset",
            all(rel_diff(a, b) < 5e-6
                for a, b in zip(freqs_in_file, params["frequencies"])),
        )

        check(
            f"[{preset_name}] DCV/DCE never sent",
            not any(w.startswith(("DCV", "DCE")) for w in fake.writes),
        )
        check(
            f"[{preset_name}] expected setup commands sent once each",
            all(
                fake.writes.count(c) == 1
                for c in ("ZREFMODE=-3", "ZLLCOR=1", "ZSLCAL=1",
                          "FRS=2", "DRS=0 0", "ACV=1", "MTM=0.5")
            ),
            "checked ZREFMODE/ZLLCOR/ZSLCAL/FRS/DRS/ACV/MTM",
        )
        close_tail = fake.writes[fake.writes.index("<CLOSE>") - 4:]
        check(
            f"[{preset_name}] generator parked on close "
            "(ACV=0, ZCONSPL=0, *RST)",
            "ACV=0" in close_tail and "ZCONSPL=0" in close_tail
            and "*RST" in close_tail,
            str(close_tail),
        )
        check(
            f"[{preset_name}] every command acknowledgment was read "
            "(no buffer desync)",
            not fake.pending, f"unread: {fake.pending}",
        )
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def run_dry_run():
    print("\n== 4. Mock-instrument end-to-end dry run (both presets) ==")
    for preset in fscan.FREQ_PRESETS:
        dry_run_preset(preset)


# ---------------------------------------------------------------------------
# pytest entry points: each section must add no new FAILURES entries.
# (check() only records failures, so without these asserts pytest would
# silently pass a failing section.)
# ---------------------------------------------------------------------------

def _pytest_section(runner, needs_refs=False):
    if needs_refs and not REFERENCES_PRESENT:
        import pytest
        pytest.skip(REFS_MISSING_MSG)
    before = len(FAILURES)
    runner()
    new = FAILURES[before:]
    assert not new, f"failed checks: {new}"


def test_numeric_regression():
    _pytest_section(run_numeric_regression, needs_refs=True)


def test_frequency_presets():
    _pytest_section(run_frequency_presets, needs_refs=True)


def test_header_format():
    # Runs everywhere: the section itself skips only the byte-comparison
    # checks when the reference exports are absent.
    _pytest_section(run_header_format)


def test_dry_run():
    _pytest_section(run_dry_run)


# ---------------------------------------------------------------------------

def main():
    print(f"Program under test : {PROGRAM}")
    print(f"References         : {REF_DIR}")

    if REFERENCES_PRESENT:
        run_numeric_regression()
        run_frequency_presets()
    else:
        print(f"\nSKIPPED sections 1-2: {REFS_MISSING_MSG}")
    run_header_format()
    run_dry_run()

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
