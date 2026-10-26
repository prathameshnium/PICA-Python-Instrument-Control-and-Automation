import multiprocessing

if __name__ == '__main__':
    # Opens launcher v2 straight into Advanced Options (Home returns to
    # Quick Select). The classic launcher (pica/main.py) is outdated: View >
    # Legacy Launcher, or legacy_stuff/run_pica_legacy.py. Imported here,
    # not at the top, for the same reason as in run_pica_v2.py: with 'spawn'
    # every launched module re-runs this file as __mp_main__.
    from pica.main_v2 import main
    # Essential for multiprocessing under a frozen/bundled executable.
    multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    main(advanced=True)
