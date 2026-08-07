import multiprocessing
from pica.main_v2 import main

if __name__ == '__main__':
    # Essential for multiprocessing under a frozen/bundled executable.
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main()
