"""PyInstaller entry point for the sidecar binary the desktop app spawns.

Kept separate from the package so PyInstaller has a plain script to analyse.
"""

import multiprocessing
import sys

from pdf2md.service import main

if __name__ == "__main__":
    # Harmless on the single-process service, required if PyInstaller ever
    # re-executes the bundle in a child process.
    multiprocessing.freeze_support()
    sys.exit(main())
