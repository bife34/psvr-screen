"""A View: one piece of VR interface (the dock, a dialog, a window's title bar, a native app's page).

Subclasses say what to show: `state()` returns everything the picture depends on, `build()` returns the
widget tree for it. When the state, the hovered or the pressed widget changes, the view is rebuilt and
redrawn into its texture - otherwise nothing is drawn at all. Small views draw on the render thread; a view
whose drawing takes long (a native app's page full of pictures: ~130 ms) draws in the background while the
headset keeps showing its last picture, so the VR never stops for it (that froze head movement and everything).

The view receives pointer events (it is a surface's `target`) and passes them to its widgets.
"""
import copy
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

from ..core import geometry
from ..render.gl import Texture
from . import theme


SLOW_DRAW_SECONDS = 0.012   # a view that took longer than this to draw draws in the background from then on


def meters_per_px(distance):
    """Image pixel size in meters for a view at `distance`, so 1 degree of view has PX_PER_DEGREE pixels."""
    return geometry.meters_per_degree(distance) / theme.PX_PER_DEGREE


class _Context:
    """What widgets may know while drawing / handling input."""

    def __init__(self):
        self.hover = None        # key of the widget under the pointer
        self.press = None        # key of the widget being pressed
        self.drag_start = None   # value a slider had when it was grabbed
        self.size = (1, 1)       # the view's size in pixels (widgets turn pointer u, v into pixels with it)
        self.canvas = None       # the image being drawn (pictures are pasted onto it)

    def hovered(self, key):
        return key is not None and key == self.hover

    def pressed(self, key):
        return key is not None and key == self.press


class View:
    def __init__(self, distance=None, m_per_px=None):
        self.m_per_px = m_per_px if m_per_px is not None else meters_per_px(distance or 1.0)
        self.ctx = _Context()
        self.texture = None
        self.root = None
        self.size_px = (1, 1)
        self._drawn_key = None
        self._v_slow = False          # its last drawing took long: draw in the background
        self._v_job = None            # (key) being drawn in the background
        self._v_done = None           # (key, root, size, image) ready to show

    # -- for subclasses ---------------------------------------------------------------------------
    def state(self):
        """Hashable: everything the picture shows (the view is redrawn when it changes)."""
        return None

    def build(self):
        """The widget tree to show (its root's measured size is the view's size)."""
        raise NotImplementedError

    # -- size in the room -------------------------------------------------------------------------
    @property
    def width(self):
        return self.size_px[0] * self.m_per_px

    @property
    def height(self):
        return self.size_px[1] * self.m_per_px

    # -- drawing ------------------------------------------------------------------------------------
    def invalidate(self):
        self._drawn_key = None

    def refresh(self):
        """Redraw if needed. Render thread only (uploads the texture)."""
        self._v_released = False
        if self._v_done is not None:                 # a background drawing finished: show it
            key, root, size, img = self._v_done
            self._v_done = None
            self._put_drawing(key, root, size, img)
        key = (self.state(), self.ctx.hover, self.ctx.press)   # (scaling it needs no redraw)
        if key == self._drawn_key and self.texture is not None:
            return
        if self._v_slow and self.texture is not None:
            if self._v_job is None:
                self._v_job = key
                ctx = copy.copy(self.ctx)
                threading.Thread(target=self._draw_behind, args=(key, ctx), daemon=True, name="view-draw").start()
            return
        t = time.perf_counter()
        root, size, img = self._view_draw(self.ctx)
        self._v_slow = time.perf_counter() - t > SLOW_DRAW_SECONDS
        self._put_drawing(key, root, size, img)

    def _view_draw(self, ctx):
        root = self.build()
        w, h = (max(1, int(v)) for v in root.measure())
        root.layout(0, 0, w, h)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ctx.size = (w, h)
        ctx.canvas = img
        try:
            root.draw(ImageDraw.Draw(img), ctx)
        finally:
            ctx.canvas = None
        return root, (w, h), img

    def _draw_behind(self, key, ctx):
        try:
            t = time.perf_counter()
            root, size, img = self._view_draw(ctx)
            self._v_slow = time.perf_counter() - t > SLOW_DRAW_SECONDS
            pixels = np.asarray(img, np.uint8)     # (the conversion for the GPU too: ~12 ms for a big page)
            if not getattr(self, "_v_released", False):
                self._v_done = (key, root, size, pixels)
        except Exception:
            from ..core.log import log_error
            log_error(f"draw {type(self).__name__}")
            self._drawn_key = key                  # (don't retry the same picture every frame)
        finally:
            self._v_job = None

    def _put_drawing(self, key, root, size, img):
        self._drawn_key = key
        self.root = root
        self.size_px = self.ctx.size = size
        if self.texture is None:
            self.texture = Texture()
        self.texture.upload(img)

    def release(self):
        self._v_done = None                          # (a background drawing finishing later is dropped too)
        self._v_released = True
        if self.texture is not None:
            self.texture.delete()
            self.texture = None

    # -- input --------------------------------------------------------------------------------------
    def widget_at(self, u, v):
        if self.root is None or u is None:
            return None
        x, y = u * self.size_px[0], v * self.size_px[1]
        found = None
        for w in self.root.walk():
            if w.interactive and w.contains(x, y):
                found = w   # the deepest / last one wins (buttons on top of a drag area)
        return found

    def _by_key(self, key):
        if self.root is None or key is None:
            return None
        return next((w for w in self.root.walk() if w.key == key), None)

    def on_pointer(self, e):
        if e.kind in ("move", "leave"):
            w = self.widget_at(e.u, e.v) if e.kind == "move" else None
            self.ctx.hover = w.key if w else None
        elif e.kind == "press":
            w = self.widget_at(e.u, e.v)
            self.ctx.press = w.key if w else None
            self.ctx.drag_start = None
            if w:
                w.press(self.ctx, e)
        elif e.kind == "drag":
            w = self._by_key(self.ctx.press)
            if w:
                w.drag(self.ctx, e)
        elif e.kind == "release":
            w = self._by_key(self.ctx.press)
            under = self.widget_at(e.u, e.v)
            self.ctx.press = None
            if w:
                w.release(self.ctx, e, inside=under is not None and under.key == w.key)
            self.ctx.drag_start = None
        elif e.kind == "wheel":
            w = self._by_key(self.ctx.press) or self.widget_at(e.u, e.v)
            if w:
                w.wheel(self.ctx, e)
