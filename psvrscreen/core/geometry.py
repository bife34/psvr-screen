"""3D math for the VR space: 4x4 matrices, placing things around you, curved surfaces and ray hits.

Conventions: meters; you sit at the origin looking down -z, +y is up, +x is right. Angles around you
("yaw") are in degrees, + = to your right. Matrices are numpy float32, column vectors (M @ p).
"""
import math

import numpy as np


def identity():
    return np.identity(4, np.float32)


def rigid(rot3, pos):
    m = identity()
    m[:3, :3] = rot3
    m[:3, 3] = pos
    return m


def translate(x, y, z):
    return rigid(np.identity(3), (x, y, z))


def scale(k):
    m = identity()
    m[0, 0] = m[1, 1] = m[2, 2] = k
    return m


def rotate_x(degrees):
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return rigid(np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), (0, 0, 0))


def rotate_y(degrees):
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return rigid(np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]), (0, 0, 0))


def rotate_z(degrees):
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return rigid(np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]), (0, 0, 0))


def tilt(degrees):
    """Lean the top of a surface back (+) or toward you (-) around its horizontal middle."""
    return rotate_x(-degrees)


def perspective(tan_x, tan_y, near=0.05, far=20000.0):
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = 1.0 / tan_x
    m[1, 1] = 1.0 / tan_y
    m[2, 2] = -(far + near) / (far - near)
    m[2, 3] = -2 * far * near / (far - near)
    m[3, 2] = -1.0
    return m


def placement(yaw=0.0, height=0.0, distance=1.0, turn=0.0, tilt_deg=0.0, roll=0.0):
    """Where a surface sits: around you (yaw), at its height and distance, then turned, tilted and rolled
    around its own middle."""
    return (rotate_y(-yaw) @ translate(0, height, -distance) @ rotate_y(turn) @ tilt(tilt_deg)
            @ rotate_z(-roll))


# -- angles -----------------------------------------------------------------------------------------
def degrees_wide(width, distance):
    """How much of your view (degrees) a surface `width` meters wide covers at `distance`."""
    return math.degrees(2 * math.atan(width / 2 / max(distance, 0.01)))


def width_for_degrees(degrees, distance):
    return 2 * distance * math.tan(math.radians(degrees) / 2)


def meters_per_degree(distance):
    return distance * math.tan(math.radians(1.0))


# -- curved surfaces --------------------------------------------------------------------------------
# A surface is a rectangle `width` x `height` centered at its origin, facing +z (you). With curvature > 0 it
# bends around a vertical cylinder: the edges come toward you. curvature = part of a full circle it covers.
def bend_depth(width, curvature):
    """How far a curved surface's edges come toward you from its middle (m): the depth of its box."""
    radius = curve_radius(width, curvature)
    return 0.0 if radius is None else radius * (1 - math.cos(width / (2 * radius)))


def window_model(p):
    """Where a window sits. Its distance is to the middle of its box (halfway between its middle and its curved
    edges), and it scales around that point: changing its size makes the whole window - curve included - bigger
    or smaller in place, its box staying at the same depth."""
    return (placement(p["yaw"], p["height"], p["distance"], p.get("turn", 0.0), p["tilt"], p.get("roll", 0.0))
            @ translate(0, 0, -bend_depth(p["width"], p["curvature"]) / 2))


def curve_radius(width, curvature):
    return width / (2 * math.pi * curvature) if curvature > 0.001 else None


def surface_point(width, height, curvature, u, v):
    """Point on the surface (in its own space) at picture coordinates u, v (0..1, v=0 at the top)."""
    radius = curve_radius(width, curvature)
    y = (0.5 - v) * height
    if radius is None:
        return np.array([(u - 0.5) * width, y, 0.0]), 0.0
    theta = (u - 0.5) * width / radius
    return np.array([radius * math.sin(theta), y, radius * (1 - math.cos(theta))]), theta


def surface_vertices(width, height, curvature, columns=64):
    """Triangle strip as indexed triangles: (n, 5) float32 x, y, z, u, v and uint32 indices."""
    columns = columns if curve_radius(width, curvature) else 1
    verts = []
    for i in range(columns + 1):
        u = i / columns
        top, _ = surface_point(width, height, curvature, u, 0.0)
        bottom, _ = surface_point(width, height, curvature, u, 1.0)
        verts.append((*top, u, 0.0))
        verts.append((*bottom, u, 1.0))
    idx = []
    for i in range(columns):
        a = i * 2
        idx += [a, a + 1, a + 2, a + 1, a + 3, a + 2]
    return np.array(verts, np.float32), np.array(idx, np.uint32)


def disc_vertices(segments=20):
    """Unit disc facing +z, for the pointer dot."""
    verts = [(0.0, 0.0, 0.0, 0.5, 0.5)]
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        verts.append((math.cos(a), math.sin(a), 0.0, 0.0, 0.0))
    idx = []
    for i in range(1, segments + 1):
        idx += [0, i, i + 1]
    return np.array(verts, np.float32), np.array(idx, np.uint32)


def intersect_surface(origin, direction, width, height, curvature):
    """Ray (in the surface's own space) against the surface. Returns (distance, u, v) or None."""
    radius = curve_radius(width, curvature)
    if radius is not None:
        # The cylinder: x = r sin(th), z = r (1 - cos(th)), i.e. centered at (0, *, r)
        ox, oz = origin[0], origin[2] - radius
        a = direction[0] ** 2 + direction[2] ** 2
        b = 2 * (ox * direction[0] + oz * direction[2])
        c = ox ** 2 + oz ** 2 - radius ** 2
        disc = b * b - 4 * a * c
        if a < 1e-9 or disc < 0:
            return None
        for t in sorted(((-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a))):
            if t <= 0:
                continue
            x, y, z = origin + direction * t
            u = math.atan2(x, radius - z) * radius / width + 0.5
            v = 0.5 - y / height
            if 0 <= u <= 1 and 0 <= v <= 1 and z < radius:   # the near half of the cylinder
                return t, u, v
        return None
    if abs(direction[2]) < 1e-9:
        return None
    t = -origin[2] / direction[2]
    if t <= 0:
        return None
    x, y, _ = origin + direction * t
    u, v = x / width + 0.5, 0.5 - y / height
    return (t, u, v) if 0 <= u <= 1 and 0 <= v <= 1 else None


def ray_direction(yaw, pitch):
    """Unit direction for a pointer / gaze at yaw, pitch (radians; yaw + = left, like head yaw)."""
    cp = math.cos(pitch)
    return np.array([-math.sin(yaw) * cp, math.sin(pitch), -math.cos(yaw) * cp])
