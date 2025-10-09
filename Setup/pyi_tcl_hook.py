# Setup/pyi_tcl_hook.py
"""
PyInstaller Tcl/Tk Hook Helper

This script programmatically locates the Tcl and Tk library directories
required by tkinter. It is used by the build script to ensure these
directories are correctly bundled by PyInstaller, resolving common
FileNotFoundError issues with bundled tkinter applications.

It works by creating a temporary tkinter root window to access the Tcl
interpreter's variables (`$tcl_library`, `$tk_library`) and then
derives the parent directories that need to be included.
"""
import os
import tkinter

def get_tcl_tk_paths():
    """Finds and returns the paths for Tcl and Tk data folders."""
    root = tkinter.Tk()
    root.withdraw() # Don't show the window
    tcl_path = os.path.join(root.tk.expr('$tcl_library'), '..')
    tk_path = os.path.join(root.tk.expr('$tk_library'), '..')
    root.destroy()
    
    return [os.path.normpath(tcl_path), os.path.normpath(tk_path)]