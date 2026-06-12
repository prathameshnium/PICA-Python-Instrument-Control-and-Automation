'''
===============================================================================
 PROGRAM:      PICA Time Utility
 PURPOSE:      A minimalistic clock, stopwatch, and timer for lab measurements.

===============================================================================
'''
import tkinter as tk
from tkinter import ttk, messagebox
import time
from datetime import datetime
import sys
import platform

# Attempt to import winsound for Windows-native beeps
try:
    import winsound
    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False


class PICATimeUtilityApp:

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_TITLE = ('Segoe UI', FONT_SIZE_BASE + 6, 'bold')
    FONT_SUBTITLE = ('Segoe UI', FONT_SIZE_BASE + 1, 'bold')
    FONT_DIGITAL = ('Consolas', 28, 'bold')

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Time Utility")
        self.root.geometry("450x620")
        self.root.configure(bg=self.CLR_BG_DARK)
        self.root.resizable(False, False)

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

        self.setup_styles()
        self.create_widgets()
        
        # Start background update loops
        self.update_clock()

    def setup_styles(self):
        """Initializes the PICA theme styles."""
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)
        style.configure('Sub.TFrame', background=self.CLR_FRAME_BG)
        style.configure('TLabelframe', background=self.CLR_FRAME_BG, bordercolor=self.CLR_BG_DARK, borderwidth=2, padding=10)
        style.configure('TLabelframe.Label', background=self.CLR_FRAME_BG, foreground=self.CLR_TEXT, font=self.FONT_SUBTITLE)
        style.configure('App.TButton', font=self.FONT_BASE, padding=(10, 5), foreground=self.CLR_ACCENT_GOLD, background=self.CLR_FRAME_BG, borderwidth=0)
        style.map('App.TButton', 
                  background=[('active', self.CLR_ACCENT_GOLD)], 
                  foreground=[('active', self.CLR_TEXT_DARK)])

    def create_widgets(self):
        """Constructs the Clock, Stopwatch, and Timer interfaces."""
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill='both', expand=True)

        # ==========================================
        # 1. CLOCK SECTION
        # ==========================================
        clock_frame = ttk.LabelFrame(main_frame, text="Current Time")
        clock_frame.pack(fill='x', pady=(0, 15))
        
        self.lbl_clock = tk.Label(clock_frame, text="00:00:00", font=self.FONT_DIGITAL, bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK)
        self.lbl_clock.pack(pady=(5, 0))
        
        self.lbl_date = tk.Label(clock_frame, text="YYYY-MM-DD", font=self.FONT_BASE, bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT)
        self.lbl_date.pack(pady=(0, 5))
        
        toggle_btn = ttk.Checkbutton(clock_frame, text="12-Hour Format", variable=self.is_12_hour, command=self.update_clock_display)
        toggle_btn.pack(pady=(0, 5))

        # ==========================================
        # 2. STOPWATCH SECTION
        # ==========================================
        sw_frame = ttk.LabelFrame(main_frame, text="Stopwatch")
        sw_frame.pack(fill='x', pady=(0, 15))
        
        self.lbl_stopwatch = tk.Label(sw_frame, text="00:00:00.00", font=self.FONT_DIGITAL, bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK)
        self.lbl_stopwatch.pack(pady=10)
        
        sw_btn_frame = ttk.Frame(sw_frame, style='Sub.TFrame')
        sw_btn_frame.pack(fill='x', pady=5)
        sw_btn_frame.columnconfigure((0, 1), weight=1)
        
        self.btn_sw_start = ttk.Button(sw_btn_frame, text="Start", style='App.TButton', command=self.sw_toggle)
        self.btn_sw_start.grid(row=0, column=0, padx=2, sticky='ew')
        ttk.Button(sw_btn_frame, text="Reset", style='App.TButton', command=self.sw_reset).grid(row=0, column=1, padx=2, sticky='ew')

        # ==========================================
        # 3. TIMER SECTION
        # ==========================================
        tm_frame = ttk.LabelFrame(main_frame, text="Timer")
        tm_frame.pack(fill='x')
        
        self.lbl_timer = tk.Label(tm_frame, text="00:00:00", font=self.FONT_DIGITAL, bg=self.CLR_FRAME_BG, fg=self.CLR_TEXT_DARK)
        self.lbl_timer.pack(pady=10)
        
        # Inputs for Timer
        input_frame = tk.Frame(tm_frame, bg=self.CLR_FRAME_BG)
        input_frame.pack(pady=5)
        
        tk.Label(input_frame, text="H:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(side='left')
        self.entry_h = ttk.Entry(input_frame, width=4, font=self.FONT_BASE, justify='center')
        self.entry_h.insert(0, "0")
        self.entry_h.pack(side='left', padx=(0, 10))
        
        tk.Label(input_frame, text="M:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(side='left')
        self.entry_m = ttk.Entry(input_frame, width=4, font=self.FONT_BASE, justify='center')
        self.entry_m.insert(0, "0")
        self.entry_m.pack(side='left', padx=(0, 10))
        
        tk.Label(input_frame, text="S:", bg=self.CLR_FRAME_BG, font=self.FONT_BASE).pack(side='left')
        self.entry_s = ttk.Entry(input_frame, width=4, font=self.FONT_BASE, justify='center')
        self.entry_s.insert(0, "0")
        self.entry_s.pack(side='left')

        tm_btn_frame = ttk.Frame(tm_frame, style='Sub.TFrame')
        tm_btn_frame.pack(fill='x', pady=(10, 5))
        tm_btn_frame.columnconfigure((0, 1), weight=1)
        
        self.btn_tm_start = ttk.Button(tm_btn_frame, text="Start", style='App.TButton', command=self.tm_toggle)
        self.btn_tm_start.grid(row=0, column=0, padx=2, sticky='ew')
        ttk.Button(tm_btn_frame, text="Reset", style='App.TButton', command=self.tm_reset).grid(row=0, column=1, padx=2, sticky='ew')

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
        mins, secs = divmod(seconds, 60)
        hours, mins = divmod(mins, 60)
        if show_ms:
            ms = int((seconds - int(seconds)) * 100)
            return f"{int(hours):02d}:{int(mins):02d}:{int(secs):02d}.{ms:02d}"
        return f"{int(hours):02d}:{int(mins):02d}:{int(secs):02d}"

    # ==========================================
    # STOPWATCH LOGIC
    # ==========================================
    def sw_toggle(self):
        if not self.sw_running:
            self.sw_start_time = time.time() - self.sw_elapsed
            self.sw_running = True
            self.btn_sw_start.config(text="Stop")
            self.update_stopwatch()
        else:
            self.sw_running = False
            self.btn_sw_start.config(text="Start")

    def sw_reset(self):
        self.sw_running = False
        self.sw_elapsed = 0.0
        self.btn_sw_start.config(text="Start")
        self.lbl_stopwatch.config(text="00:00:00.00")

    def update_stopwatch(self):
        if self.sw_running:
            self.sw_elapsed = time.time() - self.sw_start_time
            self.lbl_stopwatch.config(text=self.format_time(self.sw_elapsed, show_ms=True))
            self.root.after(50, self.update_stopwatch)

    # ==========================================
    # TIMER LOGIC
    # ==========================================
    def tm_toggle(self):
        if not self.tm_running:
            try:
                h = int(self.entry_h.get() or 0)
                m = int(self.entry_m.get() or 0)
                s = int(self.entry_s.get() or 0)
                total_seconds = (h * 3600) + (m * 60) + s
                
                if total_seconds <= 0 and self.tm_remaining <= 0:
                    messagebox.showwarning("Invalid Input", "Please enter a valid time greater than zero.")
                    return

                if self.tm_remaining == 0:
                    self.tm_remaining = total_seconds

                self.tm_end_time = time.time() + self.tm_remaining
                self.tm_running = True
                self.btn_tm_start.config(text="Stop")
                
                # Disable entries while running
                self.entry_h.config(state='disabled')
                self.entry_m.config(state='disabled')
                self.entry_s.config(state='disabled')
                
                self.update_timer()
            except ValueError:
                messagebox.showerror("Error", "Please enter valid integers for the timer.")
        else:
            self.tm_running = False
            self.btn_tm_start.config(text="Start")

    def tm_reset(self):
        self.tm_running = False
        self.tm_remaining = 0.0
        self.btn_tm_start.config(text="Start")
        self.lbl_timer.config(text="00:00:00")
        
        self.entry_h.config(state='normal')
        self.entry_m.config(state='normal')
        self.entry_s.config(state='normal')
        
        self.entry_h.delete(0, tk.END)
        self.entry_h.insert(0, "0")
        self.entry_m.delete(0, tk.END)
        self.entry_m.insert(0, "0")
        self.entry_s.delete(0, tk.END)
        self.entry_s.insert(0, "0")

    def update_timer(self):
        if self.tm_running:
            self.tm_remaining = self.tm_end_time - time.time()
            if self.tm_remaining <= 0:
                self.tm_running = False
                self.tm_remaining = 0.0
                self.lbl_timer.config(text="00:00:00")
                self.btn_tm_start.config(text="Start")
                self.entry_h.config(state='normal')
                self.entry_m.config(state='normal')
                self.entry_s.config(state='normal')
                self.play_beep()
            else:
                self.lbl_timer.config(text=self.format_time(self.tm_remaining, show_ms=False))
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