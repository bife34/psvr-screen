"""Thumbnails of videos and pictures, made in the background and kept.

`get(path)` returns the thumbnail (a small PIL image) or None while it's being made - ask again later;
`version` grows whenever one is ready, so pages know when to redraw. A video's thumbnail is a frame a bit into
it; a 3D video or picture (its name says so, or its halves are the same picture) shows one eye's picture
(the left / top half) - anything else keeps its whole picture, whatever its shape. Thumbnails are kept on disk
(%LOCALAPPDATA%\\PSVRScreen\\thumbs) for next time - except in private mode, when they stay in memory only.
"""
import collections
import hashlib
import os
import queue
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from ..core.log import log_error
from . import files, recognize

WIDTH = 480
MEMORY = 400                      # thumbnails kept in memory
CACHE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PSVRScreen" / "thumbs"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


def _one_eye(image, path):
    """A 3D picture (two eyes' pictures side by side or stacked): one eye's. Not 3D: all of it."""
    stereo = recognize.from_tags(path.stem).stereo
    if stereo is None:
        small = image.convert("L")
        small.thumbnail((512, 512))
        stereo = recognize.stereo_of(recognize.stereo_scores(np.asarray(small)), recognize.PHOTO_SAME_PICTURE)
    w, h = image.size
    if stereo == "side_by_side":
        return image.crop((0, 0, w // 2, h))
    if stereo == "top_bottom":
        return image.crop((0, 0, w, h // 2))
    return image


def _video_frame(path):
    import av
    with av.open(files.source(path)) as container:
        stream = container.streams.video[0]
        duration = float(container.duration or 0) / 1_000_000
        if duration > 10:
            container.seek(int(min(duration * 0.1, 60) * 1_000_000))
        frame = next(container.decode(stream))
        return frame.to_image(), duration


class Thumbnails:
    def __init__(self):
        self.version = 0
        self.private = False              # set by the app: no files written while private
        self._memory = collections.OrderedDict()   # path -> (image, duration seconds or None)
        self._asked = set()
        self._queue = queue.LifoQueue()   # newest asks first: what you're looking at now
        threading.Thread(target=self._work, daemon=True, name="thumbnails").start()

    def get(self, path):
        """(thumbnail or None, video duration or None)."""
        key = str(path)
        if key in self._memory:
            self._memory.move_to_end(key)
            return self._memory[key]
        if key not in self._asked:
            self._asked.add(key)
            self._queue.put(path if files.in_vault(path) else Path(path))
        return None, None

    def _file(self, path):
        stat = path.stat()
        name = hashlib.sha1(f"3|{path}|{stat.st_size}|{stat.st_mtime}".encode("utf-8", "ignore")).hexdigest()
        return CACHE / f"{name}.jpg"

    def _work(self):
        while True:
            path = self._queue.get()
            try:
                result = self._make(path)
            except Exception:
                log_error(f"thumbnail {path.name}")
                result = None
            self._asked.discard(str(path))
            if result is None:
                continue
            self._memory[str(path)] = result
            while len(self._memory) > MEMORY:
                self._memory.popitem(last=False)
            self.version += 1

    def _make(self, path):
        if files.in_vault(path):       # a locked room's safe: made in memory, never written to disk
            return self._make_from(path)
        cached = self._file(path)
        if cached.exists():
            image = Image.open(cached)
            image.load()
            duration = float(image.info.get("comment", b"0") or 0) or None
            return image, duration
        image, duration = self._make_from(path)
        if not self.private:
            CACHE.mkdir(parents=True, exist_ok=True)
            image.save(cached, "JPEG", quality=85, comment=str(duration or 0).encode())
        return image, duration


    @staticmethod
    def _make_from(path):
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            image, duration = _video_frame(path)
            image = _one_eye(image, path)
        else:
            image, duration = Image.open(files.source(path)), None
            image.draft("RGB", (WIDTH * 2, WIDTH * 2))   # big photos: decode small, fast
            image = _one_eye(image.convert("RGB"), path)
        image.thumbnail((WIDTH, WIDTH))
        return image, duration


THUMBNAILS = Thumbnails()
