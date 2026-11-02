"""Windows app windows: list them, identify them, move them onto the (invisible) VR monitor and back.

A window shown in VR lives on the VR monitor so it never covers or receives clicks on your main screen.
How big it is there (and in VR) is decided by apps/desktop.py.
"""
import ctypes
import os
from ctypes import wintypes

from . import displays

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
SW_RESTORE = 9
SW_HIDE = 0
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

IGNORED_TITLES = {"Program Manager", "PSVR Screen", "PSVR Screen - headset", "PSVR Screen - simulator"}


def actual_size(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def normal_size_points(hwnd):
    """The window's size when not maximized, in points (pixels at 100% scaling) - its layout's size,
    the same on any monitor."""
    placement = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
    user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
    r = placement.rcNormalPosition
    w, h = max(1, r.right - r.left), max(1, r.bottom - r.top)
    monitor = next((d for d in displays.list_displays() if d.left <= (r.left + r.right) // 2 < d.left + d.width),
                   None)
    scale = (displays.get_scale(monitor) or 100) if monitor else 100
    return w * 100 / scale, h * 100 / scale


def shown_size_points(hwnd):
    """The size you see the window at in Windows, in points: a maximized window counts at its maximized size
    (its normal size is the smaller one it gets back when un-maximized)."""
    if not user32.IsZoomed(hwnd):
        return normal_size_points(hwnd)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = max(1, r.right - r.left), max(1, r.bottom - r.top)
    monitor = next((d for d in displays.list_displays() if d.left <= (r.left + r.right) // 2 < d.left + d.width),
                   None)
    scale = (displays.get_scale(monitor) or 100) if monitor else 100
    return w * 100 / scale, h * 100 / scale


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


def foreground():
    """The window that gets the keyboard (in front)."""
    return user32.GetForegroundWindow()


def bring_to_front(hwnd):
    user32.SetForegroundWindow(hwnd)


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def move_to_monitor(hwnd, kind):
    """Put a window on a monitor ("view2"...) filling it (maximized)."""
    monitor = displays.find(kind)
    if monitor is None or not is_alive(hwnd):
        return
    user32.ShowWindow(hwnd, SW_RESTORE)
    # Move first, then grow: the app rescales for the monitor's text size on the way (see WindowMover)
    user32.SetWindowPos(hwnd, None, monitor.left + 40, monitor.top + 40, 0, 0,
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


SW_MAXIMIZE = 3


def exe_path(hwnd):
    """Full path of the program a window belongs to ("" if unknown)."""
    return _process_exe(hwnd, full=True)


def _process_exe(hwnd, full=False):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value if full else os.path.basename(buf.value)
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
        if not user32.IsWindowVisible(hwnd):
            placement.showCmd = SW_HIDE   # an app hidden in the tray (Steam) stays hidden, now on the main screen
        user32.SetWindowPlacement(hwnd, ctypes.byref(placement))

    def back_all(self, kinds=("vr",)):
        """Every window we moved goes back; then any other window left on these invisible monitors (`kinds`) comes
        to the main monitor too - windows that opened there by themselves (Windows opens a new window where the
        app's last one was) were never ours to put back, but nobody can see them there."""
        for hwnd in list(self._original):
            self.back(hwnd)
        rescue_windows(kinds)


def _monitor_of(rect):
    x = (rect.left + rect.right) // 2
    return next((d for d in displays.list_displays() if d.left <= x < d.left + d.width), None)


def windows_on(kinds):
    """App windows (hwnd) whose middle is on one of these monitors ("vr", "view1"...)."""
    found = []
    for hwnd, _, _ in list_open_windows():
        if user32.IsIconic(hwnd):
            continue
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        monitor = _monitor_of(r)
        if monitor is not None and monitor.kind in kinds:
            found.append(hwnd)
    return found


def rescue_windows(kinds=("vr",)):
    """Bring app windows sitting on these (invisible) monitors to the main monitor, same size in points."""
    main = displays.find("main")
    if main is None:
        return
    for i, hwnd in enumerate(windows_on(kinds)):
        to_main(hwnd, i)


def to_main(hwnd, step=0):
    """Put a window on the main monitor at its size in points (`step` staggers several)."""
    main = displays.find("main")
    if main is None or not is_alive(hwnd):
        return
    w, h = normal_size_points(hwnd)
    scale = (displays.get_scale(main) or 100) / 100
    w, h = min(int(w * scale), main.width - 160), min(int(h * scale), main.height - 160)
    x, y = main.left + 80 + step * 40, main.top + 80 + step * 40
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Move first, then size (the DPI trap, see WindowMover.to_vr_monitor).
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
    user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
