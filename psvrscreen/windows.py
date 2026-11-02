"""App windows for the VR space: list them, move them onto the (invisible) VR monitor, and put them back.

A window shown in VR lives on the VR monitor so it never covers or receives clicks on your main screen.
Its pixel size there sets how big everything inside it looks: fewer pixels on the same VR panel = bigger.
"""
import ctypes
import math
import os
from ctypes import wintypes

from . import displays
from .settings import new_panel

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
SW_RESTORE = 9
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

BASE_WINDOW_SIZE = (1600, 1000)   # window pixels at text size 1.0 (readable sizing off)
WINDOW_ASPECT = 1.6               # width / height of windows in VR
VR_MONITOR_MODE = (2560, 1440, 225)   # the VR monitor: many pixels and Windows' biggest scaling there
APP_TEXT_PX = 27                  # normal app text height at 225% scaling (9 pt), in pixels
TEXT_DEGREES = 1.4                # how tall that text should look in VR at text size 1.0 (~15 headset pixels:
                                  # clearly readable; 1.8 made every app a wall in front of you)
# New windows open this far away: big windows close up crowd you; farther + bigger looks the same to the eye
# (same angle, same text size) but calm. Their width is scaled for the distance (see natural_layout).
NEW_WINDOW_WIDTH, NEW_WINDOW_DISTANCE = 4.3, 2.5
MIN_WINDOW_WIDTH, MAX_WINDOW_WIDTH = 480, VR_MONITOR_MODE[0]
MAX_WINDOW_DEGREES = 110          # a window never grows wider than this much of your view
NEW_WINDOW_MAX_DEGREES = 50       # a newly opened window is at most half your view (others stay visible)
WINDOW_BOTTOM_DEGREES = 12        # new windows end at most this far below eye level (the player starts ~14 deg)
IGNORED_TITLES = {"Program Manager", "PSVR Screen", "PSVR Screen - headset", "psvr-screen-gl"}


def pixels_per_degree(panel):
    """Window pixels per degree of view that give app text TEXT_DEGREES (times its text size) in VR."""
    return APP_TEXT_PX / (TEXT_DEGREES * panel["text_zoom"])


def width_for_pixels(panel, pixel_width):
    """VR width (m) at the panel's distance that shows `pixel_width` window pixels with readable text -
    for apps that refuse to be made smaller (e.g. Steam): then the VR window grows instead."""
    degrees = min(pixel_width / pixels_per_degree(panel), MAX_WINDOW_DEGREES)   # then the text gets a bit smaller
    return 2 * panel["distance"] * math.tan(math.radians(degrees) / 2)


def actual_size(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def natural_layout(hwnd, distance=NEW_WINDOW_DISTANCE):
    """An app opens in VR exactly as it is in Windows: the same size in points (so the same layout), on the
    VR monitor's 225% that is 2.25x the pixels of a 100% screen. Returns its VR width (m), shape
    (height / width) and pixel width. The VR width gives readable text, but at most NEW_WINDOW_MAX_DEGREES
    of your view (a big app then starts with smaller text - scale it up in VR, the page scales along)."""
    placement = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
    user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
    r = placement.rcNormalPosition          # its size when not maximized
    w, h = max(1, r.right - r.left), max(1, r.bottom - r.top)
    monitor = next((d for d in displays.list_displays()
                    if d.left <= (r.left + r.right) // 2 < d.left + d.width), None)
    scale = (displays.get_scale(monitor) or 100) if monitor else 100
    aspect = h / w
    vr_pixels = w * 100 / scale * VR_MONITOR_MODE[2] / 100   # same size in points, on the VR monitor
    # The VR monitor is 2560x1440: a bigger app is made as big as fits (its layout gets a bit more compact).
    vr_pixels = max(MIN_WINDOW_WIDTH, min(vr_pixels, VR_MONITOR_MODE[0], VR_MONITOR_MODE[1] / aspect))
    biggest = NEW_WINDOW_MAX_DEGREES / max(1.0, aspect)   # tall windows (calculator): limit the height too
    degrees = max(15.0, min(biggest, vr_pixels * TEXT_DEGREES / APP_TEXT_PX))
    return 2 * distance * math.tan(math.radians(degrees) / 2), aspect, vr_pixels


def window_pixels(panel, readable=True):
    """How many real pixels a window gets on the VR monitor.

    A window keeps its own pixel size ("pixels", at text size 1.0): making it bigger or smaller in VR scales
    the whole page with it, like a picture - text included. Only resizing the app itself changes what fits
    inside (see SourceManager._follow_window), and Text size 1.5x = 1.5x fewer pixels = everything 1.5x
    bigger in the same VR window. With the VR monitor at 225% the letters are drawn big and then smoothly
    scaled down: round, like the VR dock - not blocky."""
    if not readable:
        width = BASE_WINDOW_SIZE[0] / panel["text_zoom"]
    else:
        width = base_pixels(panel) / panel["text_zoom"]
    width = max(MIN_WINDOW_WIDTH, min(MAX_WINDOW_WIDTH, int(width / 20) * 20))   # 20 px steps
    return width, int(width * panel.get("aspect", 1 / WINDOW_ASPECT))   # the app's own shape


def base_pixels(panel):
    """The window's pixel width at text size 1.0. Older panels without one: what gives readable text at
    their current size in VR."""
    if panel.get("pixels"):
        return panel["pixels"]
    degrees = math.degrees(2 * math.atan(panel["width"] / 2 / max(panel["distance"], 0.1)))
    return degrees * APP_TEXT_PX / TEXT_DEGREES


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


def title_of(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def exe_of(hwnd):
    """The app's program file name. Store apps (Calculator...) run inside a Windows host window
    (ApplicationFrameHost.exe): then the real app is the process behind its child window."""
    exe = _process_exe(hwnd)
    if exe.lower() == "applicationframehost.exe":
        host_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(host_pid))
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def child(h, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value != host_pid.value:
                found.append(_process_exe(h))
                return False
            return True

        user32.EnumChildWindows(hwnd, child, 0)
        if found and found[0]:
            return found[0]
    return exe


def _process_exe(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def is_alive(hwnd):
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def _is_app_window(hwnd):
    if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER: skip dialogs/popups
        return False
    if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    cloaked = ctypes.c_int()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    if cloaked.value:   # hidden UWP / other-desktop windows
        return False
    title = title_of(hwnd)
    return bool(title) and title not in IGNORED_TITLES


def list_open_windows():
    """[(hwnd, title, exe)] of normal app windows, excluding our own."""
    own_pid = os.getpid()
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != own_pid and _is_app_window(hwnd):
            result.append((hwnd, title_of(hwnd), exe_of(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    return result


def new_window_panel(hwnd, title, exe):
    """A VR panel for an app window, at its natural size and shape (see natural_layout)."""
    width, aspect, pixels = natural_layout(hwnd)
    # Eye level, but a tall window is lifted so its bottom stays above the dock and music player.
    lowest = -NEW_WINDOW_DISTANCE * math.tan(math.radians(WINDOW_BOTTOM_DEGREES))
    height = max(0.0, lowest + width * aspect / 2)
    panel = new_panel("window", title, exe, hwnd, width=width, distance=NEW_WINDOW_DISTANCE, height=height)
    panel["aspect"] = aspect
    panel["pixels"] = pixels   # the app's own size from Windows; the VR window scales the page
    return panel


def find_window(exe, title):
    """Find a saved panel's window again (after a restart): same app, same title if possible."""
    candidates = [w for w in list_open_windows() if w[2].lower() == (exe or "").lower()]
    for hwnd, t, _ in candidates:
        if t == title:
            return hwnd
    return candidates[0][0] if len(candidates) == 1 else None


class WindowMover:
    """Moves windows onto the VR monitor and remembers where they were, to put them back."""

    def __init__(self):
        self._original = {}   # hwnd -> WINDOWPLACEMENT

    def to_vr_monitor(self, hwnd, size, slot=0):
        """size: (width, height) in real pixels, see window_pixels()."""
        vr = displays.find("vr")
        if vr is None or not is_alive(hwnd):
            return False
        if hwnd not in self._original:
            placement = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
            user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
            self._original[hwnd] = placement
        user32.ShowWindow(hwnd, SW_RESTORE)
        w, h = min(vr.width, size[0]), min(vr.height, size[1])
        # Stagger windows a little; they may overlap on the VR monitor, the capture still sees each one fully.
        x = vr.left + min(slot * 40, max(0, vr.width - w))
        y = vr.top + min(slot * 40, max(0, vr.height - h))
        # Arriving on the 225% VR monitor, Windows makes the app rescale itself (2020 px -> 4545 px): so move
        # it there first, then give it its size - otherwise it ends up 2.25x too big with tiny text.
        user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
        user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        return True

    def back(self, hwnd):
        placement = self._original.pop(hwnd, None)
        if placement is None or not is_alive(hwnd):
            return
        vr, main = displays.find("vr"), displays.find("main")
        r = placement.rcNormalPosition
        if vr and main and vr.left <= r.left < vr.left + vr.width:
            # It was already on the VR monitor (e.g. left there by a crash): bring it to the main monitor.
            w, h = r.right - r.left, r.bottom - r.top
            r.left, r.top = main.left + 80, main.top + 80
            r.right, r.bottom = r.left + min(w, main.width - 160), r.top + min(h, main.height - 160)
        # Same DPI trap as on the way in: arriving on a 100% monitor the app shrinks itself 2.25x. Move it
        # there first, then restore its size.
        user32.SetWindowPos(hwnd, None, r.left, r.top, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
        user32.SetWindowPlacement(hwnd, ctypes.byref(placement))

    def back_all(self):
        for hwnd in list(self._original):
            self.back(hwnd)
