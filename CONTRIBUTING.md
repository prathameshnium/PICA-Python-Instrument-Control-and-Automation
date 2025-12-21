# Contributing to PICA

First of all, thank you for considering contributing to PICA. With support from the community, we can create an open-source tool for scientific measurement and instrument automation to use instead of costly proprietary licensed software.

# Building a Collaborative Ecosystem

PICA is distributed under the permissive [MIT License](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/LICENSE) because we believe scientific software should be a dynamic, living project rather than a static tool. Our goal is to foster a community-driven ecosystem where researchers are free to modify and extend the codebase to support their specific instruments.

We designed PICA with a modular architecture specifically to make this process straightforward preventing the redundant effort of rewriting automation scripts from scratch. By contributing your custom drivers or experimental protocols back to the project via GitHub Pull Requests, you help create a more robust and versatile tool for the entire scientific community. Additionally, we rely on community feedback via GitHub Issues to identify edge cases and resolve bugs across diverse hardware setups, accelerating development far beyond what a single team could achieve.

# Commitment to Transparency & Reproducibility

Beyond practical utility, making PICA open source is a statement of scientific integrity. Unlike proprietary "black box" software that obscures experimental methodology, PICA ensures complete transparency. By accessing the full source code, researchers can audit the exact SCPI command sequences, timing delays, and data handling procedures used in an experiment. This methodological clarity is essential for rigorous validation and reproducibility in modern experimental science.

## How to Report Bugs

If you encounter a bug, please help us by reporting it. A well-reported bug significantly speeds up the resolution process. Before opening a new issue, please check if a similar issue already exists.

When reporting a bug, please include as much detail as possible:

*   **Steps to reproduce:** Clearly describe how to trigger the bug.
*   **Expected behavior:** What did you expect to happen?
*   **Actual behavior:** What actually happened?
*   **PICA version:** Specify the [version](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/releases) of PICA you are using.
*   **Operating system:** Your OS and version (e.g., Windows 10, Ubuntu 20.04).
*   **Hardware setup:** Briefly describe your instrument setup if relevant.
*   **Error messages/logs:** Include any relevant error messages or console output.

You can use the [bug report issue template](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues/new?assignees=&labels=bug&projects=&template=bug_report.md&title=%5BBUG%5D) to ensure you provide all necessary information.

## Contributing Code

If this is something you think you can fix, then [fork PICA](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/fork) and create a branch with a descriptive name.

A good branch name would be:

```sh
git checkout -b feature/add-new-instrument
```

### Get the test suite running

Make sure you're running the [test suite locally](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/blob/main/docs/User_Manual.md#34-development-dependencies-for-testing-optional). It's a good idea to do this before you start making changes to ensure that everything is working correctly.

### Coding Style Guide

PICA adheres to [PEP 8](https://www.python.org/dev/peps/pep-0008/) for Python code style. Please ensure your contributions follow these guidelines. You can use tools like `flake8` or `black` to help format your code.

### Implement your fix or feature

At this point, you're ready to make your changes! Feel free to ask for help; everyone is a beginner at first:

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

Finally, go to GitHub and [make a Pull Request](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/compare)

### Keeping your Pull Request updated

If a maintainer asks you to "rebase" your PR, they're saying that a lot of code has changed, and that you need to update your branch so it's easier to merge.

To learn more about rebasing and merging, check out this guide from Atlassian:
[https://www.atlassian.com/git/tutorials/merging-vs-rebasing](https://www.atlassian.com/git/tutorials/merging-vs-rebasing)

### Adding a New Instrument Module

One of the most important contributions you can make is to add support for new instruments. The architecture was designed to make this process straightforward by following a set of well-defined steps. Essentially, these are the same steps the original developers followed when adding new modules.

#### Understanding the Architecture
Code duplication is considered acceptable to avoid over-abstraction, which can make the project harder to maintain.

#### Start with a Template

The easiest way to start is to copy an existing module that is closest to your task or has the functionality you need.

#### 1. Create New Files

1.  Create a new directory for your instrument under the appropriate vendor (e.g., `pica/new_vendor/my_instrument/`).
2.  Paste the copied files into this new directory.
3.  Rename the files to reflect your new module (e.g., `IV_ABC_Instrument_GUI.py` and `IV_ABC_Instrument_Control.py`).

#### 2. Implement the Instrument Control Logic

Start with the basic instrument communication, then add measurement protocols, data saving, and plotting. Ensure that instrument communication (handled by `PyVISA` or `pymeasure`) uses the correct SCPI commands,  Replace the existing SCPI commands (e.g., `*IDN?`, `:SOUR:VOLT`, `:MEAS:CURR?`) with the commands specific to your instrument.. Your instrument's programming manual is the definitive source for these commands.Adjust the data parsing logic to handle the output format of your instrument.

#### 3. Modify the GUI (`..._GUI.py`)

1.  Open your new `..._GUI.py` file.
2.  Update the `Tkinter` widgets (labels, entry boxes, dropdowns) to match the parameters required for your instrument (e.g., voltage range, compliance, measurement speed).
3.  Ensure the "Start" button calls your new control logic (copy the logic from Instrument_Control file to here), passing the necessary parameters from the GUI.

#### 4. Integrate into the PICA Launcher

To make your new module accessible from the main dashboard:

1.  Open `pica/main.py`.
2.  Find the section where the instrument buttons are created (search for a `tk.Button` that launches an existing module).
3.  Add a new `tk.Button` for your module. The button's `command` should be a function that launches your new `..._GUI.py` script. You can model this on the existing launcher functions.

#### 5. Test and Document

- Run your new module standalone and from the PICA launcher to ensure it works as expected. Test edge cases. It's often helpful to have a new user try it, as they can often find bugs you might have missed.
- Consider adding a section for your new instrument in `docs/User_Manual.md` and adding a screenshot to `pica/assets/Images/screenshots/`.

## Support

For any questions, support, or assistance, please open a GitHub Issue in the main repository. This is the preferred and primary method for getting in touch with the development team and community for support.

## Code of Conduct

Everyone interacting in the PICA project's codebases, issue trackers, chat rooms, and mailing lists is expected to follow the [PICA Code of Conduct](CODE_OF_CONDUCT.md).