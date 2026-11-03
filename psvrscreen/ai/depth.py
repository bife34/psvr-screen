"""Depth: how near every part of a picture is (Depth Anything V2, small) - for 2D pictures and videos in 3D,
stepping into a photo and living photos.

`estimate(rgb)` -> float32 (h, w) from 0 (farthest) to 1 (nearest), at about `long_side` pixels on its long
side (a multiple of 14, as the model wants). Pictures' depth is kept on disk (not in private mode).
"""
import hashlib
import os

import numpy as np

from ..core.paths import DATA_DIR
from . import models

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
CACHE = DATA_DIR / "ai" / "depth"
PICTURE_SIDE = 518            # pictures: the model's own size (sharpest)
VIDEO_SIDE = 350              # videos: smaller, to keep up (~60 ms on 6 cores of a Ryzen 9 9900X)


def estimate(rgb, long_side=PICTURE_SIDE):
    """rgb: (h, w, 3) uint8 array."""
    import cv2
    h, w = rgb.shape[:2]
    k = long_side / max(h, w)
    th, tw = max(14, round(h * k / 14) * 14), max(14, round(w * k / 14) * 14)
    x = cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None]
    d = models.session("depth.onnx").run(None, {"pixel_values": x})[0][0]
    lo, hi = np.percentile(d, (2, 98))
    return np.clip((d - lo) / max(hi - lo, 1e-6), 0.0, 1.0).astype(np.float32)


def _cache_file(path, variant=""):
    stat = os.stat(path)
    name = hashlib.sha1(f"{variant}{path}|{stat.st_size}|{stat.st_mtime}".encode("utf-8", "ignore")).hexdigest()
    return CACHE / f"{name}.png"


def of_picture(path, rgb, private=False, variant=""):
    """A picture file's depth (kept on disk unless private)."""
    from PIL import Image
    try:
        cached = _cache_file(path, variant)
        if cached.exists():
            return np.asarray(Image.open(cached), np.float32) / 65535.0
    except OSError:
        cached = None
    depth = estimate(rgb)
    if cached is not None and not private:
        CACHE.mkdir(parents=True, exist_ok=True)
        Image.fromarray((depth * 65535).astype(np.uint16)).save(cached)
    return depth
