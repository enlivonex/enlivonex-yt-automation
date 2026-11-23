"""Packaged desktop entry point."""

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    # Keep settings and generated user files alongside the installed app, not
    # in PyInstaller's temporary extraction directory or an arbitrary cwd.
    os.chdir(Path(sys.executable).resolve().parent)

from app.pyside_gui import main


if __name__ == "__main__":
    main()
