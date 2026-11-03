"""Which kind of VR video is this? The same rules for videos from any site, files and browser windows.

Like the VR players (DeoVR, Heresphere, Skybox), from the most to the least reliable:
1. Tags in the title, the page address or the file name - the naming everyone uses:
     180 / 360                      how much of the sphere it fills
     LR, SBS, 3dh, side-by-side     3D, the eyes' pictures next to each other (left = left eye)
     TB, OU, 3dv, over-under        3D, the eyes' pictures stacked (top = left eye)
     FISHEYE, FISHEYE190, MKX200,   fisheye lenses (round pictures) and their field of view in degrees
     MKX220, VRCA220, RF52
2. The picture: the halves showing the same details = 3D; round pictures with black corners = fisheye; the
   shape of one eye's picture tells 180° (about square) from 360° (twice as wide as tall).
Whatever is still unknown keeps the caller's guess.
"""
import re
from dataclasses import dataclass

import numpy as np

SAME_PICTURE = 0.3               # halves whose details correlate this much hold the same picture (3D);
                                 # measured: 3D 0.45-0.92, normal videos below 0.05
PHOTO_SAME_PICTURE = 0.45        # one still picture: stricter - ordinary photos of tiles, walls, water or
                                 # symmetric rooms reach 0.35 (measured on phone photos); real 3D photos 0.45+
DARK = 24                        # brightness (0-255) counted as black: borders, fisheye corners
FISHEYE_FOV = {"mkx200": 200, "mkx220": 220, "vrca220": 220, "rf52": 190}
DEFAULT_FISHEYE_FOV = 190

_WORD = r"(?<![a-z0-9]){}(?![a-z0-9])"
TAGS = {
    "side_by_side": re.compile("|".join(_WORD.format(t) for t in ("lr", "sbs", "3dh", r"side[\s_-]?by[\s_-]?side",
                                                                   "half[\\s_-]?sbs", "full[\\s_-]?sbs")), re.I),
    "top_bottom": re.compile("|".join(_WORD.format(t) for t in ("tb", "ou", "3dv", r"over[\s_-]?under",
                                                                 r"top[\s_-]?bottom")), re.I),
    "180": re.compile(_WORD.format(r"180(?:x180)?(?:°|deg)?") + "|" + _WORD.format("vr180"), re.I),
    "360": re.compile(_WORD.format(r"360(?:°|deg)?") + "|" + _WORD.format("vr360"), re.I),
    "fisheye": re.compile(r"(?<![a-z0-9])(fisheye(\d{3})?|mkx200|mkx220|vrca220|rf52)(?![a-z])", re.I),
}
VR_WORDS = re.compile(_WORD.format(r"(vr|3d|sbs|180|360|stereoscopic|virtual[\s_-]?reality|vr180|vr360)"), re.I)


@dataclass
class Layout:
    projection: str = None       # "equi" (360°), "equi180", "fisheye", "screen" (flat), or None = unknown
    stereo: str = None           # "mono", "side_by_side", "top_bottom", or None = unknown
    fov: float = DEFAULT_FISHEYE_FOV   # fisheye: degrees the lens sees

    def merged(self, other):
        """This layout, with what it doesn't know taken from `other`."""
        return Layout(self.projection or other.projection, self.stereo or other.stereo,
                      self.fov if self.projection == "fisheye" else other.fov)


def looks_vr(text):
    """A title / address that talks about VR (worth a closer look)."""
    return bool(VR_WORDS.search(text or "")) or bool(TAGS["fisheye"].search(text or ""))


def from_tags(*texts):
    """What the usual naming says (title, address, file name...)."""
    text = " ".join(t for t in texts if t).replace(".", " ")
    layout = Layout()
    if TAGS["side_by_side"].search(text):
        layout.stereo = "side_by_side"
    elif TAGS["top_bottom"].search(text):
        layout.stereo = "top_bottom"
    fisheye = TAGS["fisheye"].search(text)
    if fisheye:
        layout.projection = "fisheye"
        tag = fisheye.group(1).lower()
        layout.fov = float(fisheye.group(2)) if fisheye.group(2) else FISHEYE_FOV.get(tag, DEFAULT_FISHEYE_FOV)
    elif TAGS["180"].search(text):
        layout.projection = "equi180"
    elif TAGS["360"].search(text):
        layout.projection = "equi"
    return layout


# -- the picture ------------------------------------------------------------------------------------
def _similar(a, b):
    a, b = a - a.mean(), b - b.mean()
    norm = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / norm) if norm else 0.0


def _blocks(a, n):
    """Average of n x n pixel blocks (a smaller picture)."""
    h, w = a.shape[0] // n * n, a.shape[1] // n * n
    return a[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))


def _detail(a, k=5):
    """Fine detail only: the picture minus a blurred copy of itself."""
    s = 2 * k + 1
    c = np.pad(np.cumsum(np.cumsum(np.pad(a, k, mode="edge"), 0), 1), ((1, 0), (1, 0)))
    blur = (c[s:, s:] - c[:-s, s:] - c[s:, :-s] + c[:-s, :-s]) / (s * s)
    return a - blur[:a.shape[0], :a.shape[1]]


def _same_picture(a, b):
    """How much two halves show the same details (-1..1): only their middle (the edges often hold mirrored
    lens masks), allowing a small sideways shift (each eye sees things a little shifted)."""
    n = max(1, min(a.shape) // 96)
    a, b = _detail(_blocks(a, n)), _detail(_blocks(b, n))
    w = a.shape[1]
    m0, m1 = w // 5, w - w // 5
    return max(_similar(a[:, m0:m1], b[:, m0 + s:m1 + s])
               for s in range(-(w // 16), w // 16 + 1) if m0 + s >= 0 and m1 + s <= w)


def stereo_scores(gray):
    """(side by side, top / bottom) similarity of a frame's brightness (2D array)."""
    y = gray.astype(np.float32)
    h, w = y.shape
    return (_same_picture(y[:, :w // 2], y[:, w // 2:w // 2 * 2]),
            _same_picture(y[:h // 2], y[h // 2:h // 2 * 2]))


def stereo_of(scores, threshold=SAME_PICTURE):
    """The layout from averaged stereo_scores (threshold: PHOTO_SAME_PICTURE for a single still picture)."""
    side, stacked = scores
    if max(side, stacked) < threshold:
        return "mono"
    return "side_by_side" if side >= stacked else "top_bottom"


def content_box(gray):
    """(x0, y0, x1, y1) as parts of the picture (0..1) without black borders around it (a video playing
    in a window, letterboxed)."""
    h, w = gray.shape
    rows = np.nonzero(gray.max(axis=1) > DARK)[0]
    cols = np.nonzero(gray.max(axis=0) > DARK)[0]
    if len(rows) < h * 0.1 or len(cols) < w * 0.1:
        return 0.0, 0.0, 1.0, 1.0
    return cols[0] / w, rows[0] / h, (cols[-1] + 1) / w, (rows[-1] + 1) / h


def _is_fisheye(eye):
    """A round picture: the corners are black, the middle is not."""
    h, w = eye.shape
    k = max(2, min(h, w) // 12)
    corners = [eye[:k, :k], eye[:k, -k:], eye[-k:, :k], eye[-k:, -k:]]
    middle = eye[h // 3:2 * h // 3, w // 3:2 * w // 3]
    return all(c.mean() < DARK for c in corners) and middle.mean() > DARK * 1.5


def from_picture(gray, stereo=None):
    """What one frame (brightness, without black borders) shows. `stereo`: already known from the frames."""
    h, w = gray.shape
    stereo = stereo or stereo_of(stereo_scores(gray))
    eye = gray[:, :w // 2] if stereo == "side_by_side" else gray[:h // 2] if stereo == "top_bottom" else gray
    eh, ew = eye.shape
    if stereo == "mono":                       # a flat picture's shape says nothing (movies come 2:1 too,
        return Layout(None, stereo)            # dark scenes have dark corners): only tags can tell
    if _is_fisheye(eye):
        return Layout("fisheye", stereo)
    aspect = ew / max(eh, 1)
    if 1.9 < aspect < 2.1:
        return Layout("equi", stereo)          # each eye twice as wide as tall: all around
    if 0.8 < aspect < 1.25:
        return Layout("equi180", stereo)       # about square per eye: the half in front (VR180)
    return Layout(None, stereo)


def frame_gray(frame):
    """Brightness of a PyAV frame, small enough to look at quickly."""
    gray = frame.to_ndarray(format="gray")
    step = max(1, gray.shape[1] // 1920)
    return gray[::step, ::step]
