import multiprocessing
import os
import sys

# This script sits in legacy_stuff/, so sys.path[0] is that folder, not the
# repo root: put the root first so "import pica" finds the tree next to it.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if __name__ == '__main__':
    # The classic (v1) launcher, pica/main.py. Outdated and kept as the
    # legacy launcher page; run_pica.py and pica-gui open launcher v2.
    from pica.main import main
    # Essential for multiprocessing under a frozen/bundled executable.
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main()
