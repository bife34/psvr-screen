"""The apps that can be shown in VR windows.

A window's saved `source` says which app shows it: "window" (a Windows app), "main" / "view1" / "view2"
(a whole monitor), or "app:<name>" for a native VR app registered in NATIVE_APPS.
To add a native app: write apps/<name>.py with a Content subclass and register it below.
"""
from .desktop import DesktopWindow
from .monitor import MonitorView

NATIVE_APPS = {}   # name -> (title, icon, Content class); filled as native apps are added


def create(panel, host):
    """The Content for a saved window, or None if its kind is unknown."""
    source = panel["source"]
    if source == "window":
        return DesktopWindow(panel, host)
    if source.startswith("app:"):
        entry = NATIVE_APPS.get(source[4:])
        return entry[2](panel, host) if entry else None
    return MonitorView(panel, host)
