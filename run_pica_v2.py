import multiprocessing

if __name__ == '__main__':
    # Imported here, not at the top: with 'spawn' every launched module
    # re-runs this file as __mp_main__, and a top-level import made each of
    # them load the whole launcher before its own script started.
    from pica.main_v2 import main
    # Essential for multiprocessing under a frozen/bundled executable.
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main()
