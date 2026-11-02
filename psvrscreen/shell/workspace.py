"""The rules of the VR space: where new windows open, making room, layers, moving and bending.

Windows are the entries of settings.panels (dicts); positions are angles around you (yaw, degrees, + = right)
plus height and distance. Pure functions on those dicts, plus WindowMove for an ongoing move.
"""
import math

from ..core.settings import LIMITS, clamp, locked

IN_FRONT_STEP = 0.35       # one layer: a new window in front comes this much closer / old ones go back (m)
ROOM_GAP_DEGREES = 2.0     # space left between windows that slid aside for a new one
TUCKED_BEHIND = 0.3        # a window that stepped back may stay this much of its width behind the new one
MAX_LAYER_DISTANCE = 4.0   # windows don't step back farther than this (m)
CURVE_PER_DEGREE = 0.01    # bending with the wheel button: curve change per degree of up / down movement
WHEEL_DISTANCE_FACTOR = 1.08  # mouse wheel while moving a window: 8% farther / closer per notch


def half_degrees(panel):
    """Half the angle a window covers around you, left to right."""
    return math.degrees(math.atan(panel["width"] / 2 / max(panel["distance"], 0.1)))


def add_window(store, panel, center_yaw):
    """Open a new window where you look; the others make room (see arrange_new). Returns its index."""
    return store.add_panel(panel, center_yaw, arrange=arrange_new)


# -- a new window -----------------------------------------------------------------------------------
def arrange_new(panels, new):
    """A new window opens right where you look and becomes the point in front. App windows already there
    step one layer back (same look) and slide aside just enough to peek out from behind it (left ones left,
    right ones right, a centered one to the emptier side). Monitors and
    position-locked windows stay; the new window comes just in front of them."""
    sliding, fixed = {}, []
    for p in panels:
        if not p.get("shown", True):
            continue
        if p.get("source") != "main" and not p.get("source", "").startswith("view") and not p.get("lock_position"):
            sliding[p["id"]] = [p]
        else:
            fixed.append(p)
    center, half = new["yaw"], half_degrees(new)
    blocks = []
    for members in sliding.values():
        left = min(m["yaw"] - half_degrees(m) for m in members)
        right = max(m["yaw"] + half_degrees(m) for m in members)
        if left < center + half and right > center - half:   # in the way: one layer back
            # ...but always in front of a monitor there, or it would disappear behind it
            behind = [f["distance"] - IN_FRONT_STEP for f in fixed
                      if abs(f["yaw"] - (left + right) / 2) < half_degrees(f) + (right - left) / 2]
            for m in members:
                _step_back(m, min(behind + [MAX_LAYER_DISTANCE]))
            left = min(m["yaw"] - half_degrees(m) for m in members)
            right = max(m["yaw"] + half_degrees(m) for m in members)
        blocks.append([left, right, members])
    sides, ahead = {1: [], -1: []}, []
    for b in blocks:
        mid = (b[0] + b[1]) / 2 - center
        (ahead if abs(mid) < 1.0 else sides[1 if mid > 0 else -1]).append(b)
    for b in ahead:   # straight ahead: to the side with less already there
        side = 1 if sum(x[1] - x[0] for x in sides[1]) <= sum(x[1] - x[0] for x in sides[-1]) else -1
        sides[side].append(b)
    # Push each block (closest to the new window first) past the edge of the one before it, minus what may
    # stay tucked behind.
    edge = center + half
    for left, right, members in sorted(sides[1], key=lambda b: b[0]):
        shift = max(0.0, edge - left - TUCKED_BEHIND * (right - left))
        for m in members:
            m["yaw"] = clamp("yaw", m["yaw"] + shift)
        edge = right + shift + ROOM_GAP_DEGREES
    edge = center - half
    for left, right, members in sorted(sides[-1], key=lambda b: -b[1]):
        shift = min(0.0, edge - right + TUCKED_BEHIND * (right - left))
        for m in members:
            m["yaw"] = clamp("yaw", m["yaw"] + shift)
        edge = left + shift - ROOM_GAP_DEGREES
    in_the_way = [p for p in fixed if abs(p["yaw"] - center) < half_degrees(p) + half]
    if in_the_way:
        _fit_in_front(new, min(in_the_way, key=lambda p: p["distance"]))


def _step_back(panel, farthest):
    """One layer farther away (not beyond `farthest`), grown by the same factor: it looks exactly as big
    as before (same text)."""
    distance = clamp("distance", min(panel["distance"] + IN_FRONT_STEP, farthest))
    if distance <= panel["distance"] or locked(panel, "distance"):
        return
    k = distance / panel["distance"]
    panel["distance"], panel["width"], panel["height"] = distance, panel["width"] * k, panel["height"] * k


def _fit_in_front(panel, behind):
    """Just in front of a monitor / locked window, shaped to match it: the same curve around the same
    center (like rings), so it doesn't poke through its curved edges. Coming closer it shrinks by the same
    factor, so it still looks just as big."""
    distance = clamp("distance", min(panel["distance"], behind["distance"] - IN_FRONT_STEP))
    k = distance / panel["distance"]
    panel["distance"], panel["width"], panel["height"] = distance, panel["width"] * k, panel["height"] * k
    panel["tilt"] = behind["tilt"]
    if behind["curvature"] > 0.001:
        radius_behind = behind["width"] / (2 * math.pi * behind["curvature"])
        radius = radius_behind - (behind["distance"] - panel["distance"])
        panel["curvature"] = clamp("curvature", panel["width"] / (2 * math.pi * radius)) if radius > 0.3 else 0.0
    else:
        panel["curvature"] = 0.0


# -- moving a window ---------------------------------------------------------------------------------
class WindowMove:
    """Moving a window with the pointer (its title bar, or Alt + drag). The window keeps the same spot under
    the pointer; the wheel pulls it closer / pushes it away."""

    def __init__(self, store, window_id, e):
        self.store, self.window_id = store, window_id
        me = next(p for p in store.get().panels if p["id"] == window_id)
        self.start = (e.yaw, e.pitch, me["yaw"], me["height"], me["distance"])
        self.distance = me["distance"]
        self.grab_distance = e.distance or me["distance"]   # the pointer dot stays on the grabbed spot

    def drag(self, e):
        yaw0, pitch0, panel_yaw, panel_height, _ = self.start
        d = self.distance
        new_yaw = clamp("yaw", panel_yaw + (e.yaw - yaw0))
        new_height = clamp("height", panel_height + d * (math.tan(math.radians(e.pitch))
                                                         - math.tan(math.radians(pitch0))))
        self.store.update_window(self.window_id, yaw=new_yaw, height=new_height, distance=d)

    def wheel(self, e):
        factor = 1 / WHEEL_DISTANCE_FACTOR if e.delta > 0 else WHEEL_DISTANCE_FACTOR
        self.distance = clamp("distance", self.distance * factor)
        self.grab_distance = max(0.3, self.grab_distance * factor)
        self.store.update_window(self.window_id, distance=self.distance)
        me = next((p for p in self.store.get().panels if p["id"] == self.window_id), None)
        if me:   # it moved straight back (its height too): keep dragging from here
            self.start = (e.yaw, e.pitch, me["yaw"], me["height"], me["distance"])
            self.distance = me["distance"]

    def release(self, e):
        pass


class WindowBend:
    """Wheel button held on a window + up / down: bends it more / flattens it."""

    def __init__(self, store, window_id, e):
        self.store, self.window_id = store, window_id
        me = next(p for p in store.get().panels if p["id"] == window_id)
        self.start_pitch, self.start_curve = e.pitch, me["curvature"]

    def drag(self, e):
        self.store.update_window(self.window_id,
                                 curvature=self.start_curve + (e.pitch - self.start_pitch) * CURVE_PER_DEGREE)

    def wheel(self, e):
        pass

    def release(self, e):
        pass


def nudge_distance(store, window_id, delta):
    """Alt + wheel on a window: closer / farther (straight back, see core.settings._straight_back)."""
    me = next((p for p in store.get().panels if p["id"] == window_id), None)
    if me is not None:
        lo, hi = LIMITS["distance"]
        factor = 1 / WHEEL_DISTANCE_FACTOR if delta > 0 else WHEEL_DISTANCE_FACTOR
        store.update_window(window_id, distance=max(lo, min(hi, me["distance"] * factor)))
