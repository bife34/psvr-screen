"""Sending mouse input to a spot on the Windows desktop (e.g. an app window on the invisible VR monitor).

Each event moves the cursor there and acts in the same input sequence, so nothing lands anywhere else.
"""
import ctypes
from ctypes import wintypes

from . import displays

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
WHEEL, HWHEEL = 0x0800, 0x1000
BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}   # (down, up)
DWMWA_EXTENDED_FRAME_BOUNDS = 9
HWND_TOP = 0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send(*events):
    """events: (x, y, flags, data) - each one moves the cursor to x, y (screen pixels) and does `flags`."""
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    inputs = (INPUT * len(events))()
    for i, (x, y, flags, data) in enumerate(events):
        inputs[i].type = 0   # INPUT_MOUSE
        inputs[i].mi.dx = int((x - vx) * 65535 / max(1, vw - 1))
        inputs[i].mi.dy = int((y - vy) * 65535 / max(1, vh - 1))
        inputs[i].mi.mouseData = data & 0xFFFFFFFF
        inputs[i].mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | flags
    user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))


def park_point():
    """Where the real cursor waits while the mouse is in VR: the middle of the VR monitor (no screen edge
    stops its movement there, and it is not on your main screen)."""
    vr = displays.find("vr") or displays.find("main")
    return vr.left + vr.width // 2, vr.top + vr.height // 2


def park():
    send((*park_point(), 0, 0))


def window_rect(hwnd):
    """The visible frame of a window (without the invisible resize borders): left, top, right, bottom."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def raise_window(hwnd):
    """Put a window on top of the others (they may overlap on the VR monitor) without taking focus."""
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def spot(rect, u, v):
    l, t, r, b = rect
    return int(l + u * (r - l - 1)), int(t + v * (b - t - 1))
