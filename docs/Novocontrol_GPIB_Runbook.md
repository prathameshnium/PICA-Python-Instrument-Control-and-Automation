# Novocontrol Alpha over the NI GPIB card — bring-up runbook

Goal: establish Python ↔ Alpha-AN communication on the lab PC. The PC now has
an **NI PCI GPIB interface** on a fresh install of **NI-488.2, the Keysight IO
Libraries, and 64-bit Python 3.10** with all packages. The previous
Novocontrol/ines card (and its pyvisa-py + gpib-ctypes workaround chain) is
retired; that era's runbook and code live in git history.

## Ground rules — the tools are gentle by design

The Alpha is delicate and expensive, and the old card's lock-ups were never
fully explained. All PICA tooling therefore follows these rules:

- **Refresh/scan sends nothing.** The SCPI Console's Refresh only *lists*
  VISA addresses; no instrument is opened or queried until you press
  **Connect**, which sends exactly one read-only `*IDN?`.
- **One attempt per operation.** No retries, no automatic recovery — a
  failure is reported once and the tool waits for you.
- **Every state-changing write asks for confirmation** (anything not ending
  in `?`), in both the GUI and the lifeline terminal. Queries send freely.
- **DCV/DCE and RSTH are blocked outright** in both tools: DCV/DCE set the
  DC bias (this mainframe has no bias hardware; `pica/novocontrol` never
  sends them) and RSTH is a hard reset.
- **One program on the bus.** Never run WinDETA (or NI MAX's communicator)
  and a PICA tool at the same time.

## At the lab PC, in order

1. **Close WinDETA** and any other GPIB program.
2. Optional sanity check without Python: open **NI MAX** (installed with
   NI-488.2) → Devices and Interfaces → the GPIB board should be listed as
   `GPIB0`. Don't use "Scan for Instruments" if you want to stay fully
   hands-off — the PICA tools don't need it.
3. GUI: `python pica/utils/SCPI_Console_GUI.py` → the log names the active
   VISA backend (NI or Keysight, whichever is registered as primary) →
   **Refresh** lists addresses only. Select the `GPIB0::N::INSTR` entry or
   type it (the Alpha's address is in WinDETA's device settings; historically
   it has been 5, i.e. `GPIB0::5::INSTR`). **Term auto-sets to EOI** for GPIB
   addresses → **Connect**.
4. `*IDN?` should return `NOVOCONTROL … Alpha`. Then try `INTTYP?` — the ZG4
   interface reports code 5. Both are read-only.
5. If the GUI cannot connect, drop to the lifeline (stdlib-only, bypasses
   pyvisa and talks to the NI-488.2 DLL directly):

       python pica/utils/GPIB_Lifeline_CLI.py --address 5

   `--address` is **required** — there is no bus sweep; the tool sends a
   single `*IDN?` to that one address and opens an interactive terminal.
   Everything is saved to `gpib_lifeline_report_<timestamp>.txt`.

## Troubleshooting

- **Refresh lists no GPIB resources** → type the address manually
  (`GPIB0::5::INSTR`) and press Connect anyway; listing depends on the VISA
  resource database, not the instrument.
- **`*IDN?` times out (ENOL / no listener)** → check Alpha power, cable
  seating, the address in WinDETA's device settings, and that no other GPIB
  program is running. Try `--board 1` in the lifeline if NI MAX shows the
  board at a different index.
- **Lifeline: "No loadable NI-488.2 DLL"** → repair the NI-488.2
  installation (it places `gpib-32.dll` in System32), or point at a specific
  DLL with `--dll <path>`.
- After any driver install or repair, the acceptance test is: **start
  WinDETA and confirm it still talks to the Alpha**, close it, then continue
  with Python.

## Notes specific to the Alpha

- EOI-only framing: no newline terminators in either direction (the GUI
  auto-selects Term = EOI for GPIB addresses). Responses are padded with
  CR/NUL/DLE bytes; the tools strip them.
- It answers IEEE-488.2 common commands (`*IDN?`, `*RST`) plus the
  proprietary set from the Alpha manual (`INTTYP?`, `GFR=`, `MST`, `ZRE?` …)
  used by `pica/novocontrol/`.
- `*RST` parks the generator and **preserves** the stored calibration tables
  (per the Alpha manual and `pica/novocontrol`) — but like every
  state-changing write it now requires an explicit yes before it is sent.
- In the lifeline terminal, `:safe` parks the analyzer with the documented
  sequence `MBK, ACV=0, ZCONSPL=0` (same order as
  `pica/novocontrol` `safe_state()`).
