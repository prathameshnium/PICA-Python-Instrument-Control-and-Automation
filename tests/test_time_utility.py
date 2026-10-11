"""PICA Time Utility: button colours, notes, and the time-stamped log.

The log-line format is plain Python and always tested. The window tests
build the real utility on a Toplevel of one shared Tk root (several roots in
one process are unreliable on Windows) and skip where no display is
available. Dialogs are replaced, so nothing modal ever opens.
"""
import importlib.util
import os
import sys
import time
from datetime import datetime

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TU_PATH = os.path.join(REPO_ROOT, "pica", "utils", "Time_Utility_GUI.py")


def _load():
    spec = importlib.util.spec_from_file_location("pica_time_utility_test", TU_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pica_time_utility_test"] = module
    spec.loader.exec_module(module)
    return module


tu = _load()


# =============================================================================
#  The log line (no Tk)
# =============================================================================
@pytest.mark.parametrize("seconds, expected", [
    (0, "00:00:00"),
    (300.0, "00:05:00"),
    (299.99, "00:05:00"),               # the last second has not gone yet
    (299.0, "00:04:59"),
    (0.4, "00:00:01"),                  # never zero before it is zero
    (-0.2, "00:00:00"),
])
def test_time_left_rounds_up_to_the_whole_second(seconds, expected):
    assert tu.format_time_left(seconds) == expected


@pytest.mark.parametrize("seconds, hundredths, expected", [
    (0, False, "00:00:00"),
    (59.4, False, "00:00:59"),
    (3725, False, "01:02:05"),
    (90061, False, "25:01:01"),
    (1.5, True, "00:00:01.50"),
    (1.999, True, "00:00:01.99"),       # never rolls over to ".100"
    (-3, False, "00:00:00"),            # a late tick never reads negative
])
def test_format_seconds(seconds, hundredths, expected):
    assert tu.format_seconds(seconds, show_hundredths=hundredths) == expected


def test_a_log_line_has_date_time_source_event_reading_and_note():
    when = datetime(2026, 10, 10, 3, 50, 37)
    line = tu.format_log_line(when, "stopwatch", "Stopped at", "00:01:23.45", "cooldown")
    assert line == "2026-10-10 03:50:37  STOPWATCH  Stopped at 00:01:23.45  |  cooldown"


def test_sources_line_up_in_one_column():
    when = datetime(2026, 10, 10, 3, 50, 37)
    lines = [tu.format_log_line(when, s, "Event") for s in ("TIMER", "STOPWATCH", "NOTE", "CLOCK")]
    assert len({line.index("Event") for line in lines}) == 1


def test_a_note_is_kept_to_one_line_and_left_out_when_empty():
    when = datetime(2026, 10, 10, 3, 50, 37)
    assert tu.format_log_line(when, "TIMER", "Finished", note="a\n  b\tc") \
        .endswith("Finished  |  a b c")
    assert "|" not in tu.format_log_line(when, "TIMER", "Finished", note="   ")


# =============================================================================
#  The window
# =============================================================================
_SHARED = {"root": None, "error": None}


def _shared_root():
    if _SHARED["error"] is not None:
        pytest.skip(f"no usable Tk: {_SHARED['error']}")
    if _SHARED["root"] is None:
        try:
            import tkinter as tk
            _SHARED["root"] = tk.Tk()
            _SHARED["root"].withdraw()
        except Exception as e:  # no display, mocked tkinter, incomplete Tcl
            _SHARED["error"] = e
            pytest.skip(f"no usable Tk: {e}")
    return _SHARED["root"]


def _cancel_timers(root):
    try:
        for ident in root.tk.splitlist(root.tk.call("after", "info")):
            root.tk.call("after", "cancel", ident)
    except Exception:
        pass


class Dialogs:
    """Stands in for messagebox / filedialog and records what was asked."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def __getattr__(self, name):
        def dialog(*args, **kwargs):
            self.calls.append(name)
            return self.answers.get(name)
        return dialog


@pytest.fixture
def app(monkeypatch):
    import tkinter as tk
    root = _shared_root()
    if type(root).__module__ != "tkinter":
        pytest.skip("tkinter is mocked in this run")
    dialogs = Dialogs()
    monkeypatch.setattr(tu, "messagebox", dialogs)
    monkeypatch.setattr(tu, "filedialog", dialogs)
    monkeypatch.setattr(tu.PICATimeUtilityApp, "play_beep", lambda self: self.__dict__.setdefault("beeps", []).append(1))
    window = tk.Toplevel(root)
    try:
        utility = tu.PICATimeUtilityApp(window)
    except Exception as e:
        window.destroy()
        pytest.fail(f"Time Utility could not be built: {e}")
    utility.dialogs = dialogs
    window.update()
    yield utility
    try:
        if window.winfo_exists():
            window.destroy()
    finally:
        _cancel_timers(root)
        root.update()


def _style(button):
    return str(button.cget("style"))


def _events(app):
    return [line[21:] for line in app.log_lines]       # without the time stamp


# ------------------------------------------------------------- clock
def test_the_clock_starts_in_12_hour_format(app):
    assert app.is_12_hour.get() is True
    app.update_clock_display()
    assert app.lbl_clock.cget("text")[-2:] in ("AM", "PM")
    app.is_12_hour.set(False)
    app.update_clock_display()
    assert app.lbl_clock.cget("text")[-2:] not in ("AM", "PM")


# ------------------------------------------------------------- buttons
def test_start_is_green_and_turns_into_a_red_stop(app):
    assert _style(app.btn_sw_start) == "Start.TButton"
    assert _style(app.btn_tm_start) == "Start.TButton"
    assert _style(app.btn_sw_reset) == "Aux.TButton"
    app.sw_toggle()
    assert app.btn_sw_start.cget("text") == "Stop"
    assert _style(app.btn_sw_start) == "Stop.TButton"
    app.sw_toggle()
    assert app.btn_sw_start.cget("text") == "Resume"
    assert _style(app.btn_sw_start) == "Start.TButton"
    app.sw_reset()
    assert app.btn_sw_start.cget("text") == "Start"


def test_the_button_styles_carry_real_colours(app):
    from tkinter import ttk
    style = ttk.Style(app.root)
    assert style.lookup("Start.TButton", "background") == app.CLR_START
    assert style.lookup("Stop.TButton", "background") == app.CLR_STOP
    assert style.lookup("Start.TButton", "foreground") == app.CLR_TEXT_LIGHT
    assert style.lookup("Start.TButton", "background") != style.lookup("Aux.TButton", "background")


# ------------------------------------------------------------- stopwatch
def test_stopwatch_events_are_logged_with_the_note(app):
    app.entry_sw_note.insert(0, "cooldown to base")
    app.sw_toggle()
    time.sleep(0.05)
    app.sw_toggle()
    app.sw_toggle()
    app.sw_reset()
    events = _events(app)
    assert events[0].startswith("CLOCK      Time Utility opened")
    assert events[1].startswith("STOPWATCH  Started at 00:00:00.00")
    assert events[2].startswith("STOPWATCH  Stopped at 00:00:00.")
    assert events[3].startswith("STOPWATCH  Resumed at")
    assert events[4].startswith("STOPWATCH  Reset from")
    assert all(e.endswith("|  cooldown to base") for e in events[1:])


def test_a_stopwatch_note_is_logged_with_the_reading_at_that_moment(app):
    app.sw_toggle()
    time.sleep(0.05)
    app.entry_sw_note.insert(0, "valve opened")
    app.sw_log_note()
    last = _events(app)[-1]
    assert last.startswith("STOPWATCH  Note at 00:00:00.")
    assert last.endswith("|  valve opened")


def test_an_empty_note_logs_nothing(app):
    before = len(app.log_lines)
    app.sw_log_note()
    app.tm_log_note()
    app.log_general_note()
    assert len(app.log_lines) == before


def test_resetting_an_unused_stopwatch_logs_nothing(app):
    before = len(app.log_lines)
    app.sw_reset()
    assert len(app.log_lines) == before


# ------------------------------------------------------------- timer
def _set_timer(app, h=0, m=0, s=0):
    for entry, value in ((app.entry_h, h), (app.entry_m, m), (app.entry_s, s)):
        entry.delete(0, "end")
        entry.insert(0, str(value))


def test_timer_start_pause_resume_reset_are_logged(app):
    _set_timer(app, m=5)
    app.entry_tm_note.insert(0, "settle")
    app.tm_toggle()
    assert app.btn_tm_start.cget("text") == "Pause"
    assert _style(app.btn_tm_start) == "Stop.TButton"
    app.tm_toggle()
    assert app.btn_tm_start.cget("text") == "Resume"
    app.tm_toggle()
    app.tm_reset()
    events = _events(app)[1:]
    assert events[0] == "TIMER      Started for 00:05:00  |  settle"
    # Time left is rounded up, as the display shows it, so a pause straight
    # after the start reads 00:05:00 on every machine. (Rounded down it was
    # 00:04:59 on Linux and 00:05:00 on Windows, whose clock is coarser.)
    assert events[1] == "TIMER      Paused with 00:05:00 left  |  settle"
    assert events[2] == "TIMER      Resumed with 00:05:00 left  |  settle"
    assert events[3].startswith("TIMER      Reset with 00:0")
    assert events[3].endswith("left  |  settle")
    assert app.btn_tm_start.cget("text") == "Start"
    assert str(app.entry_h.cget("state")) == "normal"


def test_the_log_and_the_display_agree_on_time_left(app):
    _set_timer(app, m=5)
    app.tm_toggle()
    app.tm_toggle()                      # pause at once
    shown = app.lbl_timer.cget("text")
    assert _events(app)[-1].startswith(f"TIMER      Paused with {shown} left")


@pytest.mark.parametrize("elapsed", [0.0, 0.004, 0.016, 0.9])
def test_a_pause_logs_the_same_on_every_clock(app, monkeypatch, elapsed):
    """Windows' clock may see no time pass between Start and Pause; Linux
    sees a few milliseconds. Both must log the time left the display shows."""
    now = [1000.0]
    monkeypatch.setattr(tu.time, "monotonic", lambda: now[0])
    _set_timer(app, m=5)
    app.tm_toggle()
    now[0] += elapsed
    app.tm_toggle()
    assert _events(app)[-1] == "TIMER      Paused with 00:05:00 left"
    assert app.lbl_timer.cget("text") == "00:05:00"


def test_the_timer_finish_is_logged_and_beeps(app):
    _set_timer(app, s=30)
    app.entry_tm_note.insert(0, "next step")
    app.tm_toggle()
    app.tm_end_time = time.monotonic() - 0.01        # the time has run out
    app.update_timer()
    assert _events(app)[-1] == "TIMER      Finished (00:00:30)  |  next step"
    assert app.beeps == [1]
    assert app.lbl_timer.cget("text") == "00:00:00"
    assert _style(app.btn_tm_start) == "Start.TButton"


def test_the_timer_never_shows_zero_before_it_finishes(app):
    _set_timer(app, s=30)
    app.tm_toggle()
    app.tm_end_time = time.monotonic() + 0.4         # under a second left
    app.update_timer()
    assert app.lbl_timer.cget("text") == "00:00:01"


@pytest.mark.parametrize("values", [("x", "0", "0"), ("0", "-5", "0")])
def test_bad_timer_input_is_refused_and_not_logged(app, values):
    for entry, value in zip((app.entry_h, app.entry_m, app.entry_s), values):
        entry.delete(0, "end")
        entry.insert(0, value)
    before = len(app.log_lines)
    app.tm_toggle()
    assert app.dialogs.calls == ["showerror"]
    assert not app.tm_running
    assert len(app.log_lines) == before


def test_a_zero_timer_is_refused(app):
    app.tm_toggle()
    assert app.dialogs.calls == ["showwarning"]
    assert not app.tm_running


# ------------------------------------------------------------- the log
def test_a_general_note_is_logged_and_the_box_cleared(app):
    app.entry_log_note.insert(0, "Sample A mounted")
    app.log_general_note()
    assert _events(app)[-1] == "NOTE       Note  |  Sample A mounted"
    assert app.entry_log_note.get() == ""


def test_a_fresh_window_has_nothing_to_save(app):
    assert app._unsaved_count() == 0
    assert app.lbl_log_status.cget("text") == "Nothing to save yet"


def test_the_status_counts_unsaved_entries(app):
    app.entry_log_note.insert(0, "one")
    app.log_general_note()
    assert app.lbl_log_status.cget("text") == "1 unsaved entry"
    app.entry_log_note.insert(0, "two")
    app.log_general_note()
    assert app.lbl_log_status.cget("text") == "2 unsaved entries"


def test_the_log_is_shown_in_the_console(app):
    app.entry_log_note.insert(0, "visible")
    app.log_general_note()
    shown = app.txt_log.get("1.0", "end")
    for line in app.log_lines:
        assert line in shown


def test_saving_writes_every_line_and_marks_the_log_saved(app, tmp_path):
    app.sw_toggle()
    app.sw_toggle()
    path = tmp_path / "log.txt"
    assert app.save_log(str(path)) == str(path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("PICA Time Utility log\n")
    for line in app.log_lines:
        assert line in text
    assert app._unsaved_count() == 0
    assert app.lbl_log_status.cget("text") == "All entries saved"


def test_save_asks_for_a_file_and_cancel_saves_nothing(app):
    app.entry_log_note.insert(0, "x")
    app.log_general_note()
    app.dialogs.answers["asksaveasfilename"] = ""
    assert app.save_log() is None
    assert app.dialogs.calls == ["asksaveasfilename"]
    assert app._unsaved_count() == 1


def test_a_failed_save_says_so(app, tmp_path):
    assert app.save_log(str(tmp_path / "no_such_dir" / "log.txt")) is None
    assert app.dialogs.calls == ["showerror"]


def test_clearing_unsaved_entries_asks_first(app):
    app.entry_log_note.insert(0, "keep me")
    app.log_general_note()
    app.dialogs.answers["askyesno"] = False
    app.clear_log()
    assert any("keep me" in line for line in app.log_lines)
    app.dialogs.answers["askyesno"] = True
    app.clear_log()
    assert app.log_lines == []
    assert app.txt_log.get("1.0", "end").strip() == ""


def test_closing_with_unsaved_entries_asks_and_cancel_keeps_it_open(app):
    app.entry_log_note.insert(0, "unsaved")
    app.log_general_note()
    app.dialogs.answers["askyesnocancel"] = None
    app.on_close()
    assert app.root.winfo_exists()
    app.dialogs.answers["askyesnocancel"] = False      # close without saving
    app.on_close()
    assert not app.root.winfo_exists()


def test_closing_with_nothing_unsaved_does_not_ask(app):
    app.on_close()
    assert app.dialogs.calls == []
    assert not app.root.winfo_exists()
