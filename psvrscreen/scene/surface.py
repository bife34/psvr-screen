"""Surfaces: everything you see in the VR space.

A surface is a rectangle (or disc) with a size in meters, placed in the room by a 4x4 matrix, maybe curved
around a vertical cylinder, showing a texture or a plain color. The renderer draws surfaces and the pointer
hits them; whoever owns a surface (an app, the dock...) sets `target` to receive the pointer's input there.
"""
from dataclasses import dataclass, field

import numpy as np

from ..core import geometry

# Drawing order. WORLD: windows and things in the room (hidden behind nearer things). INTERFACE: bars, dock,
# dialogs - drawn after the world, still behind nearer surfaces. OVERLAY: always visible (the pointer dot).
WORLD, INTERFACE, OVERLAY = 0, 1, 2


@dataclass(eq=False)
class Surface:
    id: str
    model: np.ndarray                 # world transform of its center
    width: float
    height: float
    curvature: float = 0.0
    texture: object = None            # render.gl.Texture; None = plain `color`
    color: tuple = (0.12, 0.14, 0.18, 1.0)
    opacity: float = 1.0
    layer: int = WORLD
    shape: str = "rect"               # "rect", "disc" or "sphere" (a VR video around you, render/video_sphere.py)
    uv: tuple = (0.0, 0.0, 1.0, 1.0)  # part of the texture shown (u0, v0, u1, v1) - the zoom lens uses it
    target: object = None             # gets pointer events (see shell.pointer); None = can't be pointed at
    data: dict = field(default_factory=dict)   # owner's notes (e.g. which window it belongs to)

    def hit(self, origin, direction):
        """Ray in world space -> (distance, u, v) or None. u, v: 0..1 across the picture, v=0 at the top."""
        if self.shape != "rect" or self.target is None:
            return None
        inv = np.linalg.inv(self.model)
        o = inv[:3, :3] @ origin + inv[:3, 3]
        d = inv[:3, :3] @ direction
        return geometry.intersect_surface(o, d, self.width, self.height, self.curvature)

    def point(self, u, v, lift=0.0):
        """World position of picture coordinates u, v; `lift` moves it toward you (m)."""
        p, _ = geometry.surface_point(self.width, self.height, self.curvature, u, v)
        return (self.model @ np.array([p[0], p[1], p[2] + lift, 1.0]))[:3]

    def frame_at(self, u, v, lift=0.0):
        """Matrix of a small flat thing lying on the surface at u, v, facing you (follows the curve)."""
        p, theta = geometry.surface_point(self.width, self.height, self.curvature, u, v)
        return self.model @ geometry.translate(p[0], p[1], p[2] + lift) @ geometry.rotate_y(-np.degrees(theta))
