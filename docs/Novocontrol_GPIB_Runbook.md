# Novocontrol Alpha over the Novocontrol/ines GPIB card — bring-up runbook

Goal: establish Python ↔ Alpha-AN communication on the lab PC (64-bit
Python 3.10, no NI-VISA, Novocontrol's own PCI GPIB card — an OEM'd **ines**
card). WinDETA already works there, so the card, driver, and cable are good.

## Why it failed so far

- The default PyVISA backend (NI-VISA) cannot see ines cards at all. On the
  lab PC PyVISA runs on **pyvisa-py**, and the `ASRL1::INSTR` it lists is
  just COM1 (a bare serial port) — `*IDN?` there can only time out.
- pyvisa-py reaches GPIB through **gpib-ctypes**, which needs the ines
  driver's **NI-488.2 compatible `gpib-32.dll`**. That DLL is either not
  installed or exists only as 32-bit (unloadable from 64-bit Python).

## Bring along (download before going to the lab — it may be offline)

1. This repo, updated (`git pull`).
2. The **ines GPIB driver for Windows x64** with the NI-488.2 compatibility
   component (from Novocontrol support or ines; the WinDETA install media
   may also contain it).
3. Fallback: the **32-bit Python 3.10 installer** (python.org, "Windows
   installer (32-bit)"). The lifeline script is stdlib-only, so 32-bit
   Python needs **no pip and no internet** to use it.

## At the lab PC, in order

1. **Close WinDETA** (only one program may drive the board).
2. `pip uninstall pygpib` — an empty name-squatted package that shadows the
   real GPIB bindings.
3. Run the lifeline first — it needs nothing but Python:

       python pica/utils/GPIB_Lifeline_CLI.py

   It reports the environment, hunts and classifies every GPIB DLL on the
   machine, scans the bus, queries `*IDN?`, and drops into an interactive
   terminal. Everything is saved to `gpib_lifeline_report_<timestamp>.txt`.

4. Follow its **VERDICT**:
   - *No loadable DLL* → install the ines x64 driver (step above), or run
     the same script with 32-bit Python: `py -3-32 pica/utils/GPIB_Lifeline_CLI.py`
   - *Driver works, nothing answers* → check Alpha power, cable, board index
     (`--board 1`), or force the address from WinDETA's device settings
     (`--address 5`).
   - *COMMUNICATION ESTABLISHED* → try `INTTYP?` in its terminal (the ZG4
     interface reports code 5), then move to the GUI.
5. GUI: `python pica/utils/SCPI_Console_GUI.py` → **Refresh** (the log now
   names the backend and gives a GPIB-driver verdict) → select the
   `GPIB0::N::INSTR` entry or type it → **Term = EOI** → **Connect**.
   `*IDN?` should return `NOVOCONTROL … Alpha`.
6. **Restart the GUI after any driver install** — the GPIB backend is
   chosen once per process.

## Safety — instrument and system

Instrument (the Alpha can never be harmed by the bring-up itself):

- The **only traffic any PICA tool sends automatically is `*IDN?`** — a
  read-only IEEE-488.2 identification query. Scans never write settings.
- The whole bring-up needs only read-only queries: `*IDN?`, `INTTYP?`.
- In the lifeline terminal: queries send freely; any state-changing write
  asks for confirmation; **DCV/DCE (DC bias) are blocked outright** — this
  mainframe has no bias hardware and `pica/novocontrol` never sends them.
  `:safe` parks the analyzer with the documented sequence
  `MBK, ACV=0, ZCONSPL=0`.
- In the SCPI Console GUI, DCV/DCE/RSTH writes pop a confirmation dialog
  (default No) before anything reaches the bus.
- `*RST` is safe on the Alpha: it parks the generator and **preserves** the
  stored calibration tables (per the Alpha manual and `pica/novocontrol`).
- Never run WinDETA and Python at the same time — one program on the bus.

System (the PC that WinDETA depends on):

- Python-side fixes (`pip uninstall pygpib`, running the scripts) touch
  nothing outside Python — the lifeline only *reads* DLLs and files.
- The one system-level change is the **driver install**. Before it:
  create a Windows restore point. Prefer adding the NI-488.2 compatibility
  component of the *same* ines driver version already installed over
  upgrading the whole driver, so the stack WinDETA uses stays untouched.
- Acceptance test after any driver change: **start WinDETA and confirm it
  still talks to the Alpha**, then close it and continue with Python.

## If it still fails

Run `python pica/utils/GPIB_Lifeline_CLI.py --deep` (walks all of `C:\` for
GPIB DLLs, takes a few minutes) and keep the report file — it contains
everything needed to debug offline.

## Notes specific to the Alpha

- EOI-only framing: no newline terminators in either direction
  (GUI: Term = EOI). Responses are padded with CR/NUL/DLE bytes.
- It answers IEEE-488.2 common commands (`*IDN?`, `*RST`) plus the
  proprietary set from the Alpha manual (`INTTYP?`, `GFR=`, `MST`, `ZRE?` …)
  used by `pica/novocontrol/`.
- `*RST` parks the generator and preserves calibration tables — safe.
