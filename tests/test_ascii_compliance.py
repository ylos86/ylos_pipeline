# -*- coding: utf-8 -*-
# tests/test_ascii_compliance.py
# Every .py file in the repo (excluding _vendor and generated dirs)
# must be decodable as strict ASCII. This test auto-enforces the rule
# so it cannot silently regress.

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

REPO_ROOT    = Path(__file__).parent.parent
EXCLUDE_DIRS = {"_vendor", "__pycache__", "dist", ".git"}


def _py_files():
    files = []
    for f in sorted(REPO_ROOT.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in f.parts):
            continue
        files.append(f)
    return files


@pytest.mark.parametrize(
    "py_file",
    _py_files(),
    ids=lambda f: str(f.relative_to(REPO_ROOT)),
)
def test_ascii_compliance(py_file: Path):
    """
    File must be decodable as strict ASCII.
    Non-ASCII chars in comments/strings are forbidden (Windows compat rule).
    Replace: section sign S- (arch doc S-N), special Unicode -> ASCII equivalent.
    """
    try:
        py_file.read_text(encoding="ascii")
    except UnicodeDecodeError as e:
        pytest.fail(
            f"{py_file.relative_to(REPO_ROOT)}: "
            f"non-ASCII byte at position {e.start} -- {e}"
        )
