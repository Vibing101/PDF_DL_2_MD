"""Tests for the build's Python version guard.

The guard exists because pip's failure on an unsupported Python says nothing
useful: markitdown needs magika and onnxruntime, whose wheels trail new Python
releases, so pip reports 'ResolutionImpossible' rather than naming the version.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "desktop" / "scripts" / "check_python.py"

_spec = importlib.util.spec_from_file_location("check_python", GUARD)
check_python = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_python)

problem = check_python.problem
MINIMUM = check_python.MINIMUM
NEWEST_TESTED = check_python.NEWEST_TESTED


@pytest.mark.parametrize(
    "version",
    [MINIMUM + (0,), (3, 11, 15), NEWEST_TESTED + (4,)],
)
def test_supported_versions_pass(version):
    assert problem(version) is None


def test_the_version_that_actually_failed_is_rejected():
    """Python 3.14.4 on a dev machine, where pip died with ResolutionImpossible."""
    complaint = problem((3, 14, 4))
    assert complaint is not None
    assert "3.14.4" in complaint
    assert "onnxruntime" in complaint
    # The message has to say what to do, not just what is wrong.
    assert "brew install python@3.13" in complaint


def test_too_old_is_rejected_and_says_so():
    complaint = problem((3, 8, 10))
    assert complaint is not None
    assert "too old" in complaint
    assert "3.9" in complaint


def test_the_boundaries_are_inclusive():
    assert problem((NEWEST_TESTED[0], NEWEST_TESTED[1], 99)) is None
    assert problem((NEWEST_TESTED[0], NEWEST_TESTED[1] + 1, 0)) is not None
    assert problem((MINIMUM[0], MINIMUM[1] - 1, 0)) is not None


def test_the_guard_exits_nonzero_for_a_bad_version(monkeypatch, capsys):
    monkeypatch.setattr(check_python.sys, "version_info", (3, 14, 4, "final", 0))
    assert check_python.main() == 1
    assert "3.14.4" in capsys.readouterr().err
