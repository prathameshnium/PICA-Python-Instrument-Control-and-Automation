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
    connected_backend, run_common_backend_checks)
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
    def config(self, **kw):
        pass


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
    for name in ("lbl_current_freq", "start_button", "stop_button", "scan_button"):
        setattr(w, name, _Widget())
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
        # only once the window has run out does the window thread step in
        w._stop_deadline = time.time() - 1.0
        w._poll_worker_stopped("")
        assert spy.calls == ["force_zero_bias", "close"] and w._bias_confirmed is True
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


if __name__ == "__main__":
    run_module_tests(dict(globals()))
