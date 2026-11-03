"""Windows programs that work in VR - added one by one, each once it's been made to work well (the rest stay on
the PC screen unless Settings > Controls > "Windows programs in VR" is on).

Each one: how to find it, how to start it (or bring back its window when it's hidden in the tray), and which of
its windows is the main one. Its windows may come into VR whatever that setting says.

    Spotify      the desktop app (spotify.com or the Microsoft Store). Its playback also shows in the dock's
                 music player (Windows' media controls), so it can be steered without its window.
"""
import os
from pathlib import Path

from ..ui.theme import Icon
from . import windows

APPS = {   # key -> what it is
    "spotify": {
        "name": "Spotify",
        "subtitle": "Music",
        "icon": Icon.MUSIC,
        "color": (30, 215, 96, 255),
        "exe": "spotify.exe",
        "files": [Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe"],
        "store": "SpotifyAB.SpotifyMusic",        # (the Microsoft Store version's package)
        "uri": "spotify:",                        # starts it, or shows its window again from the tray
    },
}


def installed(key):
    """Is it on this PC?"""
    app = APPS[key]
    if any(p.exists() for p in app["files"]):
        return True
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    try:
        return any(p.name.startswith(app["store"]) for p in packages.iterdir())
    except OSError:
        return False


def owns(exe):
    """The integrated app this program file belongs to (its key), or None."""
    exe = (exe or "").lower()
    return next((key for key, app in APPS.items() if app["exe"] == exe), None)


def main_window(key):
    """(hwnd, title, exe) of the app's open main window, or None (not running, or hidden in the tray)."""
    exe = APPS[key]["exe"]
    found = [w for w in windows.list_open_windows() if w[2].lower() == exe]
    return max(found, key=lambda w: _area(w[0])) if found else None


def _area(hwnd):
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.right - r.left) * (r.bottom - r.top)


def launch(key):
    """Start it, or show its window again (it was in the tray). Its window then appears - see Shell."""
    app = APPS[key]
    try:
        os.startfile(app["uri"])
        return
    except OSError:
        pass
    for p in app["files"]:
        if p.exists():
            os.startfile(str(p))
            return
    raise OSError(f"{app['name']} isn't installed")
