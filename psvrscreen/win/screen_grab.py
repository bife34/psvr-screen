"""Grabs a monitor as a BGRA numpy image, with the cursor drawn in."""
import ctypes

import mss
import numpy as np

from . import displays

# Classic arrow: X = black outline, . = white fill.
_CURSOR = [
    "X",
    "XX",
    "X.X",
    "X..X",
    "X...X",
    "X....X",
    "X.....X",
    "X......X",
    "X.......X",
    "X........X",
    "X.....XXXXX",
    "X..X..X",
    "X.X X..X",
    "XX  X..X",
    "X    X..X",
    "     X..X",
    "      XX",
]
_OUTLINE = [(y, x) for y, row in enumerate(_CURSOR) for x, c in enumerate(row) if c == "X"]
_FILL = [(y, x) for y, row in enumerate(_CURSOR) for x, c in enumerate(row) if c == "."]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos():
    p = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


MONITOR_CHOICES = [("main", "Main monitor"), ("vr", "VR monitor (its own text size)")]


def _draw_cursor(frame, x, y):
    h, w = frame.shape[:2]
    for pixels, value in ((_OUTLINE, 0), (_FILL, 255)):
        for dy, dx in pixels:
            px, py = x + dx, y + dy
            if 0 <= px < w and 0 <= py < h:
                frame[py, px, :3] = value
                frame[py, px, 3] = 255


class Capture:
    """Must be created and used on the same thread (mss requirement)."""

    def __init__(self):
        self._sct = mss.mss()
        self._cache = {}

    def close(self):
        self._sct.close()

    def _monitor(self, kind):
        """kind: "main", "vr" or "view1"... (falls back to the main monitor if that one isn't there)."""
        if kind not in self._cache:   # a new Capture is made whenever the monitor layout changes
            self._cache[kind] = self._find_monitor(kind)
        return self._cache[kind]

    def _find_monitor(self, kind):
        mons = self._sct.monitors[1:]
        wanted = displays.find(kind) or displays.find("main")
        if wanted is not None:
            for m in mons:
                if (m["left"], m["top"]) == (wanted.left, wanted.top):
                    return m
        return next((m for m in mons if (m["left"], m["top"]) == (0, 0)), mons[0])

    def grab(self, monitor):
        mon = self._monitor(monitor)
        shot = self._sct.grab(mon)
        frame = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4).copy()
        frame[:, :, 3] = 255
        cx, cy = cursor_pos()
        _draw_cursor(frame, cx - mon["left"], cy - mon["top"])
        return frame
