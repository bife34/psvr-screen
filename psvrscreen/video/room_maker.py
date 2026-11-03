"""Your photo as the room around you: a 360° surroundings picture (render/environment.py) made from a photo.

A 360° photo is used as it is. A normal photo is placed in front of you as wide as a camera sees (~100°) and
the rest of the sphere is filled from its own edges - stretched, blurred and darkened toward the back - so you
stand in its colors and light. (No AI painting of the missing parts: that needs far bigger models.)
"""
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..render import environment

WIDTH, HEIGHT = 4096, 2048
PHOTO_DEGREES = 100.0


def make(path, projection="screen"):
    """Saves the room picture; returns its name in Settings > Surroundings."""
    import cv2
    path = Path(path)
    name = "Photo - " + re.sub(r"[^\w\- ]", "", path.stem)[:40].strip()
    environment.MINE.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as image:
        image.draft("RGB", (WIDTH, WIDTH))
        image = image.convert("RGB")
    if projection in ("equi", "mesh") or 1.9 < image.width / image.height < 2.1:
        room = image.resize((WIDTH, HEIGHT), Image.LANCZOS)
    else:
        photo = np.asarray(image, np.float32)
        h, w = photo.shape[:2]
        lon = (np.arange(WIDTH, dtype=np.float32) + 0.5) / WIDTH * 2 * np.pi - np.pi      # 0 = in front
        lat = np.pi / 2 - (np.arange(HEIGHT, dtype=np.float32) + 0.5) / HEIGHT * np.pi
        lon, lat = np.meshgrid(lon, lat)
        x, y, z = np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)
        tan_half = np.tan(np.radians(PHOTO_DEGREES / 2))
        aspect = h / w
        zz = np.maximum(z, 1e-3)
        u = 0.5 + x / zz / (2 * tan_half)
        v = 0.5 - y / zz / (2 * tan_half * aspect)
        # outside the photo: its nearest edge (in direction), stretched along
        behind = z <= 1e-3
        u = np.where(behind, 0.5 + np.sign(x) * 0.5 * np.minimum(1, np.abs(lon) / np.pi * 2), u)
        v = np.where(behind, 0.5 - np.sign(y) * 0.5 * np.minimum(1, np.abs(lat) / (np.pi / 2) * 2), v)
        outside = np.maximum(np.maximum(-u, u - 1), np.maximum(-v, v - 1)).clip(0)
        outside = np.where(behind, 1.0 + (np.abs(lon) - np.pi / 2) / np.pi, outside)
        map_x = (np.clip(u, 0, 1) * (w - 1)).astype(np.float32)
        map_y = (np.clip(v, 0, 1) * (h - 1)).astype(np.float32)
        sharp = cv2.remap(photo, map_x, map_y, cv2.INTER_LINEAR)
        small = cv2.resize(photo, (64, max(8, int(64 * aspect))), interpolation=cv2.INTER_AREA)
        soft = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 6)
        blurred = cv2.remap(soft, map_x, map_y, cv2.INTER_LINEAR)
        k = np.clip(outside * 6, 0, 1)[..., None]
        dark = (1.0 - 0.55 * np.clip(outside, 0, 1))[..., None]
        room = Image.fromarray(np.clip((sharp * (1 - k) + blurred * k) * dark, 0, 255).astype(np.uint8))
        room = room.filter(ImageFilter.SMOOTH)
    room.save(environment.MINE / f"{name}.jpg", quality=92)
    environment.forget_preview(name)
    return name
