"""Gamepad (any Xbox-style pad, the DS4 through DS4Windows, or the DS4 read directly - ds4_motion.py - when
there is no Xbox-style pad) as mouse + VR screen controls.

Controls (DS4 names / Xbox names):
  Left stick           move the mouse
  Cross (A) or R2      left click (hold to drag)
  Circle (B) or L2     right click
  Right stick          scroll
  D-pad up / down      screen bigger / smaller
  D-pad left / right   screen closer / farther
  L1 + D-pad up/down   screen higher / lower
  L1 + D-pad l/r       tilt the screen
  Triangle (Y)         recenter the screen in front of you
  Options              mouse / pad between VR and the PC screen
  Share + Options      turn gamepad mouse on/off (so games can use the pad)
"""
import ctypes
import threading
import time
from ctypes import wintypes

from ..win import displays

DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
START, BACK, LB = 0x0010, 0x0020, 0x0100
BTN_A, BTN_B, BTN_X, BTN_Y = 0x1000, 0x2000, 0x4000, 0x8000

MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000

STICK_DEADZONE = 7000
TRIGGER_THRESHOLD = 60
REPEAT_DELAY, REPEAT_RATE = 0.35, 0.08


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XINPUT_GAMEPAD)]


def _load_xinput():
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return getattr(ctypes.windll, name)
        except OSError:
            continue
    return None


def _mouse(flags, dx=0, dy=0, data=0):
    ctypes.windll.user32.mouse_event(flags, dx, dy, ctypes.c_ulong(data & 0xFFFFFFFF), 0)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _keep_on_main_monitor(area):
    """The headset is a monitor too; a click on its window breaks the VR image, so never go there."""
    user32 = ctypes.windll.user32
    left, top, right, bottom = area
    p = _POINT()
    user32.GetCursorPos(ctypes.byref(p))
    x, y = min(max(p.x, left), right - 1), min(max(p.y, top), bottom - 1)
    if (x, y) != (p.x, p.y):
        user32.SetCursorPos(x, y)


def _stick(value):
    """-1..1 with deadzone and a curve for fine control near the center."""
    if abs(value) < STICK_DEADZONE:
        return 0.0
    sign = 1 if value > 0 else -1
    v = (abs(value) - STICK_DEADZONE) / (32767 - STICK_DEADZONE)
    return sign * min(1.0, v) ** 2


class GamepadMouse(threading.Thread):
    def __init__(self, store, engine):
        super().__init__(daemon=True, name="gamepad")
        self.store = store
        self.engine = engine
        self.status = "No controller found"
        self._xinput = _load_xinput()
        self._stop_event = threading.Event()
        self.motion = None        # device.ds4_motion.DS4Motion: the DS4 read directly (set by __main__)

    def stop(self):
        self._stop_event.set()

    def run(self):
        if self._xinput is None:
            self.status = "XInput not available"
            return
        prev = 0
        left_down = right_down = False
        repeat_at = {}
        frac_x = frac_y = scroll_acc = hscroll_acc = 0.0
        last = time.perf_counter()
        state = _XINPUT_STATE()
        mouse_area, area_checked = displays.mouse_area("shown", "main"), last

        while not self._stop_event.wait(0.008):
            now = time.perf_counter()
            dt, last = now - last, now
            pad_index = next((i for i in range(4) if self._xinput.XInputGetState(i, ctypes.byref(state)) == 0), None)
            if pad_index is None:
                direct = self.motion.pad_state() if self.motion is not None else None
                if direct is None:
                    self.status = "No controller found (connect the DS4 or an Xbox controller)"
                    prev = 0
                    continue
                g = state.Gamepad                         # the DS4 read directly, in the same layout
                (g.wButtons, g.bLeftTrigger, g.bRightTrigger, g.sThumbLX, g.sThumbLY, g.sThumbRX,
                 g.sThumbRY) = direct
                pad_index = 0
            if getattr(self.engine, "handover", False):     # SteamVR has the headset: the pad is the game's
                self.status = f"Controller {pad_index + 1}: in use by SteamVR"
                prev = 0
                continue
            g = state.Gamepad
            buttons = g.wButtons
            pressed = buttons & ~prev
            s = self.store.get()

            pointer = getattr(self.engine, "pointer", None)
            shell = getattr(self.engine, "shell", None)
            in_game = shell is not None and shell.game_in_front()
            both = (buttons & (BACK | START)) == (BACK | START) and pressed & (BACK | START)
            if in_game:
                # A game is being played: the pad is the game's. Share + Options: back to VR.
                if both and pointer is not None:
                    pointer.enabled = True
                self.status = f"Controller {pad_index + 1}: playing the game (Share + Options: back to VR)"
            elif both:
                self.store.update(gamepad_mouse=not s.gamepad_mouse)
                s = self.store.get()
            elif pressed & START and pointer is not None:
                pointer.toggle()   # Options: mouse/pad between VR and the main screen
            if not in_game:
                self.status = f"Controller {pad_index + 1} connected - mouse {'ON' if s.gamepad_mouse else 'OFF'}"

            if not s.gamepad_mouse or in_game:
                if left_down:
                    _mouse(MOUSEEVENTF_LEFTUP)
                    left_down = False
                if right_down:
                    _mouse(MOUSEEVENTF_RIGHTUP)
                    right_down = False
                prev = buttons
                continue

            # While the mouse is in VR the pad drives the VR pointer instead of the Windows cursor.
            vr = pointer is not None and pointer.active

            # Mouse movement
            speed = 1400.0 * s.mouse_speed * dt
            frac_x += _stick(g.sThumbLX) * speed
            frac_y -= _stick(g.sThumbLY) * speed
            mx, my = int(frac_x), int(frac_y)
            if mx or my:
                if vr:
                    pointer.move(mx, my)
                else:
                    _mouse(MOUSEEVENTF_MOVE, mx, my)
                    if now >= area_checked + 2.0:
                        mouse_area, area_checked = displays.mouse_area("shown", "main"), now
                    _keep_on_main_monitor(mouse_area)
                frac_x -= mx
                frac_y -= my

            # Scrolling (120 = one wheel notch)
            scroll_acc += _stick(g.sThumbRY) * 1200.0 * dt
            hscroll_acc += _stick(g.sThumbRX) * 1200.0 * dt
            if abs(scroll_acc) >= 40:
                pointer.wheel(int(scroll_acc)) if vr else _mouse(MOUSEEVENTF_WHEEL, data=int(scroll_acc))
                scroll_acc = 0.0
            if abs(hscroll_acc) >= 40:
                if vr:
                    pointer.wheel(int(hscroll_acc), horizontal=True)
                else:
                    _mouse(MOUSEEVENTF_HWHEEL, data=int(hscroll_acc))
                hscroll_acc = 0.0

            # Clicks
            want_left = bool(buttons & BTN_A) or g.bRightTrigger > TRIGGER_THRESHOLD
            want_right = bool(buttons & BTN_B) or g.bLeftTrigger > TRIGGER_THRESHOLD
            if want_left != left_down:
                if vr:
                    pointer.button("left", want_left)
                else:
                    _mouse(MOUSEEVENTF_LEFTDOWN if want_left else MOUSEEVENTF_LEFTUP)
                left_down = want_left
            if want_right != right_down:
                if vr:
                    pointer.button("right", want_right)
                else:
                    _mouse(MOUSEEVENTF_RIGHTDOWN if want_right else MOUSEEVENTF_RIGHTUP)
                right_down = want_right

            # Screen controls
            if pressed & BTN_Y:
                self.engine.recenter()
            l1 = bool(buttons & LB)
            actions = {
                DPAD_UP: ("height", 0.05) if l1 else ("width", 0.1),
                DPAD_DOWN: ("height", -0.05) if l1 else ("width", -0.1),
                DPAD_LEFT: ("tilt", -3.0) if l1 else ("distance", -0.1),
                DPAD_RIGHT: ("tilt", 3.0) if l1 else ("distance", 0.1),
            }
            for bit, (name, delta) in actions.items():
                if pressed & bit:
                    self.store.nudge(name, delta)
                    repeat_at[bit] = now + REPEAT_DELAY
                elif buttons & bit and now >= repeat_at.get(bit, now + 1):
                    self.store.nudge(name, delta)
                    repeat_at[bit] = now + REPEAT_RATE
            prev = buttons

        if left_down:
            _mouse(MOUSEEVENTF_LEFTUP)
        if right_down:
            _mouse(MOUSEEVENTF_RIGHTUP)
