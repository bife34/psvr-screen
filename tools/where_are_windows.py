"""Show where every app window is, and flag the ones sitting on the invisible VR monitor."""
import ctypes
import json
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from psvrscreen import displays, windows  # noqa: E402

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
vr = displays.find("vr")
for hwnd, title, exe in windows.list_open_windows():
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    on_vr = vr is not None and vr.left <= r.left < vr.left + vr.width
    print(f"{exe:22s} {(r.left, r.top, r.right, r.bottom)}  {'<-- ON VR MONITOR' if on_vr else ''}")
settings = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
print("panels:", [(p["source"], p["exe"]) for p in json.load(open(settings, encoding="utf-8"))["panels"]])
