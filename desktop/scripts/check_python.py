"""Refuse to build on a Python that cannot install the dependencies.

markitdown pulls in magika, and so onnxruntime, whose compiled wheels trail new
Python releases by months. On a Python with no wheel, pip backtracks through
older and older versions and eventually gives up with

    ERROR: Cannot install markitdown because these package versions have
    conflicting dependencies.
    ERROR: ResolutionImpossible

which says nothing about the real cause. Checking first turns that into one
line naming the version and what to do about it.
"""

from __future__ import annotations

import sys

#: The oldest Python the package supports; matches pyproject.toml.
MINIMUM = (3, 9)
#: The newest Python the test matrix covers and the dependencies have wheels
#: for. Raise it once CI is green on the next release.
NEWEST_TESTED = (3, 13)


def describe(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def problem(version: tuple[int, int, int]) -> str | None:
    """Why this Python cannot build, or None when it can."""
    if version[:2] < MINIMUM:
        return (
            f"Python {describe(version)} is too old to build pdf2md — "
            f"{describe(MINIMUM)} or newer is required."
        )
    if version[:2] > NEWEST_TESTED:
        return (
            f"Python {describe(version)} is newer than the dependencies support.\n"
            f"markitdown needs magika and onnxruntime, whose wheels do not cover "
            f"{describe(version[:2])} yet, so pip fails with an unreadable "
            f"'ResolutionImpossible'.\n"
            f"Build with Python {describe(MINIMUM)}–{describe(NEWEST_TESTED)}, for example:\n"
            f"    brew install python@{describe(NEWEST_TESTED)}\n"
            f'    "$(brew --prefix python@{describe(NEWEST_TESTED)})/bin/python{describe(NEWEST_TESTED)}"'
            f" -m venv .venv\n"
            f"    source .venv/bin/activate"
        )
    return None


def main() -> int:
    complaint = problem(sys.version_info[:3])
    if complaint:
        print(complaint, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
