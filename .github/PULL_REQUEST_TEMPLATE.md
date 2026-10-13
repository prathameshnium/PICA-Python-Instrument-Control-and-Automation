## What this changes

<!-- One or two sentences. Link the issue it fixes, e.g. "Fixes #12". -->

## Instruments and modules touched

<!-- e.g. Keithley 2400 I-V (pica/keithley/k2400/IV_K2400_GUI.py) -->

## How it was tested

- [ ] `python -m pytest tests/` passes locally (mocked instruments, no hardware needed)
- [ ] New or changed behaviour has a test in `tests/`
- [ ] Run against real hardware (say which instrument, firmware and OS below), or not tested on hardware

<!-- Hardware notes: -->

## Checklist

- [ ] Each program stays self-contained. If the same logic lives in sibling copies (for example the memristor modules' common block, or the L340 / L350 / CC34 variants), every copy is updated
- [ ] No real sample names, measurement data or lab paths in code, tests or docs
- [ ] Data-file headers and `.seq` files stay ASCII-safe
- [ ] `CHANGELOG.md` has a line under `[Unreleased]` if users will notice the change
- [ ] Docs (README / `docs/User_Manual.md`) updated if a user-facing step changed
