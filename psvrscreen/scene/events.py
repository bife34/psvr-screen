"""Pointer input as the things in the VR space receive it.

The pointer (shell/pointer.py) turns mouse / controller input into these events and sends them to the
`target` of the surface under it. A target is anything with `on_pointer(event)`.

Kinds: "move" (pointer over the surface), "leave", "press", "drag" (moving while a button is held - sent to
the pressed target even when the pointer is no longer on it), "release", "wheel".
"""
from dataclasses import dataclass


@dataclass
class PointerEvent:
    kind: str
    u: float = None              # where on the surface, 0..1 (None: pointer is off the surface)
    v: float = None
    button: str = None           # "left", "right", "middle"
    yaw: float = 0.0             # pointer direction, degrees (+ = right, + pitch = up)
    pitch: float = 0.0
    start_yaw: float = 0.0       # direction when the button was pressed (for drags)
    start_pitch: float = 0.0
    delta: int = 0               # wheel: 120 per notch, + = away from you / up
    horizontal: bool = False     # sideways wheel
    alt: bool = False
    shift: bool = False
    distance: float = 0.0        # how far the hit is from your eyes (m)

    @property
    def on_surface(self):
        return self.u is not None
