# Contributing to PICA

First of all, thank you for considering contributing to PICA. With support from the community, we can create an open-source tool for scientific measurement and instrument automation to use instead of costly proprietary licensed software.

## Building a Collaborative Ecosystem

PICA is distributed under the permissive [MIT License](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/LICENSE) because we believe scientific software should be a dynamic, living project rather than a static tool. Our goal is to foster a community-driven ecosystem where researchers are free to modify and extend the codebase to support their specific instruments.

We designed PICA with a modular architecture specifically to make this process straightforward, preventing the redundant effort of rewriting automation scripts from scratch. By contributing your custom drivers or experimental protocols back to the project via GitHub Pull Requests, you help create a more robust and versatile tool for the entire scientific community. Additionally, we rely on community feedback via GitHub Issues to identify edge cases and resolve bugs across diverse hardware setups, accelerating development far beyond what a single team could achieve.

## Commitment to Transparency & Reproducibility

Beyond practical utility, making PICA open source is a statement of scientific integrity. Unlike proprietary "black box" software that obscures experimental methodology, PICA ensures complete transparency. By accessing the full source code, researchers can audit the exact SCPI command sequences, timing delays, and data handling procedures used in an experiment. This methodological clarity is essential for rigorous validation and reproducibility in modern experimental science.

## How to Report Bugs

If you encounter a bug, please help us by reporting it. A well-reported bug significantly speeds up the resolution process. Before opening a new issue, please check if a similar issue already exists.

When reporting a bug, please include as much detail as possible:

*   **Steps to reproduce:** Clearly describe how to trigger the bug.
*   **Expected behavior:** What did you expect to happen?
*   **Actual behavior:** What actually happened?
*   **PICA version:** Specify the [version](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases) of PICA you are using.
*   **Operating system:** Your OS and version (e.g., Windows 11, Ubuntu 24.04).
*   **Hardware setup:** Briefly describe your instrument setup if relevant.
*   **Measurement module:** The script file name or launcher label of the module involved.
*   **Error messages/logs:** The module's console log (Ctrl+A, Ctrl+C in the console pane) and any terminal traceback.

Two issue templates collect this for you:

*   [Bug report (Windows)](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues/new?template=bug_report.md) for Windows, the platform PICA is validated on with real instruments.
*   [Linux report](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues/new?template=linux_report.md) for anything Linux-specific. Running against real instruments from Linux is not yet verified, so successes are as welcome as problems. This template also asks for the VISA backend, `pyvisa-info` output and GPIB/USB/serial permission details.

macOS has not been tested.

## Why Measurement Modules Are Self-Contained

It is noticable that PICA's measurement modules repeat the same code: connection and reset, keep-awake, file writing, shutdown. This is a design decision, not an oversight, and this section explains it so that it is not reported as a defect.

**The rule.** One measurement is one self contained file. The GUI, the instrument commands, the protocol, the safety limits and the data writer for a measurement all live in that one `*_GUI.py` file. There are no shared base classes or helper modules for measurement code.

**Safety.** A lab user must be able to open one file and see every command that reaches the instrument, every voltage or current cap, and what happens on Stop or error. A shared helper would hide part of that behind an import and abstractions.

**Validated protocols stay validated.** A module that has been run on real hardware changes only when that file is edited. A fix for one instrument can never silently change another module's command sequence, compliance limit or shutdown path. With shared code, every refactor would re-open every protocol that depends on it and will need repeated instrument validations.

**Process isolation needs it.** The launcher starts each module in its own process. A self-contained file is what makes that boundary real: a module can be run, tested, copied to another PC or reverted on its own.

**What this costs, and how the cost is handled.** Duplicated common blocks exist and are expected. Sibling copies of the same logic (for example the Lake Shore 340, Lake Shore 350 and Cryo-con 34 variants of a module, or the common block shared by the three memristor modules) must be edited together, and each module has its own test file in `tests/` that drives the real module code against a mocked instrument, so drift between copies is caught there.

**What this means for review.** The same block appearing in many modules is the intended architecture. Suggestions to collect measurement code into shared modules are declined for the reasons above.

**Where sharing is allowed.** The launcher, the utilities under `pica/utils/`, and the test suite may share code. The rule applies to measurement modules only.

## Contributing Code

If this is something you think you can fix, then [fork PICA](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/fork) and create a branch with a descriptive name.

A good branch name would be:

```sh
git checkout -b feature/add-new-instrument
```

### Get the test suite running

Make sure you're running the [test suite locally](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/docs/User_Manual.md#34-development-dependencies-for-testing-optional). It's a good idea to do this before you start making changes to ensure that everything is working correctly.

```sh
pip install -e .
pip install -r requirements-dev.txt
python -B -m pytest -p no:cacheprovider tests/
```

Tests live in `tests/` (one test file per module). Every instrument is mocked, so no hardware is needed. Tests that open real windows are skipped unless `PICA_REAL_WINDOWS=1` is set. If you change a module's behaviour, add or update a test for it.

### Coding Style Guide

PICA adheres to [PEP 8](https://www.python.org/dev/peps/pep-0008/) for Python code style. Please ensure your contributions follow these guidelines. CI runs `flake8` (syntax errors fail the build; style is reported with `--max-line-length=127`). Please do not run an auto-formatter such as `black` over existing files; it produces large unrelated diffs.

### Implement your fix or feature

At this point, you're ready to make your changes. Feel free to ask for help in an issue; everyone is a beginner at first. Please keep real sample names, measurement data and lab paths out of code, tests and docs, keep data-file headers ASCII-safe, and add a line under `[Unreleased]` in `CHANGELOG.md` for user-visible changes.

### Make a Pull Request

Once you've made your changes and tested them locally, you're ready to create a Pull Request (PR). A good PR makes it easy for maintainers to understand and review your changes. Please include:

*   **A clear and concise title:** Summarize the main purpose of your PR.
*   **Detailed description:** Explain the problem your PR solves, how you solved it, and any significant design decisions.
*   **References to issues:** Link to any related issues (e.g., "Fixes #123" or "Closes #456").
*   **Testing done:** Describe how you tested your changes.

At this point, you should switch back to your main branch and make sure it's up to date with the latest upstream version of PICA.

```sh
git remote add upstream git@github.com:prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
git checkout main
git pull upstream main
```

Then update your feature branch from your local copy of main, and push it!

```sh
git checkout feature/add-new-instrument
git rebase main
git push --force-with-lease origin feature/add-new-instrument
```

Finally, go to GitHub and [make a Pull Request](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/pulls)

### Keeping your Pull Request updated

If a maintainer asks you to "rebase" your PR, they're saying that a lot of code has changed, and that you need to update your branch so it's easier to merge.

To learn more about rebasing and merging, check out this guide from Atlassian:
[https://www.atlassian.com/git/tutorials/merging-vs-rebasing](https://www.atlassian.com/git/tutorials/merging-vs-rebasing)

### Adding a New Instrument Module

One of the most important contributions you can make is to add support for new instruments. The architecture was designed to make this process straightforward by following a set of well-defined steps. Essentially, these are the same steps the original developers followed when adding new modules.

#### Understanding the Architecture

Each measurement program is a single self-contained file that can be run on its own; the launcher starts each one in its own process. By design there are no shared base classes or helper modules; see [Why Measurement Modules Are Self-Contained](#why-measurement-modules-are-self-contained) for the reasoning. When sibling copies of the same logic exist (for example the Lake Shore 340, Lake Shore 350 and Cryo-con 34 variants of a module, or the common block shared by the three memristor modules), update every copy.

#### Start with a Template

The easiest way to start is to copy an existing module that is closest to your task or has the functionality you need.

#### 1. Create New Files

1.  Create a new directory for your instrument under the appropriate vendor (e.g., `pica/new_vendor/my_instrument/`).
2.  Paste the copied file into this new directory. Each measurement program is one self-contained `*_GUI.py` file.
3.  Rename the file to reflect your new module (e.g., `IV_ABC_Instrument_GUI.py`). An optional command-line version (e.g., `IV_ABC_Instrument_Control.py`) goes in an `Instrument_Control/` subfolder.

#### 2. Implement the Instrument Control Logic

Start with the basic instrument communication, then add measurement protocols, data saving, and plotting. Ensure that instrument communication (handled by `PyVISA` or `pymeasure`) uses the correct SCPI commands. Replace the existing SCPI commands (e.g. `:SOUR:VOLT`, `:MEAS:CURR?`) with the commands specific to your instrument. Your instrument's programming manual is the definitive source for these commands. Adjust the data parsing logic to handle the output format of your instrument.

#### 3. Modify the GUI (`..._GUI.py`)

1.  Open your new `..._GUI.py` file.
2.  Update the `Tkinter` widgets (labels, entry boxes, dropdowns) to match the parameters required for your instrument (e.g., voltage range, compliance, measurement speed).
3.  Ensure the "Start" button runs your control logic inside the same file (not imported from a shared module), passing the necessary parameters from the GUI.

#### 4. Integrate into the PICA Launcher

To make your new module accessible from the PICA launcher (`pica-gui`):

1.  **Script path (`pica/main.py`):** add your script to the `SCRIPT_PATHS` dictionary (a display key mapped to `resource_path("vendor/.../Your_Module_GUI.py")`). The launcher reads its paths from this table, so this step is needed even though the classic launcher in that file is outdated.
2.  **Launcher (`pica/main_v2.py`):** add the same key to the matching group in `CATALOG` (Advanced Options), to `QUICK_CATALOG` if it also belongs on Quick Select, and add a hover-card description for the same key to `MODULE_INFO` in `pica/module_info.py` (`tests/test_launcher_v2_search_and_hover.py` fails otherwise).
3.  **Legacy launcher (optional):** to show it in the outdated classic launcher too (`tools/legacy_stuff/run_pica_legacy.py`), add an entry `(button label, script key)` to the button list of the matching `self._create_suite_frame(...)` call in `create_launcher_panel` in `pica/main.py`. Add `'control'` or `'sensing'` as a third item if the module drives or only reads a temperature controller.
4.  Run the launcher tests (`python -B -m pytest tests/test_gui_module_registry.py tests/test_launcher_v2_wiring.py tests/test_launcher_v2_search_and_hover.py -p no:cacheprovider`) to check that every key points to a file that exists.

#### 5. Test and Document

- Run your new module standalone and from the PICA launcher to ensure it works as expected. Test edge cases. It's often helpful to have a new user try it, as they can often find bugs you might have missed.
- Consider adding a section for your new instrument in `docs/User_Manual.md` and adding a screenshot to `pica/assets/Images/screenshots/`.

## Use of Generative AI

PICA started before AI coding tools were in use. As these tools became more capable, we used them more, to improve the code quality, the tests, the user interfaces and the reliability of the software in production. What did not change is that we understand the code, knowing what is happening at every step is essential when the software drives real instruments and scientific measuremenets. The instrument manuals are the source of truth. Every instrument command is verified against them and tested, and we think through every edge case, because in a lab experiment:

> *"Whatever could go wrong will go wrong."* (Prathamesh Deshmukh)

**Tools used:** Google Gemini (2.5) in the earlier stages of development, and later Anthropic's Claude (Claude Code).

**What they were used for:** writing and refactoring code, writing tests that run against mocked instruments, and drafting and proofreading documentation.

**Rules:**

- Every change is reviewed, run and understood by a maintainer before it is committed. The maintainers are responsible for all code in PICA, whoever or whatever drafted it.
- Instrument-facing code is checked against the instrument manual and, wherever possible, on the real instrument before release. AI output alone is never treated as proof that an instrument command is correct.
- Contributors may use AI tools, but must say so in their pull request and must understand what they are submitting.
- Issues, reviews and discussions are written by people. AI may be used only for grammar or translation.

## Support

For any questions, support, or assistance, please open a GitHub Issue in the main repository. This is the preferred and primary method for getting in touch with the development team and community for support.

## Code of Conduct

Everyone interacting in the PICA project's codebases, issue trackers and pull requests is expected to follow the [PICA Code of Conduct](CODE_OF_CONDUCT.md).