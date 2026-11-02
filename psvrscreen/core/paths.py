"""Where things are: the app's own files (read-only when installed) and your data (settings, log).

From the source folder both are the psvr-screen folder, as always. Installed (PSVR Screen.exe, built with
PyInstaller) the app's files are next to the program and your data is in %LOCALAPPDATA%/PSVRScreen.
"""
import os
import sys
from pathlib import Path

INSTALLED = getattr(sys, "frozen", False)
APP_DIR = Path(sys._MEIPASS) if INSTALLED else Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PSVRScreen" if INSTALLED else APP_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
