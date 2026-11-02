"""Errors go to psvr-screen.log (next to the launcher), with where they happened, for finding causes later."""
import os
import time
import traceback

from .paths import DATA_DIR

ERROR_LOG = str(DATA_DIR / "psvr-screen.log")


def log_error(where=""):
    """Call inside `except`: writes the error being handled."""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + (f"[{where}] " if where else "") + traceback.format_exc() + "\n")
    except OSError:
        pass
