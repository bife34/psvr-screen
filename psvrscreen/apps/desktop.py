"""A Windows app window in VR.

The real window moves onto the invisible VR monitor (so it never covers your main screen), is captured live
and shown on the VR window; clicks and the wheel are sent to the matching spot of the real window.

Sizing: an app opens in VR at the same size it has in Windows (same points = same layout; on the VR
monitor's 225% that is 2.25x the pixels of a 100% screen). Making the VR window bigger or smaller scales the
whole page with it, like a picture; only resizing the app itself changes what fits inside - and then the VR
window follows the app. Text size 1.5x = 1.5x fewer pixels = everything 1.5x bigger in the same VR window.
The VR monitor's high scaling makes apps draw big letters that are then smoothly scaled down: round text.
"""
import ctypes
import math

from ..core import geometry
from ..core.settings import new_panel
from ..render.gl import Texture
from ..ui import theme
from ..win import displays, inject, windows
from ..win.capture import WindowCapture
from .base import Content
from .forwarding import ForwardedInput

APP_TEXT_PX = 27                  # normal app text height at 225% scaling (9 pt), in pixels
TEXT_DEGREES = 1.4                # how tall that text looks in VR for a window opened at readable size
NEW_WINDOW_DISTANCE = 2.5         # new windows open this far away (big windows close up crowd you)
NEW_WINDOW_MAX_DEGREES = 50       # a newly opened window is at most half your view (others stay visible)
WINDOW_BOTTOM_DEGREES = 12        # new windows end at most this far below eye level (above the dock)
MIN_WINDOW_WIDTH = 480            # pixels
MAX_WINDOW_DEGREES = 70           # the VR window never grows wider than this when following the app
FOLLOW_CHECK_SECONDS = 1.0        # how often to look whether the app was resized
WM_CLOSE = 0x0010


def new_panel_for(hwnd, title, exe):
    """A VR window for an app window, at its Windows size and shape (see the module doc)."""
    w_pt, h_pt = windows.shown_size_points(hwnd)
    aspect = h_pt / w_pt
    vr_w, vr_h, scale = displays.VR_MONITOR_MODE
    pixels = w_pt * scale / 100
    # The VR monitor is 2560x1440: a bigger app is made as big as fits (its layout gets a bit more compact).
    pixels = max(MIN_WINDOW_WIDTH, min(pixels, vr_w, vr_h / aspect))
    # Readable text at its pixel size, but at most NEW_WINDOW_MAX_DEGREES of your view (also its height).
    biggest = NEW_WINDOW_MAX_DEGREES / max(1.0, aspect)
    degrees = max(15.0, min(biggest, pixels * TEXT_DEGREES / APP_TEXT_PX))
    width = geometry.width_for_degrees(degrees, NEW_WINDOW_DISTANCE)
    # Eye level, but a tall window is lifted so its bottom stays above the dock and music player.
    lowest = -NEW_WINDOW_DISTANCE * math.tan(math.radians(WINDOW_BOTTOM_DEGREES))
    height = max(0.0, lowest + width * aspect / 2)
    panel = new_panel("window", title, exe, hwnd, width=width, distance=NEW_WINDOW_DISTANCE, height=height)
    panel["aspect"] = aspect
    panel["pixels"] = pixels   # the app's own size; the VR window scales the page
    return panel


def new_game_panel(hwnd, title, exe):
    """A game's window: a big screen in front of you, the game drawn as big as the VR monitor allows (sharp)."""
    w_pt, h_pt = windows.shown_size_points(hwnd)
    aspect = h_pt / w_pt if w_pt else 9 / 16
    vr_w, vr_h, _ = displays.VR_MONITOR_MODE
    panel = new_panel("window", title, exe, hwnd, width=GAME_WIDTH, distance=GAME_DISTANCE, height=GAME_HEIGHT,
                      curvature=GAME_CURVE)
    panel["aspect"] = aspect
    panel["pixels"] = min(vr_w, vr_h / aspect)
    panel["game"] = True
    return panel


GAME_WIDTH, GAME_DISTANCE, GAME_HEIGHT, GAME_CURVE = 5.0, 3.5, 0.3, 0.05   # a cinema screen, a bit above the dock


def base_pixels(panel):
    """The window's pixel width at text size 1.0. Older windows without one: readable at their VR size."""
    if panel.get("pixels"):
        return panel["pixels"]
    return geometry.degrees_wide(panel["width"], panel["distance"]) * APP_TEXT_PX / TEXT_DEGREES


def window_pixels(panel):
    """The real window's size on the VR monitor (20 px steps, so small changes don't make it jitter)."""
    vr_w = displays.VR_MONITOR_MODE[0]
    width = max(MIN_WINDOW_WIDTH, min(vr_w, int(base_pixels(panel) / panel["text_zoom"] / 20) * 20))
    return width, int(width * panel.get("aspect", 0.625))


class DesktopWindow(Content):
    icon = theme.Icon.WINDOW

    def __init__(self, panel, host):
        super().__init__(panel, host)
        hwnd = panel.get("hwnd")
        if not (windows.is_alive(hwnd) and windows.exe_of(hwnd).lower() == (panel["exe"] or "").lower()):
            hwnd = windows.find_window(panel["exe"], panel["title"])   # saved from last time
        self.hwnd = hwnd
        self._capture = None
        self._texture = None
        self._seq = -1
        self._placed = None            # the size we gave the real window
        self._checked = 0.0
        self._input = ForwardedInput(lambda: inject.window_rect(self.hwnd), self._before_press)
        if not hwnd:
            self.closed = True
            return
        self._capture = WindowCapture(hwnd)

    @property
    def texture(self):
        return self._texture

    def _before_press(self):
        inject.raise_window(self.hwnd)   # windows may overlap on the VR monitor
        self.host.note_click()

    # -- every frame -------------------------------------------------------------------------------
    def update(self, panel, now):
        if self.closed:
            return
        if self._capture.closed or not windows.is_alive(self.hwnd):
            self.closed = True
            return
        self._keep_size(panel, now)
        seq, frame = self._capture.latest()
        if frame is not None and seq != self._seq:
            if self._texture is None:
                self._texture = Texture()
            self._texture.upload(frame, bgra=True)
            self._seq = seq

    def _keep_size(self, panel, now):
        size = window_pixels(panel)
        if size != self._placed:
            self.host.mover.to_vr_monitor(self.hwnd, size, self.host.slot_of(self.window_id))
            self._placed, self._checked = size, now
        elif now - self._checked > FOLLOW_CHECK_SECONDS and not panel.get("lock_look"):
            self._checked = now
            self._follow_app(panel, size)

    def _follow_app(self, panel, size):
        """You resized or maximized the app yourself, or it refuses our size (Explorer's minimum height,
        Steam's minimum width...): the app keeps its size and the VR window grows / shrinks by the same
        factor and takes its shape - instead of forcing the app back."""
        actual_w, actual_h = windows.actual_size(self.hwnd)
        if abs(actual_w - size[0]) <= max(24, size[0] * 0.04) and abs(actual_h - size[1]) <= max(24, size[1] * 0.04):
            return
        vr_w, vr_h, _ = displays.VR_MONITOR_MODE
        if actual_w > vr_w or actual_h > vr_h:
            # Bigger than the VR monitor (a player sizing itself to an 8K video): not followed - it gets the
            # size it had, so one app can't blow up the whole layout.
            self.host.mover.to_vr_monitor(self.hwnd, size, self.host.slot_of(self.window_id))
            return
        k = actual_w / size[0]
        width = min(panel["width"] * k, geometry.width_for_degrees(MAX_WINDOW_DEGREES, panel["distance"]))
        aspect = actual_h / max(1, actual_w)
        pixels = base_pixels(panel) * k
        if abs(width - panel["width"]) < 0.01 and abs(aspect - panel.get("aspect", 0)) < 0.01:
            return
        followed = dict(panel, width=width, aspect=aspect, pixels=pixels)
        self.host.update_panel(self.window_id, width=width, aspect=aspect, pixels=pixels)
        self._placed = window_pixels(followed)   # its own size counts as placed: no resize fight

    # -- input ---------------------------------------------------------------------------------------
    def on_pointer(self, e):
        if not self.closed:
            self._input.handle(e)

    def cancel_input(self):
        self._input.cancel()

    # -- lifetime ------------------------------------------------------------------------------------
    def request_close(self):
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)   # the app closes as usual

    def release(self):
        self.cancel_input()
        if self._capture is not None:
            self._capture.stop()
        if self._texture is not None:
            self._texture.delete()
            self._texture = None
        if self.hwnd:
            self.host.mover.back(self.hwnd)

