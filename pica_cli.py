#!/usr/bin/env python3
"""
PICA Command Line Launcher
--------------------------
Use this script to run PICA measurement modules without the Graphical User Interface.
Ideal for remote access (SSH) or automated environments.
"""

from pica.cli import main

if __name__ == "__main__":
    main()