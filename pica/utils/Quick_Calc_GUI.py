'''
===============================================================================
 PROGRAM:      PICA Quick Calc
 PURPOSE:      A minimalistic inline Python evaluator for quick lab calculations.
===============================================================================
'''
import tkinter as tk
from tkinter import ttk, scrolledtext
import math

class PICAQuickCalcApp:

    # --- PICA Theme Constants ---
    CLR_BG_DARK = '#B8A392'
    CLR_FRAME_BG = '#E5DCD3'
    CLR_ACCENT_GOLD = '#BA6B5E'
    CLR_TEXT = '#2C2825'
    CLR_TEXT_DARK = '#1A1A1A'
    
    FONT_SIZE_BASE = 12
    FONT_BASE = ('Segoe UI', FONT_SIZE_BASE)
    FONT_CONSOLE = ('Consolas', 11)

    def __init__(self, root):
        self.root = root
        self.root.title("PICA Quick Calc")
        self.root.geometry("500x350")
        self.root.configure(bg=self.CLR_BG_DARK)
        
        # --- Python Execution Namespace ---
        # Initialize with standard builtins and the math module
        self.namespace = {"__builtins__": __builtins__}
        self.namespace.update(math.__dict__)
        self.namespace['_'] = None # Stores the last evaluated result
        
        # Try to include numpy if the user has it installed
        try:
            import numpy as np
            self.namespace['np'] = np
        except ImportError:
            pass

        self.setup_styles()
        self.create_widgets()
        
        # Print a welcome message
        self.log("PICA Inline Python Evaluator", is_info=True)
        self.log("Math module loaded. Use '_' for the last result.", is_info=True)
        self.log("-" * 45, is_info=True)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.CLR_BG_DARK, foreground=self.CLR_TEXT)
        style.configure('TFrame', background=self.CLR_BG_DARK)

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill='both', expand=True)

        # --- Input Area ---
        input_frame = ttk.Frame(main_frame)
        input_frame.pack(side='bottom', fill='x')

        tk.Label(input_frame, text=">>>", font=self.FONT_CONSOLE, bg=self.CLR_BG_DARK, fg=self.CLR_TEXT_DARK).pack(side='left', padx=(0, 5))
        
        self.entry = ttk.Entry(input_frame, font=self.FONT_CONSOLE)
        self.entry.pack(side='left', fill='x', expand=True)
        self.entry.focus()
        
        # Bind the Enter key to execution
        self.entry.bind("<Return>", self.execute_code)

        # --- History Output Area ---
        self.history_text = scrolledtext.ScrolledText(
            main_frame, 
            wrap='word', 
            font=self.FONT_CONSOLE, 
            bg=self.CLR_FRAME_BG, 
            fg=self.CLR_TEXT_DARK,
            bd=0,
            padx=10,
            pady=10
        )
        self.history_text.pack(side='top', fill='both', expand=True, pady=(0, 10))
        
        # Tag configurations for different types of text
        self.history_text.tag_configure("input", foreground="#2E5266", font=('Consolas', 11, 'bold'))
        self.history_text.tag_configure("result", foreground=self.CLR_TEXT_DARK)
        self.history_text.tag_configure("error", foreground="#BA2D2D")
        self.history_text.tag_configure("info", foreground="#666666", font=('Consolas', 10, 'italic'))
        
        self.history_text.config(state='disabled')

    def log(self, message, is_input=False, is_result=False, is_error=False, is_info=False):
        """Helper to print formatted text to the history area."""
        self.history_text.config(state='normal')
        
        tag = "result"
        if is_input: tag = "input"
        elif is_error: tag = "error"
        elif is_info: tag = "info"

        self.history_text.insert('end', f"{message}\n", tag)
        self.history_text.see('end')
        self.history_text.config(state='disabled')

    def execute_code(self, event=None):
        code = self.entry.get().strip()
        if not code:
            return
            
        self.log(f">>> {code}", is_input=True)
        self.entry.delete(0, tk.END)

        try:
            # First, try to evaluate it as an expression (e.g., "2 + 2" or "sin(pi/2)")
            result = eval(code, self.namespace)
            if result is not None:
                self.log(repr(result), is_result=True)
                self.namespace['_'] = result  # Save to underscore variable
                
        except SyntaxError:
            # If it's not an expression, it might be a statement (e.g., "a = 5" or "import os")
            try:
                exec(code, self.namespace)
            except Exception as e:
                self.log(f"Error: {e}", is_error=True)
        except Exception as e:
            self.log(f"Error: {e}", is_error=True)


if __name__ == '__main__':
    root = tk.Tk()
    app = PICAQuickCalcApp(root)
    root.mainloop()