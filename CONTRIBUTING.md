# Contributing to PICA

First off, thank you for considering contributing to PICA! It's people like you that make PICA such a great tool.

# Building a Collaborative Ecosystem

PICA is distributed under the permissive MIT License because we believe scientific software should be a dynamic, living project rather than a static tool. Our goal is to foster a community-driven ecosystem where researchers are free to modify and extend the codebase to support their specific instruments.

We designed PICA’s  with a modular architecture specifically to make this process straightforward preventing the redundant effort of rewriting automation scripts from scratch. By contributing your custom drivers or experimental protocols back to the project via GitHub Pull Requests, you help create a more robust and versatile tool for the entire scientific community. Additionally, we rely on community feedback via GitHub Issues to identify edge cases and resolve bugs across diverse hardware setups, accelerating development far beyond what a single team could achieve.

# Commitment to Transparency & Reproducibility

Beyond practical utility, making PICA open source is a statement of scientific integrity. Unlike proprietary "black box" software that obscures experimental methodology, PICA ensures complete transparency. By accessing the full source code, researchers can audit the exact SCPI command sequences, timing delays, and data handling procedures used in an experiment. This methodological clarity is essential for rigorous validation and reproducibility in modern experimental science.

## Where do I go from here?

If you've noticed a bug or have a feature request, [make one](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/issues/new)! It's generally best if you get confirmation of your bug or approval for your feature request this way before starting to code.

### Fork & create a branch

If this is something you think you can fix, then [fork PICA](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/fork) and create a branch with a descriptive name.

A good branch name would be (where issue #325 is the ticket you're working on):

```sh
git checkout -b 325-add-marathi-translations
```

### Get the test suite running

Make sure you're running the test suite locally. It's a good idea to do this before you start making changes to ensure that everything is working correctly.

### Implement your fix or feature

At this point, you're ready to make your changes! Feel free to ask for help; everyone is a beginner at first:

### Make a Pull Request

At this point, you should switch back to your master branch and make sure it's up to date with the latest upstream version of PICA.

```sh
git remote add upstream git@github.com:prathameshnium/PICA-Python-Instrument-Control-and-Automation.git
git checkout master
git pull upstream master
```

Then update your feature branch from your local copy of master, and push it!

```sh
git checkout 325-add-marathi-translations
git rebase master
git push --force-with-lease origin 325-add-marathi-translations
```

Finally, go to GitHub and [make a Pull Request](https://github.com/prathameshnium/PICA-Python-Instrument-Control-and-Automation/compare)

### Keeping your Pull Request updated

If a maintainer asks you to "rebase" your PR, they're saying that a lot of code has changed, and that you need to update your branch so it's easier to merge.

To learn more about rebasing and merging, check out this guide from Atlassian:
[https://www.atlassian.com/git/tutorials/merging-vs-rebasing](https://www.atlassian.com/git/tutorials/merging-vs-rebasing)

## How to get in touch

You can reach out to the maintainers of PICA by creating an issue.

## Code of Conduct

Everyone interacting in the PICA project's codebases, issue trackers, chat rooms, and mailing lists is expected to follow the [PICA Code of Conduct](CODE_OF_CONDUCT.md).