"""3D pointer for the VR space.

You switch the mouse into VR yourself (Ctrl+Alt+P, Options on the DS4, or the panel). While it is in VR,
the real mouse no longer moves the Windows cursor: a low-level mouse hook
takes its movement, clicks and wheel, and turns them into a pointer ray in the VR room. The ray is tested
against the panels; a click or scroll is sent to the matching spot of the window under the pointer (on the
invisible VR monitor) as one input sequence "move there + click", so nothing lands on your main screen.
Between actions the real cursor waits in the middle of the VR monitor, so its movement is never stopped by
a screen edge.

Middle button drag (or Alt + left drag) on a panel moves it; Alt + wheel changes its distance.
The keyboard shortcuts (mouse VR / PC, zoom lens, recenter) are registered here too; they are changeable
in the settings (Shortcuts tab).
"""
import ctypes
import math
import queue
import threading
import time
from ctypes import wintypes

import numpy as np

from .win import displays, shortcuts
from .core.settings import LIMITS
from .vrui import hotspot_at as vrui_hotspot_at

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

WH_MOUSE_LL = 14
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0204, 0x0205, 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL, WM_HOTKEY, WM_QUIT = 0x020A, 0x020E, 0x0312, 0x0012
WM_RELOAD_SHORTCUTS = 0x8001   # WM_APP + 1
WM_UPDATE_HOOK = 0x8002        # WM_APP + 2: install / remove the mouse hook
LLMHF_INJECTED = 0x01
VK_MENU = 0x12
HOTKEY_ID = 0x5053
DWMWA_EXTENDED_FRAME_BOUNDS = 9
HWND_TOP = 0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
BUTTON_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

DEGREES_PER_COUNT = 0.06        # pointer turn per mouse count (times the mouse speed setting)
NO_HIT_DISTANCE = 2.0
SLIDER_FULL_RANGE_DEGREES = 25.0  # pointer movement that takes a settings slider from one end to the other
CURVE_PER_DEGREE = 0.01         # wheel-button drag: curve change per degree of up/down pointer movement
SNAP_DEGREES = 4.0              # magnets: edges closer than this snap together
SNAP_GAP_DEGREES = 0.4          # small gap between joined windows
VK_SHIFT = 0x10


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
kernel32.GetModuleHandleW.restype = wintypes.HMODULE   # default int would cut the 64-bit handle in half


def _frame_rect(hwnd):
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _send(*events):
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


class Pointer:
    def __init__(self, store):
        self.store = store
        self._thread_id = None
        self._enabled = False          # mouse in VR? Ctrl+Alt+P / DS4 Options / panel switch it
        self._running = False          # set by the engine while the VR picture is running
        self._hook = None              # the mouse hook, only installed while the mouse is in VR
        self.hook_ok = None            # did Windows accept the mouse hook (last time we asked)?
        self.last_click = 0.0          # time of the last click into VR (new windows after it go into VR)
        self.yaw = 0.0                 # pointer direction in the room (radians; + = left, like head yaw)
        self.pitch = 0.0
        self.hit = None                # (panel snapshot, u, v, world point) or None
        self._panels = []              # set by the renderer every frame
        self._held = set()             # buttons currently pressed into a window
        self._drag = None              # panel being moved
        self._slider = None            # (card id, slider payload, pointer yaw, value) being dragged
        self._curve = None             # (panel id, pointer pitch, curve) while the wheel button bends a window
        self._snapped_to = None        # window the dragged one snapped to (magnets)
        self._group_start = []         # [(panel id, yaw, height)] of windows joined to the dragged one
        self._grab_distance = NO_HIT_DISTANCE
        self._physical_buttons = set() # mouse buttons really held right now (seen by the hook)
        self._raised = None
        self._thread = None
        self._events = queue.Queue()   # mouse hook -> worker
        self.ui_handler = None         # called with (action, payload) for clicks on the VR interface
        self.shortcut_handler = None   # called with the action of a keyboard shortcut (except mouse_vr)
        self.shortcut_problems = []    # shortcuts that could not be registered

    # -- state --------------------------------------------------------------------------------
    # The mouse hook is only installed while the mouse is in VR: a hook sits in the path of every mouse
    # movement on the PC, and with it gone the normal mouse is completely untouched by this app.
    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = value
        self._update_hook()

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, value):
        if value != self._running:
            self._running = value
            self._update_hook()

    def _update_hook(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_UPDATE_HOOK, 0, 0)

    @property
    def active(self):
        return self._enabled and self._running and self._hook is not None

    def toggle(self):
        self.enabled = not self.enabled
        self._physical_buttons.clear()   # presses made while the mouse was on the PC screen don't count
        if not self.enabled:
            self.cancel()

    def cancel(self):
        """Stop any drag / slider / held click (e.g. the mouse goes back to the PC screen mid-drag)."""
        self._drag = None
        self._slider = None
        self._curve = None
        for name in list(self._held):
            self._release(name)

    def set_panels(self, snapshot):
        """snapshot: list of dicts with id, index, source, hwnd, model (4x4), width/height (picture size, m),
        curvature, yaw, distance, elevation (the panel's height setting)."""
        self._panels = snapshot

    def refresh(self):
        if not self._drag:
            self.hit = self._hit_test()

    def recenter(self, yaw=0.0, pitch=0.0):
        self.yaw, self.pitch = yaw, pitch
        self.hit = self._hit_test()

    def direction(self):
        cp = math.cos(self.pitch)
        return np.array([-math.sin(self.yaw) * cp, math.sin(self.pitch), -math.cos(self.yaw) * cp])

    def end_point(self):
        """Where to draw the pointer dot (world)."""
        if self._drag:
            return self.direction() * self._grab_distance   # moves with the window you are dragging
        hit = self.hit
        return hit[3] if hit else self.direction() * NO_HIT_DISTANCE

    @staticmethod
    def park_point():
        vr = displays.find("vr") or displays.find("main")
        return vr.left + vr.width // 2, vr.top + vr.height // 2

    # -- hit testing ----------------------------------------------------------------------------
    def _hit_test(self):
        d = self.direction()
        best = None
        for p in self._panels:
            inv = np.linalg.inv(p["model"])
            o = inv[:3, 3]                       # ray origin (eyes at 0,0,0) in panel space
            dl = inv[:3, :3] @ d
            hit = _intersect(o, dl, p["width"], p["height"], p["curvature"])
            if hit and (best is None or hit[0] < best[0]):
                best = (hit[0], p, hit[1], hit[2])
        if best is None:
            return None
        t, p, u, v = best
        return p, u, v, d * t

    def _spot(self):
        """Screen pixel under the pointer, or None."""
        if not self.hit:
            return None
        p, u, v, _ = self.hit
        if p["source"] == "window":
            if not p.get("hwnd"):
                return None
            l, t, r, b = _frame_rect(p["hwnd"])
            if self._raised != p["hwnd"]:
                # Windows can overlap on the VR monitor: put this one on top (without taking focus).
                user32.SetWindowPos(p["hwnd"], HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                self._raised = p["hwnd"]
        else:
            mon = displays.find(p["source"])
            if mon is None:
                return None
            l, t, r, b = mon.rect
        return int(l + u * (r - l - 1)), int(t + v * (b - t - 1))

    # -- input (mouse hook, gamepad) ------------------------------------------------------------
    def move(self, dx, dy):
        speed = DEGREES_PER_COUNT * self.store.get().mouse_speed
        self.yaw -= math.radians(dx * speed)
        self.pitch = max(-1.4, min(1.4, self.pitch - math.radians(dy * speed)))
        if (self._slider or self._drag or self._curve) and not self._physical_buttons:
            # The release got lost somewhere: no button is really held, so the drag is over.
            self._slider = self._drag = self._curve = None
        if self._slider:
            self._drag_slider()
            self.hit = self._hit_test()
            return
        if self._drag:
            self._drag_panel()
            return
        if self._curve:
            self._bend()
            return
        self.hit = self._hit_test()
        if self._held:
            spot = self._spot()   # dragging inside a window (e.g. selecting text): keep the cursor on it
            if spot:
                _send((*spot, 0, 0))

    def hotspot(self):
        """(action, payload) of the VR interface button under the pointer, or None."""
        hit = self.hit
        if not hit or not hit[0].get("ui"):
            return None
        p, u, v, _ = hit
        return vrui_hotspot_at(p["hotspots"], u * p["img_w"], v * p["img_h"])

    def _ui_button(self, name, down):
        """A click on the VR interface (window bars, settings, dock) instead of on a window."""
        if not down or name != "left":
            return
        spot = self.hotspot()
        if spot is None:
            return
        p = self.hit[0]
        if p.get("index") is not None:
            self.store.update(selected=p["index"])
        if spot[0] == "slider":
            pid, key = spot[1][0], spot[1][1]
            s = self.store.get()
            if pid is None:   # a setting for everything (dock's Settings card)
                self._slider = (p["id"], spot[1], self.yaw, getattr(s, key))
            else:
                panel = next((x for x in s.panels if x["id"] == pid), None)
                if panel is not None:
                    self._slider = (p["id"], spot[1], self.yaw, panel.get(key, 0.0))
        elif spot[0] == "drag":
            d = p["drag"]
            if not d.get("locked"):
                self._start_drag(p["panel_id"], d)
        elif self.ui_handler:
            self.ui_handler(*spot)

    def active_slider(self):
        """(panel id, field) of the settings slider being dragged, or None."""
        return (self._slider[1][0], self._slider[1][1]) if self._slider else None

    def _start_drag(self, panel_id, d):
        """Grab a window to move it. The dot stays on the spot you grabbed (same distance along the ray).
        Windows joined to it (magnets) move along; holding Shift detaches it from them instead."""
        self._drag = (panel_id, self.yaw, self.pitch, d["yaw"], d["elevation"], d["distance"])
        self._grab_distance = float(np.linalg.norm(self.hit[3])) if self.hit else NO_HIT_DISTANCE
        self._snapped_to = None
        panels = self.store.get().panels
        me = next((p for p in panels if p["id"] == panel_id), None)
        group = me.get("group") if me else None
        if group and user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
            self.store.update_panel(self._index_of(panel_id), group=None)
            group = None
        self._group_start = [(p["id"], p["yaw"], p["height"]) for p in panels
                             if group and p.get("group") == group and p["id"] != panel_id]

    def _finish_drag(self):
        """Released after snapping to another window: join them (magnets)."""
        panel_id = self._drag[0]
        self._drag = None
        if not self._snapped_to:
            return
        panels = self.store.get().panels
        other = next((p for p in panels if p["id"] == self._snapped_to), None)
        if other is None:
            return
        group = other.get("group") or ("g" + other["id"])
        for pid in (panel_id, other["id"]):
            index = self._index_of(pid)
            if index is not None:
                self.store.update_panel(index, group=group)

    def _snap(self, panel_id, yaw, elevation, distance):
        """Magnets: if the dragged window's edge comes near another window's edge, put them edge to edge
        (left, right, above, below) at the same distance. Returns (yaw, elevation, distance, snapped id)."""
        me = next((p for p in self._panels if p["id"] == panel_id and not p.get("ui")), None)
        if me is None:
            return yaw, elevation, distance, None
        members = {pid for pid, _, _ in self._group_start}   # its own group never pulls on it

        def extents(width, height, dist):
            return (math.degrees(math.atan(width / 2 / dist)), math.degrees(math.atan(height / 2 / dist)))

        for q in self._panels:
            if q.get("ui") or q["id"] == panel_id or q["id"] in members:
                continue
            d = q["distance"]
            hw_p, hh_p = extents(me["width"], me["height"], d)
            hw_q, hh_q = extents(q["width"], q["height"], d)
            pitch_p = math.degrees(math.atan(elevation / d))
            pitch_q = math.degrees(math.atan(q["elevation"] / d))
            side_by_side = abs(pitch_p - pitch_q) < hh_p + hh_q
            stacked = abs(yaw - q["yaw"]) < hw_p + hw_q
            new_yaw, new_pitch = None, pitch_p
            if side_by_side and abs((yaw - hw_p) - (q["yaw"] + hw_q)) < SNAP_DEGREES:
                new_yaw = q["yaw"] + hw_q + hw_p + SNAP_GAP_DEGREES          # right of it
            elif side_by_side and abs((yaw + hw_p) - (q["yaw"] - hw_q)) < SNAP_DEGREES:
                new_yaw = q["yaw"] - hw_q - hw_p - SNAP_GAP_DEGREES          # left of it
            elif stacked and abs((pitch_p - hh_p) - (pitch_q + hh_q)) < SNAP_DEGREES:
                new_pitch, new_yaw = pitch_q + hh_q + hh_p + SNAP_GAP_DEGREES, q["yaw"]   # above it
            elif stacked and abs((pitch_p + hh_p) - (pitch_q - hh_q)) < SNAP_DEGREES:
                new_pitch, new_yaw = pitch_q - hh_q - hh_p - SNAP_GAP_DEGREES, q["yaw"]   # below it
            if new_yaw is not None:
                if new_pitch == pitch_p and abs(pitch_p - pitch_q) < SNAP_DEGREES:
                    new_pitch = pitch_q   # side by side and nearly level: line them up
                return new_yaw, d * math.tan(math.radians(new_pitch)), d, q["id"]
        return yaw, elevation, distance, None

    def _drag_slider(self):
        """Move the dragged slider by how far the pointer moved sideways since grabbing it (relative, so it
        never jumps): SLIDER_FULL_RANGE_DEGREES of pointer movement covers the slider's whole range."""
        _, (pid, key, _, _), yaw0, value0 = self._slider
        lo, hi = LIMITS[key]
        frac = math.degrees(yaw0 - self.yaw) / SLIDER_FULL_RANGE_DEGREES
        value = max(lo, min(hi, value0 + frac * (hi - lo)))
        if self.ui_handler:
            self.ui_handler("set", (pid, key, value))

    def button(self, name, down, alt=False):
        hit = self.hit
        (self._physical_buttons.add if down else self._physical_buttons.discard)(name)   # also for the DS4
        if not down and self._slider:               # end of dragging a settings slider
            self._slider = None
            return
        if not down and self._drag:                 # end of moving a window
            self._finish_drag()
            return
        if not down and self._curve and name == "middle":
            self._curve = None
            return
        if not down and name in self._held:         # release a click that went into a window
            self._release(name)
            return
        if hit and hit[0].get("ui"):                # window bars, settings card, dock
            if down and name == "middle" and hit[0].get("panel_id"):
                self._start_bend(hit[0]["panel_id"])
            else:
                self._ui_button(name, down)
            return
        if down and self.ui_handler:
            # A click into a window or into empty space closes open settings cards.
            self.ui_handler("outside_click", hit[0]["id"] if hit else None)
        if down and name == "middle":               # wheel button held + up / down = bend the window
            if hit:
                self.store.update(selected=hit[0]["index"])
                self._start_bend(hit[0]["id"])
            return
        if down and name == "left" and alt:         # Alt + drag anywhere on a window moves it
            if hit:
                p = hit[0]
                self.store.update(selected=p["index"])
                if not p.get("locked"):
                    self._start_drag(p["id"], {"yaw": p["yaw"], "elevation": p["elevation"],
                                               "distance": p["distance"]})
            return
        if down:
            if not hit:
                return
            self.last_click = time.monotonic()
            self.store.update(selected=hit[0]["index"])
            spot = self._spot()
            if spot:
                _send((*spot, BUTTON_FLAGS[name][0], 0))
                self._held.add(name)

    def _release(self, name):
        self._held.discard(name)
        spot = (self._spot() if self.hit and not self.hit[0].get("ui") else None) or self.park_point()
        events = [(*spot, BUTTON_FLAGS[name][1], 0)]
        if not self._held:
            events.append((*self.park_point(), 0, 0))
        _send(*events)

    def wheel(self, delta, horizontal=False, alt=False):
        if self._drag and not horizontal:
            # While dragging a window, the wheel pulls it closer / pushes it away.
            panel_id, yaw0, pitch0, panel_yaw, panel_height, distance = self._drag
            distance = max(LIMITS["distance"][0], min(LIMITS["distance"][1],
                                                      distance + (-0.1 if delta > 0 else 0.1)))
            index = self._index_of(panel_id)
            if index is not None:
                self.store.update_panel(index, distance=distance)
                if not any(p["id"] == panel_id and p.get("lock_position") for p in self.store.get().panels):
                    self._grab_distance = max(0.3, self._grab_distance + (-0.1 if delta > 0 else 0.1))
                    self._drag = (panel_id, yaw0, pitch0, panel_yaw, panel_height, distance)
            return
        if not self.hit or self.hit[0].get("ui"):
            return
        if alt:
            self.store.update(selected=self.hit[0]["index"])
            self.store.nudge("distance", -0.1 if delta > 0 else 0.1)
            return
        spot = self._spot()
        if spot:
            events = [(*spot, MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, delta)]
            if not self._held:
                events.append((*self.park_point(), 0, 0))
            _send(*events)

    def _index_of(self, panel_id):
        return next((i for i, p in enumerate(self.store.get().panels) if p["id"] == panel_id), None)

    def _start_bend(self, panel_id):
        index = self._index_of(panel_id)
        if index is not None:
            self._curve = (panel_id, self.pitch, self.store.get().panels[index]["curvature"])

    def _bend(self):
        """Wheel button held: moving up bends the window more, down flattens it."""
        panel_id, pitch0, curve0 = self._curve
        index = self._index_of(panel_id)
        if index is None:
            self._curve = None
            return
        self.store.update_panel(index, curvature=curve0 + math.degrees(self.pitch - pitch0) * CURVE_PER_DEGREE)

    def _drag_panel(self):
        """Move exactly the window that was grabbed (never just "whatever is selected")."""
        panel_id, yaw0, pitch0, panel_yaw, panel_height, distance = self._drag
        index = self._index_of(panel_id)
        if index is None:
            self._drag = None
            return
        new_yaw = max(LIMITS["yaw"][0], min(LIMITS["yaw"][1], panel_yaw - math.degrees(self.yaw - yaw0)))
        new_height = max(LIMITS["height"][0], min(LIMITS["height"][1],
                                                  panel_height + distance * (math.tan(self.pitch) - math.tan(pitch0))))
        new_yaw, new_height, new_distance, self._snapped_to = self._snap(panel_id, new_yaw, new_height, distance)
        self.store.update_panel(index, yaw=new_yaw, height=new_height, distance=new_distance)
        # Joined windows move along by the same amount
        for pid, yaw_start, height_start in self._group_start:
            i = self._index_of(pid)
            if i is not None:
                self.store.update_panel(i, yaw=yaw_start + (new_yaw - panel_yaw),
                                        height=height_start + (new_height - panel_height))

    def park(self):
        if not self._held:
            _send((*self.park_point(), 0, 0))

    # -- mouse hook thread ----------------------------------------------------------------------
    # The hook must return at once: Windows waits for it on every mouse event, and anything we send from
    # inside it (clicks) would pass through the hook again while it is still busy - Windows then freezes the
    # mouse until a timeout and finally drops the hook. So the hook only queues; a worker does the rest.
    def start(self):
        self._thread = threading.Thread(target=self._hook_loop, daemon=True, name="mouse-hook")
        self._thread.start()
        threading.Thread(target=self._worker, daemon=True, name="pointer-worker").start()

    def stop(self):
        self._events.put(None)
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _worker(self):
        while True:
            event = self._events.get()
            if event is None:
                return
            if event[0] == "move":
                dx, dy = event[1], event[2]
                # Merge the moves that piled up meanwhile, keep everything else in order.
                pending = []
                while not self._events.empty():
                    nxt = self._events.get_nowait()
                    if nxt is None:
                        return
                    if nxt[0] == "move" and not pending:
                        dx, dy = dx + nxt[1], dy + nxt[2]
                    else:
                        pending.append(nxt)
                try:
                    self.move(dx, dy)
                except Exception:
                    pass
                for e in pending:
                    self._handle(e)
            else:
                self._handle(event)

    def _handle(self, event):
        try:
            if event[0] == "move":
                self.move(event[1], event[2])
            elif event[0] == "button":
                self.button(event[1], event[2], alt=event[3])
            elif event[0] == "wheel":
                self.wheel(event[1], horizontal=event[2], alt=event[3])
        except Exception:
            pass

    def _hook_loop(self):
        self._thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def proc(n_code, w_param, l_param):
            if n_code == 0 and self.active:
                info = MSLLHOOKSTRUCT.from_address(l_param)
                if not info.flags & LLMHF_INJECTED:
                    try:
                        self._on_mouse(w_param, info)
                    except Exception:
                        pass
                    return 1   # swallow: while the pointer is active the real cursor is ours
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def set_hook():
            want = self._enabled and self._running
            if want and self._hook is None:
                hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, kernel32.GetModuleHandleW(None), 0)
                self.hook_ok = bool(hook)
                self._hook = hook or None
            elif not want and self._hook is not None:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

        registered = self._register_shortcuts({})
        set_hook()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in registered:
                    self._on_shortcut(registered[msg.wParam])
                elif msg.message == WM_RELOAD_SHORTCUTS:
                    registered = self._register_shortcuts(registered)
                elif msg.message == WM_UPDATE_HOOK:
                    set_hook()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            if self._hook is not None:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

    def _register_shortcuts(self, old):
        """(Re)register the keyboard shortcuts from the settings. Returns {hotkey id: action}."""
        for hotkey_id in old:
            user32.UnregisterHotKey(None, hotkey_id)
        registered, self.shortcut_problems = {}, []
        for i, (action, keys) in enumerate(self.store.get().shortcuts.items()):
            parsed = shortcuts.parse(keys)
            if parsed and user32.RegisterHotKey(None, HOTKEY_ID + i, *parsed):
                registered[HOTKEY_ID + i] = action
            else:
                self.shortcut_problems.append(keys)   # invalid, or already used by another program
        return registered

    def reload_shortcuts(self):
        """Call after the shortcuts were changed in the settings."""
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_RELOAD_SHORTCUTS, 0, 0)

    def _on_shortcut(self, action):
        if action == "mouse_vr":
            self.toggle()
        elif self.shortcut_handler:
            self.shortcut_handler(action)

    def _on_mouse(self, kind, info):
        """Runs inside the hook: only queue, never do real work here."""
        if kind == WM_MOUSEMOVE:
            cur = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(cur))
            dx, dy = info.pt.x - cur.x, info.pt.y - cur.y
            if dx or dy:
                self._events.put(("move", dx, dy))
            return
        alt = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        buttons = {WM_LBUTTONDOWN: ("left", True), WM_LBUTTONUP: ("left", False),
                   WM_RBUTTONDOWN: ("right", True), WM_RBUTTONUP: ("right", False),
                   WM_MBUTTONDOWN: ("middle", True), WM_MBUTTONUP: ("middle", False)}
        if kind in buttons:
            name, down = buttons[kind]
            (self._physical_buttons.add if down else self._physical_buttons.discard)(name)
            self._events.put(("button", name, down, alt))
        elif kind in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            delta = ctypes.c_short(info.mouseData >> 16).value
            self._events.put(("wheel", delta, kind == WM_MOUSEHWHEEL, alt))


def _intersect(o, d, width, height, curvature):
    """Ray (panel space) vs panel. Returns (distance, u, v) or None. v=0 is the top of the picture."""
    if curvature > 0.001:
        # The panel lies on a vertical cylinder of radius r around (0, *, r): x = r sin(th), z = r (1 - cos(th))
        r = width / (2 * math.pi * curvature)
        ox, oz = o[0], o[2] - r
        a = d[0] ** 2 + d[2] ** 2
        b = 2 * (ox * d[0] + oz * d[2])
        c = ox ** 2 + oz ** 2 - r ** 2
        disc = b * b - 4 * a * c
        if a < 1e-9 or disc < 0:
            return None
        for t in sorted(((-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a))):
            if t <= 0:
                continue
            x, y, z = o + d * t
            theta = math.atan2(x, r - z)
            u = theta * r / width + 0.5
            v = 0.5 - y / height
            if 0 <= u <= 1 and 0 <= v <= 1 and z < r:   # the near half of the cylinder
                return t, u, v
        return None
    if abs(d[2]) < 1e-9:
        return None
    t = -o[2] / d[2]
    if t <= 0:
        return None
    x, y, _ = o + d * t
    u, v = x / width + 0.5, 0.5 - y / height
    return (t, u, v) if 0 <= u <= 1 and 0 <= v <= 1 else None
