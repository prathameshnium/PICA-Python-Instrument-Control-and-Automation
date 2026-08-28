"""Instrument-safety tests for the Novocontrol Alpha-AN frequency scan.

Three editions ship: the VISA GUI, the 32-bit raw-GPIB GUI and the headless
CLI. They are deliberately self-contained copies of each other, which means a
safety rule fixed in one can silently rot in the others -- so every rule here
is asserted against every edition that has it.

The rules being pinned:
  * the command-ack protocol. Every Novocontrol set command leaves 'OK' in
    the result buffer and that response MUST be read; an unread ack
    desynchronises every later query, so ZRE? would return a stale 'OK'
    instead of data. MST is the one exception and must NOT go through the
    ack path.
  * the ZG4 AC-voltage ceiling, which is frequency-dependent and is enforced
    before a byte reaches the bus.
  * DCV / DCE are never transmitted -- this mainframe has no bias hardware.
  * every exit path drives the generator to a safe state (MBK, ACV=0,
    ZCONSPL=0) before the session is released, and none of it can raise.
  * a fatal measurement status aborts the sweep instead of logging the point.

The backends are driven against a fake instrument that records every byte, so
no hardware, VISA backend or 32-bit interpreter is needed.

Runnable as plain Python as well as under pytest.
"""

import ast
import importlib.util
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

VISA_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol",
                         "Frequency_Scan_AlphaAN_GUI.py")
BIT32_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol",
                          "Frequency_Scan_AlphaAN_32bit_GUI.py")
CLI_PATH = os.path.join(REPO_ROOT, "pica", "novocontrol", "Instrument_Control",
                        "AlphaAN_FreqScan_Instrument_Control.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


visa_ed = _load("alpha_visa_under_test", VISA_PATH)
bit32_ed = _load("alpha_32bit_under_test", BIT32_PATH)
cli_ed = _load("alpha_cli_under_test", CLI_PATH)

GUI_EDITIONS = (("VISA", visa_ed), ("32-bit", bit32_ed))
ALL_EDITIONS = GUI_EDITIONS + (("CLI", cli_ed),)

SOURCES = {
    "VISA": open(VISA_PATH, encoding="utf-8").read(),
    "32-bit": open(BIT32_PATH, encoding="utf-8").read(),
    "CLI": open(CLI_PATH, encoding="utf-8").read(),
}


class FakeAlpha:
    """Records every byte sent. Answers 'OK' to set commands by default."""

    def __init__(self, answers=None, srq_raises=False):
        self.traffic = []            # every write and query, in order
        self.answers = dict(answers or {})
        self.srq_raises = srq_raises
        self.stb_reads = 0
        self.closed = False
        self.timeout = 0
        self.read_termination = None
        self.write_termination = None
        self.send_end = None
        self._pending = None

    # --- transport ---
    def write(self, command):
        self.traffic.append(("write", command))

    def query(self, command):
        self.traffic.append(("query", command))
        if command in self.answers:
            answer = self.answers[command]
            if isinstance(answer, list):
                return answer.pop(0) if answer else "OK"
            return answer
        return "OK"

    def read(self):
        self.traffic.append(("read", None))
        return self.answers.get("__read__", "OK")

    def read_stb(self):
        self.stb_reads += 1
        return 0x41

    def wait_for_srq(self, timeout_ms=25000):
        self.traffic.append(("wait_for_srq", timeout_ms))
        if self.srq_raises:
            raise TimeoutError("no SRQ")

    def close(self):
        self.closed = True

    # --- helpers for assertions ---
    @property
    def commands(self):
        return [payload for kind, payload in self.traffic
                if kind in ("write", "query")]

    def kind_of(self, command):
        for kind, payload in self.traffic:
            if payload == command:
                return kind
        return None


def _backend(module, fake, task_running=False):
    backend = module.AlphaAN_Backend()
    backend.instrument = fake
    backend._task_may_be_running = task_running
    return backend


# --------------------------------------------------- the ack protocol

def test_a_set_command_is_sent_as_a_query_so_its_ack_is_read():
    """An unread 'OK' desynchronises every later query on the bus."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha()
        backend = _backend(module, fake)
        backend._exec("ACV=1.0")
        assert fake.traffic == [("query", "ACV=1.0")], (label, fake.traffic)


def test_a_non_ok_answer_to_a_set_command_raises_with_its_meaning():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"ACV=1.0": "IP"})
        backend = _backend(module, fake)
        try:
            backend._exec("ACV=1.0")
        except RuntimeError as exc:
            assert "IP" in str(exc), label
            assert "Invalid command parameter" in str(exc), label
        else:
            raise AssertionError(f"{label}: an error code was read as success")


def test_an_unrecognised_answer_is_still_treated_as_a_failure():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"MTM=0.5": "ZZ"})
        backend = _backend(module, fake)
        try:
            backend._exec("MTM=0.5")
        except RuntimeError as exc:
            assert "unrecognized response" in str(exc), label
        else:
            raise AssertionError(f"{label}: an unknown answer was accepted")


def test_the_ack_strip_removes_the_alpha_response_padding():
    """Replies carry trailing NUL/DLE that a plain .strip() would keep."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"ACV=1.0": "OK\x00\x10\r\n"})
        backend = _backend(module, fake)
        backend._exec("ACV=1.0")     # must not raise


def test_exec_tolerant_never_raises_on_a_shutdown_path():
    for label, module in GUI_EDITIONS:
        class Exploding(FakeAlpha):
            def query(self, command):
                raise IOError("bus is gone")

        backend = _backend(module, Exploding())
        assert backend._exec_tolerant("ACV=0") is False, label

        backend = _backend(module, FakeAlpha(answers={"ACV=0": "ER"}))
        assert backend._exec_tolerant("ACV=0") is False, label

        backend = _backend(module, FakeAlpha())
        assert backend._exec_tolerant("ACV=0") is True, label


def test_the_error_code_table_is_the_same_in_every_edition():
    assert visa_ed.CMD_ERROR_MEANINGS == bit32_ed.CMD_ERROR_MEANINGS
    assert visa_ed.CMD_ERROR_MEANINGS == cli_ed.CMD_ERROR_MEANINGS
    for code in ("CA", "CR", "EC", "ER", "II", "IM", "IP", "MR", "UC"):
        assert code in visa_ed.CMD_ERROR_MEANINGS, code


def test_mst_is_written_directly_and_never_through_the_ack_path():
    """MST leaves no buffered ack; querying it would hang until timeout."""
    for label, source in SOURCES.items():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", "")
            if name not in ("query", "_exec", "_exec_tolerant", "exec_cmd",
                            "exec_tolerant"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "MST":
                    raise AssertionError(f"{label}: MST goes through {name}")


def test_measure_point_writes_mst_and_reads_the_result_with_zre():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"ZRE?": "ZRE=1000 -2000 1000.0 2 0"})
        backend = _backend(module, fake)
        result = backend.measure_point(1000.0)
        assert ("write", "MST") in fake.traffic, label
        assert ("query", "ZRE?") in fake.traffic, label
        assert result[3] == module.STATUS_RESULT_VALID, label


def test_stale_status_is_cleared_before_every_trigger():
    """Otherwise wait_for_srq latches onto the previous point's completion."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"ZRE?": "ZRE=1 1 1000.0 2 0"})
        backend = _backend(module, fake)
        backend.measure_point(1000.0)
        assert fake.stb_reads >= 1, label


# ------------------------------------------------------- the ZG4 ceiling

def test_the_acv_ceiling_steps_down_with_frequency():
    for label, module in ALL_EDITIONS:
        assert module.acv_ceiling(1e3) == 3.0, label
        assert module.acv_ceiling(4e6) == 3.0, label      # boundary inclusive
        assert module.acv_ceiling(5e6) == 2.0, label
        assert module.acv_ceiling(10e6) == 2.0, label     # boundary inclusive
        assert module.acv_ceiling(11e6) == 1.0, label


def test_every_edition_agrees_on_the_ceiling():
    for freq in (1, 1e3, 3.9e6, 4e6, 4.1e6, 9.9e6, 10e6, 15e6, 20e6):
        values = {module.acv_ceiling(freq) for _l, module in ALL_EDITIONS}
        assert len(values) == 1, (freq, values)


def test_an_acv_above_the_ceiling_is_refused_before_transmission():
    for label, module in GUI_EDITIONS:
        params = _good_params(module)
        params["acv"] = 3.5
        try:
            module.validate_parameters(params)
        except ValueError as exc:
            assert "AC voltage" in str(exc), label
        else:
            raise AssertionError(f"{label}: 3.5 Vrms was accepted")


def test_a_zero_or_negative_acv_is_refused():
    for label, module in GUI_EDITIONS:
        for acv in (0.0, -1.0):
            params = _good_params(module)
            params["acv"] = acv
            try:
                module.validate_parameters(params)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{label}: ACV={acv} was accepted")


def test_the_cli_validator_enforces_the_same_ceiling():
    try:
        cli_ed.validate(3.5, 0.5, "2", 10.0, 1.0)
    except ValueError as exc:
        assert "AC voltage" in str(exc)
    else:
        raise AssertionError("the CLI accepted 3.5 Vrms")
    cli_ed.validate(1.0, 0.5, "2", 10.0, 1.0)     # must not raise


def test_integration_time_bounds_are_enforced():
    for label, module in GUI_EDITIONS:
        for mtm in (0.0, 0.001, 1001.0):
            params = _good_params(module)
            params["mtm"] = mtm
            try:
                module.validate_parameters(params)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{label}: MTM={mtm} was accepted")


def test_a_frequency_outside_the_hardware_span_is_refused():
    for label, module in GUI_EDITIONS:
        params = _good_params(module)
        params["frequencies"] = (1e-6,)
        try:
            module.validate_parameters(params)
        except ValueError as exc:
            assert "outside the Alpha-AN range" in str(exc), label
        else:
            raise AssertionError(f"{label}: a sub-hardware frequency passed")

        params = _good_params(module)
        params["frequencies"] = (25e6,)
        try:
            module.validate_parameters(params)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{label}: 25 MHz was accepted")


def test_a_non_positive_sample_geometry_is_refused():
    for label, module in GUI_EDITIONS:
        for key in ("diameter_mm", "thickness_mm"):
            params = _good_params(module)
            params[key] = 0.0
            try:
                module.validate_parameters(params)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{label}: {key}=0 was accepted")


def _grid(module):
    """The 115-point, 20 Hz - 1 MHz sweep, whatever the edition calls it."""
    return getattr(module, "FREQUENCIES_115PT_1MHZ", None)         or module.FREQUENCIES_HZ


def _good_params(module):
    return {
        "frequencies": _grid(module),
        "acv": 1.0,
        "mtm": 0.5,
        "wire_mode": "2",
        "geometry_mode": "diameter",
        "diameter_mm": 10.0,
        "area_cm2": 0.785,
        "thickness_mm": 1.0,
        "delay": 1.0,
    }


# ------------------------------------------------------ no bias hardware

def test_dcv_and_dce_are_never_transmitted_by_any_edition():
    """This mainframe has no bias hardware; a DC command has nowhere to go."""
    for label, source in SOURCES.items():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") not in (
                    "write", "query", "_exec", "_exec_tolerant"):
                continue
            for arg in node.args:
                value = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                elif isinstance(arg, ast.JoinedStr):
                    value = "".join(
                        v.value for v in arg.values
                        if isinstance(v, ast.Constant)
                        and isinstance(v.value, str))
                if value is None:
                    continue
                assert not value.startswith("DCV"), (label, value)
                assert not value.startswith("DCE"), (label, value)


# ---------------------------------------------------------- safe shutdown

def test_safe_state_parks_the_generator_in_the_documented_order():
    """MBK first (a task may be in flight), then ACV=0, then ZCONSPL=0."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha()
        backend = _backend(module, fake, task_running=True)
        backend.safe_state()
        assert fake.commands == ["MBK", "ACV=0", "ZCONSPL=0"], \
            (label, fake.commands)


def test_safe_state_skips_the_abort_when_no_task_is_running():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha()
        backend = _backend(module, fake, task_running=False)
        backend.safe_state()
        assert fake.commands == ["ACV=0", "ZCONSPL=0"], (label, fake.commands)


def test_safe_state_still_parks_the_generator_when_the_abort_fails():
    """A failing MBK must not stop the ACV=0 that actually matters."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"MBK": "MR"})
        backend = _backend(module, fake, task_running=True)
        backend.safe_state()          # must not raise
        assert "ACV=0" in fake.commands, label
        assert "ZCONSPL=0" in fake.commands, label


def test_safe_state_never_raises_even_with_a_dead_bus():
    for label, module in GUI_EDITIONS:
        class Dead(FakeAlpha):
            def query(self, command):
                raise IOError("bus is gone")

        backend = _backend(module, Dead(), task_running=True)
        backend.safe_state()          # must not raise


def test_safe_state_on_a_closed_backend_is_a_no_op():
    for label, module in GUI_EDITIONS:
        backend = module.AlphaAN_Backend()
        backend.instrument = None
        backend.safe_state()          # must not raise


def test_closing_parks_the_generator_before_resetting_and_releasing():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha()
        backend = _backend(module, fake, task_running=True)
        backend.close_instrument()
        commands = fake.commands
        assert commands.index("ACV=0") < commands.index("*RST"), \
            (label, commands)
        assert fake.closed is True, label
        assert backend.instrument is None, label


def test_a_failing_reset_escalates_to_rsth_and_still_closes():
    for label, module in GUI_EDITIONS:
        class NoReset(FakeAlpha):
            def write(self, command):
                FakeAlpha.write(self, command)
                if command == "*RST":
                    raise IOError("reset refused")

        fake = NoReset()
        backend = _backend(module, fake)
        backend.close_instrument()
        assert "RSTH" in fake.commands, (label, fake.commands)
        assert fake.closed is True, label


def test_the_session_is_released_even_when_everything_fails():
    for label, module in GUI_EDITIONS:
        class AllBroken(FakeAlpha):
            def query(self, command):
                raise IOError("dead")

            def write(self, command):
                raise IOError("dead")

        fake = AllBroken()
        backend = _backend(module, fake, task_running=True)
        backend.close_instrument()     # must not raise
        assert fake.closed is True, label


def test_closing_a_backend_that_never_connected_is_a_no_op():
    for label, module in GUI_EDITIONS:
        backend = module.AlphaAN_Backend()
        backend.instrument = None
        backend.close_instrument()     # must not raise


# ------------------------------------------------- abort and diagnostics

def test_an_srq_timeout_aborts_first_and_only_then_asks_for_the_state():
    """ZTSTAT? during a live measurement degrades point accuracy."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"ZTSTAT?": "idle"}, srq_raises=True)
        backend = _backend(module, fake)
        try:
            backend._wait_for_srq(1.0, "a measurement")
        except TimeoutError as exc:
            assert "Aborted (MBK)" in str(exc), label
        else:
            raise AssertionError(f"{label}: an SRQ timeout went unreported")
        commands = fake.commands
        assert commands.index("MBK") < commands.index("ZTSTAT?"), \
            (label, commands)


def test_ztstat_is_never_queried_on_the_happy_path():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"ZRE?": "ZRE=1 1 1000.0 2 0"})
        backend = _backend(module, fake)
        backend.measure_point(1000.0)
        assert "ZTSTAT?" not in fake.commands, label


def test_abort_and_diagnose_reports_rather_than_raising_when_mbk_fails():
    for label, module in GUI_EDITIONS:
        class Dead(FakeAlpha):
            def query(self, command):
                raise IOError("bus is gone")

        backend = _backend(module, Dead())
        state = backend.abort_and_diagnose()
        assert "MBK failed" in state, (label, state)


# -------------------------------------------------------- result handling

def test_status_2_is_the_only_valid_result():
    for label, module in ALL_EDITIONS:
        assert module.STATUS_RESULT_VALID == 2, label


def test_a_disconnected_signal_source_is_fatal_not_merely_flagged():
    for label, module in ALL_EDITIONS:
        assert 6 in module.STATUS_FATAL, label
        assert "disconnected" in module.STATUS_MEANINGS[6], label


def test_every_documented_status_code_has_a_meaning():
    for label, module in ALL_EDITIONS:
        for code in range(0, 7):
            assert code in module.STATUS_MEANINGS, (label, code)


def test_parse_zre_reports_a_missing_calibration_in_plain_words():
    for label, module in ALL_EDITIONS:
        try:
            module.parse_zre("ZRE=CR")
        except RuntimeError as exc:
            assert "Recalibrate" in str(exc), label
            assert "ZSLCAL" in str(exc), label
        else:
            raise AssertionError(f"{label}: a 'CR' answer was parsed as data")


def test_parse_zre_rejects_a_short_response():
    for label, module in ALL_EDITIONS:
        try:
            module.parse_zre("ZRE=1 2 3")
        except ValueError:
            pass
        else:
            raise AssertionError(f"{label}: a truncated ZRE? was accepted")


def test_parse_zre_strips_the_alpha_padding_and_the_prefix():
    for label, module in ALL_EDITIONS:
        zr, zi, freq, status, ref = module.parse_zre(
            "ZRE=1.0e3 -2.0e3 1000.0 2 1\x00\x10\r\n")
        assert (zr, zi, freq) == (1000.0, -2000.0, 1000.0), label
        assert (status, ref) == (2, 1), label


def test_parse_inttyp_reads_the_numeric_interface_code():
    for label, module in ALL_EDITIONS:
        assert module.parse_inttyp("INTTYP=5 12345") == 5, label
        assert module.parse_inttyp(" 5 12345 \x00") == 5, label
        assert module.ZG4_INTERFACE_CODE == 5, label


# ------------------------------------------------------ the frequency grid

def test_every_edition_sweeps_exactly_the_same_default_grid():
    """A GUI scan and a CLI scan must stay comparable point-for-point."""
    assert _grid(visa_ed) == cli_ed.FREQUENCIES_HZ
    assert _grid(bit32_ed) == cli_ed.FREQUENCIES_HZ


def test_both_guis_offer_exactly_the_same_presets():
    assert visa_ed.FREQ_PRESETS == bit32_ed.FREQ_PRESETS
    assert visa_ed.DEFAULT_FREQ_PRESET == bit32_ed.DEFAULT_FREQ_PRESET
    assert visa_ed.DEFAULT_FREQ_PRESET in visa_ed.FREQ_PRESETS


def test_the_default_grid_runs_from_20_hz_to_exactly_1_mhz():
    for label, module in ALL_EDITIONS:
        freqs = _grid(module)
        assert freqs[0] == 20.0, label
        assert freqs[-1] == 1e6, label
        assert len(freqs) == 115, label


def test_the_ten_mhz_preset_ends_on_exactly_10_mhz():
    for label, module in GUI_EDITIONS:
        freqs = module.FREQUENCIES_40PT_10MHZ
        assert freqs[0] == 20.0, label
        assert freqs[-1] == 1e7, label
        assert len(freqs) == 40, label


def test_every_preset_is_strictly_increasing_and_inside_the_hardware_span():
    for label, module in GUI_EDITIONS:
        for name, freqs in module.FREQ_PRESETS.items():
            assert all(b > a for a, b in zip(freqs, freqs[1:])), (label, name)
            assert all(module.FREQ_HW_MIN <= f <= module.FREQ_HW_MAX
                       for f in freqs), (label, name)


def test_the_1_mhz_grid_sits_under_the_flat_3_vrms_ceiling():
    """Nothing in that sweep reaches the 4 MHz step-down, so 3 Vrms binds."""
    for label, module in ALL_EDITIONS:
        assert min(module.acv_ceiling(f) for f in _grid(module)) == 3.0, label


def test_the_10_mhz_preset_binds_the_acv_to_the_lower_2_vrms_ceiling():
    """The ZG4 ceiling steps down at 4 MHz, so this preset must too."""
    for label, module in GUI_EDITIONS:
        freqs = module.FREQUENCIES_40PT_10MHZ
        assert min(module.acv_ceiling(f) for f in freqs) == 2.0, label

        params = _good_params(module)
        params["frequencies"] = freqs
        params["acv"] = 2.5           # legal at 1 MHz, not at 10 MHz
        try:
            module.validate_parameters(params)
        except ValueError as exc:
            assert "AC voltage" in str(exc), label
        else:
            raise AssertionError(
                f"{label}: 2.5 Vrms was accepted on a sweep reaching 10 MHz")

        params["acv"] = 2.0           # exactly the ceiling: allowed
        module.validate_parameters(params)


# ------------------------------------------------- WinDETA conversion rules

def test_sigma_imaginary_subtracts_the_vacuum_displacement():
    """WinDETA's sigma* = i.w.eps0.(eps* - 1): sig'' uses (eps'-1), not eps'."""
    for label, module in ALL_EDITIONS:
        c0 = module.compute_c0(10.0, 1.0)
        f = 1.0e5
        # A pure capacitor of exactly C0 gives eps' = 1, eps'' = 0, so a
        # sig'' built on eps' rather than (eps'-1) would be non-zero here.
        omega = 2 * math.pi * f
        zi = -1.0 / (omega * c0)
        row = module.impedance_to_dielectric(1e-12, zi, f, c0)
        eps1, eps2, sig2 = row[1], row[2], row[8]
        assert abs(eps1 - 1.0) < 1e-6, (label, eps1)
        assert abs(eps2) < 1e-6, (label, eps2)
        assert abs(sig2) < 1e-12, (label, sig2)


def test_zp_columns_are_the_parallel_equivalent_not_series_r_and_x():
    for label, module in ALL_EDITIONS:
        c0 = module.compute_c0(10.0, 1.0)
        zr, zi, f = 1000.0, -2000.0, 1000.0
        row = module.impedance_to_dielectric(zr, zi, f, c0)
        z_mag_sq = zr ** 2 + zi ** 2
        assert abs(row[5] - z_mag_sq / zr) < 1e-6, label
        assert abs(row[6] + z_mag_sq / zi) < 1e-6, label
        # ... and therefore NOT the raw series values.
        assert abs(row[5] - zr) > 1.0, label


def test_the_conversion_returns_the_nine_windeta_columns():
    for label, module in ALL_EDITIONS:
        row = module.impedance_to_dielectric(
            1000.0, -2000.0, 1000.0, module.compute_c0(10.0, 1.0))
        assert len(row) == 9, label
        assert row[0] == 1000.0, label


def test_the_windeta_header_puts_zp_before_sigma():
    for label, module in ALL_EDITIONS:
        header = module.WINDETA_HEADER
        assert header.index("Zp'") < header.index("Sig'"), label


def test_zero_and_degenerate_inputs_do_not_divide_by_zero():
    for label, module in ALL_EDITIONS:
        c0 = module.compute_c0(10.0, 1.0)
        module.impedance_to_dielectric(0.0, 0.0, 0.0, c0)    # must not raise
        module.impedance_to_dielectric(0.0, 0.0, 1000.0, 0.0)


def test_compute_c0_refuses_a_non_positive_geometry():
    for label, module in ALL_EDITIONS:
        for diameter, thickness in ((0.0, 1.0), (10.0, 0.0), (-1.0, 1.0)):
            try:
                module.compute_c0(diameter, thickness)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"{label}: C0 accepted {diameter}/{thickness}")


def test_c0_matches_the_parallel_plate_formula():
    for label, module in ALL_EDITIONS:
        diameter_mm, thickness_mm = 10.0, 1.0
        area = math.pi * ((diameter_mm * 1e-3) / 2.0) ** 2
        expected = 8.8541878128e-12 * area / (thickness_mm * 1e-3)
        assert abs(module.compute_c0(diameter_mm, thickness_mm) - expected) \
            < 1e-18, label


# ---------------------------------------------- connection identity check

class _ConnectHarness:
    """Make either edition's connect() open a FakeAlpha instead of hardware.

    The VISA edition opens through a ResourceManager; the 32-bit edition
    builds an AlphaGpibSession on a driver handle. Both are swapped here so
    the same connect() assertions run against both.
    """

    def __init__(self, module, fake):
        self.module = module
        self.fake = fake

    def __enter__(self):
        fake = self.fake
        backend = self.module.AlphaAN_Backend()

        class _RM:
            def open_resource(self, address):
                return fake

        backend.rm = _RM()
        if hasattr(self.module, "AlphaGpibSession"):
            backend.driver = object()          # any truthy driver handle
            self._old_session = self.module.AlphaGpibSession
            self.module.AlphaGpibSession =                 lambda driver, board, pad, **kw: fake
        else:
            self._old_session = None
        return backend

    def __exit__(self, *exc):
        if self._old_session is not None:
            self.module.AlphaGpibSession = self._old_session
        return False


def test_connect_refuses_anything_that_is_not_a_zg4_interface():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"*IDN?": "NOVOCONTROL,ALPHA-A,1,1",
                                  "INTTYP?": "INTTYP=3 999"})
        with _ConnectHarness(module, fake) as backend:
            try:
                backend.connect("GPIB0::5::INSTR")
            except ConnectionError as exc:
                assert "ZG4" in str(exc), label
            else:
                raise AssertionError(
                    f"{label}: a non-ZG4 interface was accepted")
        assert fake.closed is True, f"{label}: half-open session left behind"


def test_a_failed_handshake_leaves_no_half_open_session():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"*IDN?": "NOVOCONTROL,ALPHA-A,1,1",
                                  "INTTYP?": "INTTYP=3 999"})
        with _ConnectHarness(module, fake) as backend:
            try:
                backend.connect("GPIB0::5::INSTR")
            except ConnectionError:
                pass
            assert backend.instrument is None, label


def test_a_successful_connect_leaves_the_generator_untouched():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"*IDN?": "NOVOCONTROL,ALPHA-A,1,1",
                                  "INTTYP?": "INTTYP=5 999"})
        with _ConnectHarness(module, fake) as backend:
            backend.connect("GPIB0::5::INSTR")
        assert fake.commands == ["*IDN?", "INTTYP?"], (label, fake.commands)


def test_connect_clears_stale_bus_status_before_the_first_srq_wait():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"*IDN?": "NOVOCONTROL,ALPHA-A,1,1",
                                  "INTTYP?": "INTTYP=5 999"})
        with _ConnectHarness(module, fake) as backend:
            backend.connect("GPIB0::5::INSTR")
        assert fake.stb_reads >= 1, label


# ----------------------------------------------------- setup ordering

def test_initialisation_applies_the_settings_after_the_reset():
    """*RST resets ACV and MTM, so settings written before it are lost."""
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"INTTYP?": "INTTYP=5 999"})
        backend = _backend(module, fake)
        backend.initialize_instrument(_good_params(module))
        commands = fake.commands
        assert commands.index("*RST") < commands.index("ACV=1"), \
            (label, commands)
        assert commands.index("*RST") < commands.index("MTM=0.5"), \
            (label, commands)


def test_initialisation_selects_impedance_mode_and_the_reference_scheme():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"INTTYP?": "INTTYP=5 999"})
        backend = _backend(module, fake)
        backend.initialize_instrument(_good_params(module))
        for expected in ("MODE=IMP", "ZREFMODE=-3", "ZLLCOR=1", "ZSLCAL=1",
                         "FRS=2", "DRS=0 0"):
            assert expected in fake.commands, (label, expected)


def test_initialisation_refuses_an_unsafe_parameter_set_before_connecting():
    for label, module in GUI_EDITIONS:
        fake = FakeAlpha(answers={"INTTYP?": "INTTYP=5 999"})
        backend = _backend(module, fake)
        params = _good_params(module)
        params["acv"] = 99.0
        try:
            backend.initialize_instrument(params)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{label}: 99 Vrms reached the instrument")
        assert fake.commands == [], (label, fake.commands)


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
