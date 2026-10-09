'''
===============================================================================
 PROGRAM:      PICA Time Utility
 PURPOSE:      A clock, stopwatch and countdown timer for lab measurements,
               with a time-stamped log.

               Every stopwatch and timer event -- start, stop, pause,
               resume, reset, finish -- is written to the log on the right,
               with the date and time it happened, the reading at that
               moment and the note typed for that section ("cooldown to
               base", "sample A, 5 T"). A note can also be logged on its own
               at any moment, from either section or from the general note
               box under the log. "Save log…" writes the whole log to a
               text file; closing the window with unsaved entries asks
               first.

 TIMING:       The stopwatch and timer run on time.monotonic(), which does
               not jump when the computer's clock is set or synchronised.
               The log's time stamps are wall-clock times, which is what a
               lab notebook wants.
===============================================================================
'''
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import tkinter.font as tkfont
import time
from datetime import datetime
import os

# Attempt to import winsound for Windows-native beeps
try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False


# -----------------------------------------------------------------------------
#  The log line (no Tk: tested on its own)
# -----------------------------------------------------------------------------
LOG_SOURCE_WIDTH = 9        # "STOPWATCH" is the longest source


def format_seconds(seconds, show_hundredths=False):
    """HH:MM:SS, or HH:MM:SS.hh with hundredths. Negative reads as zero."""
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    mins, secs = divmod(rest, 60)
    text = f"{hours:02d}:{mins:02d}:{secs:02d}"
    if show_hundredths:
        hundredths = min(99, int(round((seconds - whole) * 100)))
        text += f".{hundredths:02d}"
    return text


def format_log_line(when, source, event, reading="", note=""):
    """One log line: date and time, source, event, reading, note.

    2026-10-10 03:50:37  STOPWATCH  Stopped at 00:01:23.45  |  cooldown
    """
    line = f"{when:%Y-%m-%d %H:%M:%S}  {source.upper():<{LOG_SOURCE_WIDTH}}  {event}"
    if reading:
        line += f" {reading}"
    note = " ".join((note or "").split())      # one line, however it was typed
    if note:
        line += f"  |  {note}"
    return line


class PICATimeUtilityApp:

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_INPUT_BG = '#F1EBE4'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DIM = '#6B5F54'
    CLR_TEXT_DARK = '#1A1A1A'
    CLR_TEXT_LIGHT = '#FFFFFF'
    # Button colours: green to start, red to stop, quiet outline otherwise.
    CLR_START = '#5B7A3F'
    CLR_START_HOVER = '#47612F'
    CLR_STOP = '#B04A38'
    CLR_STOP_HOVER = '#8B3A2F'
    CLR_BORDER = '#C4B2A0'

    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_SMALL = ('Segoe UI', FONT_SIZE_BASE - 2)
    FONT_BUTTON = ('Segoe UI', FONT_SIZE_BASE, 'bold')
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')
    FONT_DIGITAL = ('Consolas', 28, 'bold')
    FONT_LOG = ('Consolas', 10)

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Time Utility")
        self.root.geometry("960x660")
        self.root.minsize(860, 600)
        self.root.configure(bg=self.CLR_BG_DARK)

        # --- State Variables ---
        self.is_12_hour = tk.BooleanVar(value=False)

        # Stopwatch state
        self.sw_running = False
        self.sw_start_time = 0.0
        self.sw_elapsed = 0.0

        # Timer state
        self.tm_running = False
        self.tm_end_time = 0.0
        self.tm_remaining = 0.0
        self.tm_set_seconds = 0         # the length the timer was started at

        # Log state: every line, and how many were already saved to a file.
        self.log_lines = []
        self.log_saved_count = 0
        self.last_save_path = None

        self.setup_styles()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log("CLOCK", "Time Utility opened")
        self.log_saved_count = len(self.log_lines)     # nothing worth saving yet

        # Start background update loops
        self.update_clock()

    # ==========================================
    # STYLES
    # ==========================================
    def setup_styles(self):
        """Initializes the PICA theme styles."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('Sub.TFrame', background=self.CLR_FRAME_BG)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_BG_DARK,
                        borderwidth=2, padding=10)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT,
                        font=self.FONT_SUBTITLE)
        style.configure('TCheckbutton', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT,
                        font=self.FONT_BASE)
        style.map('TCheckbutton', background=[('active', self.CLR_FRAME_BG)])
        style.configure('TEntry', fieldbackground=self.CLR_INPUT_BG, foreground=self.CLR_TEXT)

        # Start: filled green. It turns into Stop (filled red) while running.
        style.configure('Start.TButton', font=self.FONT_BUTTON, padding=(12, 6),
                        foreground=self.CLR_TEXT_LIGHT, background=self.CLR_START,
                        borderwidth=0, focusthickness=0, focuscolor=self.CLR_START)
        style.map('Start.TButton',
                  background=[('active', self.CLR_START_HOVER)],
                  foreground=[('active', self.CLR_TEXT_LIGHT)])
        style.configure('Stop.TButton', font=self.FONT_BUTTON, padding=(12, 6),
                        foreground=self.CLR_TEXT_LIGHT, background=self.CLR_STOP,
                        borderwidth=0, focusthickness=0, focuscolor=self.CLR_STOP)
        style.map('Stop.TButton',
                  background=[('active', self.CLR_STOP_HOVER)],
                  foreground=[('active', self.CLR_TEXT_LIGHT)])
        # Everything else (Reset, Log note, Save, Clear): a quiet outline.
        style.configure('Aux.TButton', font=self.FONT_BASE, padding=(12, 6),
                        foreground=self.CLR_TEXT, background=self.CLR_INPUT_BG,
                        bordercolor=self.CLR_BORDER, borderwidth=1,
                        focusthickness=0, focuscolor=self.CLR_INPUT_BG)
        style.map('Aux.TButton',
                  background=[('active', self.CLR_ACCENT_GOLD)],
                  foreground=[('active', self.CLR_TEXT_LIGHT)])

    # ==========================================
    # WIDGETS
    # ==========================================
    def create_widgets(self):
        """Clock, stopwatch and timer on the left; the log on the right."""
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill='both', expand=True)
        main_frame.columnconfigure(0, weight=0, minsize=420)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(0, weight=1)

        left = ttk.Frame(main_frame)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 15))
        right = ttk.Frame(main_frame)
        right.grid(row=0, column=1, sticky='nsew')

        # ==========================================
        # 1. CLOCK SECTION
        # ==========================================
        clock_frame = ttk.LabelFrame(left, text="Current Time")
        clock_frame.pack(fill='x', pady=(0, 12))

        self.lbl_clock = tk.Label(clock_frame, text="00:00:00", font=self.FONT_DIGITAL,
                                  bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK)
        self.lbl_clock.pack(pady=(2, 0))

        self.lbl_date = tk.Label(clock_frame, text="YYYY-MM-DD", font=self.FONT_BASE,
                                 bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT)
        self.lbl_date.pack(pady=(0, 4))

        toggle_btn = ttk.Checkbutton(clock_frame, text="12-Hour Format", variable=self.is_12_hour,
                                     command=self.update_clock_display)
        toggle_btn.pack(pady=(0, 2))

        # ==========================================
        # 2. STOPWATCH SECTION
        # ==========================================
        sw_frame = ttk.LabelFrame(left, text="Stopwatch")
        sw_frame.pack(fill='x', pady=(0, 12))

        self.lbl_stopwatch = tk.Label(sw_frame, text="00:00:00.00", font=self.FONT_DIGITAL,
                                      bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK)
        self.lbl_stopwatch.pack(pady=(4, 8))

        self.entry_sw_note = self._note_row(sw_frame, self.sw_log_note)

        sw_btn_frame = ttk.Frame(sw_frame, style='Sub.TFrame')
        sw_btn_frame.pack(fill='x', pady=(8, 0))
        sw_btn_frame.columnconfigure((0, 1), weight=1, uniform='sw')

        self.btn_sw_start = ttk.Button(sw_btn_frame, text="Start", style='Start.TButton',
                                       command=self.sw_toggle)
        self.btn_sw_start.grid(row=0, column=0, padx=(0, 4), sticky='ew')
        self.btn_sw_reset = ttk.Button(sw_btn_frame, text="Reset", style='Aux.TButton',
                                       command=self.sw_reset)
        self.btn_sw_reset.grid(row=0, column=1, padx=(4, 0), sticky='ew')

        # ==========================================
        # 3. TIMER SECTION
        # ==========================================
        tm_frame = ttk.LabelFrame(left, text="Timer")
        tm_frame.pack(fill='x')

        self.lbl_timer = tk.Label(tm_frame, text="00:00:00", font=self.FONT_DIGITAL,
                                  bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK)
        self.lbl_timer.pack(pady=(4, 6))

        # Inputs for Timer
        input_frame = tk.Frame(tm_frame, bg=self.CLR_FRAME_BG)
        input_frame.pack(pady=(0, 6))

        tk.Label(input_frame, text="H:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(side='left')
        self.entry_h = ttk.Entry(input_frame, width=4, font=self.FONT_BASE, justify='center')
        self.entry_h.insert(0, "0")
        self.entry_h.pack(side='left', padx=(2, 10))

        tk.Label(input_frame, text="M:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(side='left')
        self.entry_m = ttk.Entry(input_frame, width=4, font=self.FONT_BASE, justify='center')
        self.entry_m.insert(0, "0")
        self.entry_m.pack(side='left', padx=(2, 10))

        tk.Label(input_frame, text="S:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(side='left')
        self.entry_s = ttk.Entry(input_frame, width=4, font=self.FONT_BASE, justify='center')
        self.entry_s.insert(0, "0")
        self.entry_s.pack(side='left', padx=(2, 0))

        self.entry_tm_note = self._note_row(tm_frame, self.tm_log_note)

        tm_btn_frame = ttk.Frame(tm_frame, style='Sub.TFrame')
        tm_btn_frame.pack(fill='x', pady=(8, 0))
        tm_btn_frame.columnconfigure((0, 1), weight=1, uniform='tm')

        self.btn_tm_start = ttk.Button(tm_btn_frame, text="Start", style='Start.TButton',
                                       command=self.tm_toggle)
        self.btn_tm_start.grid(row=0, column=0, padx=(0, 4), sticky='ew')
        self.btn_tm_reset = ttk.Button(tm_btn_frame, text="Reset", style='Aux.TButton',
                                       command=self.tm_reset)
        self.btn_tm_reset.grid(row=0, column=1, padx=(4, 0), sticky='ew')

        # ==========================================
        # 4. LOG
        # ==========================================
        log_frame = ttk.LabelFrame(right, text="Log")
        log_frame.pack(fill='both', expand=True)

        self.txt_log = scrolledtext.ScrolledText(
            log_frame, state='disabled', wrap='word', height=10, font=self.FONT_LOG,
            bg=self.CLR_INPUT_BG, fg=self.CLR_TEXT, relief='flat', borderwidth=0,
            padx=8, pady=6)
        self.txt_log.pack(fill='both', expand=True)
        # A long entry wraps under its event, not under the time stamp, so
        # the date-and-time column stays a clean column to scan down.
        stamp = tkfont.Font(root=self.root, font=self.FONT_LOG).measure("0" * 21)
        self.txt_log.tag_configure('entry', lmargin1=0, lmargin2=stamp)

        tk.Label(log_frame, text="General note (Enter adds it to the log):",
                 bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DIM, font=self.FONT_SMALL,
                 anchor='w').pack(fill='x', pady=(10, 2))
        note_row = tk.Frame(log_frame, bg=self.CLR_FRAME_BG)
        note_row.pack(fill='x')
        self.entry_log_note = ttk.Entry(note_row, font=self.FONT_BASE)
        self.entry_log_note.pack(side='left', fill='x', expand=True, padx=(0, 8), ipady=2)
        self.entry_log_note.bind("<Return>", lambda _e: self.log_general_note())
        self.entry_log_note.bind("<KP_Enter>", lambda _e: self.log_general_note())
        ttk.Button(note_row, text="Add note", style='Aux.TButton',
                   command=self.log_general_note).pack(side='left')

        log_btns = tk.Frame(log_frame, bg=self.CLR_FRAME_BG)
        log_btns.pack(fill='x', pady=(10, 0))
        self.btn_save_log = ttk.Button(log_btns, text="Save log…", style='Start.TButton',
                                       command=self.save_log)
        self.btn_save_log.pack(side='left')
        ttk.Button(log_btns, text="Clear", style='Aux.TButton',
                   command=self.clear_log).pack(side='left', padx=(8, 0))
        self.lbl_log_status = tk.Label(log_btns, text="", bg=self.CLR_FRAME_BG,
                                       fg=self.CLR_TEXT_DIM, font=self.FONT_SMALL, anchor='e')
        self.lbl_log_status.pack(side='right')

    def _note_row(self, parent, log_command):
        """A 'Note' entry with a 'Log note' button; Enter logs it too."""
        row = tk.Frame(parent, bg=self.CLR_FRAME_BG)
        row.pack(fill='x')
        tk.Label(row, text="Note:", bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT,
                 font=self.FONT_BASE).pack(side='left', padx=(0, 6))
        entry = ttk.Entry(row, font=self.FONT_BASE)
        entry.pack(side='left', fill='x', expand=True, padx=(0, 6), ipady=2)
        entry.bind("<Return>", lambda _e: log_command())
        entry.bind("<KP_Enter>", lambda _e: log_command())
        ttk.Button(row, text="Log note", style='Aux.TButton',
                   command=log_command).pack(side='left')
        return entry

    # ==========================================
    # CLOCK LOGIC
    # ==========================================
    def update_clock(self):
        self.update_clock_display()
        self.root.after(1000, self.update_clock)

    def update_clock_display(self):
        now = datetime.now()
        fmt = "%I:%M:%S %p" if self.is_12_hour.get() else "%H:%M:%S"
        self.lbl_clock.config(text=now.strftime(fmt))
        self.lbl_date.config(text=now.strftime("%A, %B %d, %Y"))

    # ==========================================
    # FORMATTING UTILS
    # ==========================================
    def format_time(self, seconds, show_ms=False):
        return format_seconds(seconds, show_hundredths=show_ms)

    # ==========================================
    # LOG
    # ==========================================
    def log(self, source, event, reading="", note=""):
        """Add one time-stamped line to the log and show it."""
        line = format_log_line(datetime.now(), source, event, reading, note)
        self.log_lines.append(line)
        try:
            self.txt_log.config(state='normal')
            self.txt_log.insert('end', line + "\n", 'entry')
            self.txt_log.see('end')
            self.txt_log.config(state='disabled')
        except tk.TclError:
            pass
        self._update_log_status()
        return line

    def _note(self, entry):
        return " ".join(entry.get().split())

    def _unsaved_count(self):
        return max(0, len(self.log_lines) - self.log_saved_count)

    def _update_log_status(self):
        unsaved = self._unsaved_count()
        if not self.log_lines:
            text = "Log is empty"
        elif unsaved:
            text = f"{unsaved} unsaved entr{'y' if unsaved == 1 else 'ies'}"
        else:
            text = "All entries saved"
        try:
            self.lbl_log_status.config(text=text)
        except (tk.TclError, AttributeError):
            pass

    def log_general_note(self):
        note = self._note(self.entry_log_note)
        if not note:
            return
        self.log("NOTE", "Note", note=note)
        self.entry_log_note.delete(0, 'end')

    def sw_log_note(self):
        """Log the stopwatch note with the reading at this moment."""
        note = self._note(self.entry_sw_note)
        if not note:
            return
        self.log("STOPWATCH", "Note at", self.format_time(self._sw_now(), show_ms=True), note)

    def tm_log_note(self):
        """Log the timer note with the time left at this moment."""
        note = self._note(self.entry_tm_note)
        if not note:
            return
        self.log("TIMER", "Note with", f"{self.format_time(self._tm_left())} left", note)

    def log_text(self):
        """The whole log as a file would hold it."""
        header = [
            "PICA Time Utility log",
            f"Saved {datetime.now():%Y-%m-%d %H:%M:%S}",
            "",
        ]
        return "\n".join(header + self.log_lines) + "\n"

    def default_log_name(self):
        return f"PICA_Time_Log_{datetime.now():%Y%m%d_%H%M%S}.txt"

    def save_log(self, path=None):
        """Write the log to a text file. Returns the path, or None."""
        if path is None:
            initial_dir = os.path.dirname(self.last_save_path) if self.last_save_path else os.getcwd()
            path = filedialog.asksaveasfilename(
                parent=self.root, title="Save Time Utility log",
                initialdir=initial_dir, initialfile=self.default_log_name(),
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.log_text())
        except OSError as e:
            messagebox.showerror("Save failed", f"The log could not be saved:\n\n{e}",
                                 parent=self.root)
            return None
        self.last_save_path = path
        self.log_saved_count = len(self.log_lines)
        self._update_log_status()
        return path

    def clear_log(self):
        if self._unsaved_count() and not messagebox.askyesno(
                "Clear log", "The log has entries that are not saved. Clear it anyway?",
                parent=self.root):
            return
        self.log_lines = []
        self.log_saved_count = 0
        try:
            self.txt_log.config(state='normal')
            self.txt_log.delete('1.0', 'end')
            self.txt_log.config(state='disabled')
        except tk.TclError:
            pass
        self._update_log_status()

    def on_close(self):
        """Offer to save a log with unsaved entries before closing."""
        if self._unsaved_count():
            answer = messagebox.askyesnocancel(
                "Save log?", "The log has entries that are not saved.\n\n"
                "Save it before closing?", parent=self.root)
            if answer is None:
                return                      # Cancel: stay open
            if answer and self.save_log() is None:
                return                      # save dialog cancelled or failed
        self.root.destroy()

    # ==========================================
    # STOPWATCH LOGIC
    # ==========================================
    def _sw_now(self):
        if self.sw_running:
            return time.monotonic() - self.sw_start_time
        return self.sw_elapsed

    def _set_running_look(self, button, running, idle_text):
        button.config(text="Stop" if running else idle_text,
                      style='Stop.TButton' if running else 'Start.TButton')

    def sw_toggle(self):
        note = self._note(self.entry_sw_note)
        if not self.sw_running:
            resuming = self.sw_elapsed > 0
            self.sw_start_time = time.monotonic() - self.sw_elapsed
            self.sw_running = True
            self._set_running_look(self.btn_sw_start, True, "Start")
            self.log("STOPWATCH", "Resumed at" if resuming else "Started at",
                     self.format_time(self.sw_elapsed, show_ms=True), note)
            self.update_stopwatch()
        else:
            self.sw_elapsed = time.monotonic() - self.sw_start_time
            self.sw_running = False
            self._set_running_look(self.btn_sw_start, False, "Resume")
            self.lbl_stopwatch.config(text=self.format_time(self.sw_elapsed, show_ms=True))
            self.log("STOPWATCH", "Stopped at", self.format_time(self.sw_elapsed, show_ms=True), note)

    def sw_reset(self):
        was = self._sw_now()
        self.sw_running = False
        self.sw_elapsed = 0.0
        self._set_running_look(self.btn_sw_start, False, "Start")
        self.lbl_stopwatch.config(text="00:00:00.00")
        if was > 0:
            self.log("STOPWATCH", "Reset from", self.format_time(was, show_ms=True),
                     self._note(self.entry_sw_note))

    def update_stopwatch(self):
        if self.sw_running:
            self.sw_elapsed = time.monotonic() - self.sw_start_time
            self.lbl_stopwatch.config(text=self.format_time(self.sw_elapsed, show_ms=True))
            self.root.after(50, self.update_stopwatch)

    # ==========================================
    # TIMER LOGIC
    # ==========================================
    def _tm_left(self):
        if self.tm_running:
            return max(0.0, self.tm_end_time - time.monotonic())
        return self.tm_remaining

    def _set_timer_entries(self, state):
        for entry in (self.entry_h, self.entry_m, self.entry_s):
            entry.config(state=state)

    def tm_toggle(self):
        note = self._note(self.entry_tm_note)
        if not self.tm_running:
            try:
                h = int(self.entry_h.get() or 0)
                m = int(self.entry_m.get() or 0)
                s = int(self.entry_s.get() or 0)
            except ValueError:
                messagebox.showerror("Error", "Please enter valid integers for the timer.",
                                     parent=self.root)
                return
            if min(h, m, s) < 0:
                messagebox.showerror("Error", "The timer cannot be set to a negative time.",
                                     parent=self.root)
                return
            total_seconds = (h * 3600) + (m * 60) + s
            if total_seconds <= 0 and self.tm_remaining <= 0:
                messagebox.showwarning("Invalid Input", "Please enter a valid time greater than zero.",
                                       parent=self.root)
                return

            resuming = self.tm_remaining > 0
            if not resuming:
                self.tm_remaining = float(total_seconds)
                self.tm_set_seconds = total_seconds

            self.tm_end_time = time.monotonic() + self.tm_remaining
            self.tm_running = True
            self.btn_tm_start.config(text="Pause", style='Stop.TButton')
            self._set_timer_entries('disabled')
            if resuming:
                self.log("TIMER", "Resumed with", f"{self.format_time(self.tm_remaining)} left", note)
            else:
                self.log("TIMER", "Started for", self.format_time(self.tm_remaining), note)
            self.update_timer()
        else:
            self.tm_remaining = max(0.0, self.tm_end_time - time.monotonic())
            self.tm_running = False
            self.btn_tm_start.config(text="Resume", style='Start.TButton')
            self.log("TIMER", "Paused with", f"{self.format_time(self.tm_remaining)} left", note)

    def tm_reset(self):
        was_active = self.tm_running or self.tm_remaining > 0
        left = self._tm_left()
        self.tm_running = False
        self.tm_remaining = 0.0
        self.btn_tm_start.config(text="Start", style='Start.TButton')
        self.lbl_timer.config(text="00:00:00")

        self._set_timer_entries('normal')
        for entry in (self.entry_h, self.entry_m, self.entry_s):
            entry.delete(0, tk.END)
            entry.insert(0, "0")
        if was_active:
            self.log("TIMER", "Reset with", f"{self.format_time(left)} left",
                     self._note(self.entry_tm_note))

    def update_timer(self):
        if self.tm_running:
            self.tm_remaining = self.tm_end_time - time.monotonic()
            if self.tm_remaining <= 0:
                self.tm_running = False
                self.tm_remaining = 0.0
                self.lbl_timer.config(text="00:00:00")
                self.btn_tm_start.config(text="Start", style='Start.TButton')
                self._set_timer_entries('normal')
                self.log("TIMER", "Finished", f"({self.format_time(self.tm_set_seconds)})",
                         self._note(self.entry_tm_note))
                self.play_beep()
            else:
                # Round up, so the display reads 00:00:01 until the last
                # second has really gone, and never shows 00:00:00 early.
                self.lbl_timer.config(text=self.format_time(int(self.tm_remaining + 0.999)))
                self.root.after(100, self.update_timer)

    def play_beep(self):
        """Plays a system beep sound."""
        if WINSOUND_AVAILABLE:
            try:
                # MB_ICONASTERISK is a standard, distinct Windows notification sound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                # Fallback multi-beep if standard notification fails to trigger attention
                self.root.after(500, lambda: winsound.Beep(800, 300))
                self.root.after(1000, lambda: winsound.Beep(800, 300))
            except Exception:
                pass
        else:
            # Fallback for Linux/Mac
            print('\a', end='', flush=True)


if __name__ == '__main__':
    root = tk.Tk()
    app = PICATimeUtilityApp(root)
    root.mainloop()
