"""Pointer input inside a VR window, sent on to the real thing on the Windows desktop (an app window or a
monitor): press / drag / release / wheel at the matching spot. Between actions the real cursor waits at the
park point, off your main screen."""
from ..win import inject


class ForwardedInput:
    def __init__(self, rect_of, before_press=None):
        """rect_of() -> (left, top, right, bottom) on the desktop, or None; before_press() runs before a
        press is sent (e.g. bring the window to the front)."""
        self._rect_of = rect_of
        self._before_press = before_press
        self._held = set()

    def _spot(self, e):
        rect = self._rect_of() if e.on_surface else None
        return inject.spot(rect, e.u, e.v) if rect else None

    def handle(self, e):
        if e.kind == "press" and e.button in inject.BUTTONS:
            spot = self._spot(e)
            if spot:
                if self._before_press:
                    self._before_press()
                inject.send((*spot, inject.BUTTONS[e.button][0], 0))
                self._held.add(e.button)
        elif e.kind == "drag" and self._held:
            spot = self._spot(e)   # dragging inside (e.g. selecting text): the cursor follows the pointer
            if spot:
                inject.send((*spot, 0, 0))
        elif e.kind == "release" and e.button in self._held:
            self._held.discard(e.button)
            events = [(*(self._spot(e) or inject.park_point()), inject.BUTTONS[e.button][1], 0)]
            if not self._held:
                events.append((*inject.park_point(), 0, 0))
            inject.send(*events)
        elif e.kind == "wheel":
            spot = self._spot(e)
            if spot:
                events = [(*spot, inject.HWHEEL if e.horizontal else inject.WHEEL, e.delta)]
                if not self._held:
                    events.append((*inject.park_point(), 0, 0))
                inject.send(*events)

    def cancel(self):
        """Let go of anything still pressed (the mouse left VR mid-click, the window closed...)."""
        for button in list(self._held):
            self._held.discard(button)
            inject.send((*inject.park_point(), inject.BUTTONS[button][1], 0))
