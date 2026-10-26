"""CV_AlphaAN_GUI.py v1.0 and its plain-script twin against a fake Alpha-AN:
the common Alpha backend rules (acks read, MST written, ZG4 ceiling, safe
state), the DC bias (only one method forms DCV/DCE, switched on at 0 V,
ramped in 0.5 V steps, 20 V cap before any byte, ramped to 0 V and off
BEFORE the generator safe state and *RST on every exit, shutdown never
raises), f_actual used for the conversion with files keyed by the
requested frequency, status 6 aborting, NaN for no-result statuses, no
bus-error retry, validation before any instrument is opened, legacy
columns first, header contents, ASCII and plotter-compatible files, no
pop-ups and the two modules sharing one byte-identical run path.
"""

import ast
import inspect
import math
import os
import queue
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alphaan_port_test_support import (  # noqa: E402
    FakeVisaError, connected_backend, run_common_backend_checks)
from cv_alphaan_test_support import (  # noqa: E402
    FakeCVAlpha, GUI_PATH, MODULES, SCRIPT_PATH, base_params, bias_commands,
    column, gui, install_pyvisa, last_index, log_lines, make_link,
    plotter_reads, read_data_file, run_cv, run_module_tests, run_worker,
    script)


def _tmp():
    return tempfile.mkdtemp(prefix="cv_alpha_test_")


def _expect(exc_type, fn):
    try:
        fn()
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__}")


def _backend(fake):
    """connected_backend() plus the bias state AlphaAN_Backend.__init__ sets."""
    backend, fake = connected_backend(gui, fake)
    backend._bias_touched = False
    backend._bias_level = 0.0
    return backend, fake


def _assert_bias_off_before_safe_state(fake):
    """Bias at 0 V and off, and the last DCE=0 came before the generator
    park (ACV=0), ZCONSPL=0 and *RST of the close."""
    cmds = fake.commands
    assert fake.v == 0.0 and not fake.bias_on, (fake.v, fake.bias_on)
    off = last_index(cmds, "DCE=0")
    assert off < last_index(cmds, "ACV=0") < last_index(cmds, "*RST"), cmds[-12:]
    assert off < last_index(cmds, "ZCONSPL=0")
    assert fake.closed


# -- the common Alpha backend rules ---------------------------------------------

def test_common_alpha_backend_rules_hold_in_both_copies():
    for mod, path in ((gui, GUI_PATH), (script, SCRIPT_PATH)):
        run_common_backend_checks(mod, path, dc_bias_allowed=True)


def test_dc_bias_commands_are_formed_in_one_method_only():
    """Every string that starts with DCV or DCE (an f-string or a constant
    used as a value, not a docstring or comment) sits in _dc_bias_command."""
    for path in (GUI_PATH, SCRIPT_PATH):
        tree = ast.parse(open(path, encoding="utf-8").read())
        allowed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_dc_bias_command":
                allowed |= {id(n) for n in ast.walk(node)}
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)):
                docstrings.add(id(body[0].value))
        hits = []
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            if (text and text.startswith(("DCV", "DCE")) and id(node) not in docstrings
                    and id(node) not in allowed):
                hits.append((node.lineno, text))
        assert hits == [], (path, hits)
        assert "UNVERIFIED against the Alpha manual" in inspect.getsource(
            gui.AlphaAN_Backend._dc_bias_command)


def test_dc_bias_command_strings_and_cap():
    backend, fake = _backend(FakeCVAlpha())
    backend._dc_bias_command("level", 1.23456)
    backend._dc_bias_command("level", -0.0)
    backend._dc_bias_command("enable")
    backend._dc_bias_command("disable")
    assert fake.commands == ["DCV=1.235", "DCV=0.000", "DCE=1", "DCE=0"]
    assert all(fake.kind_of(c) == "query" for c in fake.commands)   # acks read
    for bad in (20.001, -25.0, float("nan")):
        _expect(ValueError, lambda: backend._dc_bias_command("level", bad))
    assert fake.commands == ["DCV=1.235", "DCV=0.000", "DCE=1", "DCE=0"]  # nothing sent
    backend._dc_bias_command("level", 20.0)                          # the cap itself is allowed
    assert fake.v == 20.0
    # a refused bias command raises in strict mode, returns False when tolerant
    backend, fake = _backend(FakeCVAlpha(na_when_off=True))
    _expect(RuntimeError, lambda: backend._dc_bias_command("level", 1.0))
    assert backend._dc_bias_command("level", 1.0, tolerant=True) is False
    assert backend._bias_level == 0.0


def test_link_ramp_refuses_over_cap_before_any_byte():
    for mod in MODULES:
        fake = FakeCVAlpha()
        link = make_link(mod, fake)
        link.open()
        n = len(fake.traffic)
        _expect(ValueError, lambda: link.ramp_bias(20.5))
        _expect(ValueError, lambda: link.ramp_bias(float("nan")))
        assert len(fake.traffic) == n
        assert mod.V_ABS_MAX == 20.0


# -- validation before any instrument is opened -------------------------------------

def test_validate_params_refuses_bad_alpha_settings_and_clamps_to_20_v():
    folder = _tmp()
    for mod in MODULES:
        for bad in (dict(ac_bias=3.0, freq_list="5000000"), dict(ac_bias=0), dict(ac_bias=3.5),
                    dict(mtm=0), dict(wire_mode="5"), dict(freq_list="30000000"),
                    dict(freq_list=""), dict(delay=-1), dict(v_step=0),
                    dict(shape=mod.SHAPE_LADDER, ladder="0.5, 21")):
            _expect(ValueError, lambda: mod.validate_params(base_params(folder, **bad)))
        p = mod.validate_params(base_params(folder, cycle_mode=False, v_start=-25, v_stop=30))
        assert p["clamped"] and p["v_start"] == -20.0 and p["v_stop"] == 20.0
        assert max(abs(v) for _, _, v in p["waveform"]) <= 20.0
        p = mod.validate_params(base_params(folder, ac_bias=3.0, freq_list="1000, 4000000"))
        assert p["mtm"] == 0.5 and p["wire_mode"] == "2" and p["freqs"] == [1000.0, 4e6]


def test_worker_rechecks_settings_and_bias_before_opening_the_instrument():
    folder = _tmp()
    for mod in MODULES:
        p = mod.validate_params(base_params(folder))
        for tamper in ({"ac_bias": 3.0, "freqs": [5e6]}, {"wire_mode": "7"},
                       {"waveform": [(1, 1, 0.0), (1, 1, 25.0)]}):
            fake = FakeCVAlpha()
            worker, msgs, files, log_path, link = run_cv(fake, folder, mod=mod, params={**p, **tamper})
            assert worker.end_reason.startswith("error"), worker.end_reason
            assert fake.traffic == [] and link.rms == [] and files == []
            assert msgs[-1][0] == "done"


# -- run path ----------------------------------------------------------------------

def test_complete_run_rows_columns_f_actual_and_bias_off_before_safe_state():
    folder = _tmp()
    fake = FakeCVAlpha(f_offset=2e-3)
    worker, msgs, files, log_path, _ = run_cv(fake, folder, n_cycles=2)
    assert worker.end_reason == "complete" and len(files) == 1
    header, columns, rows, trailer = read_data_file(files[0])
    assert columns[:2] == ["Voltage(V)", "Frequency"]                      # legacy columns first
    assert columns[2:20] == gui.DERIVED_HEADER.split("\t")
    assert columns[20:] == ["t_s", "Timestamp", "Cycle", "Segment", "Vdc_mon(V)", "Idc_mon(A)",
                            "Vac_mon(V)", "Iac_mon(A)", "R_raw", "X_raw", "Status", "f_actual(Hz)"]
    assert all(len(r) == len(columns) for r in rows)
    expect = gui.build_waveform(True, gui.SHAPE_POS_FIRST, 1.0, 0, 0, 0.5, 2)
    assert column(rows, columns, "Voltage(V)") == [v for _, _, v in expect]
    assert column(rows, columns, "Cycle", int) == [c for c, _, _ in expect]
    assert column(rows, columns, "Segment", int) == [s for _, s, _ in expect]
    assert set(column(rows, columns, "Frequency")) == {1000.0}             # keyed by the request
    assert set(column(rows, columns, "f_actual(Hz)")) == {1002.0}
    assert set(column(rows, columns, "Status", int)) == {2}
    # Cp is exact only when the conversion used f_actual
    assert all(abs(c - 1e-9) / 1e-9 < 1e-6 for c in column(rows, columns, "Cp"))
    assert all(math.isnan(x) for x in column(rows, columns, "Vdc_mon(V)"))
    assert column(rows, columns, "R(Rs)") == column(rows, columns, "R_raw")
    # the bias followed the waveform: every row was measured at its own voltage
    assert len(trailer) == 1 and trailer[0].startswith("END reason=complete | points=%d" % len(rows))
    events = log_lines(log_path)
    assert events[0].startswith("START module=CV_AlphaAN_GUI v1.0")
    assert events[-1].startswith("END reason=complete")
    assert any("UNVERIFIED" in m[1] for m in msgs if m[0] == "log")
    _assert_bias_off_before_safe_state(fake)
    assert msgs[-1] == ("done", "complete")


def test_each_point_is_measured_at_the_bias_its_row_claims():
    folder = _tmp()
    fake = FakeCVAlpha()
    at_measure = []
    fake.on_measure = lambda idx: at_measure.append((fake.v, fake.bias_on, fake.freq))
    worker, _, files, _, _ = run_cv(fake, folder, cycle_mode=False, v_start=-1.0, v_stop=1.0,
                                    freq_list="5000")
    _, columns, rows, _ = read_data_file(files[0])
    assert [v for v, _, _ in at_measure] == column(rows, columns, "Voltage(V)")
    assert all(on and f == 5000.0 for _, on, f in at_measure)


def test_bias_comes_on_at_zero_and_is_ramped_in_half_volt_steps():
    folder = _tmp()
    fake = FakeCVAlpha(na_when_off=True)       # DCV refused while DCE is 0
    worker, _, _, _, _ = run_cv(fake, folder, cycle_mode=False, v_start=-2.0, v_stop=2.0, v_step=2.0)
    assert worker.end_reason == "complete"
    cmds = bias_commands(fake)
    assert cmds[:3] == ["DCV=0.000", "DCE=1", "DCV=0.000"], cmds[:3]
    # *RST (in configure) came before the first bias byte
    assert fake.commands.index("*RST") < fake.commands.index("DCV=0.000")
    levels = [float(c[4:]) for c in cmds if c.startswith("DCV=")]
    accepted = [v for v, _ in fake.bias_history]
    jumps = [abs(b - a) for a, b in zip(accepted, accepted[1:])]
    assert max(jumps) <= gui.RAMP_STEP_V + 1e-9, jumps
    assert max(abs(v) for v in levels) <= gui.V_ABS_MAX
    _assert_bias_off_before_safe_state(fake)


def test_status_six_aborts_with_the_bias_ramped_off():
    folder = _tmp()
    fake = FakeCVAlpha(status_at={3: 6})
    worker, msgs, files, log_path, _ = run_cv(fake, folder)
    assert worker.end_reason.startswith("error: Alpha-AN status 6"), worker.end_reason
    _, _, rows, trailer = read_data_file(files[0])
    assert len(rows) == 3 and trailer[0].startswith("END reason=error: Alpha-AN status 6")
    assert any(m[0] == "error" for m in msgs) and msgs[-1][0] == "done"
    _assert_bias_off_before_safe_state(fake)


def test_every_non_valid_status_gives_nan_derived_values():
    """Changed 23 Oct 2026: range errors 3/4/5 used to keep their derived
    numbers; now only status 2 is ever written as data. The row, its raw
    Zs', Zs'' and the Status column stay (32-column layout unchanged)."""
    folder = _tmp()
    fake = FakeCVAlpha(status_at={1: 0, 2: 1, 3: 4, 5: 3, 6: 5})
    worker, msgs, files, _, _ = run_cv(fake, folder)
    _, columns, rows, _ = read_data_file(files[0])
    assert len(columns) == 32
    status = column(rows, columns, "Status", int)
    assert status[:7] == [2, 0, 1, 4, 2, 3, 5]
    for name in columns[2:20]:
        vals = column(rows, columns, name)
        assert all(math.isnan(vals[i]) for i in (1, 2, 3, 5, 6)), name
        assert not math.isnan(vals[0]) and not math.isnan(vals[4]), name
    assert not any(math.isnan(x) for x in column(rows, columns, "R_raw"))
    assert any("status 4" in m[1] and "Current out of range" in m[1] for m in msgs if m[0] == "log")
    headers, data = plotter_reads(files[0])
    assert data.shape == (len(rows), len(columns))


def test_a_bus_error_is_not_retried_and_still_ramps_the_bias_off():
    folder = _tmp()
    fake = FakeCVAlpha(fail_at={4})
    worker, msgs, files, log_path, link = run_cv(fake, folder, cycle_mode=False, v_start=0.0,
                                                 v_stop=3.0, v_step=0.5)
    assert worker.end_reason.startswith("error") and "VI_ERROR_TMO" in worker.end_reason
    assert fake.commands.count("MST") == 5 and fake.measurements == 5      # no second attempt
    assert len(link.rms) == 1                                               # never reopened
    events = log_lines(log_path)
    assert [e for e in events if e.startswith("BUS_ERROR")] == [
        "BUS_ERROR what=measure / error=VI_ERROR_TMO: timeout"]
    assert not any(e.startswith(("RETRY", "RECONNECT")) for e in events)
    _assert_bias_off_before_safe_state(fake)


def test_stop_ends_the_file_and_zeroes_the_bias():
    folder = _tmp()
    fake = FakeCVAlpha()
    worker, msgs, files, log_path, _ = run_cv(fake, folder, n_cycles=5, stop_at_measure=3)
    assert worker.end_reason == "stop_button"
    _, _, rows, trailer = read_data_file(files[0])
    assert len(rows) == 4 and trailer[0].startswith("END reason=stop_button | points=4")
    assert log_lines(log_path)[-1].startswith("END reason=stop_button")
    assert msgs[-1] == ("done", "stop_button")
    _assert_bias_off_before_safe_state(fake)


def test_locked_data_file_still_zeroes_the_bias_and_sends_done():
    for mod in MODULES:
        folder = _tmp()
        fake = FakeCVAlpha()
        p = mod.validate_params(base_params(folder, cycle_mode=False, v_start=-2.0, v_stop=2.0))
        worker = mod.CVWorker(p, make_link(mod, fake), threading.Event(), queue.Queue(),
                              sleep=lambda s: None)
        real_append = worker._append

        def append(path, text):
            if path == worker.data_path and worker.points_in_file >= 1:
                raise PermissionError("locked")
            real_append(path, text)
        worker._append = append
        msgs = run_worker(worker)
        assert msgs[-1][0] == "done" and worker.end_reason.startswith("error")
        _assert_bias_off_before_safe_state(fake)


# -- shutdown order and robustness ----------------------------------------------------

def test_close_with_a_running_task_retries_the_bias_off_after_mbk():
    """A bias command is refused (MR) while a task runs: the first bias-off
    fails, MBK stops the task, the bias-off is sent again, then *RST."""
    fake = FakeCVAlpha(mr_when_running=True)
    backend, fake = _backend(fake)
    backend._dc_bias_command("enable")
    backend._dc_bias_command("level", 2.0)
    fake.task_running = True
    backend._task_may_be_running = True
    n = len(fake.commands)
    backend.close_instrument()
    tail = fake.commands[n:]
    assert tail[0] == "DCV=1.500" and "MBK" in tail
    assert tail.index("MBK") < last_index(tail, "DCE=0") < tail.index("*RST")
    assert fake.v == 0.0 and not fake.bias_on and backend.instrument is None


def test_shutdown_never_raises_when_the_bus_is_gone():
    for mod in MODULES:
        fake = FakeCVAlpha()
        link = make_link(mod, fake)
        link.open()
        link.backend.dc_bias_on()
        link.ramp_bias(3.0)
        fake.dead = True
        assert link.force_zero_bias() is False   # every command raises inside; nothing escapes
        assert link.close() is False
        assert link.inst is None and fake.closed
        assert link.force_zero_bias() is False   # no session: still not confirmed
        assert link.close() is False


def test_close_sends_no_bias_command_when_the_bias_was_never_touched():
    backend, fake = connected_backend(gui, FakeCVAlpha())
    backend.close_instrument()
    assert bias_commands(fake) == [] and fake.commands[:2] == ["ACV=0", "ZCONSPL=0"]


# -- files and header ---------------------------------------------------------------

def test_frequencies_run_in_typed_order_one_file_each():
    folder = _tmp()
    fake = FakeCVAlpha()
    worker, _, files, _, _ = run_cv(fake, folder, freq_list="100000, 1000, 0.5", cycle_mode=False)
    assert [os.path.basename(f).split("_CV_")[1] for f in files] == [
        "100000Hz.txt", "1000Hz.txt", "0p5Hz.txt"]
    for k, path in enumerate(files):
        header, columns, rows, _ = read_data_file(path)
        assert header[0].endswith(f"file {k + 1} of 3")
        assert set(column(rows, columns, "Frequency")) == {[1e5, 1e3, 0.5][k]}
    gfr = [c for c in fake.commands if c.startswith("GFR=")]
    assert [float(c[4:]) for c in gfr] == [1e5] * 5 + [1e3] * 5 + [0.5] * 5


def test_header_states_everything_a_reader_needs():
    folder = _tmp()
    worker, _, files, log_path, _ = run_cv(FakeCVAlpha(), folder, n_cycles=2, mtm=1.5, wire_mode="4")
    header, _, rows, _ = read_data_file(files[0])
    joined = "\n".join(header)
    for needle in ("PICA CV_AlphaAN_GUI v1.0", "[instrument] idn=ALPHA Analyzer", "interface_code=5",
                   "visa=GPIB0::10::INSTR", "[sample] name=Sample_A", "mode=cycle",
                   "shape=positive first", "v_max_v=1", "points_per_file=%d" % len(rows),
                   "ramp_step_v=0.5", "v_abs_max_v=20", "[alpha] acv_vrms_set=1", "acv_ceiling_vrms=3",
                   "mtm_s=1.5", "wire_mode=4", "UNVERIFIED", "monitors=none", "f_actual(Hz)",
                   "status: 2 valid", "6 source disconnected", "waveform: DC staircase 0 -> +1 -> 0",
                   "log_file=" + os.path.basename(log_path)):
        assert needle in joined, needle
    assert "E4980" not in joined and "opt=" not in joined


def test_files_are_ascii_and_plotter_compatible():
    folder = _tmp()
    worker, _, files, log_path, _ = run_cv(FakeCVAlpha(status_at={1: 1}), folder, n_cycles=2)
    for path in files + [log_path]:
        with open(path, "rb") as fh:
            fh.read().decode("ascii")
    headers, data = plotter_reads(files[0])
    _, columns, rows, _ = read_data_file(files[0])
    assert headers == columns and data.shape == (len(rows), len(columns))
    cp = data[:, headers.index("Cp")]
    assert math.isnan(cp[1]) and not math.isnan(cp[0])


def test_ladder_run_and_waveform_functions_are_unchanged():
    pts = gui.build_waveform(True, gui.SHAPE_LADDER, 0, 0, 0, 0.5, 0, ladder=[0.5, 1.0])
    assert pts[0] == (1, 1, 0.0) and pts[-1][2] == 0.0
    folder = _tmp()
    fake = FakeCVAlpha()
    worker, _, files, _, _ = run_cv(fake, folder, shape=gui.SHAPE_LADDER, ladder="0.5, 1")
    assert worker.end_reason == "complete"
    _, columns, rows, _ = read_data_file(files[0])
    assert column(rows, columns, "Voltage(V)") == [v for _, _, v in pts]


# -- the window (no Tk root) ----------------------------------------------------------

def test_window_never_opens_a_dialog_during_or_after_a_run():
    for name in ("_handle_sweep_completion", "_handle_sweep_error", "_handle_message",
                 "_poll_queue", "_poll_worker_stopped", "_finalize_stop", "_show_alarm",
                 "_force_bias_off_from_window"):
        assert "messagebox" not in inspect.getsource(getattr(gui.LCR_Freq_GUI, name)), name


def test_window_collects_mtm_and_wire_mode_not_lcr_fields():
    src = inspect.getsource(gui.LCR_Freq_GUI._collect_params)
    assert '"mtm"' in src and '"wire_mode"' in src
    for gone in ("aper", "alc_enabled", "corr_enabled", "cable_len"):
        assert gone not in src
    assert "AlphaANLink(" in inspect.getsource(gui.LCR_Freq_GUI.start_sweep)


# -- the two modules share one run path ------------------------------------------------

def test_script_twin_shares_byte_identical_backend_link_worker_and_pure_functions():
    names = ["ascii_text", "header_value", "fmt_cell", "iso_now", "timestamp_for_name",
             "wait_with_stop", "_ramp", "half_leg", "return_leg", "build_cycle", "build_waveform",
             "segment_text", "ramp_time_s", "waveform_text", "parse_freq_list", "parse_ladder",
             "derived_columns", "nan_derived", "validate_params", "disabled_fields",
             "check_bias_cap", "freq_tag", "acv_ceiling", "parse_zre", "parse_inttyp",
             "point_timeout_s", "stop_wait_s",
             "validate_alpha_parameters", "AlphaAN_Backend", "AlphaANLink", "CVWorker"]

    def shape(obj):
        tree = ast.parse(inspect.getsource(obj).strip())
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
        return ast.dump(tree)

    for name in names:
        assert shape(getattr(gui, name)) == shape(getattr(script, name)), name
    assert gui.COLUMN_ROW == script.COLUMN_ROW and gui.SHAPES == script.SHAPES
    for const in ("V_ABS_MAX", "RAMP_STEP_V", "RAMP_DWELL_S", "FREQ_HW_MIN",
                  "FREQ_HW_MAX", "STATUS_FATAL", "MTM_DEFAULT_S", "SRQ_TIMEOUT_MEASURE_S",
                  "BIAS_OFF_FAILED_TEXT"):
        assert getattr(gui, const) == getattr(script, const), const


def test_script_twin_runs_the_same_fake():
    folder = _tmp()
    fake = FakeCVAlpha()
    worker, _, files, _, _ = run_cv(fake, folder, mod=script)
    assert worker.end_reason == "complete"
    header, columns, _, trailer = read_data_file(files[0])
    assert columns == gui.COLUMN_ROW.split("\t")
    assert header[0].startswith("PICA CV_AlphaAN_Simple_Instrument_Control v1.0")
    assert trailer[0].startswith("END reason=complete")
    _assert_bias_off_before_safe_state(fake)
    p = script.params_from_user_input()
    assert p["lcr_visa"] == "GPIB0::10::INSTR" and p["mtm"] == 0.5 and p["wire_mode"] == "2"


def test_open_refuses_a_non_zg4_interface_and_sends_no_bias():
    for mod in MODULES:
        folder = _tmp()
        fake = FakeCVAlpha()
        fake.answers["INTTYP?"] = "INTTYP=3 1"
        worker, _, files, _, _ = run_cv(fake, folder, mod=mod)
        assert worker.end_reason.startswith("error") and "ZG4" in worker.end_reason
        assert bias_commands(fake) == [] and files == []


def test_source_hygiene():
    for path in (GUI_PATH, SCRIPT_PATH):
        text = open(path, encoding="utf-8").read()
        assert "V_ABS_MAX = 20.0" in text and "pymeasure" not in text
        assert "never uses DC bias" not in text and "Not yet run on the hardware" in text
        assert "SN 284" in text and "6.25_200_40_284_ANB_16012009_G2" in text
        assert "UNVERIFIED" in text
    open(SCRIPT_PATH, "rb").read().decode("ascii")
    assert install_pyvisa is not None


# -- pre-lab fixes, 23 Oct 2026 ---------------------------------------------------------

def test_point_timeout_scales_with_mtm_and_period_and_measure_point_uses_it():
    for mod in MODULES:
        assert mod.point_timeout_s(1000.0, 0.5) == mod.SRQ_TIMEOUT_MEASURE_S
        assert mod.point_timeout_s(0.01, 0.5) == 4.0 * 100.0 + 60.0      # period-bound
        assert mod.point_timeout_s(1000.0, 100.0) == 4.0 * 100.0 + 60.0  # MTM-bound
    backend, fake = _backend(FakeCVAlpha())
    backend.params = {"mtm": 100.0}
    backend.measure_point(1000.0)
    assert ("wait_for_srq", 460000) in fake.traffic


def test_stop_window_covers_the_worst_point_and_a_full_bias_ramp():
    for mod in MODULES:
        need = mod.point_timeout_s(0.01, 0.5) + mod.ramp_time_s(2 * mod.V_ABS_MAX) + 15.0
        assert mod.stop_wait_s([1000.0, 0.01], 0.5) >= need
        assert mod.stop_wait_s([1000.0], 0.5) >= 120.0 + mod.ramp_time_s(40.0) + 15.0
        assert mod.stop_wait_s([1000.0], 0.5) > mod.RESET_READY_TIMEOUT_S


def test_wrong_idn_gets_no_shutdown_or_bias_commands():
    for mod in MODULES:
        folder = _tmp()
        fake = FakeCVAlpha()
        fake.answers["*IDN?"] = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1,C30"
        worker, msgs, files, _, link = run_cv(fake, folder, mod=mod)
        assert worker.end_reason.startswith("error") and "not a Novocontrol Alpha" in worker.end_reason
        assert fake.commands == ["*IDN?"] and fake.closed and files == []
        assert link.inst is None and msgs[-1][0] == "done"


def _dies_at(fake, volts):
    """The bus is lost right after the point at `volts` (bias still on)."""
    def on_measure(idx):
        if fake.v >= volts:
            fake.dead = True
    fake.on_measure = on_measure


def test_bus_dying_at_5_v_mid_sweep_surfaces_bias_off_failed():
    for mod in MODULES:
        folder = _tmp()
        fake = FakeCVAlpha()
        _dies_at(fake, 5.0)
        worker, msgs, files, log_path, link = run_cv(fake, folder, mod=mod, cycle_mode=False,
                                                     v_start=0.0, v_stop=8.0, v_step=1.0)
        assert worker.end_reason.startswith("error") and worker.bias_confirmed is False
        assert fake.v == 5.0 and fake.bias_on              # honestly reported, not "off"
        assert ("bias", False, mod.BIAS_OFF_FAILED_TEXT) in msgs
        assert not any(m[0] == "bias" and m[1] for m in msgs)
        assert msgs[-1][0] == "done"
        _, _, rows, trailer = read_data_file(files[0])
        assert len(rows) == 6 and trailer[0].endswith("bias=BIAS_OFF_FAILED"), trailer
        events = log_lines(log_path)
        assert sum(e.startswith("BIAS_OFF_FAILED") for e in events) == 1
        assert events[-1].startswith("END reason=error") and events[-1].endswith("bias=BIAS_OFF_FAILED")


def test_failed_bias_off_after_a_complete_file_is_appended_to_that_file():
    """The last point of a file is at 0 V, but the bias is still enabled
    (DCE=1): a bus lost there leaves DCE=0 unconfirmed."""
    folder = _tmp()
    fake = FakeCVAlpha()
    n_points = len(gui.validate_params(base_params(folder))["waveform"])
    fake.on_measure = lambda idx: setattr(fake, "dead", True) if idx == n_points - 1 else None
    worker, msgs, files, _, _ = run_cv(fake, folder)
    assert worker.end_reason == "complete" and worker.bias_confirmed is False
    _, _, rows, trailer = read_data_file(files[0])
    assert trailer[0].startswith("END reason=complete") and "bias=" not in trailer[0]
    assert len(trailer) == 2 and trailer[1].startswith("BIAS_OFF_FAILED after END"), trailer
    assert fake.bias_on


def test_a_good_run_reports_the_bias_confirmed_off():
    folder = _tmp()
    worker, msgs, files, log_path, _ = run_cv(FakeCVAlpha(), folder, mod=script)
    assert worker.bias_confirmed is True
    assert [m for m in msgs if m[0] == "bias"] == [("bias", True, "DC bias confirmed 0 V and off.")]
    assert msgs[-2][0] == "bias" and msgs[-1] == ("done", "complete")
    assert log_lines(log_path)[-1].endswith("bias=off")
    assert not any(e.startswith("BIAS_OFF_FAILED") for e in log_lines(log_path))


# -- the window, without a Tk root (stub widgets) ------------------------------------------

class _Widget:
    """Stub widget: remembers its config (state, text) and a value."""

    def __init__(self, value=""):
        self.value = value
        self.state = None
        self.texts = []

    def config(self, **kw):
        if "state" in kw:
            self.state = kw["state"]
        if "text" in kw:
            self.texts.append(kw["text"])

    def get(self):
        return self.value


class _Root:
    def __init__(self):
        self.after_calls = []
        self.bells = 0

    def after(self, ms, fn=None):
        self.after_calls.append(ms)

    def bell(self):
        self.bells += 1

    def destroy(self):
        pass


class _SpyLink:
    inst = object()

    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def force_zero_bias(self):
        self.calls.append("force_zero_bias")
        return self.ok

    def close(self):
        self.calls.append("close")
        return self.ok


def _window(**attrs):
    w = object.__new__(gui.LCR_Freq_GUI)
    w.root = _Root()
    w.logs = []
    w.log = w.logs.append
    w.alarms = []
    w._show_alarm = w.alarms.append
    for name in ("lbl_current_freq", "start_button", "stop_button", "scan_button",
                 "browse_button", "cycle_check", "lcr_combobox"):
        setattr(w, name, _Widget())
    w.wire_combobox = _Widget("2")
    w.shape_combobox = _Widget(gui.SHAPE_POS_FIRST)
    w.var_cycle = _Widget(False)
    w.entries = {k: _Widget(v) for k, v in (
        ("sample_name", "Sample_A"), ("v_start", "-1"), ("v_stop", "1"), ("v_step", "0.5"),
        ("ac_bias", "1"), ("freq_list", "1000"), ("delay", "0"), ("mtm", "0.5"),
        ("n_cycles", "1"), ("ladder", ""), ("ladder_pause", "0"))}
    w.data_queue = queue.Queue()
    w.stop_event = threading.Event()
    w.is_running = False
    w._stopping = False
    w._close_after_stop = False
    w._bias_confirmed = None
    w.alarm_banner = None
    w.link = None
    w.worker = None
    w.worker_thread = None
    w.freq_list = [1000.0]
    w.__dict__.update(attrs)
    return w


def _alive_thread():
    release = threading.Event()
    t = threading.Thread(target=release.wait, args=(30,), daemon=True)
    t.start()
    return t, release


def test_window_stop_waits_the_full_window_without_touching_the_link():
    t, release = _alive_thread()
    try:
        spy = _SpyLink()
        w = _window(is_running=True, worker_thread=t, link=spy, freq_list=[1000.0, 0.01],
                    worker=types.SimpleNamespace(p={"mtm": 0.5}))
        before = time.time()
        w.stop_sweep()
        need = gui.stop_wait_s([1000.0, 0.01], 0.5)
        assert w._stop_deadline - before >= need - 1.0 and need > 475.0
        assert spy.calls == [] and w.root.after_calls == [200]      # polling, link untouched
        w._poll_worker_stopped("")
        assert spy.calls == []
        # Changed 27 Oct 2026: past the window the worker may still be in
        # its shutdown (each command waiting out a 60 s timeout). The
        # window warns with the banner and keeps its hands off the link.
        w._stop_deadline = time.time() - 1.0
        w._poll_worker_stopped("")
        w._poll_worker_stopped("")
        assert spy.calls == [] and w.alarms == [gui.STOP_OVERDUE_TEXT]
        assert "messagebox" not in inspect.getsource(gui.LCR_Freq_GUI._poll_worker_stopped)
        # once the worker has exited with the session still open, the
        # window does the bias-off itself, and the overdue banner clears
        release.set()
        t.join(5)
        w._poll_worker_stopped("")
        assert spy.calls == ["force_zero_bias", "close"] and w._bias_confirmed is True
        assert w._overdue_alarm is False and w.lbl_current_freq.texts[-1] == "Measuring: STOPPED"
    finally:
        release.set()
        t.join(5)


def test_window_start_refuses_while_the_previous_worker_is_alive():
    t, release = _alive_thread()
    try:
        w = _window(worker_thread=t)

        def collect():
            raise AssertionError("Start went ahead")
        w._collect_params = collect
        w.start_sweep()
        assert any("still finishing" in m for m in w.logs)
    finally:
        release.set()
        t.join(5)


def test_window_reports_bias_off_only_when_confirmed():
    w = _window(_stopping=True)
    w._handle_message(("bias", False, gui.BIAS_OFF_FAILED_TEXT))
    assert w.alarms == [gui.BIAS_OFF_FAILED_TEXT] and w._bias_confirmed is False
    w._finalize_stop("")
    assert not any("bias 0 V, off" in m for m in w.logs)
    assert w.alarms[-1] == gui.BIAS_OFF_FAILED_TEXT
    w = _window(_stopping=True)
    w._handle_message(("bias", True, "DC bias confirmed 0 V and off."))
    w._finalize_stop("")
    assert w.alarms == [] and any("bias 0 V, off" in m for m in w.logs)
    w = _window(_stopping=True)                       # no word from the worker at all
    w._finalize_stop("")
    assert w.alarms == [gui.BIAS_OFF_FAILED_TEXT]
    # the window thread's own forced bias-off failing is surfaced too
    w = _window(_stopping=True, link=_SpyLink(ok=False))
    w._poll_worker_stopped("")
    assert w._bias_confirmed is False and w.alarms == [gui.BIAS_OFF_FAILED_TEXT]


# -- window bug hunt, 27 Oct 2026 --------------------------------------------------------------

def test_window_final_label_says_how_the_run_ended():
    """DONE / ERROR used to be overwritten by DISCHARGING, STOPPING... and
    finally an unconditional STOPPED."""
    cases = (
        ([("status", "DONE")], "complete", "Measuring: DONE"),
        ([("error", "boom")], "error: boom", "Measuring: ERROR (see console)"),
        ([], "stop_button", "Measuring: STOPPED by user"),
    )
    for before, reason, final in cases:
        w = _window(is_running=True)
        for msg in before + [("status", "DISCHARGING"), ("bias", True, "ok"), ("done", reason)]:
            w.data_queue.put(msg)
        if reason == "stop_button":
            w.stop_sweep()                       # the user's Stop, then the worker's "done"
        else:
            w._drain_queue()                     # "done" itself ends the run
        assert w.lbl_current_freq.texts[-1] == final, (reason, w.lbl_current_freq.texts)
        assert not w._stopping and w.alarms == []
    w = _window(_stopping=True)                  # no "done" from the worker at all
    w._finalize_stop("")
    assert w.lbl_current_freq.texts[-1] == "Measuring: STOPPED"
    # Start clears the previous run's end label and reason
    src = inspect.getsource(gui.LCR_Freq_GUI.start_sweep)
    assert 'text="Measuring: -- Hz"' in src and "self._end_reason = None" in src


def test_window_locks_every_input_during_a_run_and_restores_the_greying():
    w = _window()
    w._set_inputs_locked(True)
    widgets = list(w.entries.values()) + [w.wire_combobox, w.lcr_combobox, w.shape_combobox,
                                          w.cycle_check, w.browse_button]
    assert all(x.state == "disabled" for x in widgets)
    w._stopping = True
    w._finalize_stop("")
    assert w.wire_combobox.state == w.lcr_combobox.state == "readonly"
    assert w.cycle_check.state == w.browse_button.state == "normal"
    off = gui.disabled_fields(False, gui.SHAPE_POS_FIRST)          # linear mode
    for key, entry in w.entries.items():
        assert entry.state == ("disabled" if key in off else "normal"), key
    assert w.shape_combobox.state == "disabled"
    assert "_set_inputs_locked(True)" in inspect.getsource(gui.LCR_Freq_GUI.start_sweep)


def test_window_input_errors_name_the_box_and_refuse_non_finite_values():
    folder = _tmp()
    saved = gui.PYVISA_AVAILABLE
    gui.PYVISA_AVAILABLE = True
    try:
        def collect(cycle=False, shape=gui.SHAPE_POS_FIRST, folder=folder, **boxes):
            w = _window(file_location_path=folder)
            w.lcr_combobox.value = "GPIB0::10::INSTR  ->  ALPHA"
            w.var_cycle.value = cycle
            w.shape_combobox.value = shape
            for key, text in boxes.items():
                w.entries[key].value = text
            return w._collect_params()

        p = collect()
        assert p["freqs"] == [1000.0] and p["delay"] == 0.0
        for boxes, needle in (
                (dict(delay="inf"), "Delay per step (s)"), (dict(delay="nan"), "Delay per step (s)"),
                (dict(ac_bias=""), "AC Level (Vrms)"), (dict(mtm="-"), "Integration Time, MTM (s)"),
                (dict(v_step="inf"), "V Step (V)"), (dict(v_step="nan"), "V Step (V)"),
                (dict(v_start=""), "V Start (V)"), (dict(v_stop="inf"), "V Stop (V)"),
                (dict(freq_list="1000, abc"), "Frequencies (Hz): 'abc'"),
                (dict(sample_name="Sample\tA"), "control characters")):
            msg = str(_expect(ValueError, lambda: collect(**boxes)))
            assert needle in msg, (boxes, msg)
        # a ladder pause of inf would be an endless pause at 0 V
        msg = str(_expect(ValueError, lambda: collect(cycle=True, shape=gui.SHAPE_LADDER,
                                                      ladder="0.5, 1", ladder_pause="inf")))
        assert "Pause between ladder entries (s)" in msg
        # greyed boxes may stay blank: V Start in cycle mode, the pause off-ladder
        assert collect(cycle=True, v_start="")["v_max"] == 1.0
        assert collect(ladder_pause="")["ladder_pause"] == 0.0
        # the save folder must exist before the safety dialog
        gone = os.path.join(folder, "unplugged_usb")
        msg = str(_expect(ValueError, lambda: collect(folder=gone)))
        assert "Save Location" in msg and "not found" in msg
    finally:
        gui.PYVISA_AVAILABLE = saved


class _WheelTarget:
    def __init__(self, path, cls="Frame"):
        self.path, self.cls = path, cls

    def __str__(self):
        return self.path

    def winfo_class(self):
        return self.cls


class _WheelCanvas(_WheelTarget):
    def __init__(self, path):
        super().__init__(path, "Canvas")
        self.scrolls, self.bound = [], []

    def yview_scroll(self, n, what):
        self.scrolls.append((n, what))

    def bind_all(self, seq, fn, add=None):
        self.bound.append(seq)


def test_window_mouse_wheel_scrolls_the_parameter_panel():
    w = _window()
    canvas = _WheelCanvas(".!panedwindow.!frame.!canvas")
    w.left_canvas = canvas
    w._bind_panel_wheel(canvas)
    assert canvas.bound == ["<MouseWheel>", "<Button-4>", "<Button-5>"]
    ev = types.SimpleNamespace
    inside = _WheelTarget(".!panedwindow.!frame.!canvas.!frame.!labelframe.!entry", "Entry")
    w._on_panel_wheel(ev(widget=inside, delta=120, num="??"))       # Windows: up one notch
    w._on_panel_wheel(ev(widget=inside, delta=-240, num="??"))
    w._on_panel_wheel(ev(widget=inside, delta=0, num=5))            # X11: down
    w._on_panel_wheel(ev(widget=canvas, delta=60, num="??"))        # half a notch still moves
    assert canvas.scrolls == [(-1, "units"), (2, "units"), (1, "units"), (-1, "units")]
    for other in (_WheelTarget(".!panedwindow.!frame2.!canvas"),           # the plots
                  _WheelTarget(".!panedwindow.!frame.!canvas2"),
                  _WheelTarget(".!panedwindow.!frame.!canvas.!frame.!labelframe.!text", "Text")):
        w._on_panel_wheel(ev(widget=other, delta=120, num="??"))
    assert len(canvas.scrolls) == 4
    assert "_bind_panel_wheel(left_canvas)" in inspect.getsource(gui.LCR_Freq_GUI.create_widgets)


def test_window_log_axis_leaves_gaps_for_non_positive_values():
    from matplotlib.figure import Figure
    w = _window(y_scale_var=_Widget("log"), _decade_ylims={}, sweep_index=4,
                progress_bar={}, canvas=types.SimpleNamespace(draw_idle=lambda: None))
    fig = Figure()
    w.ax_cp, w.ax_g = fig.add_subplot(2, 1, 1), fig.add_subplot(2, 1, 2)
    w.line_cp, = w.ax_cp.plot([], [])
    w.line_g, = w.ax_g.plot([], [])
    w.data_storage = {"volt": [-1.0, 0.0, 1.0, 2.0], "cp": [1e-9, 2e-9, 3e-9, 4e-9],
                      "g": [1e-6, -2e-7, 0.0, 1e-4]}
    w._update_sweep_plot()
    g = list(w.line_g.get_ydata())
    assert g[0] == 1e-6 and g[3] == 1e-4 and math.isnan(g[1]) and math.isnan(g[2])
    assert w.ax_g.get_yscale() == "log"
    assert w.data_storage["g"] == [1e-6, -2e-7, 0.0, 1e-4]          # stored data untouched
    # linear: every value drawn as measured
    w.y_scale_var.value = "linear"
    w._on_y_scale_change()
    assert list(w.line_g.get_ydata()) == [1e-6, -2e-7, 0.0, 1e-4]
    assert w.ax_g.get_yscale() == "linear"


def test_window_labels_and_scale_bar_use_the_panel_colours():
    src = inspect.getsource(gui.LCR_Freq_GUI)
    entry_src = inspect.getsource(gui.LCR_Freq_GUI._add_entry)
    assert "bg=self.CLR_BG_DARK" in entry_src and "fg=self.CLR_FG_LIGHT" in entry_src
    frame_src = inspect.getsource(gui.LCR_Freq_GUI.create_input_frame)
    for text in ("ZG4 Wire Mode (FRS):", "Alpha-AN VISA:", "Loop shape (cycle mode):"):
        i = frame_src.index(text)
        assert "bg=self.CLR_BG_DARK" in frame_src[i:i + 120], text
    graph_src = inspect.getsource(gui.LCR_Freq_GUI.create_graph_frame)
    for style in ("Graph.TFrame", "Graph.TLabel", "Graph.TRadiobutton"):
        assert style in graph_src and style in inspect.getsource(gui.LCR_Freq_GUI.setup_styles)
    assert "CLR_GRAPH_BG" in src


# -- the script's Ctrl-C path ----------------------------------------------------------------

class _CtrlCQueue(queue.Queue):
    """Raises KeyboardInterrupt (as Ctrl-C would) on the first point at or
    above `at_v` (or as soon as the event `now` is set), and once more while
    main() waits for the discharge."""

    def __init__(self, at_v, now=None):
        super().__init__()
        self.at_v = at_v
        self.now = now
        self.fired = False
        self.second = False

    def get(self, block=True, timeout=None):
        if not self.fired and self.now is not None and self.now.is_set():
            self.fired = True
            raise KeyboardInterrupt
        msg = super().get(block, timeout)
        if not self.fired and msg[0] == "point" and msg[2] >= self.at_v:
            self.fired = True
            raise KeyboardInterrupt
        return msg

    def get_nowait(self):
        if self.fired and not self.second:
            self.second = True
            raise KeyboardInterrupt
        return super().get_nowait()


def _script_main(fake, at_v, now=None, **overrides):
    """Run script.main() against the fake with Ctrl-C at `at_v`; returns
    (exit code, the CVWorker main() made)."""
    folder = _tmp()
    settings = dict(SAVE_FOLDER=folder, FREQUENCIES_HZ=[1000.0], CYCLE_MODE=False, V_START=0.0,
                    V_STOP=4.0, V_STEP=0.5, DELAY_S=0.0, SAMPLE_NAME="Sample_A")
    settings.update(overrides)
    workers = []

    class RecordingWorker(script.CVWorker):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            workers.append(self)

    saved = {k: getattr(script, k) for k in list(settings) + ["queue", "CVWorker", "pyvisa"]}
    try:
        for k, v in settings.items():
            setattr(script, k, v)
        script.queue = types.SimpleNamespace(Queue=lambda: _CtrlCQueue(at_v, now), Empty=queue.Empty)
        script.CVWorker = RecordingWorker
        install_pyvisa(script, fake)
        rc = script.main()
    finally:
        for k, v in saved.items():
            setattr(script, k, v)
    return rc, workers[0]


def test_script_ctrl_c_ends_at_zero_volts_even_with_a_second_ctrl_c():
    fake = FakeCVAlpha()
    rc, worker = _script_main(fake, at_v=1.5)
    assert rc == 0 and worker.end_reason == "stop_button" and worker.bias_confirmed is True
    assert fake.v == 0.0 and not fake.bias_on and fake.closed
    assert max(abs(v) for v, _ in fake.bias_history) >= 1.5
    _, _, _, trailer = read_data_file(worker.files[0])
    assert trailer[0].startswith("END reason=stop_button") and trailer[0].endswith("bias=off")


def test_script_ctrl_c_with_a_hung_worker_forces_the_bias_off_and_exits_non_zero():
    fake = FakeCVAlpha()
    release, hung = threading.Event(), threading.Event()

    def hang(idx):
        if idx == 2:                    # stuck inside the ZRE? read at 1.0 V
            hung.set()
            release.wait(20)
    fake.on_measure = hang
    saved = script.stop_wait_s
    script.stop_wait_s = lambda freqs, mtm: 0.5
    try:
        rc, worker = _script_main(fake, at_v=float("inf"), now=hung)
        assert rc == 2
        assert fake.v == 0.0 and not fake.bias_on and fake.closed   # forced from main()
        assert "*RST" in fake.commands
    finally:
        script.stop_wait_s = saved
        release.set()
    deadline = time.time() + 20
    while not worker.end_reason and time.time() < deadline:
        time.sleep(0.05)
    assert worker.end_reason, "worker never finished"


# -- CV-2, 27 Oct 2026: Stop during a point's SRQ wait and the *RST rejoin ----------

class _NeverDonePoint(FakeCVAlpha):
    """The point never completes: every SRQ wait really waits its timeout,
    then fails with VI_ERROR_TMO, as pyvisa does. The task keeps running
    (bias commands answer MR) until MBK."""

    def __init__(self, mod, **kw):
        super().__init__(mr_when_running=True, **kw)
        self.mod = mod
        self.srq_started = threading.Event()
        self.slices = []

    def wait_for_srq(self, timeout_ms=25000):
        self.traffic.append(("wait_for_srq", timeout_ms))
        self.slices.append(timeout_ms)
        self.srq_started.set()
        time.sleep(timeout_ms / 1000.0)
        raise FakeVisaError(self.mod.VI_ERROR_TMO)


class _AwayAfterReset(FakeCVAlpha):
    """Answers *IDN? at connect, but never rejoins the bus after *RST."""

    def __init__(self, mod, **kw):
        super().__init__(**kw)
        self.mod = mod
        self.reset_seen = False
        self.away = threading.Event()

    def write(self, command):
        super().write(command)
        if command == "*RST":
            self.reset_seen = True

    def query(self, command):
        if command == "*IDN?" and self.reset_seen:
            self.traffic.append(("query", command))
            self.away.set()
            time.sleep(0.05)
            raise FakeVisaError(self.mod.VI_ERROR_NLISTENERS)
        return super().query(command)


def _run_and_stop_when(mod, fake, started, **extra):
    """Run the worker in a thread, press Stop once `started` is set; returns
    (worker, seconds from Stop to the worker's exit)."""
    folder = _tmp()
    p = mod.validate_params(base_params(folder, **extra))
    stop = threading.Event()
    link = make_link(mod, fake)
    worker = mod.CVWorker(p, link, stop, queue.Queue(), sleep=lambda s: None)
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    assert started.wait(10), "the wait under test never began"
    time.sleep(0.3)
    pressed = time.perf_counter()
    stop.set()
    t.join(10)
    assert not t.is_alive(), "worker ignored Stop"
    return worker, time.perf_counter() - pressed


def test_stop_during_a_long_srq_wait_aborts_the_point_and_still_discharges():
    for mod in MODULES:
        fake = _NeverDonePoint(mod)
        # 0.01 Hz: a 460 s point budget that used to block Stop
        worker, took = _run_and_stop_when(mod, fake, fake.srq_started, freq_list="0.01")
        assert took < 2.5, (mod.__name__, took)
        assert worker.end_reason == "stop_button" and worker.bias_confirmed is True
        cmds = fake.commands
        mst, mbk = cmds.index("MST"), cmds.index("MBK")
        assert mst < mbk < last_index(cmds, "DCE=0"), cmds
        assert all(s == int(mod.SRQ_SLICE_S * 1000) for s in fake.slices), fake.slices
        _assert_bias_off_before_safe_state(fake)


def test_stop_during_the_reset_rejoin_wait_ends_promptly():
    for mod in MODULES:
        fake = _AwayAfterReset(mod)
        worker, took = _run_and_stop_when(mod, fake, fake.away)
        assert took < 2.0, (mod.__name__, took)
        assert worker.end_reason == "stop_button" and worker.bias_confirmed is True
        assert "MODE=IMP" not in fake.commands and fake.closed


class _DeafAfterReset(_AwayAfterReset):
    """After *RST every transfer but the rejoin *IDN? hangs like 64-bit
    Keysight VISA does (here 1 s, then a timeout), so any shutdown command
    sent to the resetting Alpha shows up in the Stop-to-exit time."""

    HANG_S = 1.0

    def _hang(self):
        time.sleep(self.HANG_S)
        raise FakeVisaError(self.mod.VI_ERROR_TMO)

    def write(self, command):
        if self.reset_seen:
            self.traffic.append(("write", command))
            self._hang()
        super().write(command)

    def query(self, command):
        if self.reset_seen and command != "*IDN?":
            self.traffic.append(("query", command))
            self._hang()
        return super().query(command)


def test_stop_during_the_reset_rejoin_sends_nothing_after_rst():
    for mod in MODULES:
        fake = _DeafAfterReset(mod)
        worker, took = _run_and_stop_when(mod, fake, fake.away)
        assert took < 0.8, (mod.__name__, took)
        assert worker.end_reason == "stop_button" and worker.bias_confirmed is True
        assert fake.closed and worker.link.backend is None
        sent = [(k, c) for k, c in fake.traffic if k in ("write", "query")]
        rst = sent.index(("write", "*RST"))
        after = sent[rst + 1:]
        assert after and all(x == ("query", "*IDN?") for x in after), after


def test_close_while_resetting_skips_commands_unless_bias_was_touched():
    for mod in MODULES:
        backend, fake = connected_backend(mod, FakeCVAlpha())
        backend._alpha_rebooting, backend._bias_touched = True, False
        assert backend.close_instrument() is True
        assert fake.commands == [] and fake.closed and backend.instrument is None
        assert mod.AlphaANLink.force_zero_bias(types.SimpleNamespace(
            backend=backend, sleep=lambda s: None, bias_confirmed_off=True))

        # Bias ever touched: the full discharge runs regardless of the flag.
        backend, fake = connected_backend(mod, FakeCVAlpha())
        backend._alpha_rebooting = True
        backend._bias_touched, backend._bias_level = True, 0.0
        assert backend.close_instrument() is True
        assert "DCE=0" in fake.commands and "*RST" in fake.commands


def test_stop_while_connecting_is_a_stop_not_a_bus_error():
    for mod in MODULES:
        fake = FakeCVAlpha()
        fake.answers["*IDN?"] = FakeVisaError(mod.VI_ERROR_NLISTENERS)

        def query(command, _orig=fake.query, _fake=fake):
            if command == "*IDN?":
                _fake.traffic.append(("query", command))
                raise _fake.answers["*IDN?"]
            return _orig(command)
        fake.query = query
        link = make_link(mod, fake)
        link.stop_event = threading.Event()
        link.stop_event.set()
        _expect(mod.StopRequested, link.open)
        assert link.backend is None


def test_sliced_srq_wait_keeps_the_point_budget_and_a_normal_point_completes():
    for mod in MODULES:
        backend, fake = connected_backend(mod, FakeCVAlpha(srq_raises=True))
        backend.stop_event = threading.Event()
        exc = _expect(TimeoutError, lambda: backend._wait_for_srq(5.0, "a point"))
        assert "Aborted (MBK)" in str(exc)
        waits = [t for k, t in fake.traffic if k == "wait_for_srq"]
        assert waits == [1000] * 5, waits             # 5 s budget, 1 s slices

        class Broken(FakeCVAlpha):
            def wait_for_srq(self, timeout_ms=25000):
                self.traffic.append(("wait_for_srq", timeout_ms))
                raise FakeVisaError(-1073807298)      # not a timeout: no retry
        backend, fake = connected_backend(mod, Broken())
        backend.stop_event = threading.Event()
        _expect(TimeoutError, lambda: backend._wait_for_srq(5.0, "a point"))
        assert [k for k, _ in fake.traffic].count("wait_for_srq") == 1

        backend, fake = connected_backend(mod, FakeCVAlpha())
        backend._bias_touched, backend._bias_level = False, 0.0
        backend.stop_event = threading.Event()
        backend.params = {"mtm": 0.5}
        zr, zi, f_act, status, _ref = backend.measure_point(1000.0)
        assert status == mod.STATUS_RESULT_VALID and "MBK" not in fake.commands
        assert [t for k, t in fake.traffic if k == "wait_for_srq"] == [1000]


def test_pause_is_stop_aware_only_with_a_stop_event():
    for mod in MODULES:
        backend, _ = connected_backend(mod)
        backend._pause(2.0)                           # no event: plain sleep
        backend.stop_event = threading.Event()
        backend._pause(2.0)
        backend.stop_event.set()
        _expect(mod.StopRequested, lambda: backend._pause(2.0))


if __name__ == "__main__":
    run_module_tests(dict(globals()))
