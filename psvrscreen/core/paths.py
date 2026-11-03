"""Where things are: the app's own files (read-only when installed) and your data (settings, log).

From the source folder both are the psvr-screen folder, as always. From the downloadable package (Python's own
signed program renamed "PSVR Screen.exe", see tools/build_portable.py - Windows' Smart App Control lets that
run, unlike an unsigned exe) or a PyInstaller build: the app's files are next to the program and your data is in
%LOCALAPPDATA%/PSVRScreen.
"""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)                     # a PyInstaller build
EXE_DIR = Path(sys.executable).resolve().parent
BUILD_FILE = "psvr-screen-build.txt"                       # marks the downloadable package's folder
PACKAGED = not FROZEN and (EXE_DIR / BUILD_FILE).exists()
INSTALLED = FROZEN or PACKAGED
APP_DIR = Path(sys._MEIPASS) if FROZEN else EXE_DIR if PACKAGED else Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PSVRScreen" if INSTALLED else APP_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
