"""Open and close animations for everything the shell shows.

A surface that appears (a window, Home, a dialog, a menu, a title bar...) fades in while growing from a bit
smaller; one that goes away fades out while shrinking a little - if its picture still exists. Things that free
their picture when they close (dialogs, menus) hand the freeing to `later()`, so it happens after the fade.

Surfaces are rebuilt every frame: one is "the same" as last frame's if it has the same id and target.
Render thread only.
"""
import dataclasses

from ..core import geometry

OPEN_SECONDS = 0.22
CLOSE_SECONDS = 0.15
OPEN_FROM = 0.92        # size when appearing (grows to 1)
CLOSE_TO = 0.96         # size when gone


def _ease_out(p):
    return 1 - (1 - p) ** 3


def _key(surface):
    return surface.id, id(surface.target) if surface.target is not None else None


class Motion:
    def __init__(self):
        self._born = {}        # key -> when it appeared
        self._last = {}        # key -> last frame's surface (what fades out if it goes)
        self._leaving = []     # (surface, when it went)
        self._later = []       # (when, function)

    def later(self, fn):
        """Run `fn` (e.g. free a picture) after the close animation."""
        self._later.append((None, fn))

    def apply(self, surfaces, now):
        """This frame's surfaces, animated, plus the ones fading out (which can't be pointed at)."""
        due = [(t if t is not None else now + CLOSE_SECONDS + 0.05, fn) for t, fn in self._later]
        self._later = [(t, fn) for t, fn in due if t > now]
        for t, fn in due:
            if t <= now:
                fn()

        seen = {}
        for s in surfaces:
            if s.shape != "rect":
                continue
            key = _key(s)
            seen[key] = s
            p = min(1.0, (now - self._born.setdefault(key, now)) / OPEN_SECONDS)
            if p < 1.0:
                e = _ease_out(p)
                s.opacity *= e
                s.model = s.model @ geometry.scale(OPEN_FROM + (1 - OPEN_FROM) * e)
        for key, s in self._last.items():
            if key not in seen:
                self._leaving.append((dataclasses.replace(s, target=None), now))
        for key in [k for k in self._born if k not in seen]:
            del self._born[key]
        self._last = seen

        out = list(surfaces)
        still = []
        for s, went in self._leaving:
            p = (now - went) / CLOSE_SECONDS
            if p >= 1.0 or (s.texture is not None and not s.texture.ready):
                continue                       # done, or its picture is already gone
            still.append((s, went))
            e = _ease_out(p)
            out.append(dataclasses.replace(s, opacity=s.opacity * (1 - e),
                                           model=s.model @ geometry.scale(1 - (1 - CLOSE_TO) * e)))
        self._leaving = still
        return out

    def flush(self):
        """The VR picture stops: free what waits now, forget the rest."""
        for _, fn in self._later:
            fn()
        self._later, self._leaving, self._born, self._last = [], [], {}, {}
