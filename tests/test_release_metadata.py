"""Release metadata stays in one place and in step.

The release number lives only in pica/__init__.py. pyproject.toml reads it
(dynamic version) and the launchers, CLI and docs import it. CITATION.cff and
CHANGELOG.md cannot import, so these tests check them against it.

Plain-python runnable too: python tests/test_release_metadata.py
"""
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import pica  # noqa: E402


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def test_version_is_defined_once_in_the_package():
    assert re.fullmatch(r"\d+\.\d+\.\d+", pica.__version__)


def test_pyproject_reads_the_version_from_the_package():
    text = _read("pyproject.toml")
    assert re.search(r'^dynamic\s*=\s*\["version"\]', text, re.M)
    assert not re.search(r'^version\s*=\s*"', text, re.M)
    assert 'version = {attr = "pica.__version__"}' in text


def test_pyproject_uses_pep639_license_metadata():
    text = _read("pyproject.toml")
    assert re.search(r'^license\s*=\s*"MIT"', text, re.M)
    assert re.search(r'^license-files\s*=\s*\["LICENSE"\]', text, re.M)
    # A License classifier next to a license expression is rejected by
    # setuptools >= 77, so it must stay out.
    assert "License :: " not in text


def test_citation_matches_the_package_version():
    cff = _read("CITATION.cff")
    cited = re.search(r"^version:\s*(\S+)", cff, re.M).group(1)
    assert cited == pica.__version__


def test_citation_orcids_are_full_urls():
    cff = _read("CITATION.cff")
    orcids = re.findall(r'orcid:\s*"([^"]+)"', cff)
    assert orcids
    for orcid in orcids:
        assert re.fullmatch(r"https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]",
                            orcid), orcid


def test_changelog_newest_release_matches_the_package_version():
    log = _read("CHANGELOG.md")
    assert "## [Unreleased]" in log
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", log, re.M)
    assert released and released[0] == pica.__version__


def test_launchers_and_cli_do_not_hard_code_the_version():
    for rel in (("pica", "main.py"), ("pica", "cli.py"), ("docs", "conf.py")):
        text = _read(*rel)
        assert not re.search(
            r'^\s*(PROGRAM_VERSION|VERSION|release)\s*=\s*["\']\d', text, re.M), rel


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
