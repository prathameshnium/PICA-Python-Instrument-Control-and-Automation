'''
Process target for modules launched from the v2 launcher.

Kept in its own tiny, Tk-free module on purpose. With the 'spawn' start
method the child process unpickles the target by importing the module that
defines it; when the target lived in pica.main, every launched module first
imported tkinter, PIL and the whole legacy launcher (~0.3 s) before its own
script even started. Importing this file costs only os/sys/runpy.

Copied from run_script_process in pica/main.py (the v1 launcher keeps its
own copy); keep the two in step.
'''
import os
import sys
import runpy


def run_script_process(script_path, argv=None):
    """
    Execute a script with runpy, from its own directory, as __main__.
    os.chdir is fine here: this runs in a separate spawned process and does
    not touch the launcher's working directory.

    'argv' is an optional list of command-line arguments (e.g. data files to
    pre-load); it is placed in sys.argv exactly as a shell invocation would.
    """
    try:
        os.chdir(os.path.dirname(script_path))
        sys.argv = [script_path] + list(argv or [])
        runpy.run_path(script_path, run_name="__main__")
    except Exception as e:
        print(f"--- Sub-process Error in {os.path.basename(script_path)} ---")
        print(e)
        print("-------------------------")
