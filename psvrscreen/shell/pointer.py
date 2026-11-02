"""The VR pointer: your mouse (or the DS4) as a ray in the room.

You switch the mouse into VR yourself (Ctrl+Alt+P, Options on the DS4, the control panel or the dock).
While it is in VR, the mouse hook takes the real mouse and the pointer turns its movement into a direction;
the surface the ray hits gets the input as scene.events.PointerEvent - a press "captures" the pointer:
until release, that surface's target gets every drag / wheel event, even when the ray left it.

Input arrives from other threads (mouse hook, controller) and is only queued; `process()` handles it on the
render thread, once per frame.
"""
import ctypes
import math
import queue

import numpy as np

from ..core import geometry
from ..scene.events import PointerEvent
from ..win import inject
from ..win.mouse_hook import MouseHook

DEGREES_PER_COUNT = 0.06   # pointer turn per mouse count (times the mouse speed setting)
NO_HIT_DISTANCE = 2.0
VK_SHIFT, VK_MENU = 0x10, 0x12


def _key_down(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class Pointer:
    def __init__(self, store, on_shortcut):
        self.store = store
        self._events = queue.Queue()
        self._hook = MouseHook(self._events.put, self._shortcut, lambda: self.store.get().shortcuts)
        self._on_shortcut = on_shortcut
        self._enabled = False         # mouse in VR (switched by you)
        self._running = False         # the VR picture is running
        self._pad_buttons = set()     # buttons held on the controller
        self.yaw = 0.0                # radians, + = left (like head yaw)
        self.pitch = 0.0
        self.hit = None               # (surface, distance, u, v) under the ray, or None
        self._hover = None            # surface whose target got the last "move"
        self._captured = None         # (surface, button, press event) while a button is held
        self.on_press = None          # shell callback(surface or None): a press happened (closes dialogs...)

    # -- switching the mouse into VR (any thread) ------------------------------------------------
    def start(self):
        self._hook.start()

    def stop(self):
        self._hook.stop()

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)
        self._hook.want(self._enabled and self._running)
        if not self._enabled:
            self._events.put(("cancel",))

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, value):
        if value != self._running:
            self._running = value
            self._hook.want(self._enabled and self._running)

    @property
    def active(self):
        """The mouse is in VR right now (switched on, VR running and Windows accepted the hook)."""
        return self._enabled and self._running and self._hook.installed

    def toggle(self):
        self.enabled = not self.enabled

    @property
    def hook_ok(self):
        return self._hook.ok

    @property
    def shortcut_problems(self):
        return self._hook.shortcut_problems

    def reload_shortcuts(self):
        self._hook.reload_shortcuts()

    def _shortcut(self, action):
        if action == "mouse_vr":
            self.toggle()
        else:
            self._events.put(("shortcut", action))

    # -- input from the controller (its own thread) -------------------------------------------------
    def move(self, dx, dy):
        self._events.put(("move", dx, dy))

    def button(self, name, down):
        (self._pad_buttons.add if down else self._pad_buttons.discard)(name)
        self._events.put(("button", name, down, False))

    def wheel(self, delta, horizontal=False):
        self._events.put(("wheel", delta, horizontal, False))

    # -- render thread ------------------------------------------------------------------------------
    def recenter(self, yaw=0.0, pitch=0.0):
        self.yaw, self.pitch = yaw, pitch

    def direction(self):
        return geometry.ray_direction(self.yaw, self.pitch)

    def process(self, surfaces):
        """Handle the queued input against this frame's surfaces."""
        self._surfaces = [s for s in surfaces if s.target is not None]
        if not self.active:
            while not self._events.empty():
                event = self._events.get_nowait()
                if event[0] == "shortcut":
                    self._on_shortcut(event[1])
            self._cancel()
            self._set_hover(None)
            return
        moved = False
        while not self._events.empty():
            event = self._events.get_nowait()
            kind = event[0]
            if kind == "move":
                speed = DEGREES_PER_COUNT * self.store.get().mouse_speed
                self.yaw -= math.radians(event[1] * speed)
                self.pitch = max(-1.4, min(1.4, self.pitch - math.radians(event[2] * speed)))
                moved = True
            else:
                if moved:
                    self._moved()
                    moved = False
                if kind == "button":
                    self._button(event[1], event[2], event[3])
                elif kind == "wheel":
                    self._wheel(event[1], event[2], event[3])
                elif kind == "shortcut":
                    self._on_shortcut(event[1])
                elif kind == "cancel":
                    self._cancel()
        self._moved()   # also when nothing moved: the surfaces may have moved under the pointer

    def _hit_test(self):
        o, d = np.zeros(3), self.direction()
        best = None
        for s in self._surfaces:
            hit = s.hit(o, d)
            if hit and (best is None or hit[0] < best[1]):
                best = (s, *hit)
        return best

    @staticmethod
    def _same(a, b):
        """Surfaces are made anew every frame: the same thing keeps its id."""
        return a is not None and b is not None and a.id == b.id

    def _event(self, kind, surface, **kw):
        hit = self.hit if self.hit and self._same(self.hit[0], surface) else None
        press = self._captured[2] if self._captured else None
        yaw, pitch = -math.degrees(self.yaw), math.degrees(self.pitch)
        return PointerEvent(kind, u=hit[2] if hit else None, v=hit[3] if hit else None, yaw=yaw, pitch=pitch,
                            start_yaw=press.yaw if press else yaw, start_pitch=press.pitch if press else pitch,
                            distance=hit[1] if hit else 0.0, shift=_key_down(VK_SHIFT), **kw)

    def _set_hover(self, surface):
        if self._hover is not None and not self._same(self._hover, surface):
            self._hover.target.on_pointer(self._event("leave", self._hover))
        self._hover = surface

    def _moved(self):
        held = self._hook.physical_buttons | self._pad_buttons
        if self._captured and self._captured[1] not in held:
            self._button(self._captured[1], False, False)   # its release got lost somewhere: end it now
        self.hit = self._hit_test()
        surface = self.hit[0] if self.hit else None
        if self._captured:
            current = next((x for x in self._surfaces if x.id == self._captured[0].id), None)
            if current is not None:   # this frame's version of the captured surface
                self._captured = (current, *self._captured[1:])
            captured = self._captured[0]
            captured.target.on_pointer(self._event("drag", captured, button=self._captured[1]))
            return
        self._set_hover(surface)
        if surface is not None:
            surface.target.on_pointer(self._event("move", surface))

    def _button(self, name, down, alt):
        if down:
            if self._captured:
                return   # one button at a time
            surface = self.hit[0] if self.hit else None
            if self.on_press:
                self.on_press(surface)
            if surface is None:
                return
            e = self._event("press", surface, button=name, alt=alt or _key_down(VK_MENU))
            self._captured = (surface, name, e)
            surface.target.on_pointer(e)
        elif self._captured and self._captured[1] == name:
            surface = self._captured[0]
            e = self._event("release", surface, button=name)
            self._captured = None
            surface.target.on_pointer(e)

    def _wheel(self, delta, horizontal, alt):
        surface = self._captured[0] if self._captured else (self.hit[0] if self.hit else None)
        if surface is not None:
            surface.target.on_pointer(self._event("wheel", surface, delta=delta, horizontal=horizontal,
                                                  alt=alt or _key_down(VK_MENU)))

    def _cancel(self):
        """Mouse left VR mid-drag: end it as a release where the pointer is."""
        if self._captured:
            self._button(self._captured[1], False, False)

    def park(self):
        inject.park()

    def dot_distance(self):
        """How far out to draw the pointer dot: on what it hits, on the thing being dragged, or 2 m out."""
        if self._captured:
            distance = getattr(self._captured[0].target, "grab_distance", None)
            if distance:
                return distance
        return self.hit[1] if self.hit else NO_HIT_DISTANCE
